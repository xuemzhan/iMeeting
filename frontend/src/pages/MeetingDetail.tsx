import { useEffect, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ArrowLeft,
  Download,
  Sparkles,
  Loader2,
  Lightbulb,
  CheckSquare,
  Network,
  Share2,
  Info,
  PencilLine,
  History,
  ShieldCheck,
  Users,
} from 'lucide-react'
import { meetingApi, voiceprintApi, ApiError } from '../api/client'
import { formatDuration, formatTimestamp } from '../store/session'
import { useAuth } from '../store/auth'
import { TopBar } from '../components/TopBar'
import { AudioPlayer } from '../components/AudioPlayer'
import type { ApiSegment, Minutes } from '../api/types'
import { TemplatePicker } from '../components/TemplatePicker'
import { MindMap } from '../components/MindMap'
import { MeetingInfoEditor } from '../components/MeetingInfoEditor'
import { ShareDialog } from '../components/ShareDialog'
import { ActionsPanel } from '../components/ActionsPanel'
import { MinutesEditor } from '../components/MinutesEditor'
import { CompliancePanel } from '../components/CompliancePanel'
import { ReviewPanel } from '../components/ReviewPanel'
import { MembersDialog } from '../components/MembersDialog'
import { SegmentEditDialog } from '../components/SegmentEditDialog'
import { TranscriptPanel } from '../components/TranscriptPanel'

const EXPORT_FORMATS = [
  { key: 'srt', label: 'SRT 字幕' },
  { key: 'vtt', label: 'VTT 字幕' },
  { key: 'md', label: 'Markdown' },
  { key: 'txt', label: '纯文本' },
  { key: 'json', label: 'JSON' },
  { key: 'docx', label: 'Word 文档' },
  { key: 'pdf', label: 'PDF 文档' },
]

export default function MeetingDetail() {
  const { id } = useParams<{ id: string }>()
  const [searchParams] = useSearchParams()
  const meetingId = Number(id)
  const qc = useQueryClient()
  const hasToken = useAuth((s) => s.hasToken)

  const [minutes, setMinutes] = useState<Minutes | null>(null)
  const [generating, setGenerating] = useState(false)
  const [showExport, setShowExport] = useState(false)
  /** 生成失败的原因。之前没有 catch，后端 4xx 会变成未处理的 Promise 拒绝，
   *  界面只是转圈停住、不给任何反馈，用户完全不知道发生了什么。 */
  const [genError, setGenError] = useState<string | null>(null)
  const [templateId, setTemplateId] = useState<number | null>(null)
  const [showMindMap, setShowMindMap] = useState(false)
  const [showShare, setShowShare] = useState(false)
  const [showInfo, setShowInfo] = useState(false)
  const [showVersions, setShowVersions] = useState(false)
  const [editingMinutes, setEditingMinutes] = useState<Minutes | null>(null)
  const [editingSegment, setEditingSegment] = useState<ApiSegment | null>(null)
  const [compareA, setCompareA] = useState<number | null>(null)
  const [compareB, setCompareB] = useState<number | null>(null)
  const [exportStatus, setExportStatus] = useState<string | null>(null)
  const [showCompliance, setShowCompliance] = useState(false)
  const [showMembers, setShowMembers] = useState(false)
  /** 把某个说话人标签改成真实姓名（声纹库 + 全部历史片段一起更新）。 */
  const renameSpeaker = async (fromLabel: string, name: string) => {
    try {
      await voiceprintApi.relabel(meetingId, fromLabel, name)
      await qc.invalidateQueries({ queryKey: ['meeting', meetingId] })
      await qc.invalidateQueries({ queryKey: ['minutes', meetingId] })
    } catch (err) {
      setGenError(err instanceof Error ? err.message : '说话人改名失败')
    }
  }

  const { data, isLoading, error } = useQuery({
    queryKey: ['meeting', meetingId],
    queryFn: () => meetingApi.get(meetingId),
    enabled: Number.isFinite(meetingId),
  })

  const { data: existingMinutes } = useQuery({
    queryKey: ['minutes', meetingId],
    queryFn: () => meetingApi.getMinutes(meetingId),
    enabled: Number.isFinite(meetingId) && hasToken(),
  })

  const { data: versions } = useQuery({
    queryKey: ['minutes-versions', meetingId],
    queryFn: () => meetingApi.minutesVersions(meetingId),
    enabled: showVersions && Number.isFinite(meetingId),
  })

  const activeMinutes = minutes ?? existingMinutes ?? null
  const { data: mindmap, isLoading: mindmapLoading } = useQuery({
    queryKey: ['mindmap', meetingId],
    queryFn: () => meetingApi.mindmap(meetingId),
    enabled: showMindMap,
  })
  useEffect(() => {
    const seconds = Number(searchParams.get('t') || 0)
    if (!data?.has_audio || !Number.isFinite(seconds) || seconds <= 0) return
    const timer = window.setTimeout(() => {
      window.dispatchEvent(
        new CustomEvent('audio-player-seek', { detail: seconds * 1000 }),
      )
    }, 500)
    return () => window.clearTimeout(timer)
  }, [data?.has_audio, searchParams])

  /**
   * 是否有可用于生成纪要的转写内容。
   *
   * 麦克风静音或全零 PCM 的会议会落成 0 段，此时后端返回 400
   * 「该会议还没有转写内容」。这是**预期状态**而不是故障，
   * 所以按钮应直接禁用，而不是让用户点了再吃一个错误。
   */
  const hasTranscript = (data?.segment_count ?? 0) > 0
  const versionRows = versions ?? []
  const versionA = versionRows.find(
    (item) => item.version === (compareA ?? versionRows[0]?.version),
  )
  const versionB = versionRows.find(
    (item) => item.version === (compareB ?? versionRows[1]?.version),
  )

  const saveSegment = async (segment: ApiSegment, text: string) => {
    try {
      await meetingApi.editSegment(meetingId, segment.segment_id, { text })
      await qc.invalidateQueries({ queryKey: ['meeting', meetingId] })
      setEditingSegment(null)
    } catch (err) {
      setGenError(err instanceof Error ? err.message : '转写修订失败')
    }
  }

  const confirmSpeaker = async (segment: ApiSegment) => {
    if (!segment.speaker) return
    try {
      await voiceprintApi.relabel(meetingId, segment.speaker, segment.speaker)
      await qc.invalidateQueries({ queryKey: ['meeting', meetingId] })
    } catch (err) {
      setGenError(err instanceof Error ? err.message : '说话人确认失败')
    }
  }

  const handleGenerate = async () => {
    if (generating || !hasTranscript) return
    setGenerating(true)
    setGenError(null)
    try {
      const result = await meetingApi.generateMinutes(
        meetingId,
        Boolean(activeMinutes),
        templateId,
      )
      setMinutes(result)
      await qc.invalidateQueries({ queryKey: ['minutes', meetingId] })
    } catch (err) {
      // 必须 catch：否则 400/502 会变成 unhandled rejection，
      // 界面只是「转圈停住」，用户既看不到原因也不知道该重试还是放弃。
      setGenError(err instanceof Error ? err.message : '生成失败，请稍后重试')
    } finally {
      setGenerating(false)
    }
  }

  const handleExport = async (format: string) => {
    setShowExport(false)
    setExportStatus('正在创建导出任务…')
    try {
      let job = await meetingApi.createExportJob(meetingId, format)
      for (let i = 0; i < 120; i++) {
        if (job.status === 'failed') {
          throw new Error(job.last_error || '导出失败')
        }
        if (job.status === 'succeeded') break
        setExportStatus(
          job.status === 'running' ? '正在生成导出文件…' : '等待导出任务…',
        )
        await new Promise((resolve) => window.setTimeout(resolve, 1000))
        job = await meetingApi.exportJob(job.id)
      }
      if (job.status !== 'succeeded') {
        throw new Error('导出超时，请稍后在后台任务中查看')
      }
      const blob = await meetingApi.downloadExportJob(job.id)
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = job.download_name || `${data?.title || 'meeting'}.${format}`
      a.click()
      URL.revokeObjectURL(url)
      setExportStatus(null)
    } catch (e) {
      setExportStatus(e instanceof Error ? e.message : '导出失败')
    }
  }

  // 点击时间戳跳转音频
  const handleSeek = (ms: number) => {
    window.dispatchEvent(new CustomEvent('audio-player-seek', { detail: ms }))
  }

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
          <Link
            to="/history"
            className="mb-4 inline-flex items-center gap-1 text-[12px] no-underline text-tertiary hover:text-secondary"
          >
            <ArrowLeft size={13} />
            返回列表
          </Link>

          <div className="mb-4 flex items-start justify-between gap-4">
            <div>
              <h1 className="text-[16px] font-medium">{data.title}</h1>
              <p className="mt-1 flex items-center gap-2 text-[12px] text-tertiary">
                <span>{new Date(data.created_at).toLocaleString('zh-CN')}</span>
                <span aria-hidden>·</span>
                <span>{formatDuration(data.duration_ms)}</span>
                <span aria-hidden>·</span>
                <span>{data.segments.length} 段</span>
                {data.has_audio && (
                  <>
                    <span aria-hidden>·</span>
                    <span style={{ color: 'var(--live-text)' }}>有录音</span>
                  </>
                )}
              </p>
              {(data.participants || data.meeting_type || data.agenda) && (
                <p className="mt-1 max-w-[680px] text-[11px] text-tertiary">
                  {data.meeting_type && `类型：${data.meeting_type} · `}
                  {data.participants && `参会：${data.participants}`}
                  {data.participants && data.agenda && ' · '}
                  {data.agenda && `议程：${data.agenda}`}
                </p>
              )}
            </div>

            <div className="flex shrink-0 items-center gap-1.5">
              <button
                type="button"
                className="btn btn-ghost"
                onClick={() => setShowInfo(true)}
                title="编辑会议信息"
              >
                <Info size={13} /> 会议信息
              </button>
              <button
                type="button"
                className="btn btn-ghost"
                onClick={() => setShowShare(true)}
              >
                <Share2 size={13} /> 分享
              </button>
              <button
                type="button"
                className="btn btn-ghost"
                onClick={() => setShowCompliance(true)}
              >
                <ShieldCheck size={13} /> 合规
              </button>
              <button
                type="button"
                className="btn btn-ghost"
                onClick={() => setShowMembers(true)}
              >
                <Users size={13} /> 协作
              </button>
              <div className="relative">
              <button
                type="button"
                className="btn"
                onClick={() => setShowExport((v) => !v)}
                disabled={Boolean(exportStatus)}
              >
                <Download size={13} />
                {exportStatus ?? '导出'}
              </button>
              {showExport && (
                <div
                  className="panel absolute right-0 z-10 mt-1 w-[140px] overflow-hidden py-1"
                  style={{ boxShadow: '0 8px 24px rgba(0,0,0,0.12)' }}
                >
                  {EXPORT_FORMATS.map((f) => (
                    <button
                      key={f.key}
                      type="button"
                      onClick={() => void handleExport(f.key)}
                      className="block w-full px-3 py-1.5 text-left text-[12px] transition-colors hover:bg-[var(--bg-hover)]"
                    >
                      {f.label}
                    </button>
                  ))}
                </div>
              )}
              </div>
            </div>
          </div>

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

            {/* 纪要 */}
            <aside className="panel overflow-hidden">
              <div className="panel-header">
                <Sparkles size={14} style={{ color: 'var(--text-tertiary)' }} />
                <h2>会议纪要</h2>
                {activeMinutes && (
                  <button
                    type="button"
                    onClick={() => setEditingMinutes(activeMinutes)}
                    className="btn btn-ghost px-1.5 text-[11px]"
                    title="人工修订并生成新版本"
                  >
                    <PencilLine size={12} /> 修订
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => setShowVersions((value) => !value)}
                  className="btn btn-ghost px-1.5 text-[11px]"
                  title="查看纪要历史版本"
                >
                  <History size={12} /> {showVersions ? '收起版本' : '版本'}
                </button>
                <button
                  type="button"
                  onClick={() => setShowMindMap((value) => !value)}
                  className="btn btn-ghost px-1.5 text-[11px]"
                  title="用思维导图查看纪要"
                >
                  <Network size={12} />
                  {showMindMap ? '收起导图' : '思维导图'}
                </button>
              </div>

              <div className="p-4">
                {generating ? (
                  <div className="flex flex-col items-center gap-2 py-8">
                    <Loader2 size={16} className="animate-spin" style={{ color: 'var(--accent)' }} />
                    <p className="text-[12px] text-tertiary">正在分析…</p>
                  </div>
                ) : activeMinutes ? (
                  <div className="space-y-3">
                    {activeMinutes.is_draft && (
                      <p
                        className="rounded px-2 py-1 text-[11px]"
                        style={{
                          background: 'var(--accent-soft)',
                          color: 'var(--accent-text)',
                        }}
                      >
                        实时草稿 · 第 {activeMinutes.version} 版
                      </p>
                    )}
                    {activeMinutes.edited && (
                      <p
                        className="rounded px-2 py-1 text-[11px]"
                        style={{
                          background: 'var(--bg-subtle)',
                          color: 'var(--text-secondary)',
                        }}
                      >
                        人工修订版 · v{activeMinutes.version} · 原始自动版本仍保留
                      </p>
                    )}
                    {activeMinutes.summary && (
                      <p className="text-[12px] leading-relaxed text-secondary">
                        {activeMinutes.summary}
                      </p>
                    )}

                    {activeMinutes.topics.map((t, i) => (
                      <div
                        key={i}
                        className="rounded-lg px-3 py-2"
                        style={{ background: 'var(--accent-soft)' }}
                      >
                        <p
                          className="text-[12px] font-medium"
                          style={{ color: 'var(--accent-text)' }}
                        >
                          {t.title}
                        </p>
                        {t.conclusion && (
                          <p
                            className="mt-1 border-l-2 pl-2 text-[11px]"
                            style={{ borderColor: 'var(--accent)', color: 'var(--accent-text)' }}
                          >
                            {t.conclusion}
                          </p>
                        )}
                        {t.evidence && t.evidence.length > 0 && (
                          <div className="mt-1 flex flex-wrap gap-1">
                            {t.evidence.slice(0, 3).map((evidence) => (
                              <button
                                key={`${evidence.segment_id}-${evidence.start_ms}`}
                                type="button"
                                onClick={() => handleSeek(evidence.start_ms)}
                                className="rounded px-1 py-0.5 font-mono text-[10px]"
                                style={{
                                  background: 'var(--bg-surface)',
                                  color: 'var(--accent-text)',
                                }}
                                title={evidence.text}
                              >
                                原话 {formatTimestamp(evidence.start_ms)}
                              </button>
                            ))}
                          </div>
                        )}
                      </div>
                    ))}

                    {activeMinutes.todos.length > 0 && (
                      <div>
                        <p className="mb-1.5 flex items-center gap-1 text-[11px] font-medium text-tertiary">
                          <CheckSquare size={11} />
                          待办事项
                        </p>
                        {activeMinutes.todos.map((t, i) => (
                          <div
                            key={i}
                            className="mb-1 rounded-lg px-2.5 py-1.5 text-[11px]"
                            style={{ background: 'var(--warn-soft)', color: 'var(--warn-text)' }}
                          >
                            {t.task}
                            {t.owner && <span className="opacity-75"> · {t.owner}</span>}
                            {t.evidence?.[0] && (
                              <button
                                type="button"
                                onClick={() => handleSeek(t.evidence![0].start_ms)}
                                className="ml-1 font-mono text-[10px] underline-offset-2 hover:underline"
                              >
                                {formatTimestamp(t.evidence[0].start_ms)}
                              </button>
                            )}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                ) : (
                  <div className="flex flex-col items-center gap-2 py-8 text-center">
                    <Lightbulb size={18} style={{ color: 'var(--text-tertiary)' }} />
                    <p className="text-[12px] text-tertiary">还没有生成纪要</p>
                  </div>
                )}

                {genError && (
                  <div
                    className="mb-2 rounded-lg px-3 py-2 text-[11px]"
                    style={{ background: 'var(--danger-soft)', color: 'var(--danger-text)' }}
                  >
                    {genError}
                  </div>
                )}

                <TemplatePicker value={templateId} onChange={setTemplateId} />

                <button
                  type="button"
                  onClick={() => void handleGenerate()}
                  disabled={generating || !hasTranscript}
                  title={hasTranscript ? undefined : '该会议还没有转写内容，无法生成纪要'}
                  className="btn btn-primary mt-4 w-full"
                  style={{ height: 34, opacity: hasTranscript ? undefined : 0.5 }}
                >
                  {generating ? (
                    <>
                      <Loader2 size={13} className="animate-spin" />
                      生成中…
                    </>
                  ) : (
                    <>
                      <Sparkles size={13} />
                      {activeMinutes
                        ? activeMinutes.is_draft
                          ? '生成终稿'
                          : '重新生成'
                        : '生成纪要'}
                    </>
                  )}
                </button>

                {!hasTranscript && (
                  <p className="mt-2 text-center text-[11px] text-tertiary">
                    该会议没有转写内容（可能全程未采集到声音），无法生成纪要
                  </p>
                )}
              </div>
            </aside>
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
            <section className="panel mt-3 overflow-hidden">
              <div className="panel-header">
                <History size={14} style={{ color: 'var(--text-tertiary)' }} />
                <h2>纪要版本与对比</h2>
                <span className="ml-auto text-[11px] text-tertiary">
                  自动草稿、终稿与人工修订均保留
                </span>
              </div>
              <div className="grid gap-3 p-4 lg:grid-cols-[280px_1fr]">
                <div className="space-y-1.5">
                  {versionRows.length === 0 && (
                    <p className="py-6 text-center text-[11px] text-tertiary">
                      还没有纪要版本
                    </p>
                  )}
                  {versionRows.map((row) => (
                    <div
                      key={row.id}
                      className="rounded-lg px-2.5 py-2"
                      style={{ background: 'var(--bg-subtle)' }}
                    >
                      <div className="flex items-center gap-1.5">
                        <span className="text-[11px] font-medium">v{row.version}</span>
                        <span className="chip" style={{ height: 18 }}>
                          {row.edited ? '人工修订' : row.is_draft ? '会中草稿' : '自动终稿'}
                        </span>
                        <span className="ml-auto text-[10px] text-tertiary">
                          {new Date(row.created_at).toLocaleTimeString('zh-CN', {
                            hour: '2-digit',
                            minute: '2-digit',
                          })}
                        </span>
                      </div>
                      <p className="mt-1 line-clamp-2 text-[10px] leading-relaxed text-tertiary">
                        {row.summary || '无摘要'}
                      </p>
                      <button
                        type="button"
                        onClick={() => setEditingMinutes(row)}
                        className="mt-1 text-[10px] underline-offset-2 hover:underline"
                        style={{ color: 'var(--accent-text)' }}
                      >
                        基于此版修订
                      </button>
                    </div>
                  ))}
                </div>
                <div>
                  {versionRows.length >= 2 ? (
                    <>
                      <div className="mb-2 flex items-center gap-2 text-[11px] text-tertiary">
                        <select
                          value={versionA?.version ?? ''}
                          onChange={(e) => setCompareA(Number(e.target.value))}
                          className="rounded px-1.5 py-1"
                          style={{
                            background: 'var(--bg-surface)',
                            border: '1px solid var(--border-subtle)',
                          }}
                        >
                          {versionRows.map((row) => (
                            <option key={row.id} value={row.version}>
                              v{row.version} · {row.edited ? '人工' : row.is_draft ? '草稿' : '终稿'}
                            </option>
                          ))}
                        </select>
                        <span>对比</span>
                        <select
                          value={versionB?.version ?? ''}
                          onChange={(e) => setCompareB(Number(e.target.value))}
                          className="rounded px-1.5 py-1"
                          style={{
                            background: 'var(--bg-surface)',
                            border: '1px solid var(--border-subtle)',
                          }}
                        >
                          {versionRows.map((row) => (
                            <option key={row.id} value={row.version}>
                              v{row.version} · {row.edited ? '人工' : row.is_draft ? '草稿' : '终稿'}
                            </option>
                          ))}
                        </select>
                      </div>
                      <div className="grid gap-2 sm:grid-cols-2">
                        {[versionA, versionB].map((row, index) => (
                          <div
                            key={index}
                            className="max-h-[320px] overflow-y-auto rounded-lg p-3"
                            style={{ background: 'var(--bg-subtle)' }}
                          >
                            <p className="mb-2 text-[11px] font-medium">
                              v{row?.version ?? '—'} · {row?.edited ? '人工修订' : row?.is_draft ? '草稿' : '终稿'}
                            </p>
                            <p className="whitespace-pre-wrap text-[11px] leading-relaxed text-secondary">
                              {row?.summary || '无摘要'}
                            </p>
                            {row?.topics.map((topic, topicIndex) => (
                              <p key={topicIndex} className="mt-2 text-[11px] text-tertiary">
                                · {topic.title}
                                {topic.conclusion ? `：${topic.conclusion}` : ''}
                              </p>
                            ))}
                          </div>
                        ))}
                      </div>
                    </>
                  ) : (
                    <p className="py-8 text-center text-[11px] text-tertiary">
                      至少需要两个版本才能对比
                    </p>
                  )}
                </div>
              </div>
            </section>
          )}

          {showMindMap && (
            <section className="panel mt-3 overflow-hidden">
              <div className="panel-header">
                <Network size={14} style={{ color: 'var(--text-tertiary)' }} />
                <h2>纪要思维导图</h2>
                <button
                  type="button"
                  onClick={() => setShowMindMap(false)}
                  className="btn btn-ghost ml-auto px-1.5 text-[11px]"
                >
                  收起
                </button>
              </div>
              <div style={{ height: 420 }}>
                {mindmapLoading ? (
                  <div className="flex h-full items-center justify-center gap-2 text-[12px] text-tertiary">
                    <Loader2 size={14} className="animate-spin" />
                    正在生成导图…
                  </div>
                ) : mindmap ? (
                  <MindMap data={mindmap} onSeek={handleSeek} />
                ) : (
                  <p className="py-10 text-center text-[12px] text-tertiary">
                    还没有可用的纪要
                  </p>
                )}
              </div>
            </section>
          )}
        </div>
      </main>

      {showInfo && (
        <MeetingInfoEditor
          meeting={data}
          onSaved={() => {
            void qc.invalidateQueries({ queryKey: ['meeting', meetingId] })
          }}
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
          onSaved={(row) => {
            setMinutes(row)
            void qc.invalidateQueries({ queryKey: ['minutes', meetingId] })
            void qc.invalidateQueries({ queryKey: ['minutes-versions', meetingId] })
          }}
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
          onSaved={() => {
            void qc.invalidateQueries({ queryKey: ['meeting', meetingId] })
          }}
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
