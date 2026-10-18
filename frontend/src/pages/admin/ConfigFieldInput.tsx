import type { ConfigField } from '../../api/types'

interface Props {
  field: ConfigField
  value: unknown
  error?: string
  onChange: (value: unknown) => void
}

/** 根据配置项类型渲染对应控件。新增配置类型只需在这里加一个分支。 */
export function ConfigFieldInput({ field, value, error, onChange }: Props) {
  return (
    <div className="flex items-start gap-4 px-4 py-3">
      <div className="w-[204px] shrink-0 pt-0.5">
        <label className="block text-[12px] font-medium" htmlFor={field.key}>
          {field.label}
          {field.restart_required && (
            <span
              className="ml-1.5 rounded px-1 py-px text-[10px]"
              style={{ background: 'var(--warn-soft)', color: 'var(--warn-text)' }}
              title="修改后需重启服务才能生效"
            >
              需重启
            </span>
          )}
        </label>
        {field.help && (
          <p className="mt-0.5 text-[11px] leading-snug text-tertiary">{field.help}</p>
        )}
      </div>

      <div className="min-w-0 flex-1">
        <Control field={field} value={value} onChange={onChange} />

        {error && (
          <p className="mt-1 text-[11px]" style={{ color: 'var(--danger)' }}>
            {error}
          </p>
        )}

        <p className="mt-1 font-mono text-[10px] text-tertiary">{field.key}</p>
      </div>
    </div>
  )
}

/** 按类型渲染对应控件。后台配置页（宽布局）与设置抽屉（窄布局）共用。 */
export function Control({
  field,
  value,
  onChange,
}: {
  field: ConfigField
  value: unknown
  onChange: (v: unknown) => void
}) {
  switch (field.type) {
    case 'bool':
      return (
        <button
          type="button"
          role="switch"
          aria-checked={Boolean(value)}
          onClick={() => onChange(!value)}
          className="relative h-[22px] w-[38px] rounded-full transition-colors"
          style={{ background: value ? 'var(--accent)' : 'var(--border-default)' }}
        >
          <span
            className="absolute top-[3px] size-4 rounded-full bg-white transition-[left]"
            style={{ left: value ? 18 : 3 }}
          />
        </button>
      )

    case 'select':
      return (
        <select
          id={field.key}
          className="input"
          value={String(value ?? '')}
          onChange={(e) => onChange(e.target.value)}
        >
          {(field.options ?? []).map((opt) => (
            <option key={opt} value={opt}>
              {opt}
            </option>
          ))}
        </select>
      )

    case 'text':
      return (
        <textarea
          id={field.key}
          className="input h-auto min-h-[120px] resize-y py-2 leading-relaxed"
          value={String(value ?? '')}
          onChange={(e) => onChange(e.target.value)}
        />
      )

    case 'password':
      return (
        <input
          id={field.key}
          type="password"
          className="input"
          value={String(value ?? '')}
          placeholder="留空表示不修改"
          onChange={(e) => onChange(e.target.value)}
          autoComplete="new-password"
        />
      )

    case 'int':
    case 'float':
      return (
        <input
          id={field.key}
          type="number"
          className="input w-[160px]"
          value={String(value ?? '')}
          step={field.type === 'float' ? 0.1 : 1}
          min={field.min_value ?? undefined}
          max={field.max_value ?? undefined}
          onChange={(e) => {
            const raw = e.target.value
            if (raw === '') return
            onChange(field.type === 'int' ? parseInt(raw, 10) : parseFloat(raw))
          }}
        />
      )

    case 'list':
      return <ListControl field={field} value={value} onChange={onChange} />

    default:
      return (
        <input
          id={field.key}
          className="input"
          value={String(value ?? '')}
          onChange={(e) => onChange(e.target.value)}
        />
      )
  }
}

function ListControl({
  field,
  value,
  onChange,
}: {
  field: ConfigField
  value: unknown
  onChange: (v: unknown) => void
}) {
  const items = Array.isArray(value) ? value : []
  const isNumeric = items.length > 0 && typeof items[0] === 'number'

  // 数值数组（如 chunk_size）也用文本输入，逗号分隔更直观
  const text = items.join(', ')

  const commit = (raw: string) => {
    const parts = raw
      .split(',')
      .map((s) => s.trim())
      .filter(Boolean)
    onChange(isNumeric || /^\d+$/.test(parts[0] ?? '') ? parts.map(Number) : parts)
  }

  return (
    <input
      id={field.key}
      className="input"
      defaultValue={text}
      placeholder={field.key === 'hotwords' ? '用逗号分隔，例如：iMeeting, FunASR' : '用逗号分隔'}
      onBlur={(e) => commit(e.target.value)}
      onKeyDown={(e) => {
        if (e.key === 'Enter') {
          commit((e.target as HTMLInputElement).value)
          ;(e.target as HTMLInputElement).blur()
        }
      }}
    />
  )
}
