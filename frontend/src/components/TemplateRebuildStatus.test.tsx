// @vitest-environment jsdom
/**
 * 会后模板重建进度卡：状态展示、只在进行中轮询、手动重建与停用提示。
 *
 * 这些是上一轮新增界面，此前零测试。锁的要点：
 * - 空闲时不轮询（否则每个详情页都会定时打后端）；
 * - 进行中才轮询，且阶段文案要说人话；
 * - 手动重建返回 noise_disabled 时必须有可见提示，不能只写日志。
 */

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { voiceprintApi } from '../api/client'
import type { TemplateRebuildState } from '../api/types'
import { TemplateRebuildStatus } from './TemplateRebuildStatus'

vi.mock('../api/client', () => ({
  voiceprintApi: {
    rebuildStatus: vi.fn(),
    rebuildTemplates: vi.fn(),
  },
}))

const mocked = vi.mocked(voiceprintApi)

function state(overrides: Partial<TemplateRebuildState> = {}): TemplateRebuildState {
  return {
    meeting_id: 13,
    job_id: null,
    stage: 'idle',
    speakers_total: 0,
    speakers_done: 0,
    samples_done: 0,
    samples_cached: 0,
    samples_extracted: 0,
    templates_created: 0,
    templates_updated: 0,
    note: '',
    last_error: '',
    started_at: null,
    finished_at: null,
    updated_at: null,
    cache_hit_rate: null,
    ...overrides,
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  mocked.rebuildStatus.mockResolvedValue(state())
})


afterEach(() => {
  // 假定时器必须在每个用例后复位，否则后面的 waitFor 会挂死（这正是上一版超时的原因）。
  vi.useRealTimers()
})
describe('TemplateRebuildStatus', () => {
  it('从未重建过时显示「本场尚未重建」且不轮询', async () => {
    vi.useFakeTimers()
    try {
      render(<TemplateRebuildStatus meetingId={13} />)
      await act(async () => {
        await vi.advanceTimersByTimeAsync(0)
      })
      expect(mocked.rebuildStatus).toHaveBeenCalledTimes(1)
      expect(screen.getByText('本场尚未重建')).toBeTruthy()

      await act(async () => {
        await vi.advanceTimersByTimeAsync(9000)
      })
      expect(mocked.rebuildStatus).toHaveBeenCalledTimes(1)
    } finally {
      vi.useRealTimers()
    }
  })

  it('进行中显示阶段与说话人进度，并按 3 秒轮询', async () => {
    vi.useFakeTimers()
    mocked.rebuildStatus.mockResolvedValue(
      state({
        stage: 'extracting',
        speakers_total: 4,
        speakers_done: 2,
        samples_done: 9,
        samples_cached: 6,
        samples_extracted: 3,
        cache_hit_rate: 0.667,
      }),
    )
    try {
      render(<TemplateRebuildStatus meetingId={13} />)
      await act(async () => {
        await vi.advanceTimersByTimeAsync(0)
      })
      expect(screen.getByText('逐段抽取声纹')).toBeTruthy()
      expect(screen.getByText('2/4')).toBeTruthy()
      expect(screen.getByText('67%')).toBeTruthy()
      expect(screen.getByText('重建中…')).toBeTruthy()

      await act(async () => {
        await vi.advanceTimersByTimeAsync(3100)
      })
      expect(mocked.rebuildStatus.mock.calls.length).toBeGreaterThan(1)
    } finally {
      vi.useRealTimers()
    }
  })

  it('手动重建后提示本次停用了多少噪声身份', async () => {
    mocked.rebuildStatus.mockResolvedValue(state({ stage: 'done', speakers_total: 3, speakers_done: 3 }))
    mocked.rebuildTemplates.mockResolvedValueOnce({ meeting_id: 13, noise_disabled: 2 })
    render(<TemplateRebuildStatus meetingId={13} />)
    await waitFor(() => expect(screen.getByText('重建完成')).toBeTruthy())

    fireEvent.click(screen.getByText('重新重建'))

    expect(await screen.findByText(/另停用了 2 个证据不足的自动声纹/)).toBeTruthy()
    expect(mocked.rebuildTemplates).toHaveBeenCalledWith(13)
  })

  it('没有停用任何身份时不显示提示', async () => {
    mocked.rebuildTemplates.mockResolvedValueOnce({ meeting_id: 13, noise_disabled: 0 })
    render(<TemplateRebuildStatus meetingId={13} />)
    await waitFor(() => expect(screen.getByText('本场尚未重建')).toBeTruthy())

    fireEvent.click(screen.getByText('重新重建'))

    await waitFor(() => expect(mocked.rebuildTemplates).toHaveBeenCalled())
    expect(screen.queryByText(/另停用了/)).toBeNull()
  })

  it('重建失败显示后端错误，并把 last_error 展示出来', async () => {
    mocked.rebuildTemplates.mockRejectedValueOnce(new Error('会议不存在'))
    mocked.rebuildStatus.mockResolvedValue(state({ stage: 'failed', last_error: 'RuntimeError: 解码失败' }))
    render(<TemplateRebuildStatus meetingId={13} />)
    await waitFor(() => expect(screen.getByText('重建失败')).toBeTruthy())
    expect(screen.getByText('RuntimeError: 解码失败')).toBeTruthy()

    fireEvent.click(screen.getByText('重新重建'))

    expect(await screen.findByText('会议不存在')).toBeTruthy()
  })
})
