// @vitest-environment jsdom
/**
 * 历史列表：页码越界后的回退。
 *
 * 原来这段逻辑在 effect 里（删空当前页就回退一页），会多一轮渲染并被
 * `react-hooks/set-state-in-effect` 拦下。现在改为在 `refresh()` 里做：
 * 失效完成后读一次缓存，当前页空了且不是首页就回退。这里锁住它。
 */

import {
  act,
  renderHook as baseRenderHook,
  waitFor,
  type RenderHookOptions,
} from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createElement, type ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { meetingApi } from '../api/client'
import { useHistoryMeetings } from './useHistoryMeetings'

vi.mock('../api/client', () => ({
  meetingApi: {
    search: vi.fn(),
    trash: vi.fn(),
    remove: vi.fn(),
    bulkRestore: vi.fn(),
  },
}))

vi.mock('../store/auth', () => ({
  useAuth: (selector: (state: { hasToken: () => boolean }) => unknown) =>
    selector({ hasToken: () => true }),
}))

const mocked = vi.mocked(meetingApi)

let client: QueryClient
const wrapper = ({ children }: { children: ReactNode }) =>
  createElement(QueryClientProvider, { client }, children)

function renderHook<T, P>(
  callback: (props: P) => T,
  options?: RenderHookOptions<P>,
) {
  return baseRenderHook(callback, { wrapper, ...options })
}

/** 造一页结果：`count` 条，`total` 是服务端总数（与页码无关）。 */
function page(count: number, total: number) {
  return {
    items: Array.from({ length: count }, (_, index) => ({
      id: index + 1,
      title: `会议 ${index + 1}`,
      created_at: '2026-11-01T00:00:00',
      status: 'done',
      duration_ms: 0,
      tags: [],
    })),
    total,
  } as never
}

beforeEach(() => {
  vi.clearAllMocks()
  client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0, staleTime: 0 } },
  })
})

describe('useHistoryMeetings', () => {
  it('删空当前页后 refresh 会回退一页', async () => {
    mocked.search.mockResolvedValue(page(20, 21))
    const { result } = renderHook(() =>
      useHistoryMeetings({ setOpNotice: vi.fn() }),
    )
    await waitFor(() => expect(result.current.meetings).toHaveLength(20))

    // 先换好桩再翻页：否则第 1 页的请求可能仍由旧桩解析
    mocked.search.mockResolvedValue(page(1, 21))
    act(() => result.current.setPage(1))
    await waitFor(() => expect(result.current.page).toBe(1))
    await waitFor(() => expect(result.current.meetings).toHaveLength(1))

    // 把最后一页的唯一一条也删掉：服务端返回空页，但总数仍大于 0
    mocked.search.mockResolvedValue(page(0, 20))
    await act(async () => {
      await result.current.refresh()
    })

    expect(result.current.page).toBe(0)
  })

  it('首页为空时不会把页码改成负数', async () => {
    mocked.search.mockResolvedValue(page(0, 0))
    const { result } = renderHook(() =>
      useHistoryMeetings({ setOpNotice: vi.fn() }),
    )
    await waitFor(() => expect(mocked.search).toHaveBeenCalled())

    await act(async () => {
      await result.current.refresh()
    })

    expect(result.current.page).toBe(0)
  })
})
