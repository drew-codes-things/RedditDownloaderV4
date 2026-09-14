export type ErrorRsp = {error: string; status: number}

export type GetCounterRsp = {count: number}

export type IncCounterReq = {amount: number}
export type IncCounterRsp = {count: number}

export type MediaFetchSort = 'hot' | 'new' | 'top'

export type FetchMediaFormValues = {
  sort: [MediaFetchSort]
  keyword?: string
  count: number
  subreddits?: string
}

export type RelayMediaItem = {
  url: string
  subreddit: string
  postId: string
}

export type Endpoint = (typeof Endpoint)[keyof typeof Endpoint]
export const Endpoint = {
  GetCounter: 'api/counter',
  IncCounter: 'api/counter/inc',
  OnAppInstall: 'internal/on/app/install',
  OnMenuNewPost: 'internal/on/menu/new-post',
  OnMenuFetchMedia: 'internal/on/menu/fetch-media',
  OnFormFetchMedia: 'internal/on/form/fetch-media',
} as const

export const EndpointMethod = {
  [Endpoint.GetCounter]: 'GET',
  [Endpoint.IncCounter]: 'POST',
  [Endpoint.OnAppInstall]: 'POST',
  [Endpoint.OnMenuNewPost]: 'POST',
  [Endpoint.OnMenuFetchMedia]: 'POST',
  [Endpoint.OnFormFetchMedia]: 'POST',
} as const satisfies {[endpoint: string]: 'GET' | 'POST'}
