import { useState } from 'react'
import { Search, SlidersHorizontal, X } from 'lucide-react'

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

/**
 * 历史会议的筛选行。
 *
 * 桌面（>=768）：所有筛选项一行铺开，与改造前一致（容器用 `md:contents`
 * 让四个控件继续作为同一行的 flex 子项，视觉与 DOM 顺序都不变）。
 * 手机（<=767）：只留搜索框 +「筛选」按钮，其余四项收进可折叠面板 ——
 * 否则 360px 下这一行会折成三行，把列表挤到首屏之外。
 */
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
  const [open, setOpen] = useState(false)
  // 非默认筛选的个数：手机上用它提示「还有筛选在生效」
  const activeCount =
    (status ? 1 : 0) + (archived !== 'false' ? 1 : 0) + (range !== 'all' ? 1 : 0) + (tag ? 1 : 0)

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
          className="w-full rounded-lg py-2 pl-8 pr-8 text-ui outline-none"
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

      {/* 手机：折叠开关 */}
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        aria-controls="history-filter-panel"
        className="btn btn-ghost shrink-0 text-ui md:hidden"
      >
        <SlidersHorizontal size={14} />
        筛选
        {activeCount > 0 && (
          <span className="count-badge" style={{ background: 'var(--accent-soft)', color: 'var(--accent-text)' }}>
            {activeCount}
          </span>
        )}
      </button>

      <div
        id="history-filter-panel"
        className={
          open
            ? 'flex w-full flex-wrap items-center gap-2 md:contents'
            : 'hidden md:contents'
        }
      >
        <select
          value={status}
          onChange={(e) => onStatusChange(e.target.value)}
          aria-label="按状态筛选"
          className="rounded-lg px-2 py-2 text-ui outline-none"
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
          className="rounded-lg px-2 py-2 text-ui outline-none"
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
          className="rounded-lg px-2 py-2 text-ui outline-none"
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
          className="w-[110px] rounded-lg px-2 py-2 text-ui outline-none"
          style={historySelectStyle}
        />
      </div>
    </div>
  )
}
