import {
  useEffect,
  useState,
  type Dispatch,
  type MouseEvent,
  type SetStateAction,
} from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'

import { meetingApi } from '../api/client'
import type { MeetingSearchResult } from '../api/types'
import { useAuth } from '../store/auth'

interface Options {
  setOpNotice: Dispatch<SetStateAction<string | null>>
}

export function useHistoryMeetings({ setOpNotice }: Options) {
  const qc = useQueryClient()
  const [view, setView] = useState<'active' | 'trash'>('active')
  const [deleting, setDeleting] = useState<number | null>(null)
  const [q, setQ] = useState('')
  const [debouncedQ, setDebouncedQ] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [range, setRange] = useState('all')
  const [archivedFilter, setArchivedFilter] = useState('false')
  const [tagFilter, setTagFilter] = useState('')
  const [page, setPage] = useState(0)
  const [pageSize, setPageSize] = useState(20)
  const [selected, setSelected] = useState<Set<number>>(new Set())

  const hasToken = useAuth((state) => state.hasToken)
  const needsLogin = !hasToken()

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedQ(q.trim()), 300)
    return () => window.clearTimeout(timer)
  }, [q])

  const rangeStart = (value: string): string | undefined => {
    if (value !== '7d' && value !== '30d') return undefined
    const days = value === '7d' ? 7 : 30
    return new Date(Date.now() - days * 86400000).toISOString().slice(0, 10)
  }

  /** 列表查询键：`refresh` 里要按同一条串读缓存，抽出来避免两处漂移。 */
  const listKey = (targetPage: number) =>
    ['meetings', view, debouncedQ, statusFilter, range, archivedFilter, tagFilter, targetPage, pageSize] as const

  const { data, isLoading, error } = useQuery({
    queryKey: listKey(page),
    queryFn: () => {
      // 日期范围在取数时计算：渲染期调用 Date.now() 既是不纯的，也会让
      // useMemo 的依赖与实际结果脱节。放进 queryFn 后，key 里用 range 即可。
      const dateFrom = rangeStart(range)
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
    meetings.length > 0 && meetings.every((meeting) => selected.has(meeting.id))

  const toggleOne = (id: number) => {
    setSelected((previous) => {
      const next = new Set(previous)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const toggleAll = () => {
    setSelected((previous) => {
      const next = new Set(previous)
      if (allSelected) meetings.forEach((meeting) => next.delete(meeting.id))
      else meetings.forEach((meeting) => next.add(meeting.id))
      return next
    })
  }

  /**
   * 刷新列表。
   *
   * 删除/彻底删除/恢复之后当前页可能变空，这里在失效完成后回退一页。
   * 放在事件函数里而不是 effect 里：effect 体里同步 `setState` 会多一轮渲染
   * （react-hooks/set-state-in-effect），而本页所有变更都走这个函数，覆盖面
   * 与原来的 effect 等价——`queryClient` 关掉了 `refetchOnWindowFocus`，
   * 不存在「别人删了导致静默刷新」的路径；将来若打开该选项，这里要补兜底。
   */
  const refresh = async () => {
    setSelected(new Set())
    await qc.invalidateQueries({ queryKey: ['meetings'] })
    if (page === 0) return
    const current = qc.getQueryData<MeetingSearchResult>(listKey(page))
    if (current && current.items.length === 0 && current.total > 0) {
      setPage((value) => Math.max(0, value - 1))
    }
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

  const handleDelete = async (id: number, event: MouseEvent<HTMLElement>) => {
    event.stopPropagation()
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

  const handleRestore = async (id: number, event: MouseEvent<HTMLElement>) => {
    event.stopPropagation()
    await meetingApi.bulkRestore([id])
    await refresh()
  }

  const filtered =
    view === 'active' &&
    Boolean(
      debouncedQ ||
        statusFilter ||
        range !== 'all' ||
        archivedFilter !== 'false' ||
        tagFilter,
    )

  return {
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
  }
}
