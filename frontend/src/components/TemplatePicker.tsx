import { useEffect, useRef, useState } from 'react'
import { FileUp, Loader2, Trash2 } from 'lucide-react'
import { minutesTemplateApi } from '../api/client'
import type { MinutesTemplate } from '../api/types'

interface Props {
  value: number | null
  onChange: (id: number | null) => void
}

/** 纪要模板选择 + 导入（.md/.txt/.docx）。 */
export function TemplatePicker({ value, onChange }: Props) {
  const [items, setItems] = useState<MinutesTemplate[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  const load = () =>
    minutesTemplateApi
      .list()
      .then((rows) => setItems(rows))
      .catch(() => setItems([]))

  useEffect(() => {
    void load()
  }, [])

  const selected = items.find((item) => item.id === value) ?? null

  const onFile = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return
    setBusy(true)
    setError(null)
    try {
      const created = await minutesTemplateApi.importFile(file)
      await load()
      onChange(created.id)
    } catch (e) {
      setError(e instanceof Error ? e.message : '模板导入失败')
    } finally {
      setBusy(false)
    }
  }

  const remove = async () => {
    if (!selected || selected.is_builtin) return
    if (!window.confirm(`删除模板「${selected.name}」？`)) return
    setBusy(true)
    try {
      await minutesTemplateApi.remove(selected.id)
      onChange(null)
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : '删除失败')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="mb-2">
      <div className="flex items-center gap-1.5">
        <select
          value={value ?? ''}
          onChange={(e) => onChange(e.target.value ? Number(e.target.value) : null)}
          aria-label="纪要模板"
          title="选择纪要模板"
          className="min-w-0 flex-1 rounded px-1.5 py-1 text-[11px] outline-none"
          style={{
            background: 'var(--bg-surface)',
            color: 'var(--text-secondary)',
            border: '1px solid var(--border-subtle)',
          }}
        >
          <option value="">默认模板</option>
          {items.map((item) => (
            <option key={item.id} value={item.id}>
              {item.name}
              {item.is_builtin ? '' : '（自定义）'}
            </option>
          ))}
        </select>
        <button
          type="button"
          onClick={() => fileRef.current?.click()}
          disabled={busy}
          title="导入 .md / .txt / .docx 模板"
          aria-label="导入模板"
          className="rounded p-1 text-tertiary transition-colors hover:text-[var(--accent)]"
        >
          {busy ? <Loader2 size={13} className="animate-spin" /> : <FileUp size={13} />}
        </button>
        {selected && !selected.is_builtin && (
          <button
            type="button"
            onClick={() => void remove()}
            disabled={busy}
            title="删除该模板"
            aria-label="删除模板"
            className="rounded p-1 text-tertiary transition-colors hover:text-[var(--danger-text)]"
          >
            <Trash2 size={13} />
          </button>
        )}
        <input
          ref={fileRef}
          type="file"
          accept=".md,.txt,.markdown,.docx"
          className="hidden"
          onChange={(e) => void onFile(e)}
        />
      </div>
      {selected?.description && (
        <p className="mt-1 text-[10px] text-tertiary">{selected.description}</p>
      )}
      {error && (
        <p className="mt-1 text-[10px]" style={{ color: 'var(--danger-text)' }}>
          {error}
        </p>
      )}
    </div>
  )
}
