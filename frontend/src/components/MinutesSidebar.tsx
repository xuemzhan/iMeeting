import {
  CheckSquare,
  History,
  Lightbulb,
  Loader2,
  Network,
  PencilLine,
  Sparkles,
} from 'lucide-react'

import type { Minutes } from '../api/types'
import type { SpeakerRow } from '../lib/speakerRoles'
import { useSettings } from '../store/settings'
import { formatTimestamp } from '../store/session'
import { FontSizeControl } from './FontSizeControl'
import { SpeakerRolesPanel } from './SpeakerRolesPanel'
import { TemplatePicker } from './TemplatePicker'

interface MinutesSidebarProps {
  minutes: Minutes | null
  generating: boolean
  error: string | null
  hasTranscript: boolean
  templateId: number | null
  onTemplateChange: (id: number | null) => void
  showVersions: boolean
  onToggleVersions: () => void
  showMindMap: boolean
  onToggleMindMap: () => void
  onEdit: () => void
  onGenerate: () => void
  onSeek: (ms: number) => void
  /** 参会人重要程度（详情页来自接口 speakers） */
  speakers?: SpeakerRow[]
  onImportanceChange?: (row: SpeakerRow, importance: number) => void
  importanceError?: string | null
  onSpeakerConfirm?: (row: SpeakerRow) => void
  onSpeakerDelete?: (row: SpeakerRow) => void
}

export function MinutesSidebar({
  minutes,
  generating,
  error,
  hasTranscript,
  templateId,
  onTemplateChange,
  showVersions,
  onToggleVersions,
  showMindMap,
  onToggleMindMap,
  onEdit,
  onGenerate,
  onSeek,
  speakers = [],
  onImportanceChange,
  importanceError,
  onSpeakerConfirm,
  onSpeakerDelete,
}: MinutesSidebarProps) {
  const scale = useSettings((s) => s.minutesScale)
  const setScale = useSettings((s) => s.setMinutesScale)

  return (
    <aside
      className="panel reader-scope overflow-hidden"
      style={{ '--reader-scale': String(scale) } as React.CSSProperties}
    >
      <div className="panel-header">
        <Sparkles size={14} style={{ color: 'var(--text-tertiary)' }} />
        <h2 className="shrink-0 whitespace-nowrap">会议纪要</h2>
        <div className="ml-auto">
          <FontSizeControl value={scale} onChange={setScale} label="会议纪要" />
        </div>
      </div>

      {/* 操作单独一行：320px 侧栏里塞进标题行会把文字挤换行 */}
      <div
        className="flex flex-wrap items-center gap-1 px-4 pb-1 pt-2"
        role="toolbar"
        aria-label="纪要操作"
      >
        {minutes && (
          <button
            type="button"
            onClick={onEdit}
            className="btn btn-ghost px-1.5 text-[11px]"
            title="人工修订并生成新版本"
          >
            <PencilLine size={12} /> 修订
          </button>
        )}
        <button
          type="button"
          onClick={onToggleVersions}
          className="btn btn-ghost px-1.5 text-[11px]"
          title="查看纪要历史版本"
        >
          <History size={12} /> {showVersions ? '收起版本' : '版本'}
        </button>
        <button
          type="button"
          onClick={onToggleMindMap}
          className="btn btn-ghost px-1.5 text-[11px]"
          title="用思维导图查看纪要"
        >
          <Network size={12} />
          {showMindMap ? '收起导图' : '思维导图'}
        </button>
      </div>

      <div className="p-4">
        {generating ? (
          <div className="flex flex-col items-center gap-2 py-8">
            <Loader2 size={16} className="animate-spin" style={{ color: 'var(--accent)' }} />
            <p className="text-[12px] text-tertiary">正在分析…</p>
          </div>
        ) : minutes ? (
          <div className="space-y-3">
            {minutes.is_draft && (
              <p
                className="rounded px-2 py-1 text-[11px]"
                style={{
                  background: 'var(--accent-soft)',
                  color: 'var(--accent-text)',
                }}
              >
                实时草稿 · 第 {minutes.version} 版
              </p>
            )}
            {minutes.edited && (
              <p
                className="rounded px-2 py-1 text-[11px]"
                style={{
                  background: 'var(--bg-subtle)',
                  color: 'var(--text-secondary)',
                }}
              >
                人工修订版 · v{minutes.version} · 原始自动版本仍保留
              </p>
            )}
            {minutes.summary && (
              <p className="reader-body-sm text-secondary">
                {minutes.summary}
              </p>
            )}

            {minutes.topics.map((topic, index) => (
              <div
                key={index}
                className="rounded-lg px-3 py-2"
                style={{ background: 'var(--accent-soft)' }}
              >
                <p
                  className="reader-title font-medium"
                  style={{ color: 'var(--accent-text)' }}
                >
                  {topic.title}
                </p>
                {topic.conclusion && (
                  <p
                    className="reader-body-xs mt-1 border-l-2 pl-2"
                    style={{
                      borderColor: 'var(--accent)',
                      color: 'var(--accent-text)',
                    }}
                  >
                    {topic.conclusion}
                  </p>
                )}
                {topic.evidence && topic.evidence.length > 0 && (
                  <div className="mt-1 flex flex-wrap gap-1">
                    {topic.evidence.slice(0, 3).map((evidence) => (
                      <button
                        key={`${evidence.segment_id}-${evidence.start_ms}`}
                        type="button"
                        onClick={() => onSeek(evidence.start_ms)}
                        className="rounded px-1 py-0.5 font-mono text-[10px]"
                        style={{
                          background: 'var(--bg-surface)',
                          color: 'var(--accent-text)',
                        }}
                        title={evidence.text}
                      >
                        原话 {formatTimestamp(evidence.start_ms)}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            ))}

            {minutes.todos.length > 0 && (
              <div>
                <p className="mb-1.5 flex items-center gap-1 text-[11px] font-medium text-tertiary">
                  <CheckSquare size={11} />
                  待办事项
                </p>
                {minutes.todos.map((todo, index) => (
                  <div
                    key={index}
                    className="reader-body-xs mb-1 rounded-lg px-2.5 py-1.5"
                    style={{
                      background: 'var(--warn-soft)',
                      color: 'var(--warn-text)',
                    }}
                  >
                    {todo.task}
                    {todo.owner && <span className="opacity-75"> · {todo.owner}</span>}
                    {todo.evidence?.[0] && (
                      <button
                        type="button"
                        onClick={() => onSeek(todo.evidence![0].start_ms)}
                        className="ml-1 font-mono text-[10px] underline-offset-2 hover:underline"
                      >
                        {formatTimestamp(todo.evidence[0].start_ms)}
                      </button>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        ) : (
          <div className="flex flex-col items-center gap-2 py-8 text-center">
            <Lightbulb size={18} style={{ color: 'var(--text-tertiary)' }} />
            <p className="text-[12px] text-tertiary">还没有生成纪要</p>
          </div>
        )}

        {error && (
          <div
            className="mb-2 rounded-lg px-3 py-2 text-[11px]"
            style={{ background: 'var(--danger-soft)', color: 'var(--danger-text)' }}
          >
            {error}
          </div>
        )}

        <div
          className="mt-3 rounded-lg p-2.5"
          style={{ background: 'var(--bg-subtle)' }}
        >
          <SpeakerRolesPanel
            speakers={speakers}
            onChange={(row, importance) => onImportanceChange?.(row, importance)}
            onConfirm={(row) => onSpeakerConfirm?.(row)}
            onDelete={(row) => onSpeakerDelete?.(row)}
            error={importanceError}
            disabled={!onImportanceChange}
          />
        </div>

        <TemplatePicker value={templateId} onChange={onTemplateChange} />

        <button
          type="button"
          onClick={onGenerate}
          disabled={generating || !hasTranscript}
          title={hasTranscript ? undefined : '该会议还没有转写内容，无法生成纪要'}
          className="btn btn-primary mt-4 w-full"
          style={{ height: 34, opacity: hasTranscript ? undefined : 0.5 }}
        >
          {generating ? (
            <>
              <Loader2 size={13} className="animate-spin" />
              生成中…
            </>
          ) : (
            <>
              <Sparkles size={13} />
              {minutes ? (minutes.is_draft ? '生成终稿' : '重新生成') : '生成纪要'}
            </>
          )}
        </button>

        {!hasTranscript && (
          <p className="mt-2 text-center text-[11px] text-tertiary">
            该会议没有转写内容（可能全程未采集到声音），无法生成纪要
          </p>
        )}
      </div>
    </aside>
  )
}

