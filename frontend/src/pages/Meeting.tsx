import { Suspense, lazy, useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { TopBar } from '../components/TopBar'
import { TranscriptStream } from '../components/TranscriptStream'
import { ParticipantsPanel } from '../components/ParticipantsPanel'
import { MinutesPanel } from '../components/MinutesPanel'
import { ControlBar } from '../components/ControlBar'
import { IdleHero } from '../components/IdleHero'
import { FontSizeControl } from '../components/FontSizeControl'
import { PaneSplitter } from '../components/PaneSplitter'
import { useLayoutMode } from '../hooks/useLayoutMode'
import { useSession } from '../store/session'
import { useSettings } from '../store/settings'
import { useSpeakerRoles } from '../hooks/useSpeakerRoles'
import { useKeyboardShortcuts } from '../hooks/useKeyboardShortcuts'
import { checkBrowserSupport } from '../audio/recorder'
import { FileText, Info, Loader2, Network, NotebookPen, PencilLine, Users } from 'lucide-react'
import { meetingApi, getToken } from '../api/client'
import type { Meeting as MeetingRecord, Minutes } from '../api/types'
import { PANE_WIDTH_DEFAULT, clampPaneWidth } from '../lib/readerPrefs'
import { STATE_CONFIRMED, STATE_DELETED } from '../lib/speakerRoles'

// 弹窗只在用户点击时打开：懒加载，不占首屏 JS
const MeetingInfoEditor = lazy(() =>
  import('../components/MeetingInfoEditor').then((m) => ({
    default: m.MeetingInfoEditor,
  })),
)
const MindMapDialog = lazy(() =>
  import('../components/MindMapDialog').then((m) => ({
    default: m.MindMapDialog,
  })),
)

/** 会议页的三块面板：底部标签栏与左右滑动共用这个顺序 */
type PaneTab = 'transcript' | 'minutes' | 'participants'

const PANE_ORDER: PaneTab[] = ['transcript', 'minutes', 'participants']

export default function Meeting() {
  const status = useSession((s) => s.status)
  const error = useSession((s) => s.error)
  const notice = useSession((s) => s.notice)
  const segments = useSession((s) => s.segments)
  const meetingId = useSession((s) => s.meetingId)
  const renameSpeaker = useSession((s) => s.renameSpeaker)
  const updateSegmentText = useSession((s) => s.updateSegmentText)
  const liveMinutes = useSession((s) => s.liveMinutes)
  const meetingInfo = useSession((s) => s.meetingInfo)
  const metadataCaptureMinutes = useSession((s) => s.metadataCaptureMinutes)
  const speakerLowConfidenceThreshold = useSession(
    (s) => s.speakerLowConfidenceThreshold,
  )
  const elapsedMs = useSession((s) => s.elapsedMs)
  const setTitle = useSession((s) => s.setTitle)
  const setMeetingInfo = useSession((s) => s.setMeetingInfo)

  const transcriptScale = useSettings((s) => s.transcriptScale)
  const setTranscriptScale = useSettings((s) => s.setTranscriptScale)
  const minutesScale = useSettings((s) => s.minutesScale)
  const setMinutesScale = useSettings((s) => s.setMinutesScale)
  const minutesPaneWidth = useSettings((s) => s.minutesPaneWidth)
  const setMinutesPaneWidth = useSettings((s) => s.setMinutesPaneWidth)

  const mainRef = useRef<HTMLElement | null>(null)
  const {
    speakers,
    importanceByKey,
    setImportance,
    setState: setSpeakerState,
    error: speakerRoleError,
  } = useSpeakerRoles(meetingId, { segments })

  const [minutes, setMinutes] = useState<Minutes | null>(null)
  const [generating, setGenerating] = useState(false)
  const [templateId, setTemplateId] = useState<number | null>(null)
  const [supportWarning, setSupportWarning] = useState<string | null>(null)
  const [activeTab, setActiveTab] = useState<PaneTab>('transcript')
  const [infoOpen, setInfoOpen] = useState(false)
  const [mindMapOpen, setMindMapOpen] = useState(false)

  // 布局档：compact（<=767）/ medium（768–1023）/ wide（>=1024）。
  // 与 CSS 断点同源，避免「CSS 已切、JS 还没切」的半拍。
  const layout = useLayoutMode()
  const finalSegmentCount = segments.filter((s) => s.isFinal).length
  const paneTabs: { id: PaneTab; label: string; icon: React.ReactNode; badge?: number }[] = [
    { id: 'transcript', label: '转写', icon: <FileText size={16} />, badge: finalSegmentCount },
    { id: 'minutes', label: '纪要', icon: <NotebookPen size={16} /> },
    { id: 'participants', label: '参会人', icon: <Users size={16} /> },
  ]

  // 左右滑动切页（仅 compact）。三条护栏：文本选中中不切、输入框内不切、
  // 横向可滚容器内不切；阈值 40px 且横向位移必须大于纵向，避免纵向滚动误判。
  const touchStart = useRef<{ x: number; y: number; t: number } | null>(null)

  const handleTouchStart = (event: React.TouchEvent) => {
    if (layout !== 'compact') return
    const selection = window.getSelection()
    if (selection && !selection.isCollapsed) return
    const target = event.target as HTMLElement | null
    if (!target || target.closest('input, textarea, select, [contenteditable="true"]')) return
    let node: HTMLElement | null = target
    while (node && node !== mainRef.current) {
      const style = window.getComputedStyle(node)
      if (
        (style.overflowX === 'auto' || style.overflowX === 'scroll') &&
        node.scrollWidth > node.clientWidth + 1
      ) {
        return
      }
      node = node.parentElement
    }
    const touch = event.touches[0]
    touchStart.current = { x: touch.clientX, y: touch.clientY, t: Date.now() }
  }

  const handleTouchEnd = (event: React.TouchEvent) => {
    const start = touchStart.current
    touchStart.current = null
    if (!start || layout !== 'compact') return
    const touch = event.changedTouches[0]
    const dx = touch.clientX - start.x
    const dy = touch.clientY - start.y
    if (Math.abs(dx) < 40 || Math.abs(dx) <= Math.abs(dy)) return
    // 慢速拖动（>600ms 且不足 80px）更像「挑着看」，不当成切页手势
    if (Date.now() - start.t > 600 && Math.abs(dx) < 80) return
    const index = PANE_ORDER.indexOf(activeTab)
    const next = dx < 0 ? Math.min(index + 1, PANE_ORDER.length - 1) : Math.max(index - 1, 0)
    if (next !== index) setActiveTab(PANE_ORDER[next])
  }

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

  // 窗口变窄时把已保存的纪要宽度夹回可用范围，避免把转写挤没
  useEffect(() => {
    const sync = () => {
      const el = mainRef.current
      if (!el) return
      const current = useSettings.getState().minutesPaneWidth
      const clamped = clampPaneWidth(current, el.getBoundingClientRect().width)
      if (clamped !== current) {
        useSettings.getState().setMinutesPaneWidth(clamped)
      }
    }
    sync()
    window.addEventListener('resize', sync)
    return () => window.removeEventListener('resize', sync)
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

  const showHero = (status === 'idle' || status === 'error') && !meetingId && segments.length === 0

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
              className="shrink-0 rounded border border-current/20 px-2 py-0.5 text-[11px] max-md:min-h-11 max-md:px-3 max-md:text-[12px]"
            >
              <span className="inline-flex items-center gap-1">
                <PencilLine size={11} /> 会议信息
              </span>
            </button>
          )}
        </div>
      )}

      {/* 参会人条（>=768）：会中随时可见的人数与姓名 chips。
          compact 档不再占首屏高度，改收进底部「参会人」标签。 */}
      {layout !== 'compact' && meetingId !== null && !showHero && (
        <div className="px-3 pt-3">
          <ParticipantsPanel meetingId={meetingId} pollMs={30_000} compact />
        </div>
      )}

      {showHero ? (
        <IdleHero />
      ) : (
        <>
          {/* 主体：转写流 + 纪要面板。
              - wide（>=1024）：并排，宽度可拖动
              - medium（768–1023）：并排 60/40，无分隔条
              - compact（<=767）：单栏 + 底部标签切换 + 左右滑动 */}
          <main
            ref={mainRef}
            onTouchStart={handleTouchStart}
            onTouchEnd={handleTouchEnd}
            className="flex min-h-0 flex-1 flex-col gap-2 p-2 md:flex-row md:gap-3 md:p-3"
          >
            {/* 注意必须带 flex：只有 flex-col 而没有 display:flex 时，
                这一层仍是 block，子级 TranscriptStream 的 flex-1 / min-h-0
                全部失效 → 高度约束链断裂 → 内层 overflow-y-auto 永不溢出，
                既没有滚动条也滚不动，超出的转写会被 overflow-hidden 直接裁掉。 */}
            <section
              data-pane="transcript"
              data-pane-active={activeTab === 'transcript'}
              className="panel reader-scope flex min-h-0 min-w-0 flex-col overflow-hidden"
              style={{
                flex: layout === 'medium' ? '2 1 0%' : '1 1 0%',
                '--reader-scale': String(transcriptScale),
              } as React.CSSProperties}
            >
              <div className="panel-header">
                <h2>实时转写</h2>
                <span className="count-badge">
                  {segments.filter((s) => s.isFinal).length}
                </span>
                <div className="ml-auto">
                  <FontSizeControl
                    value={transcriptScale}
                    onChange={setTranscriptScale}
                    label="实时转写"
                  />
                </div>
              </div>
              <TranscriptStream
                segments={segments}
                onSeek={handleSeek}
                onRenameSpeaker={(from, to) => void renameSpeaker(from, to)}
                lowConfidenceThreshold={speakerLowConfidenceThreshold}
                importanceByKey={importanceByKey}
                onEditText={(seg, text) =>
                  void updateSegmentText(seg.segmentId, text)
                }
              />
            </section>

            {/* 拖动分隔条：只在 wide 档挂载。medium 双栏按 60/40 固定，
                手机上不出现「拖不动也看不见」的伪手柄。 */}
            {layout === 'wide' && (
              <PaneSplitter
                containerRef={mainRef}
                width={minutesPaneWidth}
                onChange={setMinutesPaneWidth}
                onReset={() => setMinutesPaneWidth(PANE_WIDTH_DEFAULT)}
              />
            )}

            {/* 纪要面板：全端单一实例（原先 compact/desktop 各渲染一份，
                两份 MinutesPanel 同时存在会重复订阅与重复挂载）。
                位置与宽度交给布局档：wide 固定像素宽度，medium 占 1/3，
                compact 独占一屏由标签切换。 */}
            <aside
              data-pane="minutes"
              data-pane-active={activeTab === 'minutes'}
              className="panel reader-scope flex min-h-0 min-w-0 flex-col overflow-hidden"
              style={{
                flex: layout === 'wide' ? '0 0 auto' : '1 1 0%',
                width: layout === 'wide' ? minutesPaneWidth : undefined,
                maxWidth: layout === 'wide' ? '60%' : undefined,
                minWidth: layout === 'medium' ? 280 : undefined,
                '--reader-scale': String(minutesScale),
              } as React.CSSProperties}
            >
              <div className="panel-header">
                <h2>会议纪要</h2>
                <div className="ml-auto">
                  <FontSizeControl
                    value={minutesScale}
                    onChange={setMinutesScale}
                    label="会议纪要"
                  />
                </div>
                <button
                  type="button"
                  onClick={() => setMindMapOpen(true)}
                  disabled={meetingId === null}
                  className="btn btn-ghost px-1.5 text-[11px]"
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
                speakers={speakers}
                onImportanceChange={(row, value) => void setImportance(row, value)}
                onSpeakerConfirm={(row) => void setSpeakerState(row, STATE_CONFIRMED)}
                onSpeakerDelete={(row) => void setSpeakerState(row, STATE_DELETED)}
                importanceError={speakerRoleError}
              />
            </aside>

            {/* 参会人（仅 compact）：与 >=768 的 chips 条互斥挂载，
                保证任何时刻只有一份 ParticipantsPanel 在轮询。 */}
            {layout === 'compact' && meetingId !== null && (
              <section
                data-pane="participants"
                data-pane-active={activeTab === 'participants'}
                className="flex min-h-0 min-w-0 flex-1 flex-col overflow-y-auto"
              >
                <ParticipantsPanel meetingId={meetingId} pollMs={30_000} compact />
              </section>
            )}
          </main>
        </>
      )}

      {/* 底部标签栏（仅 compact）：替代原先贴在主面板顶部的两标签，
          拇指区可达；每项 min-h 44 满足触控下限。 */}
      {!showHero && (
        <nav
          className="flex shrink-0 items-stretch gap-1 px-2 pt-1.5 pb-1 md:hidden"
          style={{
            borderTop: '1px solid var(--border-subtle)',
            background: 'var(--bg-surface)',
          }}
          aria-label="面板切换"
        >
          {paneTabs.map((tab) => (
            <button
              key={tab.id}
              type="button"
              onClick={() => setActiveTab(tab.id)}
              aria-current={activeTab === tab.id ? 'page' : undefined}
              className="flex min-h-11 flex-1 items-center justify-center gap-1.5 rounded-lg text-[13px] font-medium transition-colors"
              style={{
                background: activeTab === tab.id ? 'var(--accent-soft)' : 'transparent',
                color: activeTab === tab.id ? 'var(--accent-text)' : 'var(--text-tertiary)',
              }}
            >
              {tab.icon}
              {tab.label}
              {tab.badge ? (
                <span className="text-[11px] tabular-nums opacity-70">{tab.badge}</span>
              ) : null}
            </button>
          ))}
        </nav>
      )}

      {/* 底部控制栏 */}
      <footer
        className="app-footer flex h-[68px] shrink-0 items-center justify-center px-4"
        style={{ borderTop: '1px solid var(--border-subtle)', background: 'var(--bg-surface)' }}
      >
        <ControlBar />
      </footer>

      <Suspense fallback={null}>
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
      </Suspense>
    </div>
  )
}
