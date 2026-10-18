import type { ApiSegment } from '../api/types'

export interface Paragraph {
  key: string
  segmentId: string
  speaker: string | null
  text: string
  startMs: number
  endMs: number
  /** 合并段落内真实落库片段，编辑原文时按句定位。 */
  items: ApiSegment[]
}

const CONTINUATIONS = [
  '然后', '但是', '那', '如果', '所以', '因为', '就是', '不过',
  '而且', '并且', '比如说', '比如', '还有', '另外', '同时',
]

/**
 * 展示层段落合并：原始片段保留用于审计/时间戳，这里只把同一说话人、
 * 短停顿（≤2s）且语义上连续的碎片拼成可读段落。
 * 规则与后端纪要合并保持一致，避免历史页和纪要“看到两份不同的文本”。
 */
export function groupParagraphs(segments: ApiSegment[]): Paragraph[] {
  const ordered = [...segments].sort(
    (a, b) => a.start_ms - b.start_ms || a.seq - b.seq,
  )
  const out: Paragraph[] = []
  for (const seg of ordered) {
    const prev = out[out.length - 1]
    const text = seg.text || ''
    if (prev) {
      const gap = seg.start_ms - prev.endMs
      const continuation = CONTINUATIONS.some((p) => text.trimStart().startsWith(p))
      const combined = prev.text + text
      if (
        prev.speaker === seg.speaker &&
        gap <= 2000 &&
        (prev.text.length <= 40 || continuation) &&
        combined.length <= 220
      ) {
        prev.text = combined
        prev.endMs = seg.end_ms
        prev.key = `${prev.key}+${seg.segment_id}`
        prev.items.push(seg)
        continue
      }
    }
    out.push({
      key: seg.segment_id,
      segmentId: seg.segment_id,
      speaker: seg.speaker,
      text,
      startMs: seg.start_ms,
      endMs: seg.end_ms,
      items: [seg],
    })
  }
  return out
}
