import { useEffect, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import {
  CheckSquare,
  Loader2,
} from 'lucide-react'

import { ApiError } from '../api/client'
import { useMeetingDetail } from '../hooks/useMeetingDetail'
import { useSpeakerRoles } from '../hooks/useSpeakerRoles'
import { ActionsPanel } from '../components/ActionsPanel'
import { AudioPlayer } from '../components/AudioPlayer'
import { CompliancePanel } from '../components/CompliancePanel'
import { MeetingDetailHeader } from '../components/MeetingDetailHeader'
import { MeetingInfoEditor } from '../components/MeetingInfoEditor'
import { MembersDialog } from '../components/MembersDialog'
import { MinutesEditor } from '../components/MinutesEditor'
import { MindMapDialog } from '../components/MindMapDialog'
import { MinutesSidebar } from '../components/MinutesSidebar'
import { MinutesVersionsDialog } from '../components/MinutesVersionsDialog'
import { ParticipantsPanel } from '../components/ParticipantsPanel'
import { ReviewPanel } from '../components/ReviewPanel'
import { SegmentEditDialog } from '../components/SegmentEditDialog'
import { ShareDialog } from '../components/ShareDialog'
import { TopBar } from '../components/TopBar'
import { TranscriptPanel } from '../components/TranscriptPanel'
import { STATE_CONFIRMED, STATE_DELETED } from '../lib/speakerRoles'

/** 详情页 compact 档的标签（宽屏不显示标签栏，靠并排布局） */
const DETAIL_TABS = [
  { id: 'transcript', label: '转写' },
  { id: 'minutes', label: '纪要' },
  { id: 'todos', label: '待办' },
] as const

export default function MeetingDetail() {
  const { id } = useParams<{ id: string }>()
  const [searchParams] = useSearchParams()
  const meetingId = Number(id)
  // 详情页在手机上不再「并排 + 长滚动」，改为三标签切换（见 render 里的底部标签栏）
  const [detailTab, setDetailTab] = useState<'transcript' | 'minutes' | 'todos'>('transcript')
  const {
    data,
    isLoading,
    error,
    activeMinutes,
    versions,
    generating,
    genError,
    templateId,
    setTemplateId,
    showExport,
    setShowExport,
    exportStatus,
    showMindMap,
    setShowMindMap,
    showShare,
    setShowShare,
    showInfo,
    setShowInfo,
    showVersions,
    setShowVersions,
    editingMinutes,
    setEditingMinutes,
    editingSegment,
    setEditingSegment,
    showCompliance,
    setShowCompliance,
    showMembers,
    setShowMembers,
    hasTranscript,
    renameSpeaker,
    saveSegment,
    confirmSpeaker,
    handleGenerate,
    handleExport,
    handleSeek,
    onMeetingSaved,
    onMinutesSaved,
  } = useMeetingDetail(meetingId, searchParams)

  useEffect(() => {
    if (!data || !window.location.hash) return
    const id = decodeURIComponent(window.location.hash.slice(1))
    const timer = window.setTimeout(() => document.getElementById(id)?.scrollIntoView({ block: 'center' }), 100)
    return () => window.clearTimeout(timer)
  }, [data])

  // 必须无条件调用（早退分支在下面）；详情接口已带 speakers，先种一份避免闪
  const {
    speakers,
    importanceByKey,
    setImportance,
    setState: setSpeakerState,
    error: speakerRoleError,
  } = useSpeakerRoles(meetingId, { initial: data?.speakers })

  if (isLoading) {
    return (
      <div className="flex h-full flex-col">
        <TopBar />
        <div className="flex flex-1 items-center justify-center">
          <Loader2 size={18} className="animate-spin" style={{ color: 'var(--accent)' }} />
        </div>
      </div>
    )
  }

  if (error || !data) {
    // 401 说明令牌失效（过期或在别处被清掉），需要引导重新登录，
    // 而不是笼统地告诉用户「加载失败」。
    const unauthorized = error instanceof ApiError && error.status === 401

    return (
      <div className="flex h-full flex-col">
        <TopBar />
        <div className="flex flex-1 flex-col items-center justify-center gap-3 px-6">
          <p className="text-[13px] text-secondary">
            {unauthorized ? '登录状态已失效' : '加载失败'}
          </p>
          {unauthorized && (
            <p className="max-w-[320px] text-center text-[12px] text-tertiary">
              登录凭证已过期，请重新登录后查看这场会议。
            </p>
          )}
          <div className="flex items-center gap-2">
            {unauthorized ? (
              <Link to="/login" className="btn btn-primary no-underline">
                重新登录
              </Link>
            ) : (
              <Link to="/history" className="btn no-underline">
                返回列表
              </Link>
            )}
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="flex h-full flex-col">
      <TopBar />

      <main className="min-h-0 flex-1 overflow-y-auto p-4">
        <div className="mx-auto max-w-[980px]">
          <MeetingDetailHeader
            meeting={data}
            exportOpen={showExport}
            exportStatus={exportStatus}
            onToggleExport={() => setShowExport((value) => !value)}
            onExport={(format) => void handleExport(format)}
            onOpenInfo={() => setShowInfo(true)}
            onOpenShare={() => setShowShare(true)}
            onOpenCompliance={() => setShowCompliance(true)}
            onOpenMembers={() => setShowMembers(true)}
          />

          {/* 音频播放器 */}
          {data.has_audio && (
            <div className="mb-4">
              <AudioPlayer meetingId={meetingId} />
            </div>
          )}

          <div className="grid gap-3 lg:grid-cols-[1fr_320px]">
            <div
              data-pane="transcript"
              data-pane-active={detailTab === 'transcript'}
              className="min-w-0"
            >
              <TranscriptPanel
                segments={data.segments}
                hasAudio={data.has_audio}
                onSeek={handleSeek}
                onRenameSpeaker={renameSpeaker}
                onConfirmSpeaker={confirmSpeaker}
                onEditSegment={setEditingSegment}
                speakerImportance={importanceByKey}
              />
            </div>

            <div
              data-pane="minutes"
              data-pane-active={detailTab === 'minutes'}
              className="min-w-0"
            >
              <MinutesSidebar
              minutes={activeMinutes}
              generating={generating}
              error={genError}
              hasTranscript={hasTranscript}
              templateId={templateId}
              onTemplateChange={setTemplateId}
              showVersions={showVersions}
              onToggleVersions={() => {
                setShowMindMap(false)
                setShowVersions((value) => !value)
              }}
              showMindMap={showMindMap}
              onToggleMindMap={() => {
                setShowVersions(false)
                setShowMindMap((value) => !value)
              }}
              onEdit={() => {
                if (activeMinutes) setEditingMinutes(activeMinutes)
              }}
              onGenerate={() => void handleGenerate()}
              onSeek={handleSeek}
              speakers={speakers}
              onImportanceChange={(row, value) => void setImportance(row, value)}
              onSpeakerConfirm={(row) => void setSpeakerState(row, STATE_CONFIRMED)}
              onSpeakerDelete={(row) => void setSpeakerState(row, STATE_DELETED)}
              importanceError={speakerRoleError}
            />
            </div>
          </div>

          <div
            data-pane="minutes"
            data-pane-active={detailTab === 'minutes'}
            className="mt-3"
          >
            <ParticipantsPanel meetingId={meetingId} />
          </div>

          <div
            data-pane="todos"
            data-pane-active={detailTab === 'todos'}
            className="space-y-3"
          >
            <section className="panel mt-3 overflow-hidden">
              <div className="panel-header">
                <CheckSquare size={14} style={{ color: 'var(--text-tertiary)' }} />
                <h2>待办闭环</h2>
                <span className="ml-auto text-[11px] text-tertiary">
                  终稿与人工修订都会同步
                </span>
              </div>
              <ActionsPanel meetingId={meetingId} onSeek={handleSeek} />
            </section>

            <ReviewPanel meetingId={meetingId} onSeek={handleSeek} />
          </div>

          {showVersions && (
            <MinutesVersionsDialog
              versions={versions ?? []}
              onEdit={setEditingMinutes}
              onClose={() => setShowVersions(false)}
            />
          )}

          {showMindMap && (
            <MindMapDialog
              meetingId={meetingId}
              onSeek={handleSeek}
              onClose={() => setShowMindMap(false)}
            />
          )}
        </div>
      </main>

      {/* 底部标签栏（仅 compact）：把「并排 + 长滚动」换成三屏切换。
          桌面（>=768）不渲染这块，布局与改造前完全一致。 */}
      <nav
        className="flex shrink-0 items-stretch gap-1 px-2 pt-1.5 pb-1 md:hidden"
        style={{
          borderTop: '1px solid var(--border-subtle)',
          background: 'var(--bg-surface)',
        }}
        aria-label="详情面板切换"
      >
        {DETAIL_TABS.map((tab) => (
          <button
            key={tab.id}
            type="button"
            onClick={() => setDetailTab(tab.id)}
            aria-current={detailTab === tab.id ? 'page' : undefined}
            className="flex min-h-11 flex-1 items-center justify-center rounded-lg text-[13px] font-medium transition-colors"
            style={{
              background: detailTab === tab.id ? 'var(--accent-soft)' : 'transparent',
              color: detailTab === tab.id ? 'var(--accent-text)' : 'var(--text-tertiary)',
            }}
          >
            {tab.label}
          </button>
        ))}
      </nav>

      {showInfo && (
        <MeetingInfoEditor
          meeting={data}
          onSaved={onMeetingSaved}
          onClose={() => setShowInfo(false)}
        />
      )}
      {showShare && (
        <ShareDialog meetingId={meetingId} onClose={() => setShowShare(false)} />
      )}
      {editingMinutes && (
        <MinutesEditor
          meetingId={meetingId}
          minutes={editingMinutes}
          onSaved={onMinutesSaved}
          onClose={() => setEditingMinutes(null)}
        />
      )}
      {editingSegment && (
        <SegmentEditDialog
          segment={editingSegment}
          onSave={(text) => void saveSegment(editingSegment, text)}
          onClose={() => setEditingSegment(null)}
        />
      )}
      {showCompliance && (
        <CompliancePanel
          meeting={data}
          onSaved={onMeetingSaved}
          onClose={() => setShowCompliance(false)}
        />
      )}
      {showMembers && (
        <MembersDialog
          meetingId={meetingId}
          onClose={() => setShowMembers(false)}
        />
      )}
    </div>
  )
}
