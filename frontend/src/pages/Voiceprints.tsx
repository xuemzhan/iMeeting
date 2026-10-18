import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { ArrowLeft, RefreshCw, Users } from 'lucide-react'
import { TopBar } from '../components/TopBar'
import { VoiceprintEnroll } from '../components/VoiceprintEnroll'
import { VoiceprintAdminRow } from '../components/VoiceprintAdminRow'
import { useVoiceprintAdmin } from '../hooks/useVoiceprintAdmin'

/**
 * 声纹管理页。
 *
 * 与设置抽屉里的那一段的区别：这里是**完整管理**——查看全部声纹、
 * 逐个改名/删除、批量清理待命名项。设置抽屉里只保留「快速录入」，
 * 免得设置面板被撑得过长。
 */
export default function Voiceprints() {
  const {
    prints,
    loading,
    error,
    ok,
    editingId,
    draft,
    setDraft,
    suggestions,
    devices,
    activeDeviceId,
    provisional,
    named,
    load,
    startEdit,
    submitEdit,
    cancelEdit,
    remove,
    merge,
    toggleDisabled,
    enrollDone,
    clearError,
    clearOk,
  } = useVoiceprintAdmin()

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
            <Banner kind="error" onClose={clearError}>
              {error}
            </Banner>
          )}
          {ok && !error && (
            <Banner kind="ok" onClose={clearOk}>
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
              onDone={enrollDone}
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
                  <VoiceprintAdminRow
                    key={p.id}
                    print={p}
                    editing={editingId === p.id}
                    draft={draft}
                    onDraftChange={setDraft}
                    onStartEdit={() => startEdit(p)}
                    onSubmit={() => void submitEdit(p)}
                    onCancel={cancelEdit}
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
                  <VoiceprintAdminRow
                    key={p.id}
                    print={p}
                    editing={editingId === p.id}
                    draft={draft}
                    onDraftChange={setDraft}
                    onStartEdit={() => startEdit(p)}
                    onSubmit={() => void submitEdit(p)}
                    onCancel={cancelEdit}
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

function Banner({
  kind,
  children,
  onClose,
}: {
  kind: 'ok' | 'error'
  children: ReactNode
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
