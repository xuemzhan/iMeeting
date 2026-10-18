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
} from 'lucide-react'
import { useSettings } from '../store/settings'
import type { ThemeMode } from '../store/settings'
import { useSession } from '../store/session'
import { useAuth } from '../store/auth'
import { useConfigEditor } from '../hooks/useConfigEditor'
import { useSettingsVoiceprints } from '../hooks/useSettingsVoiceprints'
import { SettingsBackendConfig } from './SettingsBackendConfig'
import { SettingsRow as Row, SettingsSection as Section } from './SettingsPanelParts'
import { SettingsVoiceprintSection } from './SettingsVoiceprintSection'

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

  const voiceprints = useSettingsVoiceprints({ open })

  useEffect(() => {
    if (!open) return
    void refreshDevices()
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
        <SettingsVoiceprintSection
          deviceId={activeDeviceId ?? undefined}
          voiceprints={voiceprints}
          onClose={onClose}
        />

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
