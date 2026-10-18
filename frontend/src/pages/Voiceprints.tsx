import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowLeft, Mic, Trash2, Pencil, RefreshCw, Check, Users, GitMerge, Power } from 'lucide-react'
import { TopBar } from '../components/TopBar'
import { VoiceprintEnroll } from '../components/VoiceprintEnroll'
import { voiceprintApi } from '../api/client'
import { useSession } from '../store/session'
import type { Voiceprint } from '../api/types'

/**
 * 声纹管理页。
 *
 * 与设置抽屉里的那一段的区别：这里是**完整管理**——查看全部声纹、
 * 逐个改名/删除、批量清理待命名项。设置抽屉里只保留「快速录入」，
 * 免得设置面板被撑得过长。
 */
export default function Voiceprints() {
  const [prints, setPrints] = useState<Voiceprint[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [ok, setOk] = useState<string | null>(null)
  const [editingId, setEditingId] = useState<number | null>(null)
  const [draft, setDraft] = useState('')
  const [suggestions, setSuggestions] = useState<
    { score: number; source: Voiceprint; target: Voiceprint }[]
  >([])

  const devices = useSession((s) => s.devices)
  const activeDeviceId = useSession((s) => s.activeDeviceId)
  const refreshDevices = useSession((s) => s.refreshDevices)

  const load = () => {
    setLoading(true)
    voiceprintApi
      .list()
      .then((d) => {
        setPrints(d)
        setError(null)
        void voiceprintApi.suggestions().then(setSuggestions).catch(() => setSuggestions([]))
      })
      .catch((e) => setError(e instanceof Error ? e.message : '加载失败'))
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    load()
    void refreshDevices()
  }, [refreshDevices])

  const startEdit = (p: Voiceprint) => {
    setEditingId(p.id)
    setDraft(p.name)
    setOk(null)
  }

  const submitEdit = async (p: Voiceprint) => {
    const name = draft.trim()
    if (!name || name === p.name) {
      setEditingId(null)
      return
    }
    try {
      // 按 id 改名：后端会按该声纹所属会议回溯更新片段，
      // 并通知正在录制的会话立即使用新名字。
      const res = await voiceprintApi.rename(p.id, name)
      setOk(`已改名为「${res.name}」`)
      setError(null)
      setEditingId(null)
      load()
    } catch (e) {
      setError(e instanceof Error ? e.message : '改名失败')
    }
  }

  const remove = async (p: Voiceprint) => {
    try {
      await voiceprintApi.remove(p.id)
      setOk(`已删除「${p.name}」`)
      setError(null)
      load()
    } catch (e) {
      setError(e instanceof Error ? e.message : '删除失败')
    }
  }

  const merge = async (source: Voiceprint, targetId: number) => {
    try {
      const res = await voiceprintApi.merge(source.id, targetId)
      setOk(
        `已把「${source.name}」合并进「${res.name}」` +
          (res.updated_segments > 0 ? `，更新了 ${res.updated_segments} 条转写` : ''),
      )
      setError(null)
      load()
    } catch (e) {
      setError(e instanceof Error ? e.message : '合并失败')
    }
  }

  const toggleDisabled = async (print: Voiceprint) => {
    try {
      await voiceprintApi.setDisabled(print.id, !print.disabled)
      setOk(`${print.disabled ? '已启用' : '已停用'}「${print.name}」`)
      load()
    } catch (e) {
      setError(e instanceof Error ? e.message : '状态更新失败')
    }
  }

  const provisional = prints.filter((p) => p.provisional)
  const named = prints.filter((p) => !p.provisional)

  return (
    <div className="flex h-full flex-col">
      <TopBar />

      <main className="min-h-0 flex-1 overflow-y-auto px-4 py-5">
        <div className="mx-auto w-full max-w-[720px]">
          <div className="mb-4 flex items-center gap-2">
            <Link to="/" className="btn btn-ghost no-underline" style={{ height: 28, padding: '0 8px' }}>
              <ArrowLeft size={13} />
              返回
            </Link>
            <h1 className="text-[17px] font-semibold" style={{ color: 'var(--text-primary)' }}>
              声纹管理
            </h1>
            <span className="text-[12px]" style={{ color: 'var(--text-tertiary)' }}>
              共 {prints.length} 个
            </span>
            <div className="flex-1" />
            <button
              type="button"
              onClick={load}
              className="btn btn-ghost"
              style={{ height: 28, padding: '0 8px' }}
            >
              <RefreshCw size={13} />
              刷新
            </button>
          </div>

          {error && (
            <Banner kind="error" onClose={() => setError(null)}>
              {error}
            </Banner>
          )}
          {ok && !error && (
            <Banner kind="ok" onClose={() => setOk(null)}>
              {ok}
            </Banner>
          )}

          {/* 录入 */}
          <section className="mb-5">
            <h2 className="mb-2 text-[12px] font-medium" style={{ color: 'var(--text-secondary)' }}>
              录入新声纹
            </h2>
            <VoiceprintEnroll
              deviceId={activeDeviceId ?? undefined}
              onDone={() => {
                load()
                setError(null)
              }}
            />
            {devices.length === 0 && (
              <p className="mt-1.5 text-[11px]" style={{ color: 'var(--text-tertiary)' }}>
                还没有检测到输入设备，请先在设置里选择麦克风。
              </p>
            )}
          </section>

          {suggestions.length > 0 && (
            <section className="panel mb-5 p-3">
              <h2 className="mb-2 text-[12px] font-medium" style={{ color: 'var(--text-secondary)' }}>
                可能重复的声纹
              </h2>
              <div className="space-y-1.5">
                {suggestions.slice(0, 8).map((item) => (
                  <div
                    key={`${item.source.id}-${item.target.id}`}
                    className="flex items-center gap-2 rounded-lg px-2 py-1.5 text-[11px]"
                    style={{ background: 'var(--bg-subtle)' }}
                  >
                    <span className="min-w-0 flex-1 truncate">
                      {item.source.name} → {item.target.name}
                    </span>
                    <span className="text-tertiary">{Math.round(item.score * 100)}%</span>
                    <button
                      type="button"
                      onClick={() => void merge(item.source, item.target.id)}
                      className="btn btn-ghost text-[10px]"
                    >
                      合并
                    </button>
                  </div>
                ))}
              </div>
            </section>
          )}

          {/* 待命名 */}
          {provisional.length > 0 && (
            <section className="mb-5">
              <h2 className="mb-2 flex items-center gap-1.5 text-[12px] font-medium" style={{ color: 'var(--text-secondary)' }}>
                <Users size={13} />
                待命名（{provisional.length}）
              </h2>
              <p className="mb-2 text-[11px] leading-relaxed" style={{ color: 'var(--text-tertiary)' }}>
                这些是开会时自动发现的说话人。改成真实姓名后，以后所有会议都会直接认出这个人，
                历史转写里的旧标签也会一并更新。
              </p>
              <ul className="flex flex-col gap-1.5">
                {provisional.map((p) => (
                  <Row
                    key={p.id}
                    print={p}
                    editing={editingId === p.id}
                    draft={draft}
                    setDraft={setDraft}
                    onStartEdit={() => startEdit(p)}
                    onSubmit={() => void submitEdit(p)}
                    onCancel={() => setEditingId(null)}
                    onDelete={() => void remove(p)}
                    others={prints.filter((x) => x.id !== p.id)}
                    onMerge={(tid) => void merge(p, tid)}
                    onToggleDisabled={() => void toggleDisabled(p)}
                  />
                ))}
              </ul>
            </section>
          )}

          {/* 已命名 */}
          <section>
            <h2 className="mb-2 text-[12px] font-medium" style={{ color: 'var(--text-secondary)' }}>
              已命名（{named.length}）
            </h2>
            {loading ? (
              <p className="text-[12px]" style={{ color: 'var(--text-tertiary)' }}>
                加载中…
              </p>
            ) : named.length === 0 ? (
              <p className="text-[12px]" style={{ color: 'var(--text-tertiary)' }}>
                还没有命名的声纹。上面录入一个，或把「待命名」改成真实姓名。
              </p>
            ) : (
              <ul className="flex flex-col gap-1.5">
                {named.map((p) => (
                  <Row
                    key={p.id}
                    print={p}
                    editing={editingId === p.id}
                    draft={draft}
                    setDraft={setDraft}
                    onStartEdit={() => startEdit(p)}
                    onSubmit={() => void submitEdit(p)}
                    onCancel={() => setEditingId(null)}
                    onDelete={() => void remove(p)}
                    others={prints.filter((x) => x.id !== p.id)}
                    onMerge={(tid) => void merge(p, tid)}
                    onToggleDisabled={() => void toggleDisabled(p)}
                  />
                ))}
              </ul>
            )}
          </section>
        </div>
      </main>
    </div>
  )
}

function Row({
  print,
  editing,
  draft,
  setDraft,
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
  setDraft: (v: string) => void
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
            onChange={(e) => setDraft(e.target.value)}
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
          <button type="button" onClick={onSubmit} className="btn btn-primary" style={{ height: 26, padding: '0 10px', fontSize: 12 }}>
            <Check size={12} />
            保存
          </button>
          <button type="button" onClick={onCancel} className="btn btn-ghost" style={{ height: 26, padding: '0 8px', fontSize: 12 }}>
            取消
          </button>
        </>
      ) : (
        <>
          <span className="flex min-w-0 items-center gap-2 text-[13px]" style={{ color: 'var(--text-primary)' }}>
            <Mic size={13} style={{ color: print.provisional ? 'var(--text-tertiary)' : 'var(--accent)' }} />
            <span className="truncate font-medium">{print.name}</span>
            {print.provisional ? (
              <span
                className="shrink-0 rounded-full px-2 py-0.5 text-[10px]"
                style={{ background: 'var(--warn-soft)', color: 'var(--warn-text)' }}
              >
                待命名
              </span>
            ) : (
              <span className="shrink-0 text-[11px]" style={{ color: 'var(--text-tertiary)' }}>
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
                onClick={() => setMergeOpen((v) => !v)}
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
                <p className="px-2 py-1 text-[10px]" style={{ color: 'var(--text-tertiary)' }}>
                  合并到：
                </p>
                {others.map((o) => (
                  <button
                    key={o.id}
                    type="button"
                    onClick={() => {
                      setMergeOpen(false)
                      onMerge(o.id)
                    }}
                    className="block w-full truncate rounded px-2 py-1 text-left text-[12px]"
                    style={{ color: 'var(--text-primary)' }}
                  >
                    {o.name}
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

function Banner({
  kind,
  children,
  onClose,
}: {
  kind: 'ok' | 'error'
  children: React.ReactNode
  onClose: () => void
}) {
  const style =
    kind === 'error'
      ? { background: 'var(--danger-soft)', color: 'var(--danger-text)' }
      : { background: 'var(--accent-soft)', color: 'var(--accent-text)' }
  return (
    <div
      className="mb-3 flex items-center justify-between rounded-lg px-3 py-2 text-[12px]"
      style={style}
    >
      <span>{children}</span>
      <button type="button" onClick={onClose} className="btn btn-ghost" style={{ height: 22, width: 22, padding: 0 }}>
        ×
      </button>
    </div>
  )
}
