import { Link } from 'react-router-dom'
import {
  ExternalLink,
  Mic,
  Monitor,
  Moon,
  RefreshCw,
  ShieldCheck,
  Sun,
} from 'lucide-react'

import type { ThemeMode } from '../store/settings'
import { SettingsRow, SettingsSection } from './SettingsPanelParts'

const THEMES: { key: ThemeMode; label: string; icon: typeof Sun }[] = [
  { key: 'light', label: '浅色', icon: Sun },
  { key: 'dark', label: '深色', icon: Moon },
  { key: 'system', label: '跟随系统', icon: Monitor },
]

export function SettingsAppearanceSection({
  theme,
  onThemeChange,
}: {
  theme: ThemeMode
  onThemeChange: (theme: ThemeMode) => void
}) {
  return (
    <SettingsSection title="外观">
      <div className="grid grid-cols-3 gap-2">
        {THEMES.map(({ key, label, icon: Icon }) => {
          const active = theme === key
          return (
            <button
              key={key}
              type="button"
              onClick={() => onThemeChange(key)}
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
    </SettingsSection>
  )
}

interface InputDevice {
  deviceId: string
  label: string
}

export function SettingsMicrophoneSection({
  devices,
  activeDeviceId,
  onSelectDevice,
  onRefreshDevices,
}: {
  devices: InputDevice[]
  activeDeviceId: string | null
  onSelectDevice: (deviceId: string | null) => void
  onRefreshDevices: () => void
}) {
  return (
    <SettingsSection title="麦克风">
      <div className="mb-2 flex items-center gap-1.5">
        <select
          value={activeDeviceId ?? ''}
          onChange={(e) => onSelectDevice(e.target.value || null)}
          className="flex-1 rounded-lg px-2.5 py-1.5 text-[12px] outline-none"
          style={{
            background: 'var(--bg-subtle)',
            color: 'var(--text-primary)',
            border: '1px solid var(--border-subtle)',
          }}
          aria-label="输入设备"
        >
          <option value="">系统默认麦克风</option>
          {devices.map((device) => (
            <option key={device.deviceId} value={device.deviceId}>
              {device.label}
            </option>
          ))}
        </select>
        <button
          type="button"
          onClick={onRefreshDevices}
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
      <p
        className="mt-1.5 text-[11px] leading-relaxed"
        style={{ color: 'var(--text-tertiary)' }}
      >
        若转写一直没有字幕，自检页会逐项测出是权限、设备还是系统「音频增强」的问题。
      </p>
    </SettingsSection>
  )
}

export interface SettingsHealth {
  status: string
  version: string
  models: Record<string, { loaded: boolean }>
}

export function SettingsAboutSection({
  health,
  modelsOk,
}: {
  health: SettingsHealth | null
  modelsOk: boolean | null
}) {
  return (
    <SettingsSection title="关于">
      <SettingsRow label="名称" value="iMeeting" />
      <SettingsRow label="副标题" value="您的硅基伙伴" />
      <SettingsRow label="版本" value={health?.version ?? '—'} />
      <SettingsRow
        label="服务状态"
        value={
          health
            ? `${health.status === 'ok' ? '正常' : health.status}${modelsOk ? ' · 模型已就绪' : modelsOk === false ? ' · 模型加载中' : ''}`
            : '未连接'
        }
      />
    </SettingsSection>
  )
}

export function SettingsAdminSection({ onClose }: { onClose: () => void }) {
  return (
    <SettingsSection title="管理">
      <Link
        to="/admin"
        onClick={onClose}
        className="flex items-center gap-1.5 text-[12px] no-underline"
        style={{ color: 'var(--accent-text)' }}
      >
        <ShieldCheck size={13} />
        后台配置（全部参数）
      </Link>
    </SettingsSection>
  )
}
