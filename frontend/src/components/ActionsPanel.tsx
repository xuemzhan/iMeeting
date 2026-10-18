import { useEffect, useState } from 'react'
import { CheckSquare, Circle, Loader2 } from 'lucide-react'
import { meetingApi } from '../api/client'
import type { ActionItem } from '../api/types'

export function ActionsPanel({
  meetingId,
  onSeek,
}: {
  meetingId: number
  onSeek?: (ms: number) => void
}) {
  const [items, setItems] = useState<ActionItem[]>([])
  const [busy, setBusy] = useState<number | null>(null)

  const load = () =>
    meetingApi
      .actions(meetingId)
      .then(setItems)
      .catch(() => setItems([]))

  useEffect(() => {
    void load()
  }, [meetingId])

  const cycle = async (item: ActionItem) => {
    const next =
      item.status === 'open' ? 'doing' : item.status === 'doing' ? 'done' : 'open'
    setBusy(item.id)
    try {
      await meetingApi.updateAction(item.id, { status: next })
      await load()
    } finally {
      setBusy(null)
    }
  }

  if (items.length === 0) {
    return (
      <p className="px-4 py-3 text-[11px] text-tertiary">
        终稿纪要生成后，待办会自动同步到这里，可在应用内跟踪状态。
      </p>
    )
  }

  return (
    <div className="space-y-1.5 px-4 py-3">
      {items.map((item) => (
        <div
          key={item.id}
          className="flex items-start gap-2 rounded-lg px-2.5 py-2"
          style={{ background: 'var(--bg-subtle)' }}
        >
          <button
            type="button"
            onClick={() => void cycle(item)}
            disabled={busy === item.id}
            className="mt-0.5 shrink-0"
            title="切换状态"
          >
            {busy === item.id ? (
              <Loader2 size={14} className="animate-spin" />
            ) : item.status === 'done' ? (
              <CheckSquare size={14} style={{ color: 'var(--live-text)' }} />
            ) : (
              <Circle size={14} style={{ color: item.status === 'doing' ? 'var(--warn-text)' : 'var(--text-tertiary)' }} />
            )}
          </button>
          <div className="min-w-0 flex-1">
            <p
              className="text-[12px]"
              style={{
                textDecoration: item.status === 'done' ? 'line-through' : 'none',
                color: item.status === 'done' ? 'var(--text-tertiary)' : 'var(--text-primary)',
              }}
            >
              {item.task}
            </p>
            <p className="mt-0.5 text-[10px] text-tertiary">
              {item.owner && `责任人：${item.owner}`}
              {item.owner && item.due && ' · '}
              {item.due && `截止：${item.due}`}
              {item.status === 'doing' && ' · 进行中'}
            </p>
            {(() => {
              try {
                const evidence = JSON.parse(item.evidence || '[]') as {
                  start_ms: number
                }[]
                return evidence[0] && onSeek ? (
                  <button
                    type="button"
                    onClick={() => onSeek(evidence[0].start_ms)}
                    className="mt-0.5 text-[10px] underline-offset-2 hover:underline"
                    style={{ color: 'var(--accent-text)' }}
                  >
                    查看原话
                  </button>
                ) : null
              } catch {
                return null
              }
            })()}
          </div>
        </div>
      ))}
    </div>
  )
}
