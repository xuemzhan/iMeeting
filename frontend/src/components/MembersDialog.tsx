import { useEffect, useState } from 'react'
import { Loader2, Plus, Trash2, Users, X } from 'lucide-react'
import { meetingApi } from '../api/client'

interface Member {
  user_id: number
  username: string
  display_name: string
  role: string
}

export function MembersDialog({
  meetingId,
  onClose,
}: {
  meetingId: number
  onClose: () => void
}) {
  const [items, setItems] = useState<Member[]>([])
  const [username, setUsername] = useState('')
  const [role, setRole] = useState('viewer')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = () =>
    meetingApi
      .members(meetingId)
      .then(setItems)
      .catch((e) => setError(e instanceof Error ? e.message : '加载失败'))

  useEffect(() => {
    void load()
  }, [meetingId])

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4">
      <div className="panel w-full max-w-[520px] p-4">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="flex items-center gap-2 text-[14px] font-medium">
            <Users size={14} /> 会议协作权限
          </h2>
          <button type="button" onClick={onClose} className="p-1 text-tertiary" aria-label="关闭">
            <X size={15} />
          </button>
        </div>
        <div className="mb-3 flex gap-2">
          <input
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            placeholder="用户名"
            className="min-w-0 flex-1 rounded px-2 py-1.5 text-[12px] outline-none"
            style={{ background: 'var(--bg-surface)', border: '1px solid var(--border-subtle)' }}
          />
          <select
            value={role}
            onChange={(e) => setRole(e.target.value)}
            className="rounded px-2 py-1.5 text-[12px]"
            style={{ background: 'var(--bg-surface)', border: '1px solid var(--border-subtle)' }}
          >
            <option value="viewer">只读</option>
            <option value="editor">可编辑</option>
          </select>
          <button
            type="button"
            onClick={async () => {
              if (!username.trim()) return
              setBusy(true)
              try {
                await meetingApi.addMember(meetingId, username.trim(), role)
                setUsername('')
                await load()
              } catch (e) {
                setError(e instanceof Error ? e.message : '添加失败')
              } finally {
                setBusy(false)
              }
            }}
            disabled={busy}
            className="btn btn-primary text-[12px]"
          >
            {busy ? <Loader2 size={12} className="animate-spin" /> : <Plus size={12} />}
            添加
          </button>
        </div>
        {error && <p className="mb-2 text-[11px]" style={{ color: 'var(--danger-text)' }}>{error}</p>}
        <div className="space-y-1.5">
          {items.map((item) => (
            <div
              key={item.user_id}
              className="flex items-center gap-2 rounded-lg px-2.5 py-2"
              style={{ background: 'var(--bg-subtle)' }}
            >
              <span className="min-w-0 flex-1 truncate text-[12px]">
                {item.display_name || item.username}
                <span className="ml-1 text-[10px] text-tertiary">@{item.username}</span>
              </span>
              <span className="chip">
                {item.role === 'owner' ? '所有者' : item.role === 'editor' ? '可编辑' : '只读'}
              </span>
              {item.role !== 'owner' && (
                <button
                  type="button"
                  onClick={async () => {
                    await meetingApi.removeMember(meetingId, item.user_id)
                    await load()
                  }}
                  className="btn btn-ghost p-1"
                  aria-label="移除成员"
                >
                  <Trash2 size={12} />
                </button>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
