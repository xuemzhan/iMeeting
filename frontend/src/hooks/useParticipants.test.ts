// @vitest-environment jsdom
/**
 * 参会人 hook：列表加载、乐观更新与回滚、抽取失败的文案、跨组件变更事件。
 *
 * 这些行为此前只有 e2e 覆盖（而且 e2e 用的是隔离夹具），单测直接锁住契约：
 * - 状态改动必须是「先乐观、失败回滚」，否则用户会看到点一下没反应或值来回跳；
 * - 「自动抽取次数已用完」与「转写不足」是两种不同提示，不能混成一句。
 */

import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { personApi } from '../api/client'
import type { MeetingParticipantItem } from '../api/types'
import { useParticipants } from './useParticipants'

vi.mock('../api/client', () => ({
  personApi: {
    participants: vi.fn(),
    extract: vi.fn(),
    updateParticipant: vi.fn(),
    addParticipant: vi.fn(),
    bind: vi.fn(),
    confirmAll: vi.fn(),
    merge: vi.fn(),
  },
}))

vi.mock('../app/queryClient', () => ({
  queryClient: { invalidateQueries: vi.fn(async () => undefined) },
}))

function person(overrides: Partial<MeetingParticipantItem> = {}): MeetingParticipantItem {
  return {
    participant_id: 1,
    person_id: 1,
    speaker: '张三',
    speaker_key: 'spk_1',
    role: '',
    importance: 3,
    state: 'pending',
    confidence: 0.8,
    ambiguous: false,
    evidence: [],
    attributes: [],
    ...overrides,
  }
}

const mocked = vi.mocked(personApi)

beforeEach(() => {
  vi.clearAllMocks()
  mocked.participants.mockResolvedValue([person()])
})

describe('useParticipants', () => {
  it('meetingId 为空时不请求后端', async () => {
    const { result } = renderHook(() => useParticipants(null))
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(mocked.participants).not.toHaveBeenCalled()
    expect(result.current.items).toEqual([])
  })

  it('加载成功填充列表', async () => {
    const { result } = renderHook(() => useParticipants(7))
    await waitFor(() => expect(result.current.items).toHaveLength(1))
    expect(mocked.participants).toHaveBeenCalledWith(7)
    expect(result.current.error).toBeNull()
  })

  it('加载失败给出错误文案', async () => {
    mocked.participants.mockRejectedValueOnce(new Error('后端 500'))
    const { result } = renderHook(() => useParticipants(7))
    await waitFor(() => expect(result.current.error).toBe('后端 500'))
  })

  it('状态修改先乐观生效，失败后回滚', async () => {
    mocked.updateParticipant.mockRejectedValueOnce(new Error('权限不足'))
    const { result } = renderHook(() => useParticipants(7))
    await waitFor(() => expect(result.current.items).toHaveLength(1))

    await act(async () => {
      await result.current.setState(1, 'confirmed')
    })

    expect(result.current.error).toBe('权限不足')
    expect(result.current.items[0].state).toBe('pending')
  })

  it('状态修改成功后采用后端返回值', async () => {
    mocked.updateParticipant.mockResolvedValueOnce(person({ state: 'confirmed' }))
    const { result } = renderHook(() => useParticipants(7))
    await waitFor(() => expect(result.current.items).toHaveLength(1))

    await act(async () => {
      await result.current.setState(1, 'confirmed')
    })

    expect(result.current.items[0].state).toBe('confirmed')
  })

  it('自动抽取额度用完与转写不足是两种文案', async () => {
    mocked.extract.mockResolvedValueOnce({ skipped: 'auto_limit' } as never)
    const { result } = renderHook(() => useParticipants(7))
    await waitFor(() => expect(result.current.items).toHaveLength(1))

    await act(async () => {
      await result.current.extract()
    })
    expect(result.current.error).toContain('自动抽取次数已用完')

    mocked.extract.mockResolvedValueOnce({ skipped: 'too_short' } as never)
    await act(async () => {
      await result.current.extract()
    })
    expect(result.current.error).toContain('转写内容不足')
  })

  it('新增参会人后同一 person_id 不重复', async () => {
    mocked.addParticipant.mockResolvedValueOnce(person({ person_id: 1, speaker: '张三（改）' }))
    const { result } = renderHook(() => useParticipants(7))
    await waitFor(() => expect(result.current.items).toHaveLength(1))

    await act(async () => {
      await result.current.add('张三（改）')
    })

    expect(result.current.items).toHaveLength(1)
    expect(result.current.items[0].speaker).toBe('张三（改）')
  })

  it('空白姓名不会发请求', async () => {
    const { result } = renderHook(() => useParticipants(7))
    await waitFor(() => expect(result.current.items).toHaveLength(1))

    await act(async () => {
      await result.current.add('   ')
    })

    expect(mocked.addParticipant).not.toHaveBeenCalled()
  })

  it('外部广播的参会人变更事件会触发重载', async () => {
    const { result } = renderHook(() => useParticipants(7))
    await waitFor(() => expect(result.current.items).toHaveLength(1))
    expect(mocked.participants).toHaveBeenCalledTimes(1)

    act(() => {
      window.dispatchEvent(new CustomEvent('imeeting-participants-changed', { detail: 7 }))
    })

    await waitFor(() => expect(mocked.participants).toHaveBeenCalledTimes(2))
  })

  it('首次取数期间 loading 即为真，取到后转假（不靠 effect 里同步 setState）', async () => {
    let resolve: (value: MeetingParticipantItem[]) => void = () => undefined
    mocked.participants.mockImplementationOnce(
      () =>
        new Promise<MeetingParticipantItem[]>((res) => {
          resolve = res
        }),
    )
    const { result } = renderHook(() => useParticipants(7))

    // 派生值在首帧就是 true：不需要 effect 再触发一轮渲染才看到加载态。
    expect(result.current.loading).toBe(true)

    await act(async () => {
      resolve([person()])
    })
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.items).toHaveLength(1)
  })

  it('切换会议会重新进入加载态', async () => {
    const { result, rerender } = renderHook(({ id }: { id: number }) => useParticipants(id), {
      initialProps: { id: 7 },
    })
    await waitFor(() => expect(result.current.items).toHaveLength(1))
    expect(result.current.loading).toBe(false)

    mocked.participants.mockImplementationOnce(
      () => new Promise<MeetingParticipantItem[]>(() => undefined),
    )
    rerender({ id: 8 })

    await waitFor(() => expect(result.current.loading).toBe(true))
  })

  it('meetingId 变为 null 后对外列表清空，也不再请求', async () => {
    const { result, rerender } = renderHook(
      ({ id }: { id: number | null }) => useParticipants(id),
      { initialProps: { id: 7 as number | null } },
    )
    await waitFor(() => expect(result.current.items).toHaveLength(1))

    rerender({ id: null })

    expect(result.current.items).toEqual([])
    expect(result.current.loading).toBe(false)
    expect(mocked.participants).toHaveBeenCalledTimes(1)
  })
})
