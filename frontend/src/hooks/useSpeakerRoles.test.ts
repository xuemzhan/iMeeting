// @vitest-environment jsdom
/**
 * 参会人角色：基线 + 覆盖层。
 *
 * 原来详情数据晚到时靠在 effect 里 `adopt(initial)` 补种，既多一轮渲染又被
 * `react-hooks/set-state-in-effect` 拦下。现在 `initial` 直接参与基线派生，
 * 覆盖层只记用户改过的键，失败回滚＝删键。这里锁住三条最容易回归的行为。
 */

import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { meetingApi } from '../api/client'
import type { MeetingSpeaker } from '../api/types'
import { useSpeakerRoles } from './useSpeakerRoles'

vi.mock('../api/client', () => ({
  meetingApi: {
    speakers: vi.fn(),
    setSpeakerImportance: vi.fn(),
    setSpeakerState: vi.fn(),
  },
}))

const mocked = vi.mocked(meetingApi)

function speaker(overrides: Partial<MeetingSpeaker> = {}): MeetingSpeaker {
  return {
    speaker: '张三',
    speaker_key: 'spk_1',
    importance: 2,
    state: 0,
    segments: 3,
    speech_ms: 5000,
    ...overrides,
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  mocked.speakers.mockResolvedValue([speaker()])
})

describe('useSpeakerRoles', () => {
  it('接口结果既填充行，也决定重要程度基线', async () => {
    const { result } = renderHook(() => useSpeakerRoles(7))

    await waitFor(() => expect(result.current.speakers).toHaveLength(1))
    expect(Object.values(result.current.importanceByKey)).toEqual([2])
  })

  it('详情数据晚到时直接参与基线，不需要 effect 补种', async () => {
    mocked.speakers.mockResolvedValue([])
    const { result, rerender } = renderHook(
      ({ initial }: { initial?: MeetingSpeaker[] }) => useSpeakerRoles(7, { initial }),
      { initialProps: {} as { initial?: MeetingSpeaker[] } },
    )

    await waitFor(() => expect(mocked.speakers).toHaveBeenCalled())
    expect(result.current.speakers).toHaveLength(0)

    rerender({ initial: [speaker({ speaker_key: 'spk_2', importance: 1 })] })

    await waitFor(() => expect(result.current.speakers).toHaveLength(1))
    expect(Object.values(result.current.importanceByKey)).toEqual([1])
  })

  it('改重要程度失败后回到服务端基线', async () => {
    mocked.setSpeakerImportance.mockRejectedValueOnce(new Error('权限不足'))
    const { result } = renderHook(() => useSpeakerRoles(7))
    await waitFor(() => expect(result.current.speakers).toHaveLength(1))

    await act(async () => {
      await result.current.setImportance(result.current.speakers[0], 1)
    })

    expect(Object.values(result.current.importanceByKey)).toEqual([2])
    expect(result.current.error).toBe('权限不足')
  })
})
