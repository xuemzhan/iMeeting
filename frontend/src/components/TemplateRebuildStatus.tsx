import { useCallback, useEffect, useState } from 'react'
import { Loader2, RefreshCcw } from 'lucide-react'

import { voiceprintApi } from '../api/client'
import type { TemplateRebuildStage, TemplateRebuildState } from '../api/types'

const RUNNING = new Set<TemplateRebuildStage>(['scanning', 'extracting', 'storing'])

const STAGE_TEXT: Record<TemplateRebuildStage, string> = {
  idle: '本场尚未重建',
  scanning: '扫描会议音频',
  extracting: '逐段抽取声纹',
  storing: '写入模板',
  done: '重建完成',
  failed: '重建失败',
  skipped: '已跳过（样本不足）',
}

function percent(value: number | null): string {
  if (value == null) return '—'
  return `${Math.round(value * 100)}%`
}

/**
 * 会后模板重建的进度与手动重跑入口。
 *
 * 后端 `GET /voiceprints/templates/rebuild-status` 早已返回按说话人推进的细粒度
 * 进度（R6），但前端一直没接，所以「重建到底跑没跑、卡在哪个说话人」对用户不可见。
 * 这里只在任务进行中轮询，空闲时不打扰后端。
 */
export function TemplateRebuildStatus({ meetingId }: { meetingId: number }) {
  const [state, setState] = useState<TemplateRebuildState | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const next = await voiceprintApi.rebuildStatus(meetingId)
      setState(next)
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : '读取重建进度失败')
    }
  }, [meetingId])

  // 首次加载放在异步回调里，避免在 effect 体内同步 setState（会触发级联渲染）。
  useEffect(() => {
    let alive = true
    void (async () => {
      try {
        const next = await voiceprintApi.rebuildStatus(meetingId)
        if (!alive) return
        setState(next)
        setError(null)
      } catch (err) {
        if (!alive) return
        setError(err instanceof Error ? err.message : '读取重建进度失败')
      }
    })()
    return () => {
      alive = false
    }
  }, [meetingId])

  const stage = state?.stage ?? 'idle'
  const running = busy || RUNNING.has(stage)

  useEffect(() => {
    if (!running) return
    const timer = window.setInterval(() => {
      void load()
    }, 3000)
    return () => window.clearInterval(timer)
  }, [running, load])

  const rebuild = async () => {
    setBusy(true)
    setError(null)
    setNotice(null)
    try {
      const result = await voiceprintApi.rebuildTemplates(meetingId)
      await load()
      if (result.noise_disabled) {
        setNotice(
          `另停用了 ${result.noise_disabled} 个证据不足的自动声纹（停用可恢复，见声纹管理）`,
        )
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : '触发重建失败')
    } finally {
      setBusy(false)
    }
  }

  const speakers = state
    ? `${state.speakers_done}/${state.speakers_total || '—'}`
    : '—'

  return (
    <section className="panel mt-3 overflow-hidden">
      <div className="panel-header">
        <RefreshCcw size={14} style={{ color: 'var(--text-tertiary)' }} />
        <h2>声纹模板重建</h2>
        <span className="ml-auto text-meta text-tertiary">{STAGE_TEXT[stage]}</span>
        <button
          type="button"
          className="btn btn-ghost"
          onClick={() => void rebuild()}
          disabled={running}
        >
          {running ? <Loader2 size={13} className="animate-spin" /> : null}
          {running ? '重建中…' : '重新重建'}
        </button>
      </div>

      <div className="px-3 pb-3 text-meta" style={{ color: 'var(--text-secondary)' }}>
        <div className="flex flex-wrap gap-x-4 gap-y-1">
          <span>
            说话人 <strong>{speakers}</strong>
          </span>
          <span>
            取样 <strong>{state?.samples_done ?? 0}</strong>
            <span style={{ color: 'var(--text-tertiary)' }}>
              （缓存 {state?.samples_cached ?? 0} · 实抽 {state?.samples_extracted ?? 0}）
            </span>
          </span>
          <span>
            缓存命中 <strong>{percent(state?.cache_hit_rate ?? null)}</strong>
          </span>
          <span>
            新建 <strong>{state?.templates_created ?? 0}</strong> · 更新{' '}
            <strong>{state?.templates_updated ?? 0}</strong>
          </span>
        </div>

        {state?.note && (
          <p className="mt-1 text-micro" style={{ color: 'var(--text-tertiary)' }}>
            {state.note}
          </p>
        )}
        {state?.last_error && (
          <p className="mt-1 text-micro" style={{ color: 'var(--danger-text)' }}>
            {state.last_error}
          </p>
        )}
        {notice && (
          <p className="mt-1 text-micro" style={{ color: 'var(--accent-text)' }}>
            {notice}
          </p>
        )}
        {error && (
          <p className="mt-1 text-micro" style={{ color: 'var(--danger-text)' }}>
            {error}
          </p>
        )}
        <p className="mt-1 text-micro" style={{ color: 'var(--text-tertiary)' }}>
          重建只重新聚类本场样本，不改已有转写标签；同一场会议重复执行结果一致。
        </p>
      </div>
    </section>
  )
}
