import type { MouseEvent } from 'react'
import {
  ArrowRight,
  Clock,
  FileText,
  Loader2,
  RotateCcw,
  Trash2,
} from 'lucide-react'

import type { Meeting } from '../api/types'
import { formatDuration } from '../store/session'
import { parseHistoryTags } from './historyTags'

function statusLabel(status: string): string {
  switch (status) {
    case 'scheduled':
      return '已预约'
    case 'finished':
      return '已完成'
    case 'abandoned':
      return '已中断'
    case 'failed':
      return '异常'
    default:
      return '进行中'
  }
}

function statusStyle(status: string): React.CSSProperties {
  switch (status) {
    case 'scheduled':
      return { background: 'var(--accent-soft)', color: 'var(--accent-text)' }
    case 'finished':
      return { background: 'var(--live-soft)', color: 'var(--live-text)' }
    case 'abandoned':
      return { background: 'var(--warn-soft)', color: 'var(--warn-text)' }
    case 'failed':
      return {
        background: 'var(--danger-soft, var(--warn-soft))',
        color: 'var(--danger-text, var(--warn-text))',
      }
    default:
      return { background: 'var(--warn-soft)', color: 'var(--warn-text)' }
  }
}

interface Props {
  meeting: Meeting
  trashView: boolean
  selected: boolean
  deleting: boolean
  onOpen: () => void
  onToggle: () => void
  onDelete: (event: MouseEvent<HTMLElement>) => void
  onRestore: (event: MouseEvent<HTMLElement>) => void
}

export function HistoryMeetingRow({
  meeting,
  trashView,
  selected,
  deleting,
  onOpen,
  onToggle,
  onDelete,
  onRestore,
}: Props) {
  const tags = parseHistoryTags(meeting.tags)

  return (
    <div
      role={trashView ? undefined : 'button'}
      tabIndex={trashView ? undefined : 0}
      onClick={onOpen}
      onKeyDown={(e) => e.key === 'Enter' && onOpen()}
      className={`panel flex items-center gap-3 px-4 py-3 ${
        trashView ? '' : 'cursor-pointer transition-colors hover:bg-[var(--bg-hover)]'
      }`}
    >
      <input
        type="checkbox"
        checked={selected}
        onClick={(e) => e.stopPropagation()}
        onChange={onToggle}
        aria-label={`选择 ${meeting.title}`}
        style={{ accentColor: 'var(--accent)' }}
      />
      <span
        className="flex size-8 shrink-0 items-center justify-center rounded-lg"
        style={{ background: 'var(--accent-soft)', color: 'var(--accent-text)' }}
      >
        <FileText size={15} />
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1.5">
          <p className="truncate text-[13px] font-medium">
            {meeting.title}
            <span className="ml-1.5 font-mono text-[10px] font-normal text-tertiary">
              #{meeting.id}
            </span>
          </p>
          {meeting.archived && (
            <span
              className="chip shrink-0"
              style={{ background: 'var(--bg-subtle)', color: 'var(--text-tertiary)' }}
            >
              已归档
            </span>
          )}
        </div>
        <p className="mt-0.5 flex flex-wrap items-center gap-2 text-[11px] text-tertiary">
          <span>{new Date(meeting.created_at).toLocaleString('zh-CN')}</span>
          {meeting.duration_ms > 0 && (
            <>
              <span aria-hidden>·</span>
              <span className="inline-flex items-center gap-1">
                <Clock size={10} />
                {formatDuration(meeting.duration_ms)}
              </span>
            </>
          )}
          <span aria-hidden>·</span>
          <span>{meeting.segment_count} 段</span>
          {tags.slice(0, 3).map((tag) => (
            <span
              key={tag}
              className="chip"
              style={{ background: 'var(--bg-subtle)', color: 'var(--text-secondary)' }}
            >
              {tag}
            </span>
          ))}
          {tags.length > 3 && <span>+{tags.length - 3}</span>}
        </p>
      </div>
      <span className="chip shrink-0" style={statusStyle(meeting.status)}>
        {statusLabel(meeting.status)}
      </span>
      {trashView ? (
        <button
          type="button"
          onClick={onRestore}
          className="btn btn-ghost shrink-0 text-[11px]"
          title="恢复会议"
        >
          <RotateCcw size={13} /> 恢复
        </button>
      ) : (
        <button
          type="button"
          onClick={onDelete}
          disabled={deleting}
          className="btn btn-ghost shrink-0"
          style={{ width: 28, padding: 0, height: 28 }}
          title="移入回收站"
        >
          {deleting ? <Loader2 size={13} className="animate-spin" /> : <Trash2 size={13} />}
        </button>
      )}
      {!trashView && <ArrowRight size={14} style={{ color: 'var(--text-tertiary)' }} />}
    </div>
  )
}
