import { CheckSquare } from 'lucide-react'

import type { Minutes } from '../api/types'
import { formatTimestamp } from '../store/session'

export function SharedMeetingMinutes({ minutes }: { minutes: Minutes }) {
  return (
    <section className="panel mb-3 overflow-hidden">
      <div className="panel-header">
        <h2>会议纪要</h2>
        <span className="ml-auto text-meta text-tertiary">
          v{minutes.version}
        </span>
      </div>
      <div className="space-y-4 p-4">
        {minutes.summary && (
          <p className="text-[13px] leading-relaxed text-secondary">
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
              className="text-ui font-medium"
              style={{ color: 'var(--accent-text)' }}
            >
              {topic.title}
            </p>
            {topic.detail && (
              <p className="mt-1 text-ui text-secondary">{topic.detail}</p>
            )}
            {topic.conclusion && (
              <p
                className="mt-1 text-ui"
                style={{ color: 'var(--accent-text)' }}
              >
                结论：{topic.conclusion}
              </p>
            )}
            {topic.evidence?.[0] && (
              <p className="mt-1 font-mono text-micro text-tertiary">
                原话 {formatTimestamp(topic.evidence[0].start_ms)}
              </p>
            )}
          </div>
        ))}
        {minutes.todos.length > 0 && (
          <div>
            <p className="mb-2 flex items-center gap-1 text-meta font-medium text-tertiary">
              <CheckSquare size={12} /> 待办
            </p>
            {minutes.todos.map((todo, index) => (
              <div
                key={index}
                className="mb-1 rounded-lg px-3 py-2 text-ui"
                style={{ background: 'var(--warn-soft)', color: 'var(--warn-text)' }}
              >
                {todo.task}
                {todo.owner && ` · ${todo.owner}`}
                {todo.due && ` · ${todo.due}`}
                {todo.evidence?.[0] &&
                  ` · 原话 ${formatTimestamp(todo.evidence[0].start_ms)}`}
              </div>
            ))}
          </div>
        )}
      </div>
    </section>
  )
}
