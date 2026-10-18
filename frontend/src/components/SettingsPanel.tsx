import { useEffect, useState } from 'react'
import { X } from 'lucide-react'
import { useSettings } from '../store/settings'
import { useSession } from '../store/session'
import { useAuth } from '../store/auth'
import { useConfigEditor } from '../hooks/useConfigEditor'
import { useSettingsVoiceprints } from '../hooks/useSettingsVoiceprints'
import { SettingsBackendConfig } from './SettingsBackendConfig'
import {
  SettingsAboutSection,
  SettingsAdminSection,
  SettingsAppearanceSection,
  SettingsMicrophoneSection,
  type SettingsHealth,
} from './SettingsPreferenceSections'
import { SettingsVoiceprintSection } from './SettingsVoiceprintSection'

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

  const [health, setHealth] = useState<SettingsHealth | null>(null)

  // ---- 后端配置（模型/LLM）----
  const cfg = useConfigEditor({ enabled: open && isAdminUser })

  const voiceprints = useSettingsVoiceprints({ open })

  useEffect(() => {
    if (!open) return
    void refreshDevices()
    // 版本与模型状态直接问后端，比写死在前端可靠
    fetch('/api/v1/health')
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => setHealth(d as SettingsHealth | null))
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
        <SettingsAppearanceSection theme={theme} onThemeChange={setTheme} />

        {/* ---- 麦克风 ---- */}
        <SettingsMicrophoneSection
          devices={devices}
          activeDeviceId={activeDeviceId}
          onSelectDevice={(deviceId) => void selectDevice(deviceId)}
          onRefreshDevices={() => void refreshDevices()}
        />

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
        <SettingsAboutSection health={health} modelsOk={modelsOk} />

        {isAdminUser && <SettingsAdminSection onClose={onClose} />}
      </aside>
    </div>
  )
}
