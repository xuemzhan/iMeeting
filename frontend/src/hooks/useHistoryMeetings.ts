import {
  useEffect,
  useMemo,
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

  const dateFrom = useMemo(() => {
    if (range === '7d') {
      return new Date(Date.now() - 7 * 86400000).toISOString().slice(0, 10)
    }
    if (range === '30d') {
      return new Date(Date.now() - 30 * 86400000).toISOString().slice(0, 10)
    }
    return undefined
  }, [range])

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
    meetings.length > 0 && meetings.every((meeting) => selected.has(meeting.id))

  // 删除/彻底删除后当前页可能为空，自动回退一页
  useEffect(() => {
    if (!isLoading && meetings.length === 0 && total > 0 && page > 0) {
      setPage((current) => Math.max(0, current - 1))
    }
  }, [isLoading, meetings.length, total, page])

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
