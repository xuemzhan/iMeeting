import { useEffect, useState } from 'react'
import { Loader2, Plus, Trash2, Upload } from 'lucide-react'
import { glossaryApi } from '../api/client'
import type { GlossaryTerm } from '../api/types'

export function GlossaryPanel() {
  const [terms, setTerms] = useState<GlossaryTerm[]>([])
  const [term, setTerm] = useState('')
  const [aliases, setAliases] = useState('')
  const [category, setCategory] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const load = () =>
    glossaryApi
      .list()
      .then(setTerms)
      .catch((e) => setError(e instanceof Error ? e.message : '加载失败'))

  useEffect(() => {
    void load()
  }, [])

  const add = async () => {
    if (!term.trim()) return
    setBusy(true)
    setError(null)
    try {
      await glossaryApi.create(term.trim(), aliases.trim(), category.trim())
      setTerm('')
      setAliases('')
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : '添加失败')
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="panel overflow-hidden">
      <div className="panel-header">
        <h2>企业词表</h2>
        <span className="ml-auto text-micro text-tertiary">
          规范词会进入 ASR 热词和纪要提示
        </span>
      </div>
      <div className="p-4">
        <div className="mb-3 flex flex-wrap gap-2">
          <input
            value={term}
            onChange={(e) => setTerm(e.target.value)}
            placeholder="规范词，如 XX-1000"
            className="min-w-[160px] flex-1 rounded px-2 py-1.5 text-ui outline-none"
            style={{ background: 'var(--bg-surface)', border: '1px solid var(--border-subtle)' }}
          />
          <input
            value={aliases}
            onChange={(e) => setAliases(e.target.value)}
            placeholder="别名，逗号分隔"
            className="min-w-[160px] flex-1 rounded px-2 py-1.5 text-ui outline-none"
            style={{ background: 'var(--bg-surface)', border: '1px solid var(--border-subtle)' }}
          />
          <input
            value={category}
            onChange={(e) => setCategory(e.target.value)}
            placeholder="分类"
            className="w-[110px] rounded px-2 py-1.5 text-ui outline-none"
            style={{ background: 'var(--bg-surface)', border: '1px solid var(--border-subtle)' }}
          />
          <button type="button" onClick={() => void add()} disabled={busy} className="btn btn-primary text-ui">
            {busy ? <Loader2 size={12} className="animate-spin" /> : <Plus size={12} />} 添加
          </button>
          <label className="btn cursor-pointer text-ui">
            <Upload size={12} /> 导入 CSV
            <input
              type="file"
              accept=".csv,text/csv"
              className="hidden"
              onChange={async (e) => {
                const file = e.target.files?.[0]
                e.currentTarget.value = ''
                if (!file) return
                setBusy(true)
                setError(null)
                setNotice(null)
                try {
                  const { imported } = await glossaryApi.importCsv(file)
                  await load()
                  setNotice(`已导入 ${imported} 条词条`)
                } catch (err) {
                  setError(err instanceof Error ? err.message : '导入失败')
                } finally {
                  setBusy(false)
                }
              }}
            />
          </label>
        </div>
        {error && <p className="mb-2 text-meta" style={{ color: 'var(--danger-text)' }}>{error}</p>}
        {notice && <p className="mb-2 text-meta text-tertiary">{notice}</p>}
        <div className="space-y-1">
          {terms.length === 0 && (
            <p className="py-8 text-center text-meta text-tertiary">还没有企业词条</p>
          )}
          {terms.map((row) => (
            <div
              key={row.id}
              className="flex items-center gap-2 rounded-lg px-2.5 py-2"
              style={{ background: 'var(--bg-subtle)' }}
            >
              <span className="text-ui font-medium">{row.term}</span>
              {row.aliases && <span className="min-w-0 flex-1 truncate text-micro text-tertiary">{row.aliases}</span>}
              <span className="chip">{row.category || '术语'}</span>
              <button
                type="button"
                onClick={async () => {
                  await glossaryApi.remove(row.id)
                  await load()
                }}
                className="btn btn-ghost p-1"
                aria-label={`删除 ${row.term}`}
              >
                <Trash2 size={12} />
              </button>
            </div>
          ))}
        </div>
      </div>
    </section>
  )
}
