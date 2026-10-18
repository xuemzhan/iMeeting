import { memo, useState } from 'react'
import { cn } from '../lib/cn'
import { speakerColorIndex, speakerLabel, formatTimestamp } from '../store/session'
import type { Segment } from '../api/types'

/**
 * 说话人色板 —— 只用「白黑灰红」四色族的层次来区分。
 *
 * 不引入蓝绿紫等新色相：多人会议里说话人标签会大量重复出现，
 * 一旦色相发散，整屏就会变花，红与黑这两个真正的重点色也会被淹没。
 * 这里靠「红 / 黑 / 深灰 / 浅灰」的明度与饱和差异来区分不同说话人。
 */
const SPEAKER_STYLES = [
  { bg: 'var(--accent-soft)', border: 'var(--color-brand-200)', text: 'var(--accent-text)', dot: 'var(--accent)' },
  { bg: 'var(--bg-subtle)', border: 'var(--border-strong)', text: 'var(--text-primary)', dot: 'var(--text-primary)' },
  { bg: 'var(--bg-subtle)', border: 'var(--border-default)', text: 'var(--text-secondary)', dot: 'var(--text-secondary)' },
  { bg: 'var(--warn-soft)', border: 'var(--border-default)', text: 'var(--warn-text)', dot: 'var(--warn)' },
  { bg: 'var(--danger-soft)', border: 'var(--color-brand-100)', text: 'var(--danger-text)', dot: 'var(--danger)' },
  { bg: 'var(--bg-subtle)', border: 'var(--border-subtle)', text: 'var(--text-tertiary)', dot: 'var(--text-tertiary)' },
]

interface Props {
  segment: Segment
  /** 点击时间戳跳转音频 */
  onSeek?: (ms: number) => void
  style?: React.CSSProperties
  /** 把该条语句的说话人标签改成真实姓名 */
  onRenameSpeaker?: (fromLabel: string, toName: string) => void
  /** 后端配置的低置信阈值 */
  lowConfidenceThreshold?: number
}

export const SegmentCard = memo(function SegmentCard({
  segment,
  onSeek,
  style,
  onRenameSpeaker,
  lowConfidenceThreshold = 0.62,
}: Props) {
  const colorIdx = speakerColorIndex(segment.speaker)
  const palette = SPEAKER_STYLES[colorIdx]
  const isDraft = !segment.isFinal
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState('')
  const label = speakerLabel(segment.speaker)
  const lowConfidence =
    segment.isFinal &&
    Boolean(segment.speaker) &&
    (segment.lowConfidence ??
      (segment.speakerSource !== 'manual' &&
        (segment.speakerScore ?? 1) < lowConfidenceThreshold))

  const commit = () => {
    const name = draft.trim()
    setEditing(false)
    if (name && segment.speaker) {
      onRenameSpeaker?.(segment.speaker, name)
    }
  }

  return (
    <div
      style={{
        ...style,
        contentVisibility: 'auto',
        containIntrinsicSize: '120px',
      }}
      className="px-4 pb-2"
    >
      <div
        className={cn(
          'rounded-[10px] border px-3.5 py-2.5 transition-colors',
          isDraft && 'animate-rise',
        )}
        style={{
          background: isDraft ? 'var(--bg-subtle)' : palette.bg,
          borderColor: isDraft ? 'var(--border-default)' : palette.border,
          borderStyle: isDraft ? 'dashed' : 'solid',
        }}
      >
        {/* 头部：说话人 + 时间戳 */}
        <div className="mb-1 flex items-center gap-2">
          <span
            className="size-1.5 shrink-0 rounded-full"
            style={{ background: isDraft ? 'var(--text-tertiary)' : palette.dot }}
            aria-hidden
          />
          {editing ? (
            <input
              autoFocus
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') commit()
                if (e.key === 'Escape') setEditing(false)
              }}
              onBlur={commit}
              maxLength={32}
              placeholder="输入姓名"
              aria-label="说话人姓名"
              className="w-[130px] rounded px-1.5 py-0.5 text-[11px] outline-none"
              style={{
                background: 'var(--bg-surface)',
                color: 'var(--text-primary)',
                border: '1px solid var(--accent)',
              }}
            />
          ) : isDraft ? (
            <span className="text-[11px] font-medium" style={{ color: 'var(--text-tertiary)' }}>
              识别中
            </span>
          ) : (
            <button
              type="button"
              onClick={() => {
                if (!segment.speaker || !onRenameSpeaker) return
                setDraft(label ?? '')
                setEditing(true)
              }}
              disabled={!segment.speaker || !onRenameSpeaker}
              title={
                segment.speaker && onRenameSpeaker
                  ? lowConfidence
                    ? '低置信声纹：点击姓名并确认即可人工校正'
                    : '点击可改为真实姓名'
                  : undefined
              }
              className="rounded text-[11px] font-medium transition-colors disabled:cursor-default"
              style={{
                color: palette.text,
                padding: '1px 4px',
                margin: '-1px -4px',
                background: 'transparent',
                border: '1px dashed transparent',
              }}
              onMouseEnter={(e) => {
                if (segment.speaker && onRenameSpeaker) {
                  e.currentTarget.style.borderColor = 'var(--border-strong)'
                }
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.borderColor = 'transparent'
              }}
            >
              {label ?? '转写'}
            </button>
          )}

          {lowConfidence && (
            <span
              className="chip shrink-0"
              style={{
                height: 18,
                background: 'var(--warn-soft)',
                color: 'var(--warn-text)',
              }}
              title="声纹相似度较低，点击姓名并确认可人工校正"
            >
              低置信 {Math.round((segment.speakerScore ?? 0) * 100)}%
            </span>
          )}

          {!isDraft && (
            <button
              type="button"
              onClick={() => onSeek?.(segment.startMs)}
              className="ml-auto rounded px-1.5 py-0.5 font-mono text-[11px] tabular-nums transition-colors hover:bg-black/5"
              style={{ color: 'var(--text-tertiary)' }}
              title="跳转到音频对应位置"
            >
              {formatTimestamp(segment.startMs)}
            </button>
          )}
        </div>

        {/* 正文 */}
        <p
          className={cn('text-[15px] leading-[1.65] whitespace-pre-wrap', isDraft && 'italic')}
          style={{ color: isDraft ? 'var(--text-tertiary)' : 'var(--text-primary)' }}
        >
          {segment.text || '正在聆听…'}
        </p>
        {segment.translation && (
          <p
            className="mt-1 border-t pt-1 text-[12px] leading-relaxed"
            style={{
              borderColor: 'var(--border-subtle)',
              color: 'var(--text-tertiary)',
            }}
          >
            {segment.translation}
          </p>
        )}
      </div>
    </div>
  )
})
