import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Check, GitMerge, Mic, Pencil, Power, Trash2 } from 'lucide-react'

import type { Voiceprint } from '../api/types'

export function VoiceprintAdminRow({
  print,
  editing,
  draft,
  onDraftChange,
  onStartEdit,
  onSubmit,
  onCancel,
  onDelete,
  others,
  onMerge,
  onToggleDisabled,
}: {
  print: Voiceprint
  editing: boolean
  draft: string
  onDraftChange: (value: string) => void
  onStartEdit: () => void
  onSubmit: () => void
  onCancel: () => void
  onDelete: () => void
  /** 可合并到的其它声纹 */
  others: Voiceprint[]
  onMerge: (targetId: number) => void
  onToggleDisabled: () => void
}) {
  const [mergeOpen, setMergeOpen] = useState(false)
  return (
    <li
      className="panel flex items-center justify-between gap-2 px-3 py-2"
      style={{ background: 'var(--bg-surface)' }}
    >
      {editing ? (
        <>
          <input
            autoFocus
            value={draft}
            onChange={(e) => onDraftChange(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') onSubmit()
              if (e.key === 'Escape') onCancel()
            }}
            maxLength={32}
            className="min-w-0 flex-1 rounded px-2 py-1 text-[13px] outline-none"
            style={{
              background: 'var(--bg-subtle)',
              color: 'var(--text-primary)',
              border: '1px solid var(--accent)',
            }}
          />
          <button
            type="button"
            onClick={onSubmit}
            className="btn btn-primary"
            style={{ height: 26, padding: '0 10px', fontSize: 12 }}
          >
            <Check size={12} />
            保存
          </button>
          <button
            type="button"
            onClick={onCancel}
            className="btn btn-ghost"
            style={{ height: 26, padding: '0 8px', fontSize: 12 }}
          >
            取消
          </button>
        </>
      ) : (
        <>
          <span
            className="flex min-w-0 items-center gap-2 text-[13px]"
            style={{ color: 'var(--text-primary)' }}
          >
            <Mic
              size={13}
              style={{
                color: print.provisional ? 'var(--text-tertiary)' : 'var(--accent)',
              }}
            />
            <span className="truncate font-medium">{print.name}</span>
            {print.provisional ? (
              <span
                className="shrink-0 rounded-full px-2 py-0.5 text-[10px]"
                style={{ background: 'var(--warn-soft)', color: 'var(--warn-text)' }}
              >
                待命名
              </span>
            ) : (
              <span
                className="shrink-0 text-[11px]"
                style={{ color: 'var(--text-tertiary)' }}
              >
                {print.sample_ms > 0 ? `${(print.sample_ms / 1000).toFixed(1)}s` : '来自会议'}
              </span>
            )}
            {print.source_meeting_id != null && (
              <Link
                to={`/history/${print.source_meeting_id}`}
                className="shrink-0 text-[11px] no-underline"
                style={{ color: 'var(--text-tertiary)' }}
              >
                首次出现于 #{print.source_meeting_id}
              </Link>
            )}
            <span className="shrink-0 text-[10px] text-tertiary">
              样本 {print.sample_count} · 质量 {Math.round(print.quality_score * 100)}%
            </span>
          </span>
          <span className="relative flex shrink-0 items-center">
            {others.length > 0 && (
              <button
                type="button"
                onClick={() => setMergeOpen((value) => !value)}
                className="btn btn-ghost"
                style={{ height: 26, width: 26, padding: 0 }}
                title="合并到其它声纹（同一个人的话被拆成多个时用）"
                aria-label={`合并 ${print.name}`}
              >
                <GitMerge size={13} />
              </button>
            )}
            <button
              type="button"
              onClick={onToggleDisabled}
              className="btn btn-ghost"
              style={{ height: 26, width: 26, padding: 0 }}
              title={print.disabled ? '启用该声纹' : '停用该声纹'}
            >
              <Power size={13} style={{ opacity: print.disabled ? 0.4 : 1 }} />
            </button>
            <button
              type="button"
              onClick={onStartEdit}
              className="btn btn-ghost"
              style={{ height: 26, width: 26, padding: 0 }}
              title="改名"
              aria-label={`改名 ${print.name}`}
            >
              <Pencil size={13} />
            </button>
            <button
              type="button"
              onClick={onDelete}
              className="btn btn-ghost"
              style={{ height: 26, width: 26, padding: 0 }}
              title="删除"
              aria-label={`删除 ${print.name}`}
            >
              <Trash2 size={13} />
            </button>

            {mergeOpen && (
              <div
                className="panel absolute right-0 top-7 z-10 w-[200px] overflow-hidden p-1"
                style={{ background: 'var(--bg-surface)' }}
              >
                <p
                  className="px-2 py-1 text-[10px]"
                  style={{ color: 'var(--text-tertiary)' }}
                >
                  合并到：
                </p>
                {others.map((other) => (
                  <button
                    key={other.id}
                    type="button"
                    onClick={() => {
                      setMergeOpen(false)
                      onMerge(other.id)
                    }}
                    className="block w-full truncate rounded px-2 py-1 text-left text-[12px]"
                    style={{ color: 'var(--text-primary)' }}
                  >
                    {other.name}
                  </button>
                ))}
              </div>
            )}
          </span>
        </>
      )}
    </li>
  )
}
