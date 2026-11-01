import { useCallback, useEffect, useState } from 'react'
import { AlertTriangle, Loader2, X } from 'lucide-react'

import { voiceprintApi } from '../api/client'
import type { Voiceprint, VoiceprintConflictItem } from '../api/types'

/**
 * 待处理的声纹确认冲突（REQ-002）。
 *
 * 展示「与自己差异大 / 更像别人」的样本，让用户三选一：确认为本人、判给别人、
 * 取消。取消的样本不会进入正式模板库，只留作阈值校准。
 */
export function VoiceprintConflictList({
  prints,
  onChanged,
}: {
  prints: Voiceprint[]
  onChanged: () => void
}) {
  const [items, setItems] = useState<VoiceprintConflictItem[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [busyId, setBusyId] = useState<number | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const list = await voiceprintApi.conflicts()
      setItems(list)
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : '读取冲突失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const decide = async (
    item: VoiceprintConflictItem,
    action: 'accept' | 'reassign' | 'cancel',
    target?: number,
  ) => {
    setBusyId(item.conflict_id)
    setError(null)
    try {
      await voiceprintApi.decideConflict(item.conflict_id, action, {
        target_voiceprint_id: target,
      })
      setItems((current) => current.filter((row) => row.conflict_id !== item.conflict_id))
      onChanged()
    } catch (err) {
      setError(err instanceof Error ? err.message : '处理冲突失败')
    } finally {
      setBusyId(null)
    }
  }

  if (!loading && items.length === 0 && !error) return null

  return (
    <section className="panel mb-5 p-3" aria-label="声纹确认冲突">
      <h2 className="mb-2 flex items-center gap-1.5 text-ui font-medium" style={{ color: 'var(--text-secondary)' }}>
        <AlertTriangle size={13} style={{ color: 'var(--warn-text)' }} />
        待核对的声纹（{items.length}）
      </h2>
      <p className="mb-2 text-meta leading-relaxed" style={{ color: 'var(--text-tertiary)' }}>
        这些样本与本人原有声纹差异较大，或与另一个人更像。确认后才会写进模板库，
        取消的样本只用于校准阈值。
      </p>

      {loading && (
        <p className="flex items-center gap-1 text-meta" style={{ color: 'var(--text-tertiary)' }}>
          <Loader2 size={11} className="animate-spin" /> 读取中…
        </p>
      )}

      <ul className="flex flex-col gap-1.5">
        {items.map((item) => (
          <li
            key={item.conflict_id}
            className="rounded-lg px-2.5 py-2"
            style={{ background: 'var(--bg-subtle)' }}
          >
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
              <span className="text-ui font-medium">{item.voiceprint_name ?? '未知'}</span>
              <span className="text-meta" style={{ color: 'var(--text-tertiary)' }}>
                与本人 {formatScore(item.self_score)}
              </span>
              {item.other_name && (
                <span
                  className="chip"
                  style={{ height: 18, background: 'var(--warn-soft)', color: 'var(--warn-text)' }}
                >
                  更像 {item.other_name}（{formatScore(item.other_score)}）
                </span>
              )}
              <span className="ml-auto text-micro" style={{ color: 'var(--text-tertiary)' }}>
                {item.reason === 'both'
                  ? '差异大且像别人'
                  : item.reason === 'diverge'
                    ? '与本人差异大'
                    : '与他人类似'}
              </span>
            </div>

            <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
              <button
                type="button"
                onClick={() => void decide(item, 'accept')}
                disabled={busyId === item.conflict_id}
                className="btn btn-ghost px-1.5 text-meta"
              >
                确认为本人
              </button>
              {prints.length > 0 && (
                <label className="flex items-center gap-1 text-meta" style={{ color: 'var(--text-tertiary)' }}>
                  判给别人
                  <select
                    defaultValue=""
                    onChange={(event) => {
                      const value = Number(event.target.value)
                      if (value) void decide(item, 'reassign', value)
                    }}
                    className="rounded px-1 py-0.5 text-meta"
                    style={{
                      background: 'var(--bg-surface)',
                      border: '1px solid var(--border-subtle)',
                      color: 'var(--text-primary)',
                    }}
                  >
                    <option value="">选择声纹…</option>
                    {prints
                      .filter((print) => print.id !== item.voiceprint_id)
                      .map((print) => (
                        <option key={print.id} value={print.id}>
                          {print.name}
                        </option>
                      ))}
                  </select>
                </label>
              )}
              <button
                type="button"
                onClick={() => void decide(item, 'cancel')}
                disabled={busyId === item.conflict_id}
                className="btn btn-ghost px-1.5 text-meta"
              >
                <X size={11} /> 取消
              </button>
            </div>
          </li>
        ))}
      </ul>

      {error && (
        <p className="mt-1.5 text-micro" style={{ color: 'var(--danger-text)' }}>
          {error}
        </p>
      )}
    </section>
  )
}

function formatScore(value: number | null): string {
  if (value == null) return '—'
  return `${Math.round(value * 100)}%`
}
