import { historySelectStyle } from './historyStyles'

interface Props {
  page: number
  totalPages: number
  pageSize: number
  onPageChange: (page: number) => void
  onPageSizeChange: (pageSize: number) => void
}

export function HistoryPagination({
  page,
  totalPages,
  pageSize,
  onPageChange,
  onPageSizeChange,
}: Props) {
  return (
    <div className="flex flex-wrap items-center justify-end gap-2 pt-2 text-[11px] text-tertiary">
      <button
        type="button"
        className="btn btn-ghost text-[11px]"
        disabled={page === 0}
        onClick={() => onPageChange(Math.max(0, page - 1))}
      >
        上一页
      </button>
      <span>
        第 {page + 1} / {totalPages} 页
      </span>
      <button
        type="button"
        className="btn btn-ghost text-[11px]"
        disabled={page + 1 >= totalPages}
        onClick={() => onPageChange(page + 1)}
      >
        下一页
      </button>
      <select
        value={pageSize}
        onChange={(e) => onPageSizeChange(Number(e.target.value))}
        aria-label="每页数量"
        className="rounded px-1.5 py-1 text-[11px]"
        style={historySelectStyle}
      >
        <option value={20}>20 / 页</option>
        <option value={50}>50 / 页</option>
        <option value={100}>100 / 页</option>
      </select>
    </div>
  )
}
