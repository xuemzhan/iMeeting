import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'

import { meetingApi, voiceprintApi } from '../api/client'
import type { ApiSegment, Minutes } from '../api/types'
import { runExportJob } from '../lib/exportJob'
import { useAuth } from '../store/auth'

export function useMeetingDetail(
  meetingId: number,
  searchParams: URLSearchParams,
) {
  const qc = useQueryClient()
  const hasToken = useAuth((state) => state.hasToken)

  const [minutes, setMinutes] = useState<Minutes | null>(null)
  const [generating, setGenerating] = useState(false)
  const [showExport, setShowExport] = useState(false)
  const [genError, setGenError] = useState<string | null>(null)
  const [templateId, setTemplateId] = useState<number | null>(null)
  const [showMindMap, setShowMindMap] = useState(false)
  const [showShare, setShowShare] = useState(false)
  const [showInfo, setShowInfo] = useState(false)
  const [showVersions, setShowVersions] = useState(false)
  const [editingMinutes, setEditingMinutes] = useState<Minutes | null>(null)
  const [editingSegment, setEditingSegment] = useState<ApiSegment | null>(null)
  const [exportStatus, setExportStatus] = useState<string | null>(null)
  const [showCompliance, setShowCompliance] = useState(false)
  const [showMembers, setShowMembers] = useState(false)

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

  const hasTranscript = (data?.segment_count ?? 0) > 0

  const invalidateMeeting = () => {
    void qc.invalidateQueries({ queryKey: ['meeting', meetingId] })
  }

  const onMeetingSaved = () => {
    invalidateMeeting()
  }

  const onMinutesSaved = (row: Minutes) => {
    setMinutes(row)
    void qc.invalidateQueries({ queryKey: ['minutes', meetingId] })
    void qc.invalidateQueries({ queryKey: ['minutes-versions', meetingId] })
  }

  const renameSpeaker = async (fromLabel: string, name: string) => {
    try {
      await voiceprintApi.relabel(meetingId, fromLabel, name)
      await qc.invalidateQueries({ queryKey: ['meeting', meetingId] })
      await qc.invalidateQueries({ queryKey: ['minutes', meetingId] })
    } catch (err) {
      setGenError(err instanceof Error ? err.message : '说话人改名失败')
    }
  }

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
      setGenError(err instanceof Error ? err.message : '生成失败，请稍后重试')
    } finally {
      setGenerating(false)
    }
  }

  const handleExport = async (format: string) => {
    setShowExport(false)
    setExportStatus('正在创建导出任务…')
    try {
      await runExportJob({
        createJob: () => meetingApi.createExportJob(meetingId, format),
        getJob: meetingApi.exportJob,
        downloadJob: meetingApi.downloadExportJob,
        fallbackFilename: `${data?.title || 'meeting'}.${format}`,
        timeoutMessage: '导出超时，请稍后在后台任务中查看',
        onProgress: (job) => {
          setExportStatus(
            job.status === 'running' ? '正在生成导出文件…' : '等待导出任务…',
          )
        },
      })
      setExportStatus(null)
    } catch (err) {
      setExportStatus(err instanceof Error ? err.message : '导出失败')
    }
  }

  const handleSeek = (ms: number) => {
    window.dispatchEvent(new CustomEvent('audio-player-seek', { detail: ms }))
  }

  return {
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
  }
}

