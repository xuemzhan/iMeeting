import { useState } from 'react'
import { Loader2, Plus, Trash2, X } from 'lucide-react'
import { meetingApi } from '../api/client'
import type { Minutes, TodoItem, TopicItem } from '../api/types'

interface Props {
  meetingId: number
  minutes: Minutes
  onSaved: (minutes: Minutes) => void
  onClose: () => void
}

const inputStyle: React.CSSProperties = {
  width: '100%',
  background: 'var(--bg-surface)',
  border: '1px solid var(--border-subtle)',
  color: 'var(--text-primary)',
}

/**
 * 纪要人工修订。
 *
 * 每次保存都会在服务端生成新版本，而不是覆盖自动生成结果。这样企业用户
 * 既能修正口误/专有名词，又能追溯“模型写了什么、人改了什么”。
 */
export function MinutesEditor({ meetingId, minutes, onSaved, onClose }: Props) {
  const [summary, setSummary] = useState(minutes.summary)
  const [topics, setTopics] = useState<TopicItem[]>(
    minutes.topics.map((topic) => ({
      title: topic.title || '',
      detail: topic.detail || '',
      conclusion: topic.conclusion || '',
    })),
  )
  const [todos, setTodos] = useState<TodoItem[]>(
    minutes.todos.map((todo) => ({
      task: todo.task || '',
      owner: todo.owner || '',
      due: todo.due || '',
    })),
  )
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const patchTopic = (index: number, patch: Partial<TopicItem>) => {
    setTopics((items) =>
      items.map((item, i) => (i === index ? { ...item, ...patch } : item)),
    )
  }

  const patchTodo = (index: number, patch: Partial<TodoItem>) => {
    setTodos((items) =>
      items.map((item, i) => (i === index ? { ...item, ...patch } : item)),
    )
  }

  const save = async () => {
    setBusy(true)
    setError(null)
    try {
      const result = await meetingApi.editMinutes(meetingId, minutes.version, {
        summary,
        topics: topics.filter((topic) => topic.title.trim()),
        todos: todos.filter((todo) => todo.task.trim()),
      })
      onSaved(result)
      onClose()
    } catch (e) {
      setError(e instanceof Error ? e.message : '保存纪要失败')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-3">
      <div className="panel flex max-h-[90vh] w-full max-w-[760px] flex-col overflow-hidden">
        <div className="panel-header">
          <h2>修订纪要</h2>
          <span className="text-[11px] text-tertiary">
            基于第 {minutes.version} 版，保存后生成新版本
          </span>
          <button
            type="button"
            onClick={onClose}
            aria-label="关闭"
            className="btn btn-ghost ml-auto p-1"
          >
            <X size={15} />
          </button>
        </div>

        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-4">
          <section>
            <label className="mb-1 block text-[11px] font-medium text-tertiary">
              整体摘要
            </label>
            <textarea
              value={summary}
              onChange={(e) => setSummary(e.target.value)}
              rows={5}
              className="rounded px-2.5 py-2 text-[12px] leading-relaxed outline-none"
              style={inputStyle}
            />
          </section>

          <section>
            <div className="mb-2 flex items-center justify-between">
              <p className="text-[11px] font-medium text-tertiary">议题与结论</p>
              <button
                type="button"
                onClick={() =>
                  setTopics((items) => [
                    ...items,
                    { title: '', detail: '', conclusion: '' },
                  ])
                }
                className="btn btn-ghost text-[11px]"
              >
                <Plus size={12} /> 添加议题
              </button>
            </div>
            <div className="space-y-2">
              {topics.map((topic, index) => (
                <div
                  key={index}
                  className="rounded-lg p-2.5"
                  style={{ background: 'var(--bg-subtle)' }}
                >
                  <div className="mb-1.5 flex gap-1.5">
                    <input
                      value={topic.title}
                      onChange={(e) => patchTopic(index, { title: e.target.value })}
                      placeholder="议题名称"
                      className="min-w-0 flex-1 rounded px-2 py-1.5 text-[12px] outline-none"
                      style={inputStyle}
                    />
                    <button
                      type="button"
                      onClick={() =>
                        setTopics((items) => items.filter((_, i) => i !== index))
                      }
                      className="btn btn-ghost p-1"
                      title="删除议题"
                    >
                      <Trash2 size={12} />
                    </button>
                  </div>
                  <textarea
                    value={topic.detail}
                    onChange={(e) => patchTopic(index, { detail: e.target.value })}
                    placeholder="讨论要点"
                    rows={2}
                    className="mb-1.5 rounded px-2 py-1.5 text-[12px] outline-none"
                    style={inputStyle}
                  />
                  <input
                    value={topic.conclusion}
                    onChange={(e) =>
                      patchTopic(index, { conclusion: e.target.value })
                    }
                    placeholder="结论 / 决策"
                    className="rounded px-2 py-1.5 text-[12px] outline-none"
                    style={inputStyle}
                  />
                </div>
              ))}
            </div>
          </section>

          <section>
            <div className="mb-2 flex items-center justify-between">
              <p className="text-[11px] font-medium text-tertiary">待办事项</p>
              <button
                type="button"
                onClick={() =>
                  setTodos((items) => [
                    ...items,
                    { task: '', owner: '', due: '' },
                  ])
                }
                className="btn btn-ghost text-[11px]"
              >
                <Plus size={12} /> 添加待办
              </button>
            </div>
            <div className="space-y-2">
              {todos.map((todo, index) => (
                <div
                  key={index}
                  className="flex flex-wrap items-center gap-1.5 rounded-lg p-2"
                  style={{ background: 'var(--bg-subtle)' }}
                >
                  <input
                    value={todo.task}
                    onChange={(e) => patchTodo(index, { task: e.target.value })}
                    placeholder="事项"
                    className="min-w-[180px] flex-1 rounded px-2 py-1.5 text-[12px] outline-none"
                    style={inputStyle}
                  />
                  <input
                    value={todo.owner}
                    onChange={(e) => patchTodo(index, { owner: e.target.value })}
                    placeholder="责任人"
                    className="w-[110px] rounded px-2 py-1.5 text-[12px] outline-none"
                    style={inputStyle}
                  />
                  <input
                    value={todo.due}
                    onChange={(e) => patchTodo(index, { due: e.target.value })}
                    placeholder="截止时间"
                    className="w-[120px] rounded px-2 py-1.5 text-[12px] outline-none"
                    style={inputStyle}
                  />
                  <button
                    type="button"
                    onClick={() =>
                      setTodos((items) => items.filter((_, i) => i !== index))
                    }
                    className="btn btn-ghost p-1"
                    title="删除待办"
                  >
                    <Trash2 size={12} />
                  </button>
                </div>
              ))}
            </div>
          </section>
        </div>

        <div
          className="flex shrink-0 items-center justify-end gap-2 px-4 py-3"
          style={{ borderTop: '1px solid var(--border-subtle)' }}
        >
          {error && (
            <p className="mr-auto text-[11px]" style={{ color: 'var(--danger-text)' }}>
              {error}
            </p>
          )}
          <button type="button" onClick={onClose} className="btn text-[12px]">
            取消
          </button>
          <button
            type="button"
            onClick={() => void save()}
            disabled={busy}
            className="btn btn-primary text-[12px]"
          >
            {busy && <Loader2 size={12} className="animate-spin" />}
            保存为新版本
          </button>
        </div>
      </div>
    </div>
  )
}
