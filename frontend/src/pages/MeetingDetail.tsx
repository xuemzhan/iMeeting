import { Link, useParams, useSearchParams } from 'react-router-dom'
import {
  CheckSquare,
  Loader2,
} from 'lucide-react'

import { ApiError } from '../api/client'
import { useMeetingDetail } from '../hooks/useMeetingDetail'
import { ActionsPanel } from '../components/ActionsPanel'
import { AudioPlayer } from '../components/AudioPlayer'
import { CompliancePanel } from '../components/CompliancePanel'
import { MeetingDetailHeader } from '../components/MeetingDetailHeader'
import { MeetingInfoEditor } from '../components/MeetingInfoEditor'
import { MembersDialog } from '../components/MembersDialog'
import { MinutesEditor } from '../components/MinutesEditor'
import { MinutesMindMapPanel } from '../components/MinutesMindMapPanel'
import { MinutesSidebar } from '../components/MinutesSidebar'
import { MinutesVersionsPanel } from '../components/MinutesVersionsPanel'
import { ReviewPanel } from '../components/ReviewPanel'
import { SegmentEditDialog } from '../components/SegmentEditDialog'
import { ShareDialog } from '../components/ShareDialog'
import { TopBar } from '../components/TopBar'
import { TranscriptPanel } from '../components/TranscriptPanel'

export default function MeetingDetail() {
  const { id } = useParams<{ id: string }>()
  const [searchParams] = useSearchParams()
  const meetingId = Number(id)
  const {
    data,
    isLoading,
    error,
    activeMinutes,
    versions,
    mindmap,
    mindmapLoading,
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
            <TranscriptPanel
              segments={data.segments}
              hasAudio={data.has_audio}
              onSeek={handleSeek}
              onRenameSpeaker={renameSpeaker}
              onConfirmSpeaker={confirmSpeaker}
              onEditSegment={setEditingSegment}
            />

            <MinutesSidebar
              minutes={activeMinutes}
              generating={generating}
              error={genError}
              hasTranscript={hasTranscript}
              templateId={templateId}
              onTemplateChange={setTemplateId}
              showVersions={showVersions}
              onToggleVersions={() => setShowVersions((value) => !value)}
              showMindMap={showMindMap}
              onToggleMindMap={() => setShowMindMap((value) => !value)}
              onEdit={() => {
                if (activeMinutes) setEditingMinutes(activeMinutes)
              }}
              onGenerate={() => void handleGenerate()}
              onSeek={handleSeek}
            />
          </div>

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

          {showVersions && (
            <MinutesVersionsPanel
              versions={versions ?? []}
              onEdit={setEditingMinutes}
            />
          )}

          {showMindMap && (
            <MinutesMindMapPanel
              data={mindmap}
              loading={mindmapLoading}
              onSeek={handleSeek}
              onClose={() => setShowMindMap(false)}
            />
          )}
        </div>
      </main>

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
