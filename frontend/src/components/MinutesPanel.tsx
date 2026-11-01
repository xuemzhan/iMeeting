import { Sparkles, Loader2, AlertCircle, CheckSquare, Lightbulb } from 'lucide-react'
import { useSession } from '../store/session'
import { useSettings } from '../store/settings'
import type { Minutes } from '../api/types'
import type { SpeakerRow } from '../lib/speakerRoles'
import { SpeakerRolesPanel } from './SpeakerRolesPanel'
import { TemplatePicker } from './TemplatePicker'

interface Props {
  minutes: Minutes | null
  generating: boolean
  onGenerate: () => void
  hasContent: boolean
  templateId: number | null
  onTemplateChange: (id: number | null) => void
  /** 参会人重要程度（实时页由片段聚合） */
  speakers?: SpeakerRow[]
  onImportanceChange?: (row: SpeakerRow, importance: number) => void
  importanceError?: string | null
  onSpeakerConfirm?: (row: SpeakerRow) => void
  onSpeakerDelete?: (row: SpeakerRow) => void
}

export function MinutesPanel({
  minutes,
  generating,
  onGenerate,
  hasContent,
  templateId,
  onTemplateChange,
  speakers = [],
  onImportanceChange,
  importanceError,
  onSpeakerConfirm,
  onSpeakerDelete,
}: Props) {
  const segments = useSession((s) => s.segments)
  const scale = useSettings((s) => s.minutesScale)

  const finalCount = segments.filter((s) => s.isFinal).length

  return (
    <div
      className="reader-scope flex min-h-0 flex-1 flex-col"
      style={{ '--reader-scale': String(scale) } as React.CSSProperties}
    >
      {/* 纪要内容 */}
      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
        {generating ? (
          <LoadingState />
        ) : minutes ? (
          <MinutesContent minutes={minutes} />
        ) : (
          <IdleState hasContent={hasContent} finalCount={finalCount} />
        )}
      </div>

      {/* 底部：参会人重要程度 + 生成按钮 */}
      <div
        className="shrink-0 px-4 py-3"
        style={{ borderTop: '1px solid var(--border-subtle)' }}
      >
        <div className="mb-3">
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
          disabled={generating || !hasContent}
          className="btn btn-primary w-full"
          style={{ height: 34 }}
        >
          {generating ? (
            <>
              <Loader2 size={14} className="animate-spin" />
              生成中…
            </>
          ) : (
            <>
              <Sparkles size={14} />
              {minutes
                ? minutes.is_draft
                  ? '生成终稿纪要'
                  : '重新生成纪要'
                : '生成会议纪要'}
            </>
          )}
        </button>

        {!hasContent && (
          <p className="mt-2 text-center text-meta text-tertiary">会议结束后可生成纪要</p>
        )}
      </div>
    </div>
  )
}

function MinutesContent({ minutes }: { minutes: Minutes }) {
  if (minutes.status === 'failed') {
    return (
      <div
        className="flex items-start gap-2 rounded-lg px-3 py-2.5"
        style={{ background: 'var(--danger-soft)', color: 'var(--danger-text)' }}
      >
        <AlertCircle size={14} className="mt-0.5 shrink-0" />
        <div>
          <p className="text-ui font-medium">纪要生成失败</p>
          <p className="text-meta opacity-85">{minutes.error || '未知错误'}</p>
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      {minutes.is_draft && (
        <div
          className="flex items-center gap-2 rounded-lg px-3 py-2 text-meta"
          style={{ background: 'var(--accent-soft)', color: 'var(--accent-text)' }}
        >
          <Sparkles size={12} />
          实时草稿 · 第 {minutes.version} 版 ·{' '}
          {new Date(minutes.created_at).toLocaleTimeString('zh-CN', {
            hour: '2-digit',
            minute: '2-digit',
          })}
        </div>
      )}
      {minutes.summary && (
        <section>
          <p className="mb-1.5 text-meta font-medium text-tertiary">整体摘要</p>
          <p className="reader-body-sm text-secondary">{minutes.summary}</p>
        </section>
      )}

      {minutes.topics.length > 0 && (
        <section>
          <p className="mb-2 flex items-center gap-1.5 text-meta font-medium text-tertiary">
            <Lightbulb size={12} />
            议题与结论
          </p>
          <div className="space-y-2">
            {minutes.topics.map((topic, i) => (
              <div
                key={i}
                className="rounded-lg px-3 py-2"
                style={{ background: 'var(--accent-soft)' }}
              >
                <p className="reader-title font-medium" style={{ color: 'var(--accent-text)' }}>
                  {topic.title}
                </p>
                {topic.detail && (
                  <p className="reader-body-xs mt-1" style={{ color: 'var(--accent-text)', opacity: 0.85 }}>
                    {topic.detail}
                  </p>
                )}
                {topic.conclusion && (
                  <p
                    className="reader-body-xs mt-1.5 border-l-2 pl-2"
                    style={{ borderColor: 'var(--accent)', color: 'var(--accent-text)' }}
                  >
                    {topic.conclusion}
                  </p>
                )}
              </div>
            ))}
          </div>
        </section>
      )}

      {minutes.todos.length > 0 && (
        <section>
          <p className="mb-2 flex items-center gap-1.5 text-meta font-medium text-tertiary">
            <CheckSquare size={12} />
            待办事项
          </p>
          <div className="space-y-1.5">
            {minutes.todos.map((todo, i) => (
              <div
                key={i}
                className="flex items-start gap-2 rounded-lg px-3 py-2"
                style={{ background: 'var(--warn-soft)' }}
              >
                <span
                  className="mt-1 size-1.5 shrink-0 rounded-full"
                  style={{ background: 'var(--warn)' }}
                />
                <div className="min-w-0 flex-1">
                  <p className="reader-body-xs" style={{ color: 'var(--warn-text)' }}>
                    {todo.task}
                  </p>
                  {(todo.owner || todo.due) && (
                    <p className="reader-meta mt-0.5 opacity-80" style={{ color: 'var(--warn-text)' }}>
                      {todo.owner && `责任人：${todo.owner}`}
                      {todo.owner && todo.due && ' · '}
                      {todo.due && `截止：${todo.due}`}
                    </p>
                  )}
                </div>
              </div>
            ))}
          </div>
        </section>
      )}
    </div>
  )
}

function LoadingState() {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-3">
      <Loader2 size={20} className="animate-spin" style={{ color: 'var(--accent)' }} />
      <p className="text-ui text-secondary">正在分析会议内容…</p>
      <p className="text-meta text-tertiary">长会议会分段处理，请稍候</p>
    </div>
  )
}

function IdleState({ hasContent, finalCount }: { hasContent: boolean; finalCount: number }) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-2 px-6 text-center">
      <div
        className="mb-1 flex size-10 items-center justify-center rounded-full"
        style={{ background: 'var(--accent-soft)' }}
      >
        <Sparkles size={17} style={{ color: 'var(--accent-text)' }} />
      </div>
      <p className="text-ui text-secondary">
        {hasContent ? '已转写 ' : '会议纪要'}
      </p>
      {hasContent && (
        <p className="text-meta text-tertiary">
          已转写 {finalCount} 段，点击下方按钮生成结构化纪要
        </p>
      )}
      {!hasContent && (
        <p className="text-meta text-tertiary">
          会议结束后将自动提取议题、结论与待办事项
        </p>
      )}
    </div>
  )
}
