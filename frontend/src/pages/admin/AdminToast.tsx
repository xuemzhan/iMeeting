import { AlertTriangle, CheckCircle2 } from 'lucide-react'

export interface AdminToastMessage {
  kind: 'ok' | 'warn' | 'err'
  text: string
}

export function AdminToast({
  toast,
  onClose,
}: {
  toast: AdminToastMessage
  onClose: () => void
}) {
  const palette = {
    ok: { bg: 'var(--live-soft)', fg: 'var(--live-text)' },
    warn: { bg: 'var(--warn-soft)', fg: 'var(--warn-text)' },
    err: { bg: 'var(--danger-soft)', fg: 'var(--danger-text)' },
  }[toast.kind]

  const Icon = toast.kind === 'ok' ? CheckCircle2 : AlertTriangle

  return (
    <button
      type="button"
      onClick={onClose}
      className="mb-3 flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-[12px]"
      style={{ background: palette.bg, color: palette.fg }}
    >
      <Icon size={14} className="shrink-0" />
      <span className="flex-1">{toast.text}</span>
      <span className="text-[11px] opacity-70">点击关闭</span>
    </button>
  )
}
