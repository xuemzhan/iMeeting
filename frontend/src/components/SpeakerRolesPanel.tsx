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
      <p className="text-meta text-tertiary">
        有说话人之后，可在这里标注关键 / 次要参会人。
      </p>
    )
  }
  const visible = speakers.slice(0, max)
  // 同名不同声音（声纹把两个人认成同一人）时，两行看起来一模一样；
  // 加一个序号，用户才能判断自己删的是哪一行。
  const nameCounts = new Map<string, number>()
  for (const row of visible) {
    nameCounts.set(row.speaker, (nameCounts.get(row.speaker) ?? 0) + 1)
  }
  const seen: Record<string, number> = {}

  return (
    <div>
      <p className="mb-2 flex items-center gap-1.5 text-meta font-medium text-tertiary">
        <Users size={12} />
        参会人重要程度
        <span className="font-normal opacity-80">· 1 最高</span>
      </p>
      <div className="space-y-1.5">
        {visible.map((row) => {
          const ordinal = (seen[row.speaker] = (seen[row.speaker] ?? 0) + 1)
          const duplicated = (nameCounts.get(row.speaker) ?? 0) > 1
          return (
          <div key={row.key} className="flex flex-wrap items-center gap-x-1.5 gap-y-1">
            <span
              className="min-w-0 flex-1 truncate text-meta text-secondary"
              title={`${row.speaker} · 发言 ${formatSpeech(row.speech_ms)} · ${row.segments} 段${row.speakerKey ? ` · ${row.speakerKey}` : ''}`}
            >
              {row.speaker}
              {duplicated && (
                <span className="ml-0.5 font-mono text-[9px] opacity-70">#{ordinal}</span>
              )}
            </span>
            <div
              role="radiogroup"
              aria-label={`${row.speaker}的重要程度`}
              className="flex shrink-0 gap-1"
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
                    className="whitespace-nowrap rounded-md px-1.5 py-0.5 text-micro font-medium transition-colors disabled:opacity-60"
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
            {/* 逐行确认 / 删除写成文字：12px 图标在 260px 侧栏里既小又容易被裁掉，
                用户会以为「没有这个功能」。窄的时候整组换行，也不藏在裁剪区外。 */}
            <div className="flex shrink-0 items-center gap-0.5">
              <button
                type="button"
                onClick={() => onConfirm?.(row)}
                disabled={!onConfirm}
                title="确认是参会人（确认后不再显示）"
                aria-label={`确认参会人 ${row.speaker}${duplicated ? ` #${ordinal}` : ''}`}
                className="inline-flex items-center gap-0.5 whitespace-nowrap rounded px-1 py-0.5 text-micro font-medium transition-colors hover:bg-[var(--bg-hover)] disabled:opacity-50"
                style={{ color: 'var(--accent-text)' }}
              >
                <Check size={11} />
                确认
              </button>
              <button
                type="button"
                onClick={() => onDelete?.(row)}
                disabled={!onDelete}
                title="删除该说话人（不是参会人，删除后不再显示）"
                aria-label={`删除参会人 ${row.speaker}${duplicated ? ` #${ordinal}` : ''}`}
                className="inline-flex items-center gap-0.5 whitespace-nowrap rounded px-1 py-0.5 text-micro font-medium transition-colors hover:bg-[var(--bg-hover)] disabled:opacity-50"
                style={{ color: 'var(--danger-text)' }}
              >
                <Trash2 size={11} />
                删除
              </button>
            </div>
          </div>
          )
        })}
      </div>
      {speakers.length > visible.length && (
        <p className="mt-1 text-micro text-tertiary">
          还有 {speakers.length - visible.length} 位说话人（发言较少，未列出）
        </p>
      )}
      {error && (
        <p
          className="mt-1.5 flex items-center gap-1 text-micro"
          style={{ color: 'var(--danger-text)' }}
        >
          <AlertCircle size={10} />
          {error}
        </p>
      )}
      <p className="mt-1.5 text-micro leading-relaxed text-tertiary">
        纪要以关键参会人为主干、次要参会人为补充，其余只作背景。确认或删除后，
        该说话人不再显示在这里。
      </p>
    </div>
  )
}
