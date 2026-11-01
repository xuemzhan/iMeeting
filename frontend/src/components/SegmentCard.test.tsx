// @vitest-environment jsdom
/**
 * SegmentCard：转写条目的低置信提示、重要程度徽标、点击跳转与正文修订。
 *
 * 这些行为此前只在 e2e 里被间接覆盖，单测直接锁住渲染与回调契约
 * （对应审查报告 P3-5「前端零组件测试」）。
 */

import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { SegmentCard } from './SegmentCard'
import type { Segment } from '../api/types'

function makeSegment(overrides: Partial<Segment> = {}): Segment {
  return {
    id: 1,
    segmentId: 's-1',
    text: '这是第一句转写。',
    speaker: '说话人 1',
    speakerKey: 'spk_1',
    speakerScore: 0.9,
    speakerSource: 'cluster',
    isFinal: true,
    startMs: 65_000,
    endMs: 68_000,
    seq: 0,
    ...overrides,
  } as Segment
}

describe('SegmentCard', () => {
  it('渲染正文与时间戳', () => {
    render(<SegmentCard segment={makeSegment()} />)
    expect(screen.getByText('这是第一句转写。')).toBeTruthy()
    expect(screen.getByText('01:05')).toBeTruthy()
  })

  it('点击时间戳把毫秒位置回调给播放器', () => {
    const onSeek = vi.fn()
    render(<SegmentCard segment={makeSegment()} onSeek={onSeek} />)
    fireEvent.click(screen.getByText('01:05'))
    expect(onSeek).toHaveBeenCalledWith(65_000)
  })

  it('自动标注的低置信片段给出提示，人工标注不提示', () => {
    const { rerender } = render(
      <SegmentCard segment={makeSegment({ speakerScore: 0.4 })} />,
    )
    expect(screen.getByText(/低置信 40%/)).toBeTruthy()

    rerender(
      <SegmentCard segment={makeSegment({ speakerScore: 0.4, speakerSource: 'manual' })} />,
    )
    expect(screen.queryByText(/低置信/)).toBeNull()
  })

  it('关键参会人显示徽标，草稿不显示', () => {
    const { rerender } = render(
      <SegmentCard segment={makeSegment()} speakerImportance={1} />,
    )
    expect(screen.getByTitle(/关键参会人/)).toBeTruthy()

    rerender(<SegmentCard segment={makeSegment({ isFinal: false })} speakerImportance={1} />)
    expect(screen.queryByTitle(/关键参会人/)).toBeNull()
  })

  it('双击正文进入编辑，Enter 提交新文本', () => {
    const onEditText = vi.fn()
    render(<SegmentCard segment={makeSegment()} onEditText={onEditText} />)

    fireEvent.doubleClick(screen.getByText('这是第一句转写。'))
    const textarea = screen.getByLabelText('修订这段转写')
    fireEvent.change(textarea, { target: { value: '改好的转写。' } })
    fireEvent.keyDown(textarea, { key: 'Enter' })

    expect(onEditText).toHaveBeenCalledTimes(1)
    expect(onEditText.mock.calls[0][1]).toBe('改好的转写。')
  })

  it('Escape 放弃编辑，不触发回调', () => {
    const onEditText = vi.fn()
    render(<SegmentCard segment={makeSegment()} onEditText={onEditText} />)

    fireEvent.doubleClick(screen.getByText('这是第一句转写。'))
    fireEvent.keyDown(screen.getByLabelText('修订这段转写'), { key: 'Escape' })

    expect(screen.queryByLabelText('修订这段转写')).toBeNull()
    expect(onEditText).not.toHaveBeenCalled()
  })

  it('草稿片段不进入编辑态', () => {
    const onEditText = vi.fn()
    render(<SegmentCard segment={makeSegment({ isFinal: false })} onEditText={onEditText} />)
    fireEvent.doubleClick(screen.getByText('这是第一句转写。'))
    expect(screen.queryByLabelText('修订这段转写')).toBeNull()
  })
})
