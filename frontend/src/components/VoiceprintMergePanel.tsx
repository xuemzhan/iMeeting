import { useState } from 'react'
import { GitMerge } from 'lucide-react'

import { voiceprintApi } from '../api/client'
import type { MergeSuggestionApplyResult, Voiceprint } from '../api/types'

export type MergeSuggestion = { score: number; source: Voiceprint; target: Voiceprint }

const DEFAULT_THRESHOLD = 0.92
const VISIBLE = 8

function seconds(ms: number): string {
  if (!ms) return '0 秒'
  return `${(ms / 1000).toFixed(1)} 秒`
}

function describe(print: Voiceprint): string {
  const kind = print.provisional ? '待命名' : '已命名'
  return `${kind} · ${print.sample_count} 条样本 · ${seconds(print.sample_ms)}`
}

/**
 * 「可能重复的声纹」：单对预览 + 全库近邻合并的执行入口。
 *
 * 合并是破坏性操作（source 会被删除、转写改挂到 target），所以这里默认只
 * 出计划（dry-run），并且默认只把「待命名」并进更完整的那一条。原来的页面
 * 只有一个「合并」小按钮，既没有预览也没有批量闭环。
 */
export function VoiceprintMergePanel({
  suggestions,
  onMerge,
  onReload,
}: {
  suggestions: MergeSuggestion[]
  onMerge: (source: Voiceprint, targetId: number) => Promise<void>
  onReload: () => void
}) {
  const [preview, setPreview] = useState<MergeSuggestion | null>(null)
  const [plan, setPlan] = useState<MergeSuggestionApplyResult | null>(null)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const clear = () => {
    setNotice(null)
    setError(null)
  }

  const dryRun = async () => {
    clear()
    setBusy(true)
    try {
      const result = await voiceprintApi.applyMergeSuggestions({
        dry_run: true,
        threshold: DEFAULT_THRESHOLD,
      })
      setPlan(result)
      if (result.plan.length === 0) {
        setNotice(
          `没有可自动执行的合并：${result.candidates} 对超过阈值，但都被「不自动合并两个已命名身份」或互斥规则挡下。`,
        )
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : '生成合并计划失败')
    } finally {
      setBusy(false)
    }
  }

  const applyPlan = async () => {
    if (!plan) return
    clear()
    setBusy(true)
    try {
      const result = await voiceprintApi.applyMergeSuggestions({
        dry_run: false,
        threshold: plan.threshold,
        limit: Math.max(1, plan.plan.length),
      })
      setNotice(
        `已合并 ${result.applied.length} 对` +
          (result.failed.length ? `，${result.failed.length} 对失败（已回滚该对）` : ''),
      )
      setPlan(null)
      setPreview(null)
      onReload()
    } catch (err) {
      setError(err instanceof Error ? err.message : '执行合并失败')
    } finally {
      setBusy(false)
    }
  }

  const mergeOne = async (item: MergeSuggestion) => {
    clear()
    setBusy(true)
    try {
      await onMerge(item.source, item.target.id)
      setNotice(`已把「${item.source.name}」合并进「${item.target.name}」`)
      setPreview(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : '合并失败')
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="panel mb-5 p-3">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <GitMerge size={14} style={{ color: 'var(--text-tertiary)' }} />
        <h2 className="text-ui font-medium" style={{ color: 'var(--text-secondary)' }}>
          可能重复的声纹
        </h2>
        <span className="text-meta" style={{ color: 'var(--text-tertiary)' }}>
          {suggestions.length} 对
        </span>
        <div className="flex-1" />
        <button type="button" className="btn btn-ghost" disabled={busy} onClick={() => void dryRun()}>
          {busy ? '处理中…' : '生成合并计划…'}
        </button>
      </div>

      <p className="mb-2 text-meta leading-relaxed" style={{ color: 'var(--text-tertiary)' }}>
        合并会<strong>删除</strong>被并的那一条，把它的转写改挂到保留的那一条，因此先预览、再执行。
        自动计划默认只把「待命名」并进更完整的一条，不会仅凭相似度合并两个已命名身份。
      </p>

      {notice && (
        <p className="mb-2 text-meta" style={{ color: 'var(--accent-text)' }}>
          {notice}
        </p>
      )}
      {error && (
        <p className="mb-2 text-meta" style={{ color: 'var(--danger-text)' }}>
          {error}
        </p>
      )}

      {plan && plan.plan.length > 0 && (
        <div
          className="mb-2 rounded-lg p-2 text-meta"
          style={{ background: 'var(--bg-subtle)', color: 'var(--text-secondary)' }}
        >
          <div className="mb-1 font-medium">
            计划合并 {plan.plan.length} 对（阈值 {Math.round(plan.threshold * 100)}%，候选{' '}
            {plan.candidates} 对）
          </div>
          <ul className="mb-2 space-y-0.5">
            {plan.plan.map((item) => (
              <li key={`${item.source.id}-${item.target.id}`} className="flex items-center gap-2">
                <span className="min-w-0 flex-1 truncate">
                  {item.source.name} → {item.target.name}
                </span>
                <span style={{ color: 'var(--text-tertiary)' }}>
                  {Math.round(item.score * 100)}%
                </span>
              </li>
            ))}
          </ul>
          <div className="flex gap-2">
            <button type="button" className="btn btn-ghost" disabled={busy} onClick={() => setPlan(null)}>
              取消
            </button>
            <button
              type="button"
              className="btn btn-primary"
              disabled={busy}
              onClick={() => void applyPlan()}
            >
              执行这 {plan.plan.length} 对
            </button>
          </div>
        </div>
      )}

      <div className="space-y-1.5">
        {suggestions.slice(0, VISIBLE).map((item) => {
          const open = preview?.source.id === item.source.id && preview?.target.id === item.target.id
          return (
            <div
              key={`${item.source.id}-${item.target.id}`}
              className="rounded-lg px-2 py-1.5 text-meta"
              style={{ background: 'var(--bg-subtle)' }}
            >
              <div className="flex flex-wrap items-center gap-2">
                <span className="min-w-0 flex-1 truncate">
                  {item.source.name} → {item.target.name}
                </span>
                <span style={{ color: 'var(--text-tertiary)' }}>
                  {Math.round(item.score * 100)}%
                </span>
                <button
                  type="button"
                  className="btn btn-ghost text-micro"
                  onClick={() => setPreview(open ? null : item)}
                >
                  {open ? '收起' : '预览'}
                </button>
                {open && (
                  <button
                    type="button"
                    className="btn btn-ghost text-micro"
                    disabled={busy}
                    onClick={() => void mergeOne(item)}
                  >
                    确认合并
                  </button>
                )}
              </div>

              {open && (
                <div className="mt-1 space-y-0.5 text-micro" style={{ color: 'var(--text-tertiary)' }}>
                  <div>
                    删除：<strong>{item.source.name}</strong>（{describe(item.source)}）
                  </div>
                  <div>
                    保留：<strong>{item.target.name}</strong>（{describe(item.target)}）
                  </div>
                  <div>
                    合并后「{item.source.name}」名下的转写会改挂到「{item.target.name}」，
                    该声纹本身被删除，不可撤销。
                  </div>
                </div>
              )}
            </div>
          )
        })}
      </div>

      {suggestions.length > VISIBLE && (
        <p className="mt-2 text-micro" style={{ color: 'var(--text-tertiary)' }}>
          只显示前 {VISIBLE} 对；「生成合并计划」会按阈值处理全部候选。
        </p>
      )}
    </section>
  )
}
