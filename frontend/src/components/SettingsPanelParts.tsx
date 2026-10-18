import { useState, type ReactNode } from 'react'
import { ChevronRight } from 'lucide-react'

/** 可折叠分组：设置抽屉里塞几十个配置项会太长，默认收起更清爽。 */
export function CollapsibleSection({
  title,
  count,
  defaultOpen = false,
  children,
}: {
  title: string
  /** 字段数。显示出来是为了让用户一眼知道这组里有多少可调项。 */
  count?: number
  defaultOpen?: boolean
  children: ReactNode
}) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <section className="mb-5">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className="mb-2 flex w-full items-center gap-1.5"
      >
        <ChevronRight
          size={12}
          style={{ transform: open ? 'rotate(90deg)' : 'none', transition: 'transform .15s' }}
        />
        <span
          className="text-[11px] font-medium tracking-wide"
          style={{ color: 'var(--text-tertiary)' }}
        >
          {title}
        </span>
        {count !== undefined && (
          <span
            className="rounded-full px-1.5 text-[10px] tabular-nums"
            style={{ background: 'var(--bg-subtle)', color: 'var(--text-tertiary)' }}
          >
            {count}
          </span>
        )}
      </button>
      {open && children}
    </section>
  )
}

export function InlineMsg({
  kind,
  children,
}: {
  kind: 'ok' | 'warn' | 'err'
  children: ReactNode
}) {
  const palette = {
    ok: { background: 'var(--accent-soft)', color: 'var(--accent-text)' },
    warn: { background: 'var(--warn-soft)', color: 'var(--warn-text)' },
    err: { background: 'var(--danger-soft)', color: 'var(--danger-text)' },
  }[kind]
  return (
    <div className="mb-2 rounded-lg px-2.5 py-1.5 text-[11px]" style={palette}>
      {children}
    </div>
  )
}

export function SettingsSection({
  title,
  children,
}: {
  title: string
  children: ReactNode
}) {
  return (
    <section className="mb-5">
      <h3
        className="mb-2 text-[11px] font-medium uppercase tracking-wide"
        style={{ color: 'var(--text-tertiary)' }}
      >
        {title}
      </h3>
      {children}
    </section>
  )
}

export function SettingsRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between py-1 text-[12px]">
      <span style={{ color: 'var(--text-secondary)' }}>{label}</span>
      <span style={{ color: 'var(--text-primary)' }}>{value}</span>
    </div>
  )
}
