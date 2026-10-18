import { Link, useParams } from 'react-router-dom'
import {
  Download,
  Loader2,
  Lock,
  Printer,
} from 'lucide-react'
import { formatDuration } from '../store/session'
import { BrandMark } from '../components/BrandMark'
import { SharedMeetingMinutes } from '../components/SharedMeetingMinutes'
import { SharedMeetingTranscript } from '../components/SharedMeetingTranscript'
import { useSharedMeeting } from '../hooks/useSharedMeeting'

export default function SharedMeeting() {
  const { token = '' } = useParams<{ token: string }>()
  const {
    password,
    setPassword,
    opening,
    authError,
    downloading,
    shared,
    paragraphs,
    visibleParagraphs,
    locked,
    expired,
    open,
    downloadAudio,
    loadMore,
  } = useSharedMeeting(token)

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
              <SharedMeetingMinutes minutes={shared.data.minutes} />
            )}

            <SharedMeetingTranscript
              paragraphs={paragraphs}
              visibleParagraphs={visibleParagraphs}
              segmentCount={shared.data.segments.length}
              onLoadMore={loadMore}
            />
          </>
        )}
      </main>
    </div>
  )
}
