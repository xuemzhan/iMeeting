import type { ConfigField } from '../api/types'
import { Control } from '../pages/admin/ConfigFieldInput'

/**
 * 紧凑版配置分组渲染：标签在上、控件在下。
 *
 * 后台配置页是宽布局（左侧 204px 标签栏），设置抽屉只有 380px，
 * 直接套用会被挤变形，所以这里换一种纵向排布。
 */
export function ConfigGroupFields({
  fields,
  draft,
  errors,
  onChange,
}: {
  fields: ConfigField[]
  draft: Record<string, unknown>
  errors: Record<string, string>
  onChange: (key: string, value: unknown) => void
}) {
  return (
    <div className="flex flex-col gap-3">
      {fields.map((f) => (
        <div key={f.key}>
          <div className="mb-1 flex items-center gap-1.5">
            <label htmlFor={f.key} className="text-[12px]" style={{ color: 'var(--text-primary)' }}>
              {f.label}
            </label>
            {f.restart_required && (
              <span
                className="rounded px-1 py-px text-[10px]"
                style={{ background: 'var(--warn-soft)', color: 'var(--warn-text)' }}
                title="修改后需重启服务才能生效"
              >
                需重启
              </span>
            )}
          </div>

          <Control field={f} value={draft[f.key]} onChange={(v) => onChange(f.key, v)} />

          {f.help && (
            <p className="mt-1 text-[11px] leading-snug" style={{ color: 'var(--text-tertiary)' }}>
              {f.help}
            </p>
          )}
          {errors[f.key] && (
            <p className="mt-1 text-[11px]" style={{ color: 'var(--danger-text)' }}>
              {errors[f.key]}
            </p>
          )}
        </div>
      ))}
    </div>
  )
}
