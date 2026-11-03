import { useCallback, useEffect, useState } from 'react'
import { Loader2, Plus, Trash2, Users } from 'lucide-react'
import { meetingApi } from '../api/client'
import { Sheet } from './Sheet'

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

  const load = useCallback(
    () =>
      meetingApi
        .members(meetingId)
        .then(setItems)
        .catch((e) => setError(e instanceof Error ? e.message : '加载失败')),
    [meetingId],
  )

  useEffect(() => {
    void load()
  }, [load])

  return (
    <Sheet
      onClose={onClose}
      title={
        <>
          <Users size={14} /> 会议协作权限
        </>
      }
      ariaLabel="会议协作权限"
      maxWidth={520}
    >
        <div className="mb-3 flex gap-2">
          <input
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            placeholder="用户名"
            className="min-w-0 flex-1 rounded px-2 py-1.5 text-ui outline-none"
            style={{ background: 'var(--bg-surface)', border: '1px solid var(--border-subtle)' }}
          />
          <select
            value={role}
            onChange={(e) => setRole(e.target.value)}
            className="rounded px-2 py-1.5 text-ui"
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
            className="btn btn-primary text-ui"
          >
            {busy ? <Loader2 size={12} className="animate-spin" /> : <Plus size={12} />}
            添加
          </button>
        </div>
        {error && <p className="mb-2 text-meta" style={{ color: 'var(--danger-text)' }}>{error}</p>}
        <div className="space-y-1.5">
          {items.map((item) => (
            <div
              key={item.user_id}
              className="flex items-center gap-2 rounded-lg px-2.5 py-2"
              style={{ background: 'var(--bg-subtle)' }}
            >
              <span className="min-w-0 flex-1 truncate text-ui">
                {item.display_name || item.username}
                <span className="ml-1 text-micro text-tertiary">@{item.username}</span>
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
    </Sheet>
  )
}
