import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  X,
  Sun,
  Moon,
  Monitor,
  Mic,
  RefreshCw,
  ExternalLink,
  ShieldCheck,
  Trash2,
  Pencil,
} from 'lucide-react'
import { useSettings } from '../store/settings'
import type { ThemeMode } from '../store/settings'
import { useSession } from '../store/session'
import { useAuth } from '../store/auth'
import { voiceprintApi } from '../api/client'
import { VoiceprintEnroll } from './VoiceprintEnroll'
import { useConfigEditor } from '../hooks/useConfigEditor'
import type { Voiceprint } from '../api/types'
import { SettingsBackendConfig } from './SettingsBackendConfig'
import { SettingsRow as Row, SettingsSection as Section } from './SettingsPanelParts'

const THEMES: { key: ThemeMode; label: string; icon: typeof Sun }[] = [
  { key: 'light', label: '浅色', icon: Sun },
  { key: 'dark', label: '深色', icon: Moon },
  { key: 'system', label: '跟随系统', icon: Monitor },
]

interface Health {
  status: string
  version: string
  models: Record<string, { loaded: boolean }>
}

export function SettingsPanel({ open, onClose }: { open: boolean; onClose: () => void }) {
  const theme = useSettings((s) => s.theme)
  const setTheme = useSettings((s) => s.setTheme)
  // 订阅 user 而非 isAdmin 函数：函数引用恒定，user 变化不会触发重渲染
  const user = useAuth((s) => s.user)
  const isAdminUser = user?.role === 'admin'

  const devices = useSession((s) => s.devices)
  const activeDeviceId = useSession((s) => s.activeDeviceId)
  const refreshDevices = useSession((s) => s.refreshDevices)
  const selectDevice = useSession((s) => s.selectDevice)

  const [health, setHealth] = useState<Health | null>(null)

  // ---- 后端配置（模型/LLM）----
  const cfg = useConfigEditor({ enabled: open && isAdminUser })

  // ---- 声纹 ----
  const [prints, setPrints] = useState<Voiceprint[]>([])
  const [vpError, setVpError] = useState<string | null>(null)
  const [vpOk, setVpOk] = useState<string | null>(null)

  const loadPrints = () => {
    voiceprintApi
      .list()
      .then(setPrints)
      .catch(() => setPrints([]))
  }

  useEffect(() => {
    if (!open) return
    void refreshDevices()
    loadPrints()
    // 版本与模型状态直接问后端，比写死在前端可靠
    fetch('/api/v1/health')
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => setHealth(d as Health | null))
      .catch(() => setHealth(null))
  }, [open, refreshDevices])

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  if (!open) return null

  const modelsOk = health ? Object.values(health.models).every((m) => m.loaded) : null

  const handleDelete = async (id: number, name: string) => {
    try {
      await voiceprintApi.remove(id)
      setVpOk(`已删除「${name}」`)
      setVpError(null)
      loadPrints()
    } catch (err) {
      setVpError(err instanceof Error ? err.message : '删除失败')
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div
        className="absolute inset-0 bg-black/25"
        onClick={onClose}
        aria-hidden
      />

      <aside
        className="panel relative flex h-full w-full max-w-[380px] flex-col overflow-y-auto p-5"
        style={{ borderRadius: 0, borderRight: 'none' }}
        role="dialog"
        aria-label="设置"
      >
        <div className="mb-5 flex items-center justify-between">
          <h2 className="text-[15px] font-semibold" style={{ color: 'var(--text-primary)' }}>
            设置
          </h2>
          <button
            type="button"
            onClick={onClose}
            className="btn btn-ghost"
            style={{ height: 28, width: 28, padding: 0 }}
            aria-label="关闭"
          >
            <X size={15} />
          </button>
        </div>

        {/* ---- 外观 ---- */}
        <Section title="外观">
          <div className="grid grid-cols-3 gap-2">
            {THEMES.map(({ key, label, icon: Icon }) => {
              const active = theme === key
              return (
                <button
                  key={key}
                  type="button"
                  onClick={() => setTheme(key)}
                  className="flex flex-col items-center gap-1.5 rounded-lg py-2.5 text-[11px] transition-colors"
                  style={{
                    background: active ? 'var(--accent-soft)' : 'var(--bg-subtle)',
                    color: active ? 'var(--accent-text)' : 'var(--text-secondary)',
                    border: `1px solid ${active ? 'var(--accent)' : 'transparent'}`,
                  }}
                >
                  <Icon size={15} />
                  {label}
                </button>
              )
            })}
          </div>
        </Section>

        {/* ---- 麦克风 ---- */}
        <Section title="麦克风">
          <div className="mb-2 flex items-center gap-1.5">
            <select
              value={activeDeviceId ?? ''}
              onChange={(e) => void selectDevice(e.target.value || null)}
              className="flex-1 rounded-lg px-2.5 py-1.5 text-[12px] outline-none"
              style={{
                background: 'var(--bg-subtle)',
                color: 'var(--text-primary)',
                border: '1px solid var(--border-subtle)',
              }}
              aria-label="输入设备"
            >
              <option value="">系统默认麦克风</option>
              {devices.map((d) => (
                <option key={d.deviceId} value={d.deviceId}>
                  {d.label}
                </option>
              ))}
            </select>
            <button
              type="button"
              onClick={() => void refreshDevices()}
              className="btn btn-ghost"
              style={{ height: 30, width: 30, padding: 0 }}
              title="重新检测设备"
            >
              <RefreshCw size={13} />
            </button>
          </div>

          <a
            href="/mic-check.html"
            target="_blank"
            rel="noreferrer"
            className="flex items-center gap-1.5 text-[12px] no-underline"
            style={{ color: 'var(--accent-text)' }}
          >
            <Mic size={13} />
            打开麦克风采集自检
            <ExternalLink size={11} />
          </a>
          <p className="mt-1.5 text-[11px] leading-relaxed" style={{ color: 'var(--text-tertiary)' }}>
            若转写一直没有字幕，自检页会逐项测出是权限、设备还是系统「音频增强」的问题。
          </p>
        </Section>

        {/* ---- 声纹 ---- */}
        <Section title="声纹">
          <p className="mb-2 text-[11px] leading-relaxed" style={{ color: 'var(--text-tertiary)' }}>
            录入后，会议转写会直接显示说话人姓名，而不是「说话人 1」。
          </p>

          <VoiceprintEnroll
            deviceId={activeDeviceId ?? undefined}
            onDone={() => {
              loadPrints()
              setVpError(null)
            }}
          />

          {vpError && (
            <div
              className="mt-2 rounded-lg px-2.5 py-1.5 text-[11px]"
              style={{ background: 'var(--danger-soft)', color: 'var(--danger-text)' }}
            >
              {vpError}
            </div>
          )}
          {vpOk && !vpError && (
            <div
              className="mt-2 rounded-lg px-2.5 py-1.5 text-[11px]"
              style={{ background: 'var(--accent-soft)', color: 'var(--accent-text)' }}
            >
              {vpOk}
            </div>
          )}

          <div className="mt-4 mb-1.5 flex items-center justify-between">
            <span className="text-[11px]" style={{ color: 'var(--text-secondary)' }}>
              已录入 {prints.length} 个
            </span>
            <span className="flex items-center gap-1">
              <button
                type="button"
                onClick={loadPrints}
                className="btn btn-ghost"
                style={{ height: 22, padding: '0 8px', fontSize: 11 }}
              >
                <RefreshCw size={11} />
                刷新
              </button>
              <Link
                to="/voiceprints"
                onClick={onClose}
                className="btn btn-ghost no-underline"
                style={{ height: 22, padding: '0 8px', fontSize: 11 }}
              >
                管理页
                <ExternalLink size={10} />
              </Link>
            </span>
          </div>

          {prints.length === 0 ? (
            <p className="text-[11px]" style={{ color: 'var(--text-tertiary)' }}>
              还没有任何声纹
            </p>
          ) : (
            <ul className="flex flex-col gap-1">
              {prints.map((p) => (
                <PrintRow
                  key={p.id}
                  print={p}
                  onRenamed={(msg) => {
                    setVpOk(msg)
                    setVpError(null)
                    loadPrints()
                  }}
                  onError={setVpError}
                  onDelete={() => void handleDelete(p.id, p.name)}
                />
              ))}
            </ul>
          )}

          {prints.some((p) => p.provisional) && (
            <p className="mt-2 text-[11px] leading-relaxed" style={{ color: 'var(--text-tertiary)' }}>
              标「待命名」的是开会时自动发现的说话人。改成真实姓名后，
              以后所有会议都会直接认出这个人。
            </p>
          )}
        </Section>

        {/* ---- 后端配置（模型 / LLM）---- */}
        <SettingsBackendConfig
          isAdminUser={isAdminUser}
          cfg={cfg}
          onClose={onClose}
        />

        {/* ---- 关于 ---- */}
        <Section title="关于">
          <Row label="名称" value="iMeeting" />
          <Row label="副标题" value="您的硅基伙伴" />
          <Row label="版本" value={health?.version ?? '—'} />
          <Row
            label="服务状态"
            value={
              health
                ? `${health.status === 'ok' ? '正常' : health.status}${modelsOk ? ' · 模型已就绪' : modelsOk === false ? ' · 模型加载中' : ''}`
                : '未连接'
            }
          />
        </Section>

        {isAdminUser && (
          <Section title="管理">
            <Link
              to="/admin"
              onClick={onClose}
              className="flex items-center gap-1.5 text-[12px] no-underline"
              style={{ color: 'var(--accent-text)' }}
            >
              <ShieldCheck size={13} />
              后台配置（全部参数）
            </Link>
          </Section>
        )}
      </aside>
    </div>
  )
}

/**
 * 一条声纹。支持就地改名——「待命名」的自动发现项改名后即转正，
 * 之后所有会议都会用这个名字。
 */
function PrintRow({
  print,
  onRenamed,
  onError,
  onDelete,
}: {
  print: Voiceprint
  onRenamed: (msg: string) => void
  onError: (msg: string) => void
  onDelete: () => void
}) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(print.name)
  const [busy, setBusy] = useState(false)

  const submit = async () => {
    const name = draft.trim()
    if (!name || name === print.name) {
      setEditing(false)
      setDraft(print.name)
      return
    }
    setBusy(true)
    try {
      const updated = await voiceprintApi.rename(print.id, name)
      onRenamed(`已改名为「${updated.name}」`)
      setEditing(false)
    } catch (err) {
      onError(err instanceof Error ? err.message : '改名失败')
    } finally {
      setBusy(false)
    }
  }

  return (
    <li
      className="flex items-center justify-between gap-1.5 rounded-lg px-2.5 py-1.5 text-[12px]"
      style={{ background: 'var(--bg-subtle)' }}
    >
      {editing ? (
        <input
          autoFocus
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') void submit()
            if (e.key === 'Escape') {
              setEditing(false)
              setDraft(print.name)
            }
          }}
          onBlur={() => void submit()}
          maxLength={32}
          disabled={busy}
          className="min-w-0 flex-1 rounded px-1.5 py-0.5 text-[12px] outline-none"
          style={{
            background: 'var(--bg-surface)',
            color: 'var(--text-primary)',
            border: '1px solid var(--accent)',
          }}
        />
      ) : (
        <span className="flex min-w-0 items-center gap-1.5" style={{ color: 'var(--text-primary)' }}>
          <Mic size={12} style={{ color: print.provisional ? 'var(--text-tertiary)' : 'var(--accent)' }} />
          <span className="truncate">{print.name}</span>
          {print.provisional ? (
            <span
              className="shrink-0 rounded-full px-1.5 py-0.5 text-[10px]"
              style={{ background: 'var(--warn-soft)', color: 'var(--warn-text)' }}
            >
              待命名
            </span>
          ) : (
            <span className="shrink-0" style={{ color: 'var(--text-tertiary)' }}>
              {(print.sample_ms / 1000).toFixed(1)}s
            </span>
          )}
        </span>
      )}

      <span className="flex shrink-0 items-center">
        {!editing && (
          <button
            type="button"
            onClick={() => {
              setDraft(print.name)
              setEditing(true)
            }}
            className="btn btn-ghost"
            style={{ height: 24, width: 24, padding: 0 }}
            title="改名"
            aria-label={`改名 ${print.name}`}
          >
            <Pencil size={12} />
          </button>
        )}
        <button
          type="button"
          onClick={onDelete}
          className="btn btn-ghost"
          style={{ height: 24, width: 24, padding: 0 }}
          title="删除"
          aria-label={`删除 ${print.name}`}
        >
          <Trash2 size={12} />
        </button>
      </span>
    </li>
  )
}
