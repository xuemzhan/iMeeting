import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ArrowLeft,
  Download,
  FileText,
  Loader2,
  LogIn,
  RotateCcw,
  Tag,
  Trash2,
} from 'lucide-react'
import { meetingApi, ApiError } from '../api/client'
import type { MeetingSearchResult } from '../api/types'
import { runExportJob } from '../lib/exportJob'
import { useAuth } from '../store/auth'
import { TopBar } from '../components/TopBar'
import { HistoryFilters } from './HistoryFilters'
import { HistoryMeetingRow } from './HistoryMeetingRow'
import { HistoryPagination } from './HistoryPagination'
import { historySelectStyle } from './historyStyles'
import { parseHistoryTags } from './historyTags'

export default function History() {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [view, setView] = useState<'active' | 'trash'>('active')
  const [deleting, setDeleting] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  const [q, setQ] = useState('')
  const [debouncedQ, setDebouncedQ] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [range, setRange] = useState('all')
  const [archivedFilter, setArchivedFilter] = useState('false')
  const [tagFilter, setTagFilter] = useState('')
  const [page, setPage] = useState(0)
  const [pageSize, setPageSize] = useState(20)
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [action, setAction] = useState<'none' | 'rename' | 'tag'>('none')
  const [renameFind, setRenameFind] = useState('')
  const [renameReplace, setRenameReplace] = useState('')
  const [renamePrefix, setRenamePrefix] = useState('')
  const [renameSuffix, setRenameSuffix] = useState('')
  const [tagInput, setTagInput] = useState('')
  const [exportFormat, setExportFormat] = useState('md')
  const [exportStatus, setExportStatus] = useState<string | null>(null)
  const [opNotice, setOpNotice] = useState<string | null>(null)

  const user = useAuth((s) => s.user)
  const hasToken = useAuth((s) => s.hasToken)
  const needsLogin = !hasToken()
  void user

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedQ(q.trim()), 300)
    return () => window.clearTimeout(timer)
  }, [q])

  const dateFrom = useMemo(() => {
    if (range === '7d') {
      return new Date(Date.now() - 7 * 86400000).toISOString().slice(0, 10)
    }
    if (range === '30d') {
      return new Date(Date.now() - 30 * 86400000).toISOString().slice(0, 10)
    }
    return undefined
  }, [range])

  useEffect(() => {
    setPage(0)
    setSelected(new Set())
    setAction('none')
  }, [view, debouncedQ, statusFilter, range, archivedFilter, tagFilter])

  const { data, isLoading, error } = useQuery({
    queryKey: [
      'meetings',
      view,
      debouncedQ,
      statusFilter,
      dateFrom,
      archivedFilter,
      tagFilter,
      page,
      pageSize,
    ],
    queryFn: () => {
      if (view === 'trash') {
        return meetingApi.trash(pageSize, page * pageSize)
      }
      return meetingApi.search({
        q: debouncedQ || undefined,
        status: statusFilter || undefined,
        dateFrom,
        archived: archivedFilter === 'all' ? undefined : archivedFilter,
        tag: tagFilter || undefined,
        limit: pageSize,
        offset: page * pageSize,
      })
    },
    enabled: !needsLogin,
  })

  const meetings = data?.items ?? []
  const total = data?.total ?? 0
  const totalPages = Math.max(1, Math.ceil(total / pageSize))
  const allSelected =
    meetings.length > 0 && meetings.every((m) => selected.has(m.id))

  // 删除/彻底删除后当前页可能为空，自动回退一页
  useEffect(() => {
    if (!isLoading && meetings.length === 0 && total > 0 && page > 0) {
      setPage((p) => Math.max(0, p - 1))
    }
  }, [isLoading, meetings.length, total, page])

  const toggleOne = (id: number) => {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const toggleAll = () => {
    setSelected((prev) => {
      const next = new Set(prev)
      if (allSelected) meetings.forEach((m) => next.delete(m.id))
      else meetings.forEach((m) => next.add(m.id))
      return next
    })
  }

  const refresh = async () => {
    setSelected(new Set())
    await qc.invalidateQueries({ queryKey: ['meetings'] })
  }

  const removeFromCaches = (ids: number[]) => {
    const removed = new Set(ids)
    qc.setQueriesData<MeetingSearchResult>(
      { queryKey: ['meetings'] },
      (previous) =>
        previous
          ? {
              ...previous,
              items: previous.items.filter((item) => !removed.has(item.id)),
              total: Math.max(0, previous.total - removed.size),
            }
          : previous,
    )
  }

  const handleDelete = async (id: number, e: React.MouseEvent) => {
    e.stopPropagation()
    if (!confirm('将这场会议移入回收站？30 天内可以恢复。')) return
    setDeleting(id)
    try {
      await meetingApi.remove(id)
      removeFromCaches([id])
      await refresh()
    } catch (e) {
      setOpNotice(
        e instanceof Error
          ? e.message
          : '删除失败；会议可能仍在收尾或已被移入回收站。',
      )
    } finally {
      setDeleting(null)
    }
  }

  const handleRestore = async (id: number, e: React.MouseEvent) => {
    e.stopPropagation()
    await meetingApi.bulkRestore([id])
    await refresh()
  }

  const handleBulkTrash = async () => {
    if (!selected.size) return
    if (!confirm(`将选中的 ${selected.size} 场会议移入回收站？`)) return
    setBusy(true)
    try {
      const result = await meetingApi.bulkRemove([...selected])
      removeFromCaches(result.deleted_ids)
      await refresh()
      setOpNotice(
        result.skipped.length > 0
          ? `已移入回收站 ${result.deleted} 场；${result.skipped.length} 场被跳过（正在录音、已删除或无权限）。`
          : `已移入回收站 ${result.deleted} 场。`,
      )
    } catch (e) {
      alert(e instanceof Error ? e.message : '操作失败')
    } finally {
      setBusy(false)
    }
  }

  const handleBulkRestore = async () => {
    if (!selected.size) return
    setBusy(true)
    try {
      await meetingApi.bulkRestore([...selected])
      await refresh()
    } catch (e) {
      alert(e instanceof Error ? e.message : '恢复失败')
    } finally {
      setBusy(false)
    }
  }

  const handleBulkPurge = async () => {
    if (!selected.size) return
    if (
      !confirm(
        `彻底删除选中的 ${selected.size} 场会议？转写、纪要、音频都会删除，且不可恢复。`,
      )
    ) {
      return
    }
    setBusy(true)
    try {
      await meetingApi.bulkPurge([...selected])
      await refresh()
    } catch (e) {
      alert(e instanceof Error ? e.message : '彻底删除失败')
    } finally {
      setBusy(false)
    }
  }

  const handleArchive = async (archived: boolean) => {
    if (!selected.size) return
    setBusy(true)
    try {
      await meetingApi.bulkUpdate([...selected], { archived })
      await refresh()
    } catch (e) {
      alert(e instanceof Error ? e.message : '操作失败')
    } finally {
      setBusy(false)
    }
  }

  const applyRename = async () => {
    if (!selected.size) return
    setBusy(true)
    try {
      await meetingApi.bulkRename([...selected], {
        find: renameFind,
        replace: renameReplace,
        prefix: renamePrefix,
        suffix: renameSuffix,
      })
      setAction('none')
      await refresh()
    } catch (e) {
      alert(e instanceof Error ? e.message : '重命名失败')
    } finally {
      setBusy(false)
    }
  }

  const applyTags = async () => {
    const tags = parseHistoryTags(tagInput)
    if (!selected.size || tags.length === 0) return
    setBusy(true)
    try {
      await meetingApi.bulkUpdate([...selected], { tagsAdd: tags })
      setTagInput('')
      setAction('none')
      await refresh()
    } catch (e) {
      alert(e instanceof Error ? e.message : '打标签失败')
    } finally {
      setBusy(false)
    }
  }

  const exportSelected = async () => {
    if (!selected.size) return
    setBusy(true)
    try {
      setExportStatus('正在创建批量导出…')
      await runExportJob({
        createJob: () =>
          meetingApi.createBulkExportJob([...selected], exportFormat),
        getJob: meetingApi.exportJob,
        downloadJob: meetingApi.downloadExportJob,
        fallbackFilename: () => `meetings-${Date.now()}.zip`,
        maxPolls: 180,
        onProgress: () => setExportStatus('正在生成批量导出…'),
      })
    } catch (e) {
      alert(e instanceof Error ? e.message : '导出失败')
    } finally {
      setExportStatus(null)
      setBusy(false)
    }
  }

  const handleOpen = (id: number) => {
    if (view === 'active') navigate(`/history/${id}`)
  }
  const filtered =
    view === 'active' &&
    Boolean(debouncedQ || statusFilter || range !== 'all' || archivedFilter !== 'false' || tagFilter)

  return (
    <div className="flex h-full flex-col">
      <TopBar />

      <main className="min-h-0 flex-1 overflow-y-auto p-4">
        <div className="mx-auto max-w-[960px]">
          <Link
            to="/"
            className="mb-4 inline-flex items-center gap-1 text-[12px] no-underline text-tertiary hover:text-secondary"
          >
            <ArrowLeft size={13} />
            返回会议
          </Link>

          <div className="mb-3 flex items-end justify-between gap-2">
            <div className="flex items-center gap-3">
              <h1 className="text-[15px] font-medium">
                {view === 'active' ? '历史会议' : '回收站'}
              </h1>
              {!needsLogin && data && (
                <p className="text-[11px] text-tertiary">
                  {view === 'trash'
                    ? `共 ${total} 场 · 保留 30 天`
                    : filtered
                      ? `匹配 ${total} 场`
                      : `共 ${total} 场会议`}
                </p>
              )}
            </div>
            {!needsLogin && (
              <button
                type="button"
                onClick={() => setView(view === 'active' ? 'trash' : 'active')}
                className="btn btn-ghost text-[11px]"
              >
                {view === 'active' ? (
                  <>
                    <Trash2 size={12} /> 回收站
                  </>
                ) : (
                  <>
                    <RotateCcw size={12} /> 返回历史
                  </>
                )}
              </button>
            )}
          </div>

          {!needsLogin && view === 'active' && (
            <HistoryFilters
              q={q}
              status={statusFilter}
              archived={archivedFilter}
              range={range}
              tag={tagFilter}
              onQChange={setQ}
              onStatusChange={setStatusFilter}
              onArchivedChange={setArchivedFilter}
              onRangeChange={setRange}
              onTagChange={setTagFilter}
            />
          )}

          {selected.size > 0 && (
            <div
              className="mb-3 rounded-lg px-3 py-2"
              style={{ background: 'var(--accent-soft)', color: 'var(--accent-text)' }}
            >
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-[12px]">已选 {selected.size} 场</span>
                <button
                  type="button"
                  onClick={() => setSelected(new Set())}
                  className="text-[11px] underline-offset-2 hover:underline"
                >
                  取消选择
                </button>
                {view === 'active' ? (
                  <>
                    <select
                      value={exportFormat}
                      onChange={(e) => setExportFormat(e.target.value)}
                      aria-label="导出格式"
                      className="rounded px-1.5 py-1 text-[11px]"
                      style={historySelectStyle}
                    >
                      <option value="md">Markdown</option>
                      <option value="txt">纯文本</option>
                      <option value="srt">SRT</option>
                      <option value="vtt">VTT</option>
                      <option value="json">JSON</option>
                      <option value="docx">Word 文档</option>
                      <option value="pdf">PDF 文档</option>
                    </select>
                    <button type="button" onClick={() => void exportSelected()} disabled={busy} className="btn btn-ghost text-[11px]">
                      <Download size={12} /> {exportStatus ?? '导出'}
                    </button>
                    <button type="button" onClick={() => setAction(action === 'rename' ? 'none' : 'rename')} className="btn btn-ghost text-[11px]">
                      批量重命名
                    </button>
                    <button type="button" onClick={() => setAction(action === 'tag' ? 'none' : 'tag')} className="btn btn-ghost text-[11px]">
                      <Tag size={12} /> 标签
                    </button>
                    <button type="button" onClick={() => void handleArchive(true)} disabled={busy} className="btn btn-ghost text-[11px]">
                      归档
                    </button>
                    <button type="button" onClick={() => void handleArchive(false)} disabled={busy} className="btn btn-ghost text-[11px]">
                      取消归档
                    </button>
                    <button type="button" onClick={() => void handleBulkTrash()} disabled={busy} className="btn btn-primary ml-auto" style={{ height: 28, padding: '0 10px' }}>
                      {busy ? <Loader2 size={12} className="animate-spin" /> : <Trash2 size={12} />}
                      移入回收站
                    </button>
                  </>
                ) : (
                  <>
                    <button type="button" onClick={() => void handleBulkRestore()} disabled={busy} className="btn btn-primary ml-auto" style={{ height: 28, padding: '0 10px' }}>
                      <RotateCcw size={12} /> 恢复
                    </button>
                    <button type="button" onClick={() => void handleBulkPurge()} disabled={busy} className="btn btn-ghost text-[11px]" style={{ color: 'var(--danger-text)' }}>
                      <Trash2 size={12} /> 彻底删除
                    </button>
                  </>
                )}
              </div>
              {action === 'rename' && view === 'active' && (
                <div className="mt-2 flex flex-wrap items-center gap-1.5">
                  <input value={renameFind} onChange={(e) => setRenameFind(e.target.value)} placeholder="查找" className="w-[110px] rounded px-1.5 py-1 text-[11px]" style={historySelectStyle} />
                  <input value={renameReplace} onChange={(e) => setRenameReplace(e.target.value)} placeholder="替换为" className="w-[110px] rounded px-1.5 py-1 text-[11px]" style={historySelectStyle} />
                  <input value={renamePrefix} onChange={(e) => setRenamePrefix(e.target.value)} placeholder="前缀" className="w-[90px] rounded px-1.5 py-1 text-[11px]" style={historySelectStyle} />
                  <input value={renameSuffix} onChange={(e) => setRenameSuffix(e.target.value)} placeholder="后缀" className="w-[90px] rounded px-1.5 py-1 text-[11px]" style={historySelectStyle} />
                  <button type="button" onClick={() => void applyRename()} disabled={busy} className="btn btn-primary text-[11px]" style={{ height: 26 }}>
                    应用
                  </button>
                </div>
              )}
              {action === 'tag' && view === 'active' && (
                <div className="mt-2 flex flex-wrap items-center gap-1.5">
                  <input value={tagInput} onChange={(e) => setTagInput(e.target.value)} placeholder="标签，多个用逗号分隔" className="w-[220px] rounded px-1.5 py-1 text-[11px]" style={historySelectStyle} />
                  <button type="button" onClick={() => void applyTags()} disabled={busy} className="btn btn-primary text-[11px]" style={{ height: 26 }}>
                    添加标签
                  </button>
                </div>
              )}
            </div>
          )}

          {opNotice && (
            <div
              className="mb-3 flex items-center justify-between rounded-lg px-3 py-2 text-[11px]"
              style={{
                background: 'var(--accent-soft)',
                color: 'var(--accent-text)',
              }}
            >
              <span>{opNotice}</span>
              <button
                type="button"
                onClick={() => setOpNotice(null)}
                className="px-1"
                aria-label="关闭提示"
              >
                ×
              </button>
            </div>
          )}

          {needsLogin && (
            <div className="panel flex flex-col items-center gap-3 py-16">
              <LogIn size={22} style={{ color: 'var(--text-tertiary)' }} />
              <p className="text-[13px] text-secondary">登录后可查看历史会议</p>
              <p className="max-w-[300px] text-center text-[11px] text-tertiary">
                会议记录与纪要为账号私有数据，需要登录后访问。
              </p>
              <Link to="/login" className="btn btn-primary mt-1 no-underline">
                去登录
              </Link>
            </div>
          )}

          {!needsLogin && isLoading && (
            <div className="flex justify-center py-16">
              <Loader2 size={18} className="animate-spin" style={{ color: 'var(--accent)' }} />
            </div>
          )}

          {!needsLogin && error && <LoadError error={error} />}

          {!needsLogin && !isLoading && !error && meetings.length === 0 && (
            <div className="panel flex flex-col items-center gap-2 py-16">
              <FileText size={22} style={{ color: 'var(--text-tertiary)' }} />
              <p className="text-[13px] text-secondary">
                {view === 'trash' ? '回收站是空的' : filtered ? '没有匹配的会议' : '还没有会议记录'}
              </p>
              {view === 'active' && !filtered && (
                <Link to="/" className="btn btn-primary mt-1 no-underline">
                  开始第一场会议
                </Link>
              )}
            </div>
          )}

          {!needsLogin && meetings.length > 0 && (
            <div className="space-y-2">
              <label className="flex cursor-pointer items-center gap-2 px-1 text-[11px] text-tertiary">
                <input type="checkbox" checked={allSelected} onChange={toggleAll} aria-label="全选当前页" style={{ accentColor: 'var(--accent)' }} />
                全选当前页
              </label>
              {meetings.map((meeting) => (
                <HistoryMeetingRow
                  key={meeting.id}
                  meeting={meeting}
                  trashView={view === 'trash'}
                  selected={selected.has(meeting.id)}
                  deleting={deleting === meeting.id}
                  onOpen={() => handleOpen(meeting.id)}
                  onToggle={() => toggleOne(meeting.id)}
                  onDelete={(event) => void handleDelete(meeting.id, event)}
                  onRestore={(event) => void handleRestore(meeting.id, event)}
                />
              ))}
              <HistoryPagination
                page={page}
                totalPages={totalPages}
                pageSize={pageSize}
                onPageChange={setPage}
                onPageSizeChange={(size) => {
                  setPageSize(size)
                  setPage(0)
                }}
              />
            </div>
          )}
        </div>
      </main>
    </div>
  )
}

function LoadError({ error }: { error: unknown }) {
  const unauthorized = error instanceof ApiError && error.status === 401
  return (
    <div className="flex flex-col items-start gap-2 rounded-lg px-4 py-3 text-[12px]" style={{ background: 'var(--danger-soft)', color: 'var(--danger-text)' }}>
      <p className="font-medium">{unauthorized ? '登录状态已失效' : '加载失败'}</p>
      <p style={{ color: 'var(--text-secondary)' }}>
        {unauthorized
          ? '登录凭证已过期，请重新登录后查看历史会议。'
          : error instanceof Error
            ? error.message
            : '未知错误'}
      </p>
      {unauthorized && (
        <Link to="/login" className="btn mt-1 no-underline" style={{ height: 28 }}>
          重新登录
        </Link>
      )}
    </div>
  )
}
