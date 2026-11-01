import { useEffect, useRef, useState, type ReactNode } from 'react'
import {
  AlertCircle,
  Check,
  ChevronDown,
  ChevronUp,
  Link2,
  Loader2,
  Mic,
  Pencil,
  Plus,
  Sparkles,
  Trash2,
  Users,
  X,
} from 'lucide-react'

import { meetingApi } from '../api/client'
import type { MeetingParticipantItem, MeetingSpeaker } from '../api/types'
import { useParticipants } from '../hooks/useParticipants'
import { STATE_LABELS, pendingCount, summarizeAttributes, visibleParticipants } from '../lib/participants'
import { listenOnce, normalizeSpokenName, speechSupported } from '../lib/speech'

interface Props {
  meetingId: number | null
  /** >0 时轮询（会中页面用） */
  pollMs?: number
  /** 会中横幅用：更矮的一行 */
  compact?: boolean
}

/**
 * 参会人（REQ-001）。
 *
 * 默认只占一行：人数 + 姓名 chips + 「添加/语音/识别」三个按钮，明细按需展开。
 * 会中每识别到一个新讲话人，后端会把人自动加进来（待确认），所以这里不需要
 * 用户先做什么就能看到「说话人 N」。
 */
export function ParticipantsPanel({ meetingId, pollMs = 0, compact = false }: Props) {
  const controller = useParticipants(meetingId, { pollMs })
  const [speakers, setSpeakers] = useState<MeetingSpeaker[]>([])
  useEffect(() => {
    let current = true
    if (meetingId != null) void meetingApi.speakers(meetingId).then((items) => { if (current) setSpeakers(items) }).catch(() => { if (current) setSpeakers([]) })
    return () => { current = false }
  }, [meetingId, controller.items])
  const [includeLow, setIncludeLow] = useState(false)
  const rows = visibleParticipants(controller.items, includeLow)
  const pending = pendingCount(controller.items)
  const [open, setOpen] = useState(false)
  const [adding, setAdding] = useState(false)
  const [draft, setDraft] = useState('')
  const [speechError, setSpeechError] = useState<string | null>(null)
  const [listening, setListening] = useState(false)
  const listener = useRef<{ stop: () => void } | null>(null)
  const canSpeak = speechSupported()

  // 卸载时收掉还在跑的识别，避免浏览器一直占用麦克风
  useEffect(() => {
    return () => {
      listener.current?.stop()
      listener.current = null
    }
  }, [])

  const startAdding = () => {
    setOpen(true)
    setAdding(true)
    setSpeechError(null)
  }

  const stopListening = () => {
    listener.current?.stop()
    listener.current = null
    setListening(false)
  }

  const startListening = () => {
    setSpeechError(null)
    setAdding(true)
    setListening(true)
    listener.current = listenOnce(
      (result) => {
        setDraft((current) => normalizeSpokenName(`${current}${result.transcript}`))
      },
      (message) => {
        setSpeechError(message)
        setListening(false)
      },
      () => setListening(false),
    )
  }

  const submit = async () => {
    const name = draft.trim()
    if (!name) return
    try {
      await controller.add(name)
      setDraft('')
      setAdding(false)
      setOpen(true)
    } catch {
      /* 错误已由 hook 写进 controller.error */
    }
  }

  if (meetingId == null) return null

  return (
    <section
      className={`panel overflow-hidden ${compact ? '' : 'mt-2'}`}
      aria-label="会议参会人"
    >
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 px-2.5 py-1.5">
        <span className="flex items-center gap-1 text-ui font-medium">
          <Users size={13} style={{ color: 'var(--text-tertiary)' }} />
          参会人
          <span className="text-meta font-normal text-tertiary">{rows.length}</span>
        </span>

        {rows.length === 0 && (
          <span className="text-meta text-tertiary">
            {controller.loading ? '读取中…' : '还没有识别到讲话人'}
          </span>
        )}

        {/* compact：姓名 chips 改成一行横滑，避免把顶栏挤成三四行 */}
        <span className="flex min-w-0 flex-wrap items-center gap-1 max-md:order-last max-md:w-full max-md:flex-nowrap max-md:overflow-x-auto max-md:overscroll-x-contain">
          {rows.map((row) => (
            <ParticipantChip
              key={row.person_id}
              row={row}
              onConfirm={() => void controller.setState(row.person_id, 'confirmed')}
              onReject={() => void controller.setState(row.person_id, 'rejected')}
            />
          ))}
        </span>

        {pending > 0 && (
          <span className="text-micro" style={{ color: 'var(--warn-text)' }}>
            {pending} 位待确认
          </span>
        )}

        <span className="ml-auto flex items-center gap-0.5">
          {pending > 0 && <button type="button" className="btn btn-ghost text-meta" title="批量确认默认显示的候选；低可信候选需单独核对" onClick={() => void controller.confirmAll()}>确认全部</button>}
          {controller.items.some((item) => item.hidden_by_confidence) && <button type="button" className="btn btn-ghost text-meta" onClick={() => setIncludeLow((value) => !value)}>{includeLow ? '收起低可信候选' : '显示低可信候选'}</button>}
          <IconButton label="添加参会人" onClick={startAdding}>
            <Plus size={12} />
          </IconButton>
          {canSpeak && (
            <IconButton
              label={listening ? '停止录音' : '语音输入姓名'}
              onClick={listening ? stopListening : startListening}
              active={listening}
            >
              <Mic size={12} />
            </IconButton>
          )}
          <IconButton label="从开场介绍里识别参会人" onClick={() => void controller.extract()} busy={controller.busy}>
            <Sparkles size={12} />
          </IconButton>
          <IconButton label={open ? '收起明细' : '展开明细'} onClick={() => setOpen((value) => !value)}>
            {open ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
          </IconButton>
        </span>
      </div>

      {(adding || (open && rows.length > 0)) && (
        <div className="space-y-1.5 border-t px-2.5 py-2" style={{ borderColor: 'var(--border-subtle)' }}>
          {adding && (
            <div className="flex items-center gap-1">
              <input
                autoFocus
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') void submit()
                  if (event.key === 'Escape') {
                    stopListening()
                    setAdding(false)
                    setDraft('')
                  }
                }}
                placeholder={listening ? '请说出姓名…' : '输入姓名，回车加入'}
                maxLength={32}
                aria-label="新参会人姓名"
                className="min-w-0 flex-1 rounded px-2 py-1 text-ui outline-none"
                style={{
                  background: 'var(--bg-surface)',
                  color: 'var(--text-primary)',
                  border: '1px solid var(--border-subtle)',
                }}
              />
              <button
                type="button"
                onClick={() => void submit()}
                disabled={!draft.trim()}
                className="btn btn-primary px-2 py-1 text-meta"
              >
                加入
              </button>
              <IconButton
                label="取消"
                onClick={() => {
                  stopListening()
                  setAdding(false)
                  setDraft('')
                }}
              >
                <X size={12} />
              </IconButton>
            </div>
          )}

          {open && rows.length > 0 && (
            <div className="max-h-64 space-y-1 overflow-y-auto">
              {rows.map((row) => (
                <ParticipantRow
                  key={row.person_id}
                  row={row}
                  speakers={speakers}
                  meetingId={meetingId}
                  onConfirm={() => void controller.setState(row.person_id, 'confirmed')}
                  onReject={() => void controller.setState(row.person_id, 'rejected')}
                  onRename={(name) => void controller.rename(row.person_id, name)}
                  onBind={(key) => void controller.bind(row.person_id, key)}
                  onDetails={(unit, title) => void controller.editDetails(row.person_id, unit, title)}
                  onMerge={(target) => void controller.merge(target, row.person_id)}
                  peers={rows.filter((peer) => peer.person_id !== row.person_id)}
                />
              ))}
            </div>
          )}
        </div>
      )}

      {(controller.error || speechError) && (
        <p
          className="flex items-start gap-1 px-2.5 pb-1.5 text-micro"
          style={{ color: 'var(--danger-text)' }}
        >
          <AlertCircle size={10} className="mt-0.5 shrink-0" />
          {speechError ?? controller.error}
        </p>
      )}
    </section>
  )
}

function IconButton({
  label,
  onClick,
  children,
  busy = false,
  active = false,
}: {
  label: string
  onClick: () => void
  children: ReactNode
  busy?: boolean
  active?: boolean
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={busy}
      title={label}
      aria-label={label}
      className="flex items-center justify-center rounded p-1 hover:bg-[var(--bg-hover)] max-md:size-10"
      style={{ color: active ? 'var(--accent-text)' : 'var(--text-tertiary)' }}
    >
      {busy ? <Loader2 size={12} className="animate-spin" /> : children}
    </button>
  )
}

/** 一行里的小 chip：待确认的点一下就是确认，已确认的只显示。
 *
 * 高度走类而不是内联：桌面仍是 20px（与改造前一致），compact 抬到 32，
 * 横滑时更容易点中。
 */
function ParticipantChip({
  row,
  onConfirm,
  onReject,
}: {
  row: MeetingParticipantItem
  onConfirm: () => void
  onReject: () => void
}) {
  const isPending = row.state === 'pending'
  const label = row.speaker.length > 8 ? `${row.speaker.slice(0, 8)}…` : row.speaker
  const summary = summarizeAttributes(row.attributes)
  return (
    <span
      className="chip h-5 max-w-[140px] gap-1 pl-2 pr-1 max-md:h-8"
      style={{
        background: isPending ? 'var(--warn-soft)' : 'var(--accent-soft)',
        color: isPending ? 'var(--warn-text)' : 'var(--accent-text)',
        textDecoration: row.ambiguous ? 'underline dotted' : undefined,
      }}
      title={
        isPending
          ? `${row.speaker}｜点姓名确认，点 × 删除`
          : `${row.speaker}${summary ? `（${summary}）` : ''}`
      }
    >
      <button
        type="button"
        onClick={isPending ? onConfirm : undefined}
        className="min-w-0 truncate text-left"
        style={{ background: 'transparent', color: 'inherit', cursor: isPending ? 'pointer' : 'default' }}
      >
        {label}
      </button>
      {/* 不展开明细也能删：现场最常见的就是「识别错了一个人」，要求用户先展开再说
          是多此一举（2026-11-01 参会人控件修复里定下的行为）。 */}
      <button
        type="button"
        onClick={onReject}
        aria-label={`删除参会人 ${row.speaker}`}
        title={`不是参会人：删除「${row.speaker}」`}
        className="flex size-4 shrink-0 items-center justify-center rounded-full max-md:size-8"
        style={{ background: 'transparent', color: 'inherit', opacity: 0.65 }}
      >
        <X size={10} />
      </button>
    </span>
  )
}

function ParticipantRow({
  row,
  speakers,
  meetingId,
  onConfirm,
  onReject,
  onRename,
  onBind,
  onDetails,
  onMerge,
  peers,
}: {
  row: MeetingParticipantItem
  speakers: MeetingSpeaker[]
  meetingId: number
  onConfirm: () => void
  onReject: () => void
  onRename: (name: string) => void
  onBind: (speakerKey: string | null) => void
  onDetails: (unit: string, title: string) => void
  onMerge: (targetId: number) => void
  peers: MeetingParticipantItem[]
}) {
  const [renaming, setRenaming] = useState(false)
  const [draft, setDraft] = useState(row.speaker)
  const [binding, setBinding] = useState(false)
  const [editingDetails, setEditingDetails] = useState(false)
  const [unit, setUnit] = useState(row.attributes.find((item) => item.category === 'org' && item.key === 'unit')?.value ?? '')
  const [title, setTitle] = useState(row.attributes.find((item) => item.category === 'role' && item.key === 'title')?.value ?? '')
  const [keyDraft, setKeyDraft] = useState(row.speaker_key ?? '')
  const summary = summarizeAttributes(row.attributes)
  const isPending = row.state === 'pending'

  const commitRename = () => {
    const name = draft.trim()
    setRenaming(false)
    if (name && name !== row.speaker) onRename(name)
  }

  return (
    <div
      className="flex flex-wrap items-center gap-x-2 gap-y-1 rounded-md px-2 py-1"
      style={{ background: 'var(--bg-subtle)' }}
    >
      {renaming ? (
        <input
          autoFocus
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onBlur={commitRename}
          onKeyDown={(event) => {
            if (event.key === 'Enter') commitRename()
            if (event.key === 'Escape') setRenaming(false)
          }}
          maxLength={32}
          aria-label="参会人姓名"
          className="w-[110px] rounded px-1.5 py-0.5 text-ui outline-none"
          style={{
            background: 'var(--bg-surface)',
            color: 'var(--text-primary)',
            border: '1px solid var(--accent)',
          }}
        />
      ) : (
        <button
          type="button"
          onClick={() => {
            setDraft(row.speaker)
            setRenaming(true)
          }}
          className="rounded py-1 text-left text-[12px] font-medium max-md:text-[14px]"
          style={{ color: 'var(--text-primary)' }}
          title="点击改名"
        >
          {row.speaker}
          <Pencil size={10} className="ml-1 inline opacity-60" />
        </button>
      )}

      <span
        className="chip"
        style={{
          height: 16,
          background: isPending ? 'var(--warn-soft)' : 'var(--accent-soft)',
          color: isPending ? 'var(--warn-text)' : 'var(--accent-text)',
        }}
        title={row.ambiguous ? '与已有人员同名，请用单位/职务区分' : undefined}
      >
        {row.ambiguous && isPending ? '有歧义' : STATE_LABELS[row.state]}
      </span>

      {summary && <span className="text-meta text-tertiary">{summary}</span>}
      {row.role && <span className="text-meta text-tertiary">· {row.role}</span>}
      {row.evidence.map((segmentId, index) => <a key={segmentId} className="text-micro underline" href={`/history/${meetingId}#${encodeURIComponent(segmentId)}`}>证据{index + 1}</a>)}

      {binding ? (
        <span className="flex items-center gap-1">
          <select aria-label="选择说话人" value={keyDraft} onChange={(event) => setKeyDraft(event.target.value)} className="rounded border px-1 py-0.5 text-meta">
            <option value="">解除绑定</option>
            {speakers.filter((speaker) => !speaker.speaker_key.startsWith('label:')).map((speaker) => <option key={speaker.speaker_key} value={speaker.speaker_key}>{speaker.speaker}（{speaker.segments}段发言）</option>)}
          </select>
          <button
            type="button"
            onClick={() => {
              setBinding(false)
              onBind(keyDraft.trim() || null)
            }}
            className="rounded px-1 text-micro"
            style={{ color: 'var(--accent-text)' }}
          >
            绑定
          </button>
        </span>
      ) : (
        <button
          type="button"
          onClick={() => {
            setKeyDraft(row.speaker_key ?? '')
            setBinding(true)
          }}
          className="inline-flex items-center gap-0.5 rounded px-1 py-1 text-[10px] text-tertiary hover:bg-[var(--bg-hover)] max-md:text-[12px]"
          title={row.speaker_key ? '已绑定声音，点击修改' : '从本场已识别的说话人中选择'}
        >
          <Link2 size={10} />
          {row.speaker_key ? '已绑定声音' : '绑定声音'}
        </button>
      )}

      <button type="button" className="py-1 text-[10px] underline max-md:text-[12px]" onClick={() => setEditingDetails((value) => !value)}>编辑单位/职务</button>
      {editingDetails && <span className="flex gap-1">
        <input aria-label="参会人单位" placeholder="单位" maxLength={64} value={unit} onChange={(e) => setUnit(e.target.value)} className="w-24 rounded border px-1 text-meta" />
        <input aria-label="参会人职务" placeholder="职务" maxLength={64} value={title} onChange={(e) => setTitle(e.target.value)} className="w-24 rounded border px-1 text-meta" />
        <button type="button" className="text-meta underline" onClick={() => { onDetails(unit, title); setEditingDetails(false) }}>保存属性</button>
      </span>}
      {peers.length > 0 && <select aria-label={`合并参会人 ${row.speaker}`} className="rounded border text-micro" value="" onChange={(e) => { if (e.target.value) onMerge(Number(e.target.value)) }}>
        <option value="">合并到…</option>
        {peers.map((peer) => <option key={peer.person_id} value={peer.person_id}>{peer.speaker}</option>)}
      </select>}
      <span className="ml-auto flex items-center gap-1">
        {isPending && (
          <button
            type="button"
            onClick={onConfirm}
            className="flex items-center justify-center rounded px-1 py-0.5 hover:bg-[var(--bg-hover)] max-md:size-10"
            title="确认是参会人"
            aria-label={`确认参会人 ${row.speaker}`}
            style={{ color: 'var(--accent-text)' }}
          >
            <Check size={12} />
          </button>
        )}
        <button
          type="button"
          onClick={onReject}
          className="flex items-center justify-center rounded px-1 py-0.5 hover:bg-[var(--bg-hover)] max-md:size-10"
          title="删除该参会人（不是参会人，删除后不再显示）"
          aria-label={`删除参会人 ${row.speaker}`}
          style={{ color: 'var(--danger-text)' }}
        >
          <Trash2 size={12} />
        </button>
      </span>
    </div>
  )
}
