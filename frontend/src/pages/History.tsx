import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import {
  ArrowLeft,
  FileText,
  Loader2,
  LogIn,
  RotateCcw,
  Trash2,
} from 'lucide-react'
import { ApiError } from '../api/client'
import { useHistoryBulkActions } from '../hooks/useHistoryBulkActions'
import { useHistoryMeetings } from '../hooks/useHistoryMeetings'
import { TopBar } from '../components/TopBar'
import { HistoryFilters } from './HistoryFilters'
import { HistoryBulkActions } from './HistoryBulkActions'
import { HistoryMeetingRow } from './HistoryMeetingRow'
import { HistoryPagination } from './HistoryPagination'

export default function History() {
  const navigate = useNavigate()
  const [opNotice, setOpNotice] = useState<string | null>(null)

  const {
    view,
    setView,
    deleting,
    q,
    setQ,
    debouncedQ,
    statusFilter,
    setStatusFilter,
    range,
    setRange,
    archivedFilter,
    setArchivedFilter,
    tagFilter,
    setTagFilter,
    page,
    setPage,
    pageSize,
    setPageSize,
    selected,
    setSelected,
    needsLogin,
    data,
    isLoading,
    error,
    meetings,
    total,
    totalPages,
    allSelected,
    toggleOne,
    toggleAll,
    refresh,
    removeFromCaches,
    handleDelete,
    handleRestore,
    filtered,
  } = useHistoryMeetings({ setOpNotice })

  const {
    busy,
    action,
    setAction,
    renameFind,
    setRenameFind,
    renameReplace,
    setRenameReplace,
    renamePrefix,
    setRenamePrefix,
    renameSuffix,
    setRenameSuffix,
    tagInput,
    setTagInput,
    exportFormat,
    setExportFormat,
    exportStatus,
    handleBulkTrash,
    handleBulkRestore,
    handleBulkPurge,
    handleArchive,
    applyRename,
    applyTags,
    exportSelected,
  } = useHistoryBulkActions({
    selected,
    refresh,
    removeFromCaches,
    setOpNotice,
  })

  useEffect(() => {
    setPage(0)
    setSelected(new Set())
    setAction('none')
  }, [
    view,
    debouncedQ,
    statusFilter,
    range,
    archivedFilter,
    tagFilter,
    setAction,
    setPage,
    setSelected,
  ])

  const handleOpen = (id: number) => {
    if (view === 'active') navigate(`/history/${id}`)
  }

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
            <HistoryBulkActions
              selectedCount={selected.size}
              trashView={view === 'trash'}
              busy={busy}
              exportFormat={exportFormat}
              exportStatus={exportStatus}
              action={action}
              renameFind={renameFind}
              renameReplace={renameReplace}
              renamePrefix={renamePrefix}
              renameSuffix={renameSuffix}
              tagInput={tagInput}
              onCancelSelection={() => setSelected(new Set())}
              onExportFormatChange={setExportFormat}
              onExport={() => void exportSelected()}
              onToggleRename={() =>
                setAction(action === 'rename' ? 'none' : 'rename')
              }
              onToggleTag={() => setAction(action === 'tag' ? 'none' : 'tag')}
              onArchive={(archived) => void handleArchive(archived)}
              onBulkTrash={() => void handleBulkTrash()}
              onBulkRestore={() => void handleBulkRestore()}
              onBulkPurge={() => void handleBulkPurge()}
              onRenameFindChange={setRenameFind}
              onRenameReplaceChange={setRenameReplace}
              onRenamePrefixChange={setRenamePrefix}
              onRenameSuffixChange={setRenameSuffix}
              onApplyRename={() => void applyRename()}
              onTagInputChange={setTagInput}
              onApplyTags={() => void applyTags()}
            />
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
