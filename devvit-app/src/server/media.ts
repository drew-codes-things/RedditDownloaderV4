import type {Post} from '@devvit/reddit'
import {reddit} from '@devvit/web/server'
import type {MediaFetchSort, RelayMediaItem} from '../shared/api.ts'

const DIRECT_MEDIA_EXTENSION_PATTERN = /\.(jpe?g|png|gif|gifv|webp|mp4|mp3|mov)$/i

const PAGE_SIZE = 100

function extractMediaItems(post: Post): RelayMediaItem[] {
  const base = {subreddit: post.subredditName, postId: post.id}

  const video = post.secureMedia?.redditVideo
  if (video) {
    const url = video.hlsUrl ?? video.dashUrl ?? video.fallbackUrl
    return url ? [{...base, url}] : []
  }

  if (post.gallery.length > 0) {
    return post.gallery.map(item => ({...base, url: item.url}))
  }

  if (DIRECT_MEDIA_EXTENSION_PATTERN.test(post.url)) {
    return [{...base, url: post.url}]
  }

  return []
}

export type FetchMediaOptions = {
  subredditName: string
  sort: MediaFetchSort
  keyword?: string
  count: number
}

export async function fetchMediaLinks(
  options: FetchMediaOptions,
): Promise<RelayMediaItem[]> {
  const {subredditName, sort, keyword, count} = options

  const listing = keyword?.trim()
    ? reddit.searchPosts({
        query: keyword.trim(),
        subredditName,
        sort: sort === 'hot' ? 'hot' : sort === 'top' ? 'top' : 'new',
        limit: count,
        pageSize: Math.min(count, PAGE_SIZE),
      })
    : sort === 'hot'
      ? reddit.getHotPosts({
          subredditName,
          limit: count,
          pageSize: Math.min(count, PAGE_SIZE),
        })
      : sort === 'top'
        ? reddit.getTopPosts({
            subredditName,
            timeframe: 'all',
            limit: count,
            pageSize: Math.min(count, PAGE_SIZE),
          })
        : reddit.getNewPosts({
            subredditName,
            limit: count,
            pageSize: Math.min(count, PAGE_SIZE),
          })

  const posts = await listing.all()
  return posts.flatMap(extractMediaItems)
}
