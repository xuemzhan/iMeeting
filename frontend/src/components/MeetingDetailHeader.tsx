import { Link } from 'react-router-dom'
import {
  ArrowLeft,
  Download,
  Info,
  Share2,
  ShieldCheck,
  Users,
} from 'lucide-react'

import type { MeetingDetail } from '../api/types'
import { formatDuration } from '../store/session'

const EXPORT_FORMATS = [
  { key: 'srt', label: 'SRT 字幕' },
  { key: 'vtt', label: 'VTT 字幕' },
  { key: 'md', label: 'Markdown' },
  { key: 'txt', label: '纯文本' },
  { key: 'json', label: 'JSON' },
  { key: 'docx', label: 'Word 文档' },
  { key: 'pdf', label: 'PDF 文档' },
]

interface Props {
  meeting: MeetingDetail
  exportOpen: boolean
  exportStatus: string | null
  onToggleExport: () => void
  onExport: (format: string) => void
  onOpenInfo: () => void
  onOpenShare: () => void
  onOpenCompliance: () => void
  onOpenMembers: () => void
}

export function MeetingDetailHeader({
  meeting,
  exportOpen,
  exportStatus,
  onToggleExport,
  onExport,
  onOpenInfo,
  onOpenShare,
  onOpenCompliance,
  onOpenMembers,
}: Props) {
  return (
    <>
      <Link
        to="/history"
        className="mb-4 inline-flex items-center gap-1 text-[12px] no-underline text-tertiary hover:text-secondary"
      >
        <ArrowLeft size={13} />
        返回列表
      </Link>

      <div className="mb-4 flex items-start justify-between gap-4">
        <div>
          <h1 className="text-[16px] font-medium">{meeting.title}</h1>
          <p className="mt-1 flex items-center gap-2 text-[12px] text-tertiary">
            <span>{new Date(meeting.created_at).toLocaleString('zh-CN')}</span>
            <span aria-hidden>·</span>
            <span>{formatDuration(meeting.duration_ms)}</span>
            <span aria-hidden>·</span>
            <span>{meeting.segments.length} 段</span>
            {meeting.has_audio && (
              <>
                <span aria-hidden>·</span>
                <span style={{ color: 'var(--live-text)' }}>有录音</span>
              </>
            )}
          </p>
          {(meeting.participants || meeting.meeting_type || meeting.agenda) && (
            <p className="mt-1 max-w-[680px] text-[11px] text-tertiary">
              {meeting.meeting_type && `类型：${meeting.meeting_type} · `}
              {meeting.participants && `参会：${meeting.participants}`}
              {meeting.participants && meeting.agenda && ' · '}
              {meeting.agenda && `议程：${meeting.agenda}`}
            </p>
          )}
        </div>

        <div className="flex shrink-0 items-center gap-1.5">
          <button
            type="button"
            className="btn btn-ghost"
            onClick={onOpenInfo}
            title="编辑会议信息"
          >
            <Info size={13} /> 会议信息
          </button>
          <button
            type="button"
            className="btn btn-ghost"
            onClick={onOpenShare}
          >
            <Share2 size={13} /> 分享
          </button>
          <button
            type="button"
            className="btn btn-ghost"
            onClick={onOpenCompliance}
          >
            <ShieldCheck size={13} /> 合规
          </button>
          <button
            type="button"
            className="btn btn-ghost"
            onClick={onOpenMembers}
          >
            <Users size={13} /> 协作
          </button>
          <div className="relative">
            <button
              type="button"
              className="btn"
              onClick={onToggleExport}
              disabled={Boolean(exportStatus)}
            >
              <Download size={13} />
              {exportStatus ?? '导出'}
            </button>
            {exportOpen && (
              <div
                className="panel absolute right-0 z-10 mt-1 w-[140px] overflow-hidden py-1"
                style={{ boxShadow: '0 8px 24px rgba(0,0,0,0.12)' }}
              >
                {EXPORT_FORMATS.map((format) => (
                  <button
                    key={format.key}
                    type="button"
                    onClick={() => onExport(format.key)}
                    className="block w-full px-3 py-1.5 text-left text-[12px] transition-colors hover:bg-[var(--bg-hover)]"
                  >
                    {format.label}
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </>
  )
}
