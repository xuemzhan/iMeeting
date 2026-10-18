import { Download, Loader2, RotateCcw, Tag, Trash2 } from 'lucide-react'

import { historySelectStyle } from './historyStyles'

type BulkAction = 'none' | 'rename' | 'tag'

interface Props {
  selectedCount: number
  trashView: boolean
  busy: boolean
  exportFormat: string
  exportStatus: string | null
  action: BulkAction
  renameFind: string
  renameReplace: string
  renamePrefix: string
  renameSuffix: string
  tagInput: string
  onCancelSelection: () => void
  onExportFormatChange: (value: string) => void
  onExport: () => void
  onToggleRename: () => void
  onToggleTag: () => void
  onArchive: (archived: boolean) => void
  onBulkTrash: () => void
  onBulkRestore: () => void
  onBulkPurge: () => void
  onRenameFindChange: (value: string) => void
  onRenameReplaceChange: (value: string) => void
  onRenamePrefixChange: (value: string) => void
  onRenameSuffixChange: (value: string) => void
  onApplyRename: () => void
  onTagInputChange: (value: string) => void
  onApplyTags: () => void
}

export function HistoryBulkActions({
  selectedCount,
  trashView,
  busy,
  exportFormat,
  exportStatus,
  action,
  renameFind,
  renameReplace,
  renamePrefix,
  renameSuffix,
  tagInput,
  onCancelSelection,
  onExportFormatChange,
  onExport,
  onToggleRename,
  onToggleTag,
  onArchive,
  onBulkTrash,
  onBulkRestore,
  onBulkPurge,
  onRenameFindChange,
  onRenameReplaceChange,
  onRenamePrefixChange,
  onRenameSuffixChange,
  onApplyRename,
  onTagInputChange,
  onApplyTags,
}: Props) {
  return (
    <div
      className="mb-3 rounded-lg px-3 py-2"
      style={{ background: 'var(--accent-soft)', color: 'var(--accent-text)' }}
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[12px]">已选 {selectedCount} 场</span>
        <button
          type="button"
          onClick={onCancelSelection}
          className="text-[11px] underline-offset-2 hover:underline"
        >
          取消选择
        </button>
        {!trashView ? (
          <>
            <select
              value={exportFormat}
              onChange={(e) => onExportFormatChange(e.target.value)}
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
            <button
              type="button"
              onClick={onExport}
              disabled={busy}
              className="btn btn-ghost text-[11px]"
            >
              <Download size={12} /> {exportStatus ?? '导出'}
            </button>
            <button
              type="button"
              onClick={onToggleRename}
              className="btn btn-ghost text-[11px]"
            >
              批量重命名
            </button>
            <button
              type="button"
              onClick={onToggleTag}
              className="btn btn-ghost text-[11px]"
            >
              <Tag size={12} /> 标签
            </button>
            <button
              type="button"
              onClick={() => onArchive(true)}
              disabled={busy}
              className="btn btn-ghost text-[11px]"
            >
              归档
            </button>
            <button
              type="button"
              onClick={() => onArchive(false)}
              disabled={busy}
              className="btn btn-ghost text-[11px]"
            >
              取消归档
            </button>
            <button
              type="button"
              onClick={onBulkTrash}
              disabled={busy}
              className="btn btn-primary ml-auto"
              style={{ height: 28, padding: '0 10px' }}
            >
              {busy ? <Loader2 size={12} className="animate-spin" /> : <Trash2 size={12} />}
              移入回收站
            </button>
          </>
        ) : (
          <>
            <button
              type="button"
              onClick={onBulkRestore}
              disabled={busy}
              className="btn btn-primary ml-auto"
              style={{ height: 28, padding: '0 10px' }}
            >
              <RotateCcw size={12} /> 恢复
            </button>
            <button
              type="button"
              onClick={onBulkPurge}
              disabled={busy}
              className="btn btn-ghost text-[11px]"
              style={{ color: 'var(--danger-text)' }}
            >
              <Trash2 size={12} /> 彻底删除
            </button>
          </>
        )}
      </div>
      {action === 'rename' && !trashView && (
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          <input
            value={renameFind}
            onChange={(e) => onRenameFindChange(e.target.value)}
            placeholder="查找"
            className="w-[110px] rounded px-1.5 py-1 text-[11px]"
            style={historySelectStyle}
          />
          <input
            value={renameReplace}
            onChange={(e) => onRenameReplaceChange(e.target.value)}
            placeholder="替换为"
            className="w-[110px] rounded px-1.5 py-1 text-[11px]"
            style={historySelectStyle}
          />
          <input
            value={renamePrefix}
            onChange={(e) => onRenamePrefixChange(e.target.value)}
            placeholder="前缀"
            className="w-[90px] rounded px-1.5 py-1 text-[11px]"
            style={historySelectStyle}
          />
          <input
            value={renameSuffix}
            onChange={(e) => onRenameSuffixChange(e.target.value)}
            placeholder="后缀"
            className="w-[90px] rounded px-1.5 py-1 text-[11px]"
            style={historySelectStyle}
          />
          <button
            type="button"
            onClick={onApplyRename}
            disabled={busy}
            className="btn btn-primary text-[11px]"
            style={{ height: 26 }}
          >
            应用
          </button>
        </div>
      )}
      {action === 'tag' && !trashView && (
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          <input
            value={tagInput}
            onChange={(e) => onTagInputChange(e.target.value)}
            placeholder="标签，多个用逗号分隔"
            className="w-[220px] rounded px-1.5 py-1 text-[11px]"
            style={historySelectStyle}
          />
          <button
            type="button"
            onClick={onApplyTags}
            disabled={busy}
            className="btn btn-primary text-[11px]"
            style={{ height: 26 }}
          >
            添加标签
          </button>
        </div>
      )}
    </div>
  )
}
