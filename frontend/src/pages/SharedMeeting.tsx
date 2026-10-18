import { useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import {
  CheckSquare,
  Download,
  FileText,
  Loader2,
  Lock,
  Printer,
} from 'lucide-react'
import { ApiError, shareApi } from '../api/client'
import { formatDuration, formatTimestamp, speakerLabel } from '../store/session'
import { groupParagraphs } from '../store/paragraphs'
import { BrandMark } from '../components/BrandMark'

export default function SharedMeeting() {
  const { token = '' } = useParams<{ token: string }>()
  const [password, setPassword] = useState('')
  const [accessToken, setAccessToken] = useState('')
  const [opening, setOpening] = useState(true)
  const [authError, setAuthError] = useState<string | null>(null)
  const [downloading, setDownloading] = useState(false)
  const [visibleParagraphCount, setVisibleParagraphCount] = useState(200)

  const shared = useQuery({
    queryKey: ['shared-meeting', token, accessToken],
    queryFn: () => shareApi.get(token, accessToken),
    enabled: Boolean(token && accessToken),
    retry: false,
  })
  const paragraphs = useMemo(
    () => groupParagraphs(shared.data?.segments ?? []),
    [shared.data?.segments],
  )
  const visibleParagraphs = paragraphs.slice(0, visibleParagraphCount)

  const open = async (candidate = password) => {
    setOpening(true)
    setAuthError(null)
    try {
      const result = await shareApi.open(token, candidate)
      setAccessToken(result.access_token)
    } catch (e) {
      setAccessToken('')
      setAuthError(e instanceof Error ? e.message : '无法验证分享访问权限')
    } finally {
      setOpening(false)
    }
  }

  useEffect(() => {
    if (!token) return
    void open('')
    // token 变化时重新验证；口令由用户显式提交。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token])

  const downloadAudio = async () => {
    if (!shared.data) return
    setDownloading(true)
    try {
      const blob = await shareApi.audio(token, accessToken)
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `${shared.data.meeting.title || 'meeting'}.wav`
      a.click()
      URL.revokeObjectURL(url)
    } finally {
      setDownloading(false)
    }
  }

  const locked =
    !accessToken &&
    (authError !== null ||
      (shared.error instanceof ApiError && shared.error.status === 401))
  const expired =
    shared.error instanceof ApiError && shared.error.status === 410

  return (
    <div className="min-h-full" style={{ background: 'var(--bg-page)' }}>
      <header
        className="flex h-14 items-center gap-2 px-4"
        style={{ borderBottom: '1px solid var(--border-subtle)', background: 'var(--bg-surface)' }}
      >
        <Link
          to="/"
          className="flex items-center gap-2 text-[13px] font-medium no-underline"
          title="返回会议首页"
        >
          <BrandMark size={24} />
          <span>iMeeting</span>
        </Link>
        <span className="ml-auto text-[11px] text-tertiary">只读分享</span>
      </header>

      <main className="mx-auto max-w-[920px] px-4 py-6">
      {(opening || shared.isLoading) && (
          <div className="flex justify-center py-24">
            <Loader2 size={20} className="animate-spin" />
          </div>
        )}

        {locked && (
          <div className="panel mx-auto max-w-[380px] p-5">
            <div className="mb-3 flex items-center gap-2">
              <Lock size={15} /> <h1 className="text-[14px] font-medium">输入访问口令</h1>
            </div>
            <p className="mb-3 text-[11px] text-tertiary">
              该会议分享链接受口令保护。
            </p>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') void open()
              }}
              placeholder="访问口令"
              className="mb-2 w-full rounded px-2.5 py-2 text-[12px] outline-none"
              style={{
                background: 'var(--bg-surface)',
                border: '1px solid var(--border-default)',
                color: 'var(--text-primary)',
              }}
            />
            <button
              type="button"
              onClick={() => void open()}
              className="btn btn-primary w-full text-[12px]"
            >
              打开会议
            </button>
            {authError && (
              <p className="mt-2 text-[11px]" style={{ color: 'var(--danger-text)' }}>
                {authError}
              </p>
            )}
          </div>
        )}

        {!shared.isLoading && expired && (
          <div className="panel mx-auto max-w-[420px] p-6 text-center">
            <p className="text-[13px] font-medium">分享链接已过期</p>
            <p className="mt-1 text-[11px] text-tertiary">请联系会议发起人重新生成链接。</p>
          </div>
        )}

        {!shared.isLoading && shared.error && !locked && !expired && (
          <div className="panel mx-auto max-w-[420px] p-6 text-center">
            <p className="text-[13px] font-medium">无法打开会议</p>
            <p className="mt-1 text-[11px] text-tertiary">
              {shared.error instanceof Error ? shared.error.message : '分享链接无效'}
            </p>
            <Link to="/" className="btn mt-3 no-underline text-[12px]">
              返回首页
            </Link>
          </div>
        )}

        {shared.data && (
          <>
            <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
              <div>
                <h1 className="text-[20px] font-semibold">{shared.data.meeting.title}</h1>
                <p className="mt-1 flex flex-wrap gap-2 text-[11px] text-tertiary">
                  <span>{new Date(shared.data.meeting.created_at).toLocaleString('zh-CN')}</span>
                  <span>·</span>
                  <span>{formatDuration(shared.data.meeting.duration_ms)}</span>
                  {shared.data.meeting.participants && (
                    <>
                      <span>·</span>
                      <span>参会：{shared.data.meeting.participants}</span>
                    </>
                  )}
                </p>
              </div>
              <div className="flex gap-2">
                <button type="button" onClick={() => window.print()} className="btn text-[12px]">
                  <Printer size={13} /> 打印
                </button>
                {shared.data.allow_download && (
                  <button
                    type="button"
                    onClick={() => void downloadAudio()}
                    disabled={downloading}
                    className="btn text-[12px]"
                  >
                    {downloading ? (
                      <Loader2 size={13} className="animate-spin" />
                    ) : (
                      <Download size={13} />
                    )}
                    下载音频
                  </button>
                )}
              </div>
            </div>

            {(shared.data.meeting.agenda || shared.data.meeting.description) && (
              <section className="panel mb-3 p-4">
                {shared.data.meeting.agenda && (
                  <p className="text-[12px]">
                    <span className="font-medium">议程：</span>
                    {shared.data.meeting.agenda}
                  </p>
                )}
                {shared.data.meeting.description && (
                  <p className="mt-1 text-[12px] text-secondary">
                    {shared.data.meeting.description}
                  </p>
                )}
              </section>
            )}

            {shared.data.minutes && (
              <section className="panel mb-3 overflow-hidden">
                <div className="panel-header">
                  <h2>会议纪要</h2>
                  <span className="ml-auto text-[11px] text-tertiary">
                    v{shared.data.minutes.version}
                  </span>
                </div>
                <div className="space-y-4 p-4">
                  {shared.data.minutes.summary && (
                    <p className="text-[13px] leading-relaxed text-secondary">
                      {shared.data.minutes.summary}
                    </p>
                  )}
                  {shared.data.minutes.topics.map((topic, index) => (
                    <div
                      key={index}
                      className="rounded-lg px-3 py-2"
                      style={{ background: 'var(--accent-soft)' }}
                    >
                      <p className="text-[12px] font-medium" style={{ color: 'var(--accent-text)' }}>
                        {topic.title}
                      </p>
                      {topic.detail && (
                        <p className="mt-1 text-[12px] text-secondary">{topic.detail}</p>
                      )}
                      {topic.conclusion && (
                        <p className="mt-1 text-[12px]" style={{ color: 'var(--accent-text)' }}>
                          结论：{topic.conclusion}
                        </p>
                      )}
                      {topic.evidence?.[0] && (
                        <p className="mt-1 font-mono text-[10px] text-tertiary">
                          原话 {formatTimestamp(topic.evidence[0].start_ms)}
                        </p>
                      )}
                    </div>
                  ))}
                  {shared.data.minutes.todos.length > 0 && (
                    <div>
                      <p className="mb-2 flex items-center gap-1 text-[11px] font-medium text-tertiary">
                        <CheckSquare size={12} /> 待办
                      </p>
                      {shared.data.minutes.todos.map((todo, index) => (
                        <div
                          key={index}
                          className="mb-1 rounded-lg px-3 py-2 text-[12px]"
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
            )}

            <section className="panel overflow-hidden">
              <div className="panel-header">
                <FileText size={14} /> <h2>完整转写</h2>
                <span className="ml-auto text-[11px] text-tertiary">
                  {shared.data.segments.length} 段
                </span>
              </div>
              <div className="space-y-3 p-4">
                {visibleParagraphs.map((paragraph) => (
                  <div
                    key={paragraph.key}
                    className="flex gap-3"
                    style={{
                      contentVisibility: 'auto',
                      containIntrinsicSize: '150px',
                    }}
                  >
                    <span className="w-12 shrink-0 pt-0.5 font-mono text-[11px] text-tertiary">
                      {formatTimestamp(paragraph.startMs)}
                    </span>
                    <div className="min-w-0 flex-1">
                      {paragraph.speaker && (
                        <p className="mb-0.5 text-[11px] font-medium" style={{ color: 'var(--accent-text)' }}>
                          {speakerLabel(paragraph.speaker)}
                        </p>
                      )}
                      <p className="whitespace-pre-wrap text-[13px] leading-relaxed">
                        {paragraph.text}
                      </p>
                    </div>
                  </div>
                ))}
                {paragraphs.length > visibleParagraphCount && (
                  <div className="flex justify-center">
                    <button
                      type="button"
                      className="btn text-[12px]"
                      onClick={() =>
                        setVisibleParagraphCount((value) => value + 200)
                      }
                    >
                      加载更多（剩余 {paragraphs.length - visibleParagraphCount} 段）
                    </button>
                  </div>
                )}
              </div>
            </section>
          </>
        )}
      </main>
    </div>
  )
}
