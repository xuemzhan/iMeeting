import { Search, X } from 'lucide-react'

import { historySelectStyle } from './historyStyles'

interface Props {
  q: string
  status: string
  archived: string
  range: string
  tag: string
  onQChange: (value: string) => void
  onStatusChange: (value: string) => void
  onArchivedChange: (value: string) => void
  onRangeChange: (value: string) => void
  onTagChange: (value: string) => void
}

export function HistoryFilters({
  q,
  status,
  archived,
  range,
  tag,
  onQChange,
  onStatusChange,
  onArchivedChange,
  onRangeChange,
  onTagChange,
}: Props) {
  return (
    <div className="mb-3 flex flex-wrap items-center gap-2">
      <div className="relative min-w-[200px] flex-1">
        <Search
          size={13}
          className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2"
          style={{ color: 'var(--text-tertiary)' }}
        />
        <input
          value={q}
          onChange={(e) => onQChange(e.target.value)}
          placeholder="搜索标题或转写内容…"
          aria-label="搜索会议"
          className="w-full rounded-lg py-2 pl-8 pr-8 text-[12px] outline-none"
          style={{
            background: 'var(--bg-surface)',
            border: '1px solid var(--border-subtle)',
            color: 'var(--text-primary)',
          }}
        />
        {q && (
          <button
            type="button"
            onClick={() => onQChange('')}
            aria-label="清空搜索"
            className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-0.5 text-tertiary hover:text-secondary"
          >
            <X size={13} />
          </button>
        )}
      </div>
      <select
        value={status}
        onChange={(e) => onStatusChange(e.target.value)}
        aria-label="按状态筛选"
        className="rounded-lg px-2 py-2 text-[12px] outline-none"
        style={historySelectStyle}
      >
        <option value="">全部状态</option>
        <option value="scheduled">已预约</option>
        <option value="finished">已完成</option>
        <option value="abandoned">已中断</option>
        <option value="failed">异常</option>
        <option value="recording">进行中</option>
      </select>
      <select
        value={archived}
        onChange={(e) => onArchivedChange(e.target.value)}
        aria-label="归档状态"
        className="rounded-lg px-2 py-2 text-[12px] outline-none"
        style={historySelectStyle}
      >
        <option value="false">未归档</option>
        <option value="true">已归档</option>
        <option value="all">全部</option>
      </select>
      <select
        value={range}
        onChange={(e) => onRangeChange(e.target.value)}
        aria-label="按时间筛选"
        className="rounded-lg px-2 py-2 text-[12px] outline-none"
        style={historySelectStyle}
      >
        <option value="all">全部时间</option>
        <option value="7d">近 7 天</option>
        <option value="30d">近 30 天</option>
      </select>
      <input
        value={tag}
        onChange={(e) => onTagChange(e.target.value)}
        placeholder="标签筛选"
        aria-label="标签筛选"
        className="w-[110px] rounded-lg px-2 py-2 text-[12px] outline-none"
        style={historySelectStyle}
      />
    </div>
  )
}
