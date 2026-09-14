import {once} from 'node:events'
import type {IncomingMessage, ServerResponse} from 'node:http'
import {context, reddit, settings} from '@devvit/web/server'
import type {
  PartialJsonValue,
  TriggerResponse,
  UiResponse,
} from '@devvit/web/shared'
import {
  Endpoint,
  EndpointMethod,
  type ErrorRsp,
  type FetchMediaFormValues,
  type GetCounterRsp,
  type IncCounterReq,
  type IncCounterRsp,
  type RelayMediaItem,
} from '../shared/api.ts'
import {dbGetCounter, dbIncCounter} from './db.ts'
import {fetchMediaLinks} from './media.ts'
import {relayMediaItems} from './relay.ts'

type AnyRsp =
  | GetCounterRsp
  | IncCounterRsp
  | UiResponse
  | TriggerResponse
  | ErrorRsp

export async function onReq(
  reqMsg: IncomingMessage,
  rspMsg: ServerResponse,
): Promise<void> {
  try {
    await route(reqMsg, rspMsg)
  } catch (err) {
    const msg = `server error; ${err instanceof Error ? err.stack : err}`
    console.error(msg)
    writeJson<ErrorRsp>(500, {error: msg, status: 500}, rspMsg)
  }
}

async function route(
  reqMsg: IncomingMessage,
  rspMsg: ServerResponse,
): Promise<void> {
  const endpoint = reqMsg.url?.slice(1) as Endpoint
  const method = EndpointMethod[endpoint]

  let rsp: AnyRsp
  if (method !== reqMsg.method) {
    rsp = {error: 'not found', status: 404}
  } else {
    switch (endpoint) {
      case Endpoint.GetCounter:
        rsp = await routeGetCounter()
        break
      case Endpoint.IncCounter:
        rsp = await routeInc(reqMsg)
        break
      case Endpoint.OnMenuNewPost:
        rsp = await routeMenuNewPost()
        break
      case Endpoint.OnAppInstall:
        rsp = await routeAppInstall()
        break
      case Endpoint.OnMenuFetchMedia:
        rsp = routeFetchMediaMenu()
        break
      case Endpoint.OnFormFetchMedia:
        rsp = await routeFetchMediaForm(reqMsg)
        break
      default:
        endpoint satisfies never
        rsp = {error: 'not found', status: 404}
        break
    }
  }

  writeJson<PartialJsonValue>('status' in rsp ? rsp.status : 200, rsp, rspMsg)
}

async function routeGetCounter(): Promise<GetCounterRsp> {
  const t3 = context.postId
  if (!t3) throw Error('no t3')
  return {count: await dbGetCounter(t3)}
}

async function routeInc(reqMsg: IncomingMessage): Promise<IncCounterRsp> {
  const t3 = context.postId
  if (!t3) throw Error('no t3')
  const req = await readJson<IncCounterReq>(reqMsg)
  return {count: await dbIncCounter(t3, req.amount)}
}

async function routeMenuNewPost(): Promise<UiResponse> {
  const post = await reddit.submitCustomPost({title: context.appSlug})
  return {
    showToast: {text: `Post ${post.id} created.`, appearance: 'success'},
    navigateTo: post.url,
  }
}

function routeFetchMediaMenu(): UiResponse {
  return {
    showForm: {
      name: 'fetchMediaForm',
      form: {
        title: 'Fetch Media to Server',
        acceptLabel: 'Fetch',
        fields: [
          {
            type: 'string',
            name: 'subreddits',
            label: 'Subreddit(s)',
            helpText:
              'Comma-separated, no "r/" prefix. Leave blank to use this subreddit.',
            required: false,
          },
          {
            type: 'select',
            name: 'sort',
            label: 'Sort',
            required: true,
            defaultValue: ['hot'],
            options: [
              {label: 'Hot', value: 'hot'},
              {label: 'New', value: 'new'},
              {label: 'Top', value: 'top'},
            ],
          },
          {
            type: 'string',
            name: 'keyword',
            label: 'Keyword (optional)',
            helpText:
              'Leave blank to fetch the sorted listing without a search.',
            required: false,
          },
          {
            type: 'number',
            name: 'count',
            label: 'Number of posts to scan (per subreddit)',
            defaultValue: 25,
            required: true,
          },
        ],
      },
    },
  }
}

async function routeFetchMediaForm(
  reqMsg: IncomingMessage,
): Promise<UiResponse> {
  const body = await readJson<
    FetchMediaFormValues | {values: FetchMediaFormValues}
  >(reqMsg)
  const values = 'values' in body ? body.values : body

  const rawCount = Number(values.count)
  if (!Number.isFinite(rawCount)) {
    return {
      showToast: {
        text: 'Invalid post count.',
        appearance: 'neutral',
      },
    }
  }
  const count = Math.max(1, Math.min(500, Math.floor(rawCount)))
  const sort = values.sort?.[0] ?? 'hot'

  const discordWebhookUrl = await settings.get<string>('discordWebhookUrl')
  if (!discordWebhookUrl) {
    return {
      showToast: {
        text: 'No Discord webhook URL configured - set it in the app settings.',
        appearance: 'neutral',
      },
    }
  }

  const subredditNames = values.subreddits?.trim()
    ? values.subreddits
        .split(',')
        .map(name => name.trim().replace(/^r\//i, ''))
        .filter(name => name.length > 0)
    : [context.subredditName]

  const results = await Promise.allSettled(
    subredditNames.map(subredditName =>
      fetchMediaLinks({
        subredditName,
        sort,
        keyword: values.keyword,
        count,
      }),
    ),
  )

  const items: RelayMediaItem[] = []
  const failedSubreddits: string[] = []
  for (const [index, result] of results.entries()) {
    if (result.status === 'fulfilled') {
      items.push(...result.value)
    } else {
      failedSubreddits.push(subredditNames[index] ?? '?')
      console.error(
        `failed to fetch r/${subredditNames[index]}: ${result.reason}`,
      )
    }
  }

  const failedSuffix =
    failedSubreddits.length > 0
      ? ` (couldn't fetch: ${failedSubreddits.map(name => `r/${name}`).join(', ')})`
      : ''

  if (items.length === 0) {
    return {
      showToast: {
        text: `No matching media found.${failedSuffix}`,
        appearance: 'neutral',
      },
    }
  }

  const relayed = await relayMediaItems(items)
  return {
    showToast: {
      text: `Relayed ${relayed} of ${items.length} media link(s) to your server.${failedSuffix}`,
      appearance: 'success',
    },
  }
}

async function routeAppInstall(): Promise<TriggerResponse> {
  await reddit.submitCustomPost({title: context.appSlug})
  return {}
}

async function readJson<T>(reqMsg: IncomingMessage): Promise<T> {
  const chunks: Uint8Array[] = []
  reqMsg.on('data', chunk => chunks.push(chunk))
  await once(reqMsg, 'end')
  return JSON.parse(`${Buffer.concat(chunks)}`)
}

function writeJson<T extends PartialJsonValue>(
  status: number,
  json: Readonly<T>,
  rsp: ServerResponse,
): void {
  const body = JSON.stringify(json)
  const len = Buffer.byteLength(body)
  rsp.writeHead(status, {
    'Content-Length': len,
    'Content-Type': 'application/json',
  })
  rsp.end(body)
}
