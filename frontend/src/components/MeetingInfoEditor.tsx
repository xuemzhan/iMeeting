import { useState } from 'react'
import { Loader2, X } from 'lucide-react'
import { meetingApi } from '../api/client'
import type { Meeting } from '../api/types'

interface Props {
  meeting: Pick<
    Meeting,
    'id' | 'title' | 'participants' | 'agenda' | 'meeting_type' | 'description'
  > &
    Partial<Pick<Meeting, 'scheduled_at'>>
  onSaved: (meeting: Meeting) => void
  onClose: () => void
  captureMinutes?: number
}

export function MeetingInfoEditor({
  meeting,
  onSaved,
  onClose,
  captureMinutes = 3,
}: Props) {
  const [title, setTitle] = useState(meeting.title)
  const [participants, setParticipants] = useState(meeting.participants)
  const [agenda, setAgenda] = useState(meeting.agenda)
  const [meetingType, setMeetingType] = useState(meeting.meeting_type)
  const [description, setDescription] = useState(meeting.description)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const save = async () => {
    setBusy(true)
    setError(null)
    try {
      const updated = await meetingApi.updateMetadata(meeting.id, {
        title: title.trim(),
        participants,
        agenda,
        meeting_type: meetingType,
        description,
      })
      onSaved(updated)
      onClose()
    } catch (e) {
      setError(e instanceof Error ? e.message : '保存失败')
    } finally {
      setBusy(false)
    }
  }

  const inputStyle: React.CSSProperties = {
    background: 'var(--bg-surface)',
    border: '1px solid var(--border-subtle)',
    color: 'var(--text-primary)',
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4">
      <div className="panel w-full max-w-[520px] p-4">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-[14px] font-medium">会议信息</h2>
          <button type="button" onClick={onClose} aria-label="关闭" className="p-1 text-tertiary">
            <X size={15} />
          </button>
        </div>
        <p className="mb-3 text-[11px] text-tertiary">
          会议开始后的前 {captureMinutes} 分钟可以说出主题、参会人和议程，
          系统会自动填充；也可以在这里手动修改。手动保存后自动提取不会覆盖。
        </p>
        <div className="space-y-2">
          <label className="block text-[11px] text-tertiary">
            会议主题
            <input value={title} onChange={(e) => setTitle(e.target.value)} className="mt-1 w-full rounded px-2 py-1.5 text-[12px] outline-none" style={inputStyle} />
          </label>
          <label className="block text-[11px] text-tertiary">
            参会人（逗号分隔）
            <input value={participants} onChange={(e) => setParticipants(e.target.value)} className="mt-1 w-full rounded px-2 py-1.5 text-[12px] outline-none" style={inputStyle} />
          </label>
          <label className="block text-[11px] text-tertiary">
            会议类型
            <input value={meetingType} onChange={(e) => setMeetingType(e.target.value)} placeholder="周会 / 评审 / 访谈 / 培训" className="mt-1 w-full rounded px-2 py-1.5 text-[12px] outline-none" style={inputStyle} />
          </label>
          <label className="block text-[11px] text-tertiary">
            议程
            <textarea value={agenda} onChange={(e) => setAgenda(e.target.value)} rows={3} className="mt-1 w-full rounded px-2 py-1.5 text-[12px] outline-none" style={inputStyle} />
          </label>
          <label className="block text-[11px] text-tertiary">
            描述
            <textarea value={description} onChange={(e) => setDescription(e.target.value)} rows={2} className="mt-1 w-full rounded px-2 py-1.5 text-[12px] outline-none" style={inputStyle} />
          </label>
        </div>
        {error && <p className="mt-2 text-[11px]" style={{ color: 'var(--danger-text)' }}>{error}</p>}
        <div className="mt-3 flex justify-end gap-2">
          <button type="button" onClick={onClose} className="btn btn-ghost text-[12px]">取消</button>
          <button type="button" onClick={() => void save()} disabled={busy || !title.trim()} className="btn btn-primary text-[12px]">
            {busy ? <Loader2 size={12} className="animate-spin" /> : null}
            保存
          </button>
        </div>
      </div>
    </div>
  )
}
