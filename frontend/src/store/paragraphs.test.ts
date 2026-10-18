import { describe, expect, it } from 'vitest'
import { groupParagraphs } from './paragraphs'
import type { ApiSegment } from '../api/types'

function seg(
  id: string,
  start: number,
  end: number,
  text: string,
  speaker: string | null = null,
): ApiSegment {
  return {
    id: 0,
    segment_id: id,
    text,
    is_final: true,
    start_ms: start,
    end_ms: end,
    speaker,
    seq: 0,
  }
}

describe('groupParagraphs', () => {
  it('合并同一说话人的短停顿碎片', () => {
    const out = groupParagraphs([
      seg('a', 0, 1500, '如果从软件模拟角度啊那。', '说话人 1'),
      seg('b', 2000, 3500, '我觉得仿真嘛。', '说话人 1'),
    ])
    expect(out).toHaveLength(1)
    expect(out[0].text).toBe('如果从软件模拟角度啊那。我觉得仿真嘛。')
    expect(out[0].startMs).toBe(0)
    expect(out[0].endMs).toBe(3500)
  })

  it('不跨说话人合并', () => {
    const out = groupParagraphs([
      seg('a', 0, 1500, '第一句。', '说话人 1'),
      seg('b', 2000, 3500, '第二句。', '说话人 2'),
    ])
    expect(out).toHaveLength(2)
  })

  it('长停顿保持独立', () => {
    const out = groupParagraphs([
      seg('a', 0, 1500, '第一句。', '说话人 1'),
      seg('b', 6000, 7500, '第二句。', '说话人 1'),
    ])
    expect(out).toHaveLength(2)
  })
})
