import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import {
  ArrowLeft,
  ArrowRight,
  BookOpen,
  Loader2,
  Search,
  Tag,
} from 'lucide-react'
import { knowledgeApi } from '../api/client'
import { formatDuration, formatTimestamp } from '../store/session'
import { TopBar } from '../components/TopBar'
import { TopicMap } from '../components/TopicMap'

export default function Knowledge() {
  const [query, setQuery] = useState('')
  const [submitted, setSubmitted] = useState('')
  const [tag, setTag] = useState('')

  const topics = useQuery({
    queryKey: ['knowledge-topics'],
    queryFn: () => knowledgeApi.topics(20),
  })

  const results = useQuery({
    queryKey: ['knowledge-search', submitted, tag],
    queryFn: () => knowledgeApi.search(submitted, tag, 50),
  })

  const submit = () => setSubmitted(query.trim())
  const matchByMeeting = new Map(
    (results.data?.matches ?? []).map((item) => [item.meeting_id, item]),
  )

  return (
    <div className="flex h-full flex-col">
      <TopBar />
      <main className="min-h-0 flex-1 overflow-y-auto p-4">
        <div className="mx-auto max-w-[980px]">
          <Link
            to="/"
            className="mb-4 inline-flex items-center gap-1 text-[12px] no-underline text-tertiary hover:text-secondary"
          >
            <ArrowLeft size={13} /> 返回会议
          </Link>

          <div className="mb-4 flex items-end justify-between gap-3">
            <div>
              <h1 className="text-[16px] font-medium">会议知识库</h1>
              <p className="mt-1 text-[12px] text-tertiary">
                跨会议检索主题、结论与转写，点击主题可追溯相关会议。
              </p>
            </div>
            <span className="chip" style={{ background: 'var(--accent-soft)', color: 'var(--accent-text)' }}>
              <BookOpen size={12} /> 共 {results.data?.total ?? 0} 场
            </span>
          </div>

          <section className="panel mb-3 overflow-hidden">
            <div className="panel-header">
              <h2>主题关系图</h2>
              <span className="ml-auto text-[11px] text-tertiary">气泡大小 = 出现频次</span>
            </div>
            <div className="p-2">
              {topics.isLoading ? (
                <div className="flex h-[220px] items-center justify-center">
                  <Loader2 size={17} className="animate-spin" />
                </div>
              ) : topics.data && topics.data.length > 0 ? (
                <TopicMap
                  topics={topics.data}
                  onSelect={(topic) => {
                    setQuery(topic)
                    setSubmitted(topic)
                  }}
                />
              ) : (
                <p className="py-16 text-center text-[12px] text-tertiary">
                  还没有可用于跨会议分析的主题
                </p>
              )}
            </div>
          </section>

          <div className="mb-3 flex flex-wrap gap-2">
            <div className="relative min-w-[220px] flex-1">
              <Search
                size={13}
                className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-tertiary"
              />
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') submit()
                }}
                placeholder="搜索主题、结论或原话…"
                className="w-full rounded-lg py-2 pl-8 pr-3 text-[12px] outline-none"
                style={{
                  background: 'var(--bg-surface)',
                  border: '1px solid var(--border-subtle)',
                  color: 'var(--text-primary)',
                }}
              />
            </div>
            <div className="relative">
              <Tag
                size={12}
                className="pointer-events-none absolute left-2 top-1/2 -translate-y-1/2 text-tertiary"
              />
              <input
                value={tag}
                onChange={(e) => setTag(e.target.value)}
                placeholder="标签"
                aria-label="标签筛选"
                className="w-[120px] rounded-lg py-2 pl-7 pr-2 text-[12px] outline-none"
                style={{
                  background: 'var(--bg-surface)',
                  border: '1px solid var(--border-subtle)',
                  color: 'var(--text-primary)',
                }}
              />
            </div>
            <button type="button" onClick={submit} className="btn btn-primary text-[12px]">
              检索
            </button>
            {(submitted || tag) && (
              <button
                type="button"
                onClick={() => {
                  setQuery('')
                  setSubmitted('')
                  setTag('')
                }}
                className="btn text-[12px]"
              >
                清除
              </button>
            )}
          </div>

          {results.isLoading && (
            <div className="flex justify-center py-16">
              <Loader2 size={18} className="animate-spin" />
            </div>
          )}
          {results.error && (
            <div
              className="rounded-lg px-4 py-3 text-[12px]"
              style={{ background: 'var(--danger-soft)', color: 'var(--danger-text)' }}
            >
              {results.error instanceof Error ? results.error.message : '检索失败'}
            </div>
          )}
          {!results.isLoading && !results.error && (results.data?.items.length ?? 0) === 0 && (
            <div className="panel py-16 text-center text-[12px] text-tertiary">
              没有匹配的会议
            </div>
          )}
          <div className="space-y-2">
            {results.data?.items.map((meeting) => {
              const match = matchByMeeting.get(meeting.id)
              return (
              <Link
                key={meeting.id}
                to={`/history/${meeting.id}${match ? `?t=${match.start_ms / 1000}` : ''}`}
                className="panel flex items-center gap-3 px-4 py-3 no-underline transition-colors hover:bg-[var(--bg-hover)]"
              >
                <span
                  className="flex size-8 shrink-0 items-center justify-center rounded-lg"
                  style={{ background: 'var(--accent-soft)', color: 'var(--accent-text)' }}
                >
                  <BookOpen size={14} />
                </span>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-[13px] font-medium">{meeting.title}</p>
                  <p className="mt-0.5 flex flex-wrap gap-2 text-[11px] text-tertiary">
                    <span>{new Date(meeting.created_at).toLocaleDateString('zh-CN')}</span>
                    {meeting.duration_ms > 0 && <span>{formatDuration(meeting.duration_ms)}</span>}
                    {meeting.participants && <span>参会：{meeting.participants}</span>}
                    {meeting.tags &&
                      meeting.tags
                        .split(',')
                        .filter(Boolean)
                        .slice(0, 3)
                        .map((item) => (
                          <span key={item} className="chip">
                            {item}
                          </span>
                        ))}
                  </p>
                  {match && (
                    <p
                      className="mt-1 truncate rounded px-1.5 py-0.5 text-[10px]"
                      style={{
                        background: 'var(--bg-subtle)',
                        color: 'var(--text-secondary)',
                      }}
                    >
                      {formatTimestamp(match.start_ms)} · {match.text}
                    </p>
                  )}
                </div>
                <ArrowRight size={14} className="text-tertiary" />
              </Link>
              )
            })}
          </div>
        </div>
      </main>
    </div>
  )
}
