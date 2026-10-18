import { useEffect, useRef, useState } from 'react'
import { Mic, ChevronDown, Check, RefreshCw } from 'lucide-react'
import { useSession } from '../store/session'

/**
 * 麦克风设备选择器。
 *
 * 为什么需要它：真实机器上常挂着多个音频输入（内置麦克风阵列、USB 麦克风、
 * 蓝牙耳机、虚拟声卡、HDMI 音频……），而浏览器的「默认设备」未必是用户
 * 正在说话的那一个。选错时的表现极隐蔽 —— getUserMedia 成功、权限正常、
 * 波形甚至可能因为别的分支在动，但送出的 PCM 全是 0，字幕一个字都不出。
 *
 * 这个组件把「换一个设备」从「翻系统设置」变成「点一下」。
 */
export function DeviceSelector() {
  const devices = useSession((s) => s.devices)
  const activeDeviceId = useSession((s) => s.activeDeviceId)
  const refreshDevices = useSession((s) => s.refreshDevices)
  const selectDevice = useSession((s) => s.selectDevice)
  const status = useSession((s) => s.status)

  const [open, setOpen] = useState(false)
  const [switching, setSwitching] = useState(false)
  const boxRef = useRef<HTMLDivElement | null>(null)

  const isRecording = status === 'recording' || status === 'paused'

  // 打开菜单时（重新）枚举设备——设备可能被插拔，缓存的列表会过期
  useEffect(() => {
    if (open) void refreshDevices()
  }, [open, refreshDevices])

  // 点击外部关闭
  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [open])

  const current = devices.find((d) => d.deviceId === activeDeviceId)
  const label = current?.label ?? (activeDeviceId ? '已选设备' : '系统默认麦克风')

  const pick = async (id: string | null) => {
    setOpen(false)
    if (id === activeDeviceId) return
    if (!isRecording) {
      // 未开始录音时只记录选择，start() 时会带上
      useSession.setState({ activeDeviceId: id, error: null })
      return
    }
    setSwitching(true)
    await selectDevice(id)
    setSwitching(false)
  }

  return (
    <div ref={boxRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="btn"
        style={{ height: 40, paddingInline: 12, maxWidth: 240 }}
        title={label}
      >
        {switching ? (
          <span className="size-3 animate-spin rounded-full border-2 border-current border-t-transparent" />
        ) : (
          <Mic size={15} />
        )}
        <span className="truncate text-[13px]">{label}</span>
        <ChevronDown size={13} style={{ opacity: 0.6 }} />
      </button>

      {open && (
        <div
          className="absolute bottom-full left-0 z-50 mb-2 w-[300px] overflow-hidden rounded-[10px] border shadow-lg"
          style={{ background: 'var(--bg-primary, #fff)', borderColor: 'var(--border-default)' }}
        >
          <div
            className="flex items-center justify-between px-3 py-2"
            style={{ borderBottom: '1px solid var(--border-default)' }}
          >
            <span className="text-[11px] font-medium" style={{ color: 'var(--text-secondary)' }}>
              选择麦克风
            </span>
            <button
              type="button"
              onClick={() => void refreshDevices()}
              className="rounded p-1 transition-colors hover:bg-black/5"
              title="重新扫描设备"
            >
              <RefreshCw size={12} style={{ color: 'var(--text-tertiary)' }} />
            </button>
          </div>

          <div className="max-h-[240px] overflow-y-auto py-1">
            <DeviceRow
              label="系统默认麦克风"
              active={activeDeviceId === null}
              onClick={() => void pick(null)}
            />
            {devices.map((d) => (
              <DeviceRow
                key={d.deviceId}
                label={d.label}
                active={d.deviceId === activeDeviceId}
                onClick={() => void pick(d.deviceId)}
              />
            ))}
            {devices.length === 0 && (
              <p className="px-3 py-2 text-[12px]" style={{ color: 'var(--text-tertiary)' }}>
                未检测到输入设备
              </p>
            )}
          </div>

          {isRecording && (
            <p
              className="px-3 py-2 text-[11px]"
              style={{ borderTop: '1px solid var(--border-default)', color: 'var(--text-tertiary)' }}
            >
              会议中切换设备不会中断转写
            </p>
          )}
        </div>
      )}
    </div>
  )
}

function DeviceRow({
  label,
  active,
  onClick,
}: {
  label: string
  active: boolean
  onClick: () => void
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="flex w-full items-center gap-2 px-3 py-2 text-left transition-colors hover:bg-black/5"
    >
      <span className="min-w-0 flex-1 truncate text-[13px]" style={{ color: 'var(--text-primary)' }}>
        {label}
      </span>
      {active && <Check size={14} style={{ color: 'var(--accent)', flexShrink: 0 }} />}
    </button>
  )
}
