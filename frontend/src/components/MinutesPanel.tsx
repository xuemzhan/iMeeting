import { Sparkles, Loader2, AlertCircle, CheckSquare, Lightbulb } from 'lucide-react'
import { useSession } from '../store/session'
import type { Minutes } from '../api/types'
import { TemplatePicker } from './TemplatePicker'

interface Props {
  minutes: Minutes | null
  generating: boolean
  onGenerate: () => void
  hasContent: boolean
  templateId: number | null
  onTemplateChange: (id: number | null) => void
}

export function MinutesPanel({
  minutes,
  generating,
  onGenerate,
  hasContent,
  templateId,
  onTemplateChange,
}: Props) {
  const speakers = useSession((s) => s.speakers)
  const segments = useSession((s) => s.segments)

  const finalCount = segments.filter((s) => s.isFinal).length

  return (
    <div className="flex min-h-0 flex-1 flex-col">
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

      {/* 底部：说话人统计 + 生成按钮 */}
      <div
        className="shrink-0 px-4 py-3"
        style={{ borderTop: '1px solid var(--border-subtle)' }}
      >
        {speakers.length > 0 && (
          <div className="mb-3">
            <p className="mb-2 text-[11px] font-medium text-tertiary">说话人分布</p>
            <div className="space-y-1.5">
              {speakers.slice(0, 4).map((sp) => (
                <div key={sp.id} className="flex items-center gap-2">
                  <span className="w-14 shrink-0 truncate text-[11px] text-secondary">{sp.id}</span>
                  <div
                    className="h-1.5 flex-1 overflow-hidden rounded-full"
                    style={{ background: 'var(--bg-subtle)' }}
                  >
                    <div
                      className="h-full rounded-full transition-[width] duration-500"
                      style={{
                        width: `${Math.round(sp.ratio * 100)}%`,
                        background: 'var(--accent)',
                      }}
                    />
                  </div>
                  <span className="w-8 shrink-0 text-right font-mono text-[11px] tabular-nums text-tertiary">
                    {Math.round(sp.ratio * 100)}%
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}

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
          <p className="mt-2 text-center text-[11px] text-tertiary">会议结束后可生成纪要</p>
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
          <p className="text-[12px] font-medium">纪要生成失败</p>
          <p className="text-[11px] opacity-85">{minutes.error || '未知错误'}</p>
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      {minutes.is_draft && (
        <div
          className="flex items-center gap-2 rounded-lg px-3 py-2 text-[11px]"
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
          <p className="mb-1.5 text-[11px] font-medium text-tertiary">整体摘要</p>
          <p className="text-[13px] leading-relaxed text-secondary">{minutes.summary}</p>
        </section>
      )}

      {minutes.topics.length > 0 && (
        <section>
          <p className="mb-2 flex items-center gap-1.5 text-[11px] font-medium text-tertiary">
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
                <p className="text-[12px] font-medium" style={{ color: 'var(--accent-text)' }}>
                  {topic.title}
                </p>
                {topic.detail && (
                  <p className="mt-1 text-[12px] leading-relaxed" style={{ color: 'var(--accent-text)', opacity: 0.85 }}>
                    {topic.detail}
                  </p>
                )}
                {topic.conclusion && (
                  <p
                    className="mt-1.5 border-l-2 pl-2 text-[12px] leading-relaxed"
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
          <p className="mb-2 flex items-center gap-1.5 text-[11px] font-medium text-tertiary">
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
                  <p className="text-[12px]" style={{ color: 'var(--warn-text)' }}>
                    {todo.task}
                  </p>
                  {(todo.owner || todo.due) && (
                    <p className="mt-0.5 text-[11px] opacity-80" style={{ color: 'var(--warn-text)' }}>
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
      <p className="text-[12px] text-secondary">正在分析会议内容…</p>
      <p className="text-[11px] text-tertiary">长会议会分段处理，请稍候</p>
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
      <p className="text-[12px] text-secondary">
        {hasContent ? '已转写 ' : '会议纪要'}
      </p>
      {hasContent && (
        <p className="text-[11px] text-tertiary">
          已转写 {finalCount} 段，点击下方按钮生成结构化纪要
        </p>
      )}
      {!hasContent && (
        <p className="text-[11px] text-tertiary">
          会议结束后将自动提取议题、结论与待办事项
        </p>
      )}
    </div>
  )
}
