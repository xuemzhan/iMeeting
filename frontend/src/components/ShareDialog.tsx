import { useEffect, useState } from 'react'
import { Copy, Loader2, Share2, Trash2, X } from 'lucide-react'
import { meetingApi } from '../api/client'
import type { ShareLink } from '../api/types'

export function ShareDialog({ meetingId, onClose }: { meetingId: number; onClose: () => void }) {
  const [items, setItems] = useState<ShareLink[]>([])
  const [expires, setExpires] = useState(24)
  const [password, setPassword] = useState('')
  const [allowDownload, setAllowDownload] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = () =>
    meetingApi
      .listShares(meetingId)
      .then(setItems)
      .catch(() => setItems([]))

  useEffect(() => {
    void load()
  }, [meetingId])

  const create = async () => {
    setBusy(true)
    setError(null)
    try {
      const row = await meetingApi.createShare(meetingId, {
        expires_hours: expires,
        password,
        allow_download: allowDownload,
      })
      await load()
      await copy(row.url)
    } catch (e) {
      setError(e instanceof Error ? e.message : '创建分享失败')
    } finally {
      setBusy(false)
    }
  }

  const copy = async (url: string) => {
    try {
      await navigator.clipboard.writeText(`${window.location.origin}${url}`)
    } catch {
      setError('复制失败，请手动复制：' + window.location.origin + url)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4">
      <div className="panel w-full max-w-[560px] p-4">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="flex items-center gap-2 text-[14px] font-medium">
            <Share2 size={14} /> 分享会议
          </h2>
          <button type="button" onClick={onClose} aria-label="关闭" className="p-1 text-tertiary">
            <X size={15} />
          </button>
        </div>
        <div className="mb-3 flex flex-wrap items-end gap-2">
          <label className="text-[11px] text-tertiary">
            有效期
            <select value={expires} onChange={(e) => setExpires(Number(e.target.value))} className="mt-1 block rounded px-2 py-1.5 text-[12px]" style={{ background: 'var(--bg-surface)', border: '1px solid var(--border-subtle)' }}>
              <option value={24}>24 小时</option>
              <option value={72}>3 天</option>
              <option value={168}>7 天</option>
              <option value={720}>30 天</option>
            </select>
          </label>
          <label className="min-w-[120px] flex-1 text-[11px] text-tertiary">
            访问口令（可选）
            <input value={password} onChange={(e) => setPassword(e.target.value)} type="password" className="mt-1 w-full rounded px-2 py-1.5 text-[12px]" style={{ background: 'var(--bg-surface)', border: '1px solid var(--border-subtle)' }} />
          </label>
          <label className="flex items-center gap-1.5 text-[11px] text-tertiary">
            <input type="checkbox" checked={allowDownload} onChange={(e) => setAllowDownload(e.target.checked)} style={{ accentColor: 'var(--accent)' }} />
            允许下载音频
          </label>
          <button type="button" onClick={() => void create()} disabled={busy} className="btn btn-primary text-[12px]">
            {busy ? <Loader2 size={12} className="animate-spin" /> : <Share2 size={12} />}
            创建并复制链接
          </button>
        </div>
        {error && <p className="mb-2 text-[11px]" style={{ color: 'var(--danger-text)' }}>{error}</p>}
        <div className="space-y-1.5">
          {items.length === 0 && <p className="py-4 text-center text-[11px] text-tertiary">还没有分享链接</p>}
          {items.map((item) => (
            <div key={item.id} className="flex items-center gap-2 rounded-lg px-2.5 py-2 text-[11px]" style={{ background: 'var(--bg-subtle)' }}>
              <span className="min-w-0 flex-1 truncate font-mono text-[10px] text-secondary">{window.location.origin}{item.url}</span>
              <span className="shrink-0 text-tertiary">至 {new Date(item.expires_at).toLocaleString('zh-CN')}</span>
              {item.has_password && <span className="chip shrink-0">有口令</span>}
              <button type="button" onClick={() => void copy(item.url)} className="rounded p-1 text-tertiary hover:text-[var(--accent)]" title="复制链接"><Copy size={12} /></button>
              <button
                type="button"
                onClick={async () => {
                  await meetingApi.revokeShare(meetingId, item.token)
                  await load()
                }}
                className="rounded p-1 text-tertiary hover:text-[var(--danger-text)]"
                title="撤销"
              >
                <Trash2 size={12} />
              </button>
            </div>
          ))}
        </div>
        <p className="mt-3 text-[10px] text-tertiary">只读页面不会暴露音频文件路径；开启“允许下载音频”后才可下载。</p>
      </div>
    </div>
  )
}
