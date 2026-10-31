import { AlertCircle, Check, Trash2, Users } from 'lucide-react'

import { IMPORTANCE_OPTIONS, type SpeakerRow } from '../lib/speakerRoles'

interface Props {
  speakers: SpeakerRow[]
  onChange: (row: SpeakerRow, importance: number) => void
  error?: string | null
  disabled?: boolean
  /** 最多展示几行，其余折叠成一句话，避免把纪要面板挤爆 */
  max?: number
  /** 确认该说话人是参会人（确认后从面板消失） */
  onConfirm?: (row: SpeakerRow) => void
  /** 删除该说话人（不是参会人，删除后从面板消失） */
  onDelete?: (row: SpeakerRow) => void
}

function formatSpeech(ms: number): string {
  const total = Math.round((ms || 0) / 1000)
  if (total <= 0) return '—'
  const minutes = Math.floor(total / 60)
  const seconds = total % 60
  return minutes > 0
    ? `${minutes}′${String(seconds).padStart(2, '0')}″`
    : `${seconds}″`
}

/**
 * 参会人重要程度面板：关键（1，最高）/ 次要（2）/ 一般（3）。
 *
 * 纪要以关键参会人为主干、次要参会人作补充、其余作背景；这里的设置会随
 * 下一次生成纪要生效（会中草稿亦同）。
 */
export function SpeakerRolesPanel({
  speakers,
  onChange,
  error,
  disabled,
  onConfirm,
  onDelete,
  max = 8,
}: Props) {
  if (speakers.length === 0) {
    return (
      <p className="text-[11px] text-tertiary">
        有说话人之后，可在这里标注关键 / 次要参会人。
      </p>
    )
  }
  const visible = speakers.slice(0, max)

  return (
    <div>
      <p className="mb-2 flex items-center gap-1.5 text-[11px] font-medium text-tertiary">
        <Users size={12} />
        参会人重要程度
        <span className="font-normal opacity-80">· 1 最高</span>
      </p>
      <div className="space-y-1.5">
        {visible.map((row) => (
          <div key={row.key} className="flex items-center gap-1.5">
            <span
              className="w-14 shrink-0 truncate text-[11px] text-secondary"
              title={`${row.speaker} · 发言 ${formatSpeech(row.speech_ms)} · ${row.segments} 段`}
            >
              {row.speaker}
            </span>
            <div
              role="radiogroup"
              aria-label={`${row.speaker}的重要程度`}
              className="flex min-w-0 flex-1 gap-1"
            >
              {IMPORTANCE_OPTIONS.map((option) => {
                const active = row.importance === option.value
                return (
                  <button
                    key={option.value}
                    type="button"
                    role="radio"
                    aria-checked={active}
                    disabled={disabled}
                    onClick={() => onChange(row, option.value)}
                    title={option.hint}
                    className="flex-1 rounded-md px-1 py-0.5 text-[10px] font-medium transition-colors disabled:opacity-60"
                    style={{
                      background: active ? 'var(--accent-soft)' : 'var(--bg-subtle)',
                      color: active ? 'var(--accent-text)' : 'var(--text-tertiary)',
                      border: `1px solid ${active ? 'var(--accent)' : 'transparent'}`,
                    }}
                  >
                    {option.label}
                  </button>
                )
              })}
            </div>
            <span
              className="w-9 shrink-0 text-right font-mono text-[10px] tabular-nums text-tertiary"
              title="有效发言时长"
            >
              {formatSpeech(row.speech_ms)}
            </span>
            <div className="flex shrink-0 items-center">
              <button
                type="button"
                onClick={() => onConfirm?.(row)}
                disabled={disabled || !onConfirm}
                title="确认是参会人（确认后不再显示）"
                aria-label={`确认参会人 ${row.speaker}`}
                className="rounded px-1 py-0.5 transition-colors hover:bg-[var(--bg-hover)] disabled:opacity-50"
                style={{ color: 'var(--accent-text)' }}
              >
                <Check size={12} />
              </button>
              <button
                type="button"
                onClick={() => onDelete?.(row)}
                disabled={disabled || !onDelete}
                title="删除该说话人（不是参会人，删除后不再显示）"
                aria-label={`删除参会人 ${row.speaker}`}
                className="rounded px-1 py-0.5 transition-colors hover:bg-[var(--bg-hover)] disabled:opacity-50"
                style={{ color: 'var(--danger-text)' }}
              >
                <Trash2 size={12} />
              </button>
            </div>
          </div>
        ))}
      </div>
      {speakers.length > visible.length && (
        <p className="mt-1 text-[10px] text-tertiary">
          还有 {speakers.length - visible.length} 位说话人（发言较少，未列出）
        </p>
      )}
      {error && (
        <p
          className="mt-1.5 flex items-center gap-1 text-[10px]"
          style={{ color: 'var(--danger-text)' }}
        >
          <AlertCircle size={10} />
          {error}
        </p>
      )}
      <p className="mt-1.5 text-[10px] leading-relaxed text-tertiary">
        纪要以关键参会人为主干、次要参会人为补充，其余只作背景。确认或删除后，
        该说话人不再显示在这里。
      </p>
    </div>
  )
}
