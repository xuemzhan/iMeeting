import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { TopBar } from '../components/TopBar'
import { TranscriptStream } from '../components/TranscriptStream'
import { MinutesPanel } from '../components/MinutesPanel'
import { ControlBar } from '../components/ControlBar'
import { IdleHero } from '../components/IdleHero'
import { useSession } from '../store/session'
import { useKeyboardShortcuts } from '../hooks/useKeyboardShortcuts'
import { checkBrowserSupport } from '../audio/recorder'
import { Info, Loader2, Network, PencilLine } from 'lucide-react'
import { meetingApi, getToken } from '../api/client'
import type { Meeting as MeetingRecord, Minutes } from '../api/types'
import { MeetingInfoEditor } from '../components/MeetingInfoEditor'
import { MindMapDialog } from '../components/MindMapDialog'

export default function Meeting() {
  const status = useSession((s) => s.status)
  const error = useSession((s) => s.error)
  const notice = useSession((s) => s.notice)
  const segments = useSession((s) => s.segments)
  const meetingId = useSession((s) => s.meetingId)
  const renameSpeaker = useSession((s) => s.renameSpeaker)
  const liveMinutes = useSession((s) => s.liveMinutes)
  const meetingInfo = useSession((s) => s.meetingInfo)
  const metadataCaptureMinutes = useSession((s) => s.metadataCaptureMinutes)
  const speakerLowConfidenceThreshold = useSession(
    (s) => s.speakerLowConfidenceThreshold,
  )
  const elapsedMs = useSession((s) => s.elapsedMs)
  const setTitle = useSession((s) => s.setTitle)
  const setMeetingInfo = useSession((s) => s.setMeetingInfo)

  const [minutes, setMinutes] = useState<Minutes | null>(null)
  const [generating, setGenerating] = useState(false)
  const [templateId, setTemplateId] = useState<number | null>(null)
  const [supportWarning, setSupportWarning] = useState<string | null>(null)
  const [activeTab, setActiveTab] = useState<'transcript' | 'minutes'>('transcript')
  const [infoOpen, setInfoOpen] = useState(false)
  const [mindMapOpen, setMindMapOpen] = useState(false)

  const { data: meetingRecord } = useQuery({
    queryKey: ['meeting-info', meetingId],
    queryFn: () => meetingApi.get(meetingId as number),
    enabled: infoOpen && meetingId !== null,
  })

  // 键盘快捷键（Space 暂停、F 切句、Esc 结束）
  useKeyboardShortcuts()

  // 浏览器能力检查
  useEffect(() => {
    const support = checkBrowserSupport()
    if (!support.ok) setSupportWarning(support.reason ?? null)
  }, [])

  // 纪要拉取：会中每 30 秒刷新一次自动草稿，结束后再拉一次终稿。
  // WebSocket 的 minutes_update 会即时更新，轮询只是断线/重开页面时的兜底。
  useEffect(() => {
    if (!meetingId || !getToken()) return
    let cancelled = false
    const load = () => {
      meetingApi
        .getMinutes(meetingId)
        .then((m) => {
          if (!cancelled && m) setMinutes(m)
        })
        .catch(() => {})
    }
    load()
    const timer =
      status === 'recording' ? window.setInterval(load, 30_000) : null
    return () => {
      cancelled = true
      if (timer !== null) window.clearInterval(timer)
    }
  }, [status, meetingId])

  const hasContent = segments.some((s) => s.isFinal) && meetingId !== null
  const activeMinutes = minutes ?? liveMinutes
  const captureWindowOpen =
    (status === 'recording' || status === 'paused') &&
    metadataCaptureMinutes > 0 &&
    elapsedMs < metadataCaptureMinutes * 60_000

  const handleInfoSaved = (updated: MeetingRecord) => {
    setTitle(updated.title)
    setMeetingInfo({
      participants: updated.participants,
      agenda: updated.agenda,
      meetingType: updated.meeting_type,
      description: updated.description,
    })
  }

  const handleGenerate = async () => {
    if (!meetingId || generating) return
    setGenerating(true)
    try {
      const result = await meetingApi.generateMinutes(
        meetingId,
        Boolean(minutes && !minutes.is_draft),
        templateId,
      )
      setMinutes(result)
    } catch (err) {
      setMinutes({
        id: 0,
        meeting_id: meetingId,
        summary: '',
        topics: [],
        todos: [],
        raw_markdown: '',
        model_used: '',
        status: 'failed',
        error: err instanceof Error ? err.message : '生成失败',
        version: 0,
        is_draft: false,
        created_at: new Date().toISOString(),
      })
    } finally {
      setGenerating(false)
    }
  }

  const handleSeek = (ms: number) => {
    // 音频回放功能待接入（需保存音频文件），此处先占位
    void ms
  }

  const isIdle = status === 'idle' || status === 'error'
  const showHero = isIdle && !meetingId && segments.length === 0

  return (
    <div className="flex h-full flex-col">
      <TopBar />

      {supportWarning && (
        <div
          className="shrink-0 px-4 py-2 text-center text-[12px]"
          style={{ background: 'var(--warn-soft)', color: 'var(--warn-text)' }}
        >
          {supportWarning}
        </div>
      )}

      {error && (
        <div
          className="shrink-0 px-4 py-2 text-center text-[12px]"
          style={{ background: 'var(--danger-soft)', color: 'var(--danger-text)' }}
        >
          {error}
        </div>
      )}

      {/* 非阻塞提示：采集参数正在自动降级时用，不打断会议 */}
      {!error && notice && (
        <div
          className="flex shrink-0 items-center justify-center gap-2 px-4 py-2 text-center text-[12px]"
          style={{ background: 'var(--warn-soft)', color: 'var(--warn-text)' }}
        >
          {notice.includes('自动尝试') || notice.includes('重连') ? (
            <Loader2 size={13} className="animate-spin" />
          ) : (
            <Info size={13} />
          )}
          {notice}
        </div>
      )}

      {(captureWindowOpen || meetingInfo) && (
        <div
          className="flex shrink-0 flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2 text-[12px]"
          style={{ background: 'var(--accent-soft)', color: 'var(--accent-text)' }}
        >
          <Info size={13} className="shrink-0" />
          {meetingInfo ? (
            <span className="min-w-0 flex-1 truncate">
              已识别会议信息
              {meetingInfo.participants && ` · 参会：${meetingInfo.participants}`}
              {meetingInfo.agenda && ` · 议程：${meetingInfo.agenda}`}
            </span>
          ) : (
            <span className="min-w-0 flex-1">
              开场 {metadataCaptureMinutes} 分钟内，可自然说出
              “本次会议主题是……，参会人有……，主要讨论……”，系统会自动填入会议信息。
            </span>
          )}
          {meetingId !== null && (
            <button
              type="button"
              onClick={() => setInfoOpen(true)}
              className="shrink-0 rounded border border-current/20 px-2 py-0.5 text-[11px]"
            >
              <span className="inline-flex items-center gap-1">
                <PencilLine size={11} /> 会议信息
              </span>
            </button>
          )}
        </div>
      )}

      {showHero ? (
        <>
          <IdleHero />
        </>
      ) : (
        <>
          {/* 主体：转写流 + 纪要面板 */}
          <main className="flex min-h-0 flex-1 flex-col gap-3 p-3 lg:flex-row">
            {/* 移动端标签栏 */}
            {!isIdle && (
              <div className="flex shrink-0 gap-1 lg:hidden">
                <button
                  type="button"
                  onClick={() => setActiveTab('transcript')}
                  className="flex-1 rounded-lg py-2 text-[12px] font-medium transition-colors"
                  style={{
                    background: activeTab === 'transcript' ? 'var(--accent-soft)' : 'transparent',
                    color: activeTab === 'transcript' ? 'var(--accent-text)' : 'var(--text-tertiary)',
                  }}
                >
                  转写
                </button>
                <button
                  type="button"
                  onClick={() => setActiveTab('minutes')}
                  className="flex-1 rounded-lg py-2 text-[12px] font-medium transition-colors"
                  style={{
                    background: activeTab === 'minutes' ? 'var(--accent-soft)' : 'transparent',
                    color: activeTab === 'minutes' ? 'var(--accent-text)' : 'var(--text-tertiary)',
                  }}
                >
                  纪要
                </button>
              </div>
            )}

            {/* 注意必须带 flex：只有 flex-col 而没有 display:flex 时，
                这一层仍是 block，子级 TranscriptStream 的 flex-1 / min-h-0
                全部失效 → 高度约束链断裂 → 内层 overflow-y-auto 永不溢出，
                既没有滚动条也滚不动，超出的转写会被 overflow-hidden 直接裁掉。 */}
            <section
              className="panel flex min-w-0 flex-1 flex-col overflow-hidden"
              data-hidden-mobile={activeTab !== 'transcript' && !isIdle ? 'true' : undefined}
            >
              <div className="panel-header justify-between">
                <h2>实时转写</h2>
                <span className="count-badge">
                  {segments.filter((s) => s.isFinal).length}
                </span>
              </div>
              <TranscriptStream
                segments={segments}
                onSeek={handleSeek}
                onRenameSpeaker={(from, to) => void renameSpeaker(from, to)}
                lowConfidenceThreshold={speakerLowConfidenceThreshold}
              />
            </section>

            {/* 桌面端纪要面板（lg 以上常驻右侧） */}
            <aside className="panel hidden w-[300px] shrink-0 flex-col overflow-hidden lg:flex">
              <div className="panel-header">
                <h2>会议纪要</h2>
                <button
                  type="button"
                  onClick={() => setMindMapOpen(true)}
                  disabled={meetingId === null}
                  className="btn btn-ghost ml-auto px-1.5 text-[11px]"
                  title="查看当前纪要脑图"
                >
                  <Network size={12} /> 脑图
                </button>
              </div>
              <MinutesPanel
                minutes={activeMinutes}
                generating={generating}
                onGenerate={() => void handleGenerate()}
                hasContent={hasContent}
                templateId={templateId}
                onTemplateChange={setTemplateId}
              />
            </aside>

            {/* 移动端纪要面板（lg 以下由标签切换到） */}
            {activeTab === 'minutes' && !isIdle && (
              <section className="panel flex min-w-0 flex-1 flex-col overflow-hidden lg:hidden">
                <div className="panel-header">
                  <h2>会议纪要</h2>
                  <button
                    type="button"
                    onClick={() => setMindMapOpen(true)}
                    disabled={meetingId === null}
                    className="btn btn-ghost ml-auto px-1.5 text-[11px]"
                    title="查看当前纪要脑图"
                  >
                    <Network size={12} /> 脑图
                  </button>
                </div>
                <MinutesPanel
                  minutes={activeMinutes}
                  generating={generating}
                  onGenerate={() => void handleGenerate()}
                  hasContent={hasContent}
                  templateId={templateId}
                  onTemplateChange={setTemplateId}
                />
              </section>
            )}
          </main>
        </>
      )}

      {/* 底部控制栏 */}
      <footer
        className="flex h-[68px] shrink-0 items-center justify-center px-4"
        style={{ borderTop: '1px solid var(--border-subtle)', background: 'var(--bg-surface)' }}
      >
        <ControlBar />
      </footer>

      {infoOpen && meetingRecord && (
        <MeetingInfoEditor
          meeting={meetingRecord}
          captureMinutes={metadataCaptureMinutes}
          onSaved={handleInfoSaved}
          onClose={() => setInfoOpen(false)}
        />
      )}
      {mindMapOpen && meetingId !== null && (
        <MindMapDialog
          meetingId={meetingId}
          onClose={() => setMindMapOpen(false)}
        />
      )}
    </div>
  )
}
