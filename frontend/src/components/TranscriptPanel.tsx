import { useState } from 'react'
import { FileText, PencilLine } from 'lucide-react'

import type { ApiSegment } from '../api/types'
import { groupParagraphs, type Paragraph } from '../store/paragraphs'
import { formatTimestamp, speakerLabel } from '../store/session'

function paragraphLowScore(items: ApiSegment[]): number | null {
  const scores = items
    .filter((item) => Boolean(item.speaker) && item.speaker_low_confidence)
    .map((item) => item.speaker_score as number)
  return scores.length ? Math.min(...scores) : null
}

interface TranscriptPanelProps {
  segments: ApiSegment[]
  hasAudio: boolean
  onSeek: (ms: number) => void
  onRenameSpeaker: (fromLabel: string, name: string) => Promise<void>
  onConfirmSpeaker: (segment: ApiSegment) => Promise<void>
  onEditSegment: (segment: ApiSegment) => void
}

export function TranscriptPanel({
  segments,
  hasAudio,
  onSeek,
  onRenameSpeaker,
  onConfirmSpeaker,
  onEditSegment,
}: TranscriptPanelProps) {
  const [renaming, setRenaming] = useState<string | null>(null)
  const [renameDraft, setRenameDraft] = useState('')
  const [expandedParagraphs, setExpandedParagraphs] = useState<Set<string>>(new Set())
  const [visibleParagraphCount, setVisibleParagraphCount] = useState(200)
  const paragraphs = groupParagraphs(segments)
  const visibleParagraphs = paragraphs.slice(0, visibleParagraphCount)

  const commitRename = async (fromLabel: string) => {
    const name = renameDraft.trim()
    setRenaming(null)
    if (!name || name === speakerLabel(fromLabel)) return
    await onRenameSpeaker(fromLabel, name)
  }

  const toggleParagraph = (key: string) => {
    setExpandedParagraphs((previous) => {
      const next = new Set(previous)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  return (
    <section className="panel overflow-hidden">
      <div className="panel-header">
        <FileText size={14} style={{ color: 'var(--text-tertiary)' }} />
        <h2>完整转写</h2>
      </div>
      <div className="space-y-3 p-4">
        {segments.length === 0 && (
          <p className="py-8 text-center text-[12px] text-tertiary">没有转写内容</p>
        )}
        {visibleParagraphs.map((paragraph: Paragraph) => {
          const lowScore = paragraphLowScore(paragraph.items)
          const hasLowConfidence = lowScore != null
          return (
            <div
              key={paragraph.key}
              className="flex gap-3"
              style={{
                contentVisibility: 'auto',
                containIntrinsicSize: '150px',
              }}
            >
              {hasAudio ? (
                <button
                  type="button"
                  onClick={() => onSeek(paragraph.startMs)}
                  className="w-12 shrink-0 pt-0.5 font-mono text-[11px] tabular-nums text-tertiary transition-colors hover:text-[var(--accent)]"
                  title="跳转到此位置"
                >
                  {formatTimestamp(paragraph.startMs)}
                </button>
              ) : (
                <span className="w-12 shrink-0 pt-0.5 font-mono text-[11px] tabular-nums text-tertiary">
                  {formatTimestamp(paragraph.startMs)}
                </span>
              )}
              <div className="min-w-0 flex-1">
                {paragraph.speaker && renaming === paragraph.segmentId && (
                  <input
                    autoFocus
                    value={renameDraft}
                    onChange={(event) => setRenameDraft(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter') void commitRename(paragraph.speaker ?? '')
                      if (event.key === 'Escape') setRenaming(null)
                    }}
                    onBlur={() => void commitRename(paragraph.speaker ?? '')}
                    maxLength={32}
                    placeholder="输入姓名"
                    aria-label="说话人姓名"
                    className="mb-1 w-[140px] rounded px-1.5 py-0.5 text-[11px] outline-none"
                    style={{
                      background: 'var(--bg-surface)',
                      color: 'var(--text-primary)',
                      border: '1px solid var(--accent)',
                    }}
                  />
                )}
                {paragraph.speaker && renaming !== paragraph.segmentId && (
                  <button
                    type="button"
                    onClick={() => {
                      setRenaming(paragraph.segmentId)
                      setRenameDraft(speakerLabel(paragraph.speaker) ?? '')
                    }}
                    title="点击可改为真实姓名"
                    className="mb-0.5 inline-block rounded text-[11px] font-medium transition-colors"
                    style={{
                      color: 'var(--accent-text)',
                      padding: '0 3px',
                      margin: '0 -3px 2px',
                      background: 'transparent',
                      border: '1px dashed transparent',
                    }}
                  >
                    {speakerLabel(paragraph.speaker)}
                  </button>
                )}
                {hasLowConfidence && (
                  <button
                    type="button"
                    onClick={() =>
                      setExpandedParagraphs((previous) => {
                        const next = new Set(previous)
                        next.add(paragraph.key)
                        return next
                      })
                    }
                    className="chip mb-0.5 ml-1.5 inline-block align-middle"
                    style={{
                      height: 18,
                      background: 'var(--warn-soft)',
                      color: 'var(--warn-text)',
                    }}
                    title="该段包含低置信说话人，点击展开原始句确认"
                  >
                    低置信 {Math.round((lowScore ?? 0) * 100)}%
                  </button>
                )}
                <p className="text-[13px] leading-relaxed">{paragraph.text}</p>
                <button
                  type="button"
                  onClick={() => toggleParagraph(paragraph.key)}
                  className="mt-1 inline-flex items-center gap-1 rounded px-1 py-0.5 text-[10px] text-tertiary transition-colors hover:bg-[var(--bg-hover)] hover:text-secondary"
                >
                  <PencilLine size={10} />
                  {expandedParagraphs.has(paragraph.key) ? '收起原始句' : '按句修订文字'}
                </button>
                {expandedParagraphs.has(paragraph.key) && (
                  <div
                    className="mt-1.5 space-y-1 rounded-lg p-2"
                    style={{ background: 'var(--bg-subtle)' }}
                  >
                    {paragraph.items.map((segment) => (
                      <div
                        key={segment.segment_id}
                        className="flex items-start gap-2 border-b py-1.5 last:border-b-0"
                        style={{ borderColor: 'var(--border-subtle)' }}
                      >
                        <span className="shrink-0 pt-0.5 font-mono text-[10px] text-tertiary">
                          {formatTimestamp(segment.start_ms)}
                        </span>
                        <p className="min-w-0 flex-1 text-[11px] leading-relaxed text-secondary">
                          {segment.text}
                        </p>
                        {segment.edited_at && (
                          <span className="chip shrink-0" style={{ height: 18 }}>
                            已修订
                          </span>
                        )}
                        {(segment.speaker_source || segment.asr_source) && (
                          <span
                            className="shrink-0 text-[9px] text-tertiary"
                            title={[
                              segment.speaker_source && `声纹来源 ${segment.speaker_source}`,
                              segment.speaker_score != null &&
                                segment.speaker_score > 0 &&
                                `相似度 ${Math.round(segment.speaker_score * 100)}%`,
                              segment.asr_source && `ASR ${segment.asr_source}`,
                              segment.snr_db != null && `SNR ${segment.snr_db.toFixed(1)} dB`,
                              segment.adaptive_gain != null &&
                                `增益 ${segment.adaptive_gain.toFixed(2)}x`,
                            ]
                              .filter(Boolean)
                              .join(' · ')}
                          >
                            {segment.asr_source === 'streaming_fallback'
                              ? '草稿兜底'
                              : segment.speaker_source?.startsWith('context')
                                ? '上下文说话人'
                                : ''}
                          </span>
                        )}
                        {segment.speaker && segment.speaker_low_confidence && (
                          <button
                            type="button"
                            onClick={() => void onConfirmSpeaker(segment)}
                            className="chip shrink-0"
                            style={{
                              height: 18,
                              background: 'var(--warn-soft)',
                              color: 'var(--warn-text)',
                            }}
                            title="确认当前姓名正确"
                          >
                            确认 {Math.round((segment.speaker_score ?? 0) * 100)}%
                          </button>
                        )}
                        <button
                          type="button"
                          onClick={() => onEditSegment(segment)}
                          className="shrink-0 rounded px-1.5 py-0.5 text-[10px] transition-colors hover:bg-[var(--bg-hover)]"
                          style={{ color: 'var(--accent-text)' }}
                        >
                          修订
                        </button>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          )
        })}
        {paragraphs.length > visibleParagraphCount && (
          <div className="flex justify-center pt-1">
            <button
              type="button"
              className="btn text-[12px]"
              onClick={() => setVisibleParagraphCount((value) => value + 200)}
            >
              加载更多（剩余 {paragraphs.length - visibleParagraphCount} 段）
            </button>
          </div>
        )}
      </div>
    </section>
  )
}

