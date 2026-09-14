import {settings} from '@devvit/web/server'
import type {RelayMediaItem} from '../shared/api.ts'

const RELAY_MESSAGE_PREFIX = 'RELAY:'
const MAX_MESSAGE_CHARS = 1900
const MAX_POST_ATTEMPTS = 3

function packIntoMessages(items: RelayMediaItem[]): string[] {
  const messages: string[] = []
  let batch: RelayMediaItem[] = []

  const flush = (): void => {
    if (batch.length === 0) return
    messages.push(RELAY_MESSAGE_PREFIX + JSON.stringify(batch))
    batch = []
  }

  for (const item of items) {
    const soloSize = RELAY_MESSAGE_PREFIX.length + JSON.stringify([item]).length
    if (soloSize > MAX_MESSAGE_CHARS) {
      console.error(
        `skipping media item - its URL alone exceeds Discord's message size limit: ${item.url}`,
      )
      continue
    }

    const candidate = [...batch, item]
    const size = RELAY_MESSAGE_PREFIX.length + JSON.stringify(candidate).length
    if (size > MAX_MESSAGE_CHARS && batch.length > 0) {
      flush()
    }
    batch.push(item)
  }
  flush()

  return messages
}

async function postToWebhook(
  webhookUrl: string,
  message: string,
): Promise<boolean> {
  for (let attempt = 1; attempt <= MAX_POST_ATTEMPTS; attempt++) {
    const rsp = await fetch(webhookUrl, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({content: message}),
    })

    if (rsp.ok) return true

    if (rsp.status === 429 && attempt < MAX_POST_ATTEMPTS) {
      const body = (await rsp.json().catch(() => null)) as {
        retry_after?: number
      } | null
      const retryAfterMs = Math.ceil((body?.retry_after ?? 1) * 1000)
      console.error(
        `discord webhook rate limited; retrying in ${retryAfterMs}ms (attempt ${attempt}/${MAX_POST_ATTEMPTS})`,
      )
      await new Promise(resolve => setTimeout(resolve, retryAfterMs))
      continue
    }

    const text = await rsp.text().catch(() => '')
    console.error(
      `discord webhook post failed: HTTP ${rsp.status} ${rsp.statusText}; ${text}`,
    )
    return false
  }

  return false
}

export async function relayMediaItems(items: RelayMediaItem[]): Promise<number> {
  const webhookUrl = await settings.get<string>('discordWebhookUrl')
  if (!webhookUrl) {
    console.error('discordWebhookUrl is not configured; cannot relay media')
    return 0
  }

  let sentCount = 0
  for (const message of packIntoMessages(items)) {
    try {
      const ok = await postToWebhook(webhookUrl, message)
      if (!ok) continue

      const payload = JSON.parse(message.slice(RELAY_MESSAGE_PREFIX.length))
      sentCount += Array.isArray(payload) ? payload.length : 0
    } catch (err) {
      console.error(
        `discord webhook request failed: ${err instanceof Error ? err.message : err}`,
      )
    }
  }

  return sentCount
}
