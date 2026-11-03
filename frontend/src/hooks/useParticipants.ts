/** 会议参会人（REQ-001）：列表、抽取、确认/改名/绑定。
 *
 * 与声纹模板不同，这里的身份是「姓名」而非「声音」：抽取失败或未确认都不影响
 * 转写本身，只影响面板展示。
 */

import { useCallback, useEffect, useRef, useState } from 'react'

import { queryClient } from '../app/queryClient'
import { personApi } from '../api/client'
import type { MeetingParticipantItem, ParticipantState } from '../api/types'

interface Options {
  /** >0 时按该间隔轮询（会中页面用；会议结束后传 0 只拉一次） */
  pollMs?: number
}

export interface ParticipantsController {
  items: MeetingParticipantItem[]
  loading: boolean
  busy: boolean
  error: string | null
  reload: () => Promise<void>
  extract: () => Promise<void>
  add: (name: string, speakerKey?: string | null) => Promise<void>
  setState: (personId: number, state: ParticipantState) => Promise<void>
  rename: (personId: number, name: string) => Promise<void>
  bind: (personId: number, speakerKey: string | null) => Promise<void>
  confirmAll: () => Promise<void>
  editDetails: (personId: number, unit: string, title: string) => Promise<void>
  merge: (targetId: number, sourceId: number) => Promise<void>
  clearError: () => void
}

/** meetingId 为空时对外恒为空列表：用模块级常量，避免每次渲染都返回新数组。 */
const NO_ITEMS: MeetingParticipantItem[] = []

export function useParticipants(
  meetingId: number | null,
  { pollMs = 0 }: Options = {},
): ParticipantsController {
  const [items, setItems] = useState<MeetingParticipantItem[]>([])
  // 「已经为哪个会议取完过一次数」。loading 由它派生，而不是在 effect 体里同步
  // setLoading(true)：后者会多一轮提交，派生值天然「首帧就为真」，语义相同。
  const [loadedFor, setLoadedFor] = useState<number | null>(null)
  const [refreshing, setRefreshing] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const mounted = useRef(true)

  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
    }
  }, [])

  /** 只取数、不碰 loading：effect 体里调用它不会在提交阶段同步 setState。 */
  const fetchList = useCallback(async () => {
    if (meetingId == null) return
    try {
      const list = await personApi.participants(meetingId)
      if (mounted.current) {
        setItems(list)
        setError(null)
      }
    } catch (err) {
      if (mounted.current) {
        setError(err instanceof Error ? err.message : '读取参会人失败')
      }
    } finally {
      if (mounted.current) setLoadedFor(meetingId)
    }
  }, [meetingId])

  /** 手动刷新：显式让派生出来的 loading 为真（只从事件/按钮里调用）。 */
  const reload = useCallback(async () => {
    if (meetingId == null) return
    setRefreshing(true)
    try {
      await fetchList()
    } finally {
      if (mounted.current) setRefreshing(false)
    }
  }, [meetingId, fetchList])

  useEffect(() => {
    void fetchList()
    if (meetingId == null || pollMs <= 0) return
    // 轮询每来一次不该闪一下加载态，所以走不碰 loading 的 fetchList。
    const timer = window.setInterval(() => void fetchList(), pollMs)
    return () => window.clearInterval(timer)
  }, [meetingId, pollMs, fetchList])

  // 派生而非存储：换会议时「还没取到」自然为真，也不需要在 effect 里清空。
  const loading = meetingId != null && (loadedFor !== meetingId || refreshing)

  useEffect(() => {
    const changed = (event: Event) => {
      if ((event as CustomEvent<number>).detail === meetingId) void reload()
    }
    window.addEventListener('imeeting-participants-changed', changed)
    return () => window.removeEventListener('imeeting-participants-changed', changed)
  }, [meetingId, reload])

  const extract = useCallback(async () => {
    if (meetingId == null || busy) return
    setBusy(true)
    setError(null)
    try {
      const result = await personApi.extract(meetingId)
      // 「没抽到东西」的原因必须活过这一次 reload：reload 成功会把 error 清空，
      // 之前先 setError 再 reload，用户永远看不到提示（单测抓到的真实缺陷）。
      const skipped = result.skipped
        ? result.skipped === 'auto_limit'
          ? '本场自动抽取次数已用完，稍后再试或手动确认现有参会人'
          : '转写内容不足，暂无可抽取的信息'
        : null
      await reload()
      if (skipped && mounted.current) setError(skipped)
    } catch (err) {
      setError(err instanceof Error ? err.message : '抽取参会人失败')
    } finally {
      if (mounted.current) setBusy(false)
    }
  }, [meetingId, busy, reload])

  const patch = useCallback(
    async (
      personId: number,
      body: Parameters<typeof personApi.updateParticipant>[2],
      optimistic?: (item: MeetingParticipantItem) => MeetingParticipantItem,
    ) => {
      if (meetingId == null) return
      const snapshot = items
      if (optimistic) {
        setItems((current) => current.map((item) => (item.person_id === personId ? optimistic(item) : item)))
      }
      try {
        const updated = await personApi.updateParticipant(meetingId, personId, body)
        if (mounted.current) {
          setItems((current) =>
            current.map((item) => (item.person_id === personId ? updated : item)),
          )
          setError(null)
          void queryClient.invalidateQueries({ queryKey: ['meeting', meetingId] })
        }
      } catch (err) {
        if (mounted.current) {
          setItems(snapshot)
          setError(err instanceof Error ? err.message : '修改参会人失败')
        }
      }
    },
    [meetingId, items],
  )

  const setState = useCallback(
    (personId: number, state: ParticipantState) =>
      patch(personId, { state }, (item) => ({ ...item, state })),
    [patch],
  )

  const rename = useCallback(
    (personId: number, name: string) =>
      patch(personId, { display_name: name }, (item) => ({ ...item, speaker: name })),
    [patch],
  )

  const bind = useCallback(
    async (personId: number, speakerKey: string | null) => {
      if (meetingId == null) return
      try {
        await personApi.bind(meetingId, personId, speakerKey)
        await reload()
        await queryClient.invalidateQueries({ queryKey: ['meeting', meetingId] })
      } catch (err) {
        setError(err instanceof Error ? err.message : '绑定说话人失败')
      }
    },
    [meetingId, reload],
  )
  const add = useCallback(
    async (name: string, speakerKey?: string | null) => {
      if (meetingId == null) return
      const trimmed = name.trim()
      if (!trimmed) return
      try {
        const created = await personApi.addParticipant(meetingId, {
          name: trimmed,
          speaker_key: speakerKey ?? null,
        })
        if (mounted.current) {
          setItems((current) => [...current.filter((item) => item.person_id !== created.person_id), created])
          setError(null)
        }
      } catch (err) {
        if (mounted.current) setError(err instanceof Error ? err.message : '添加参会人失败')
        throw err
      }
    },
    [meetingId],
  )

  const confirmAll = async () => {
    if (meetingId == null) return
    try { setItems(await personApi.confirmAll(meetingId)); setError(null) }
    catch (err) { setError(err instanceof Error ? err.message : '批量确认失败') }
  }
  const editDetails = async (personId: number, unit: string, title: string) => {
    await patch(personId, { attributes: [
      { category: 'org', key: 'unit', value: unit.trim() },
      { category: 'role', key: 'title', value: title.trim() },
    ] })
  }
  const merge = async (targetId: number, sourceId: number) => {
    try { await personApi.merge(targetId, sourceId); await reload(); void queryClient.invalidateQueries({ queryKey: ['meeting', meetingId] }) }
    catch (err) { setError(err instanceof Error ? err.message : '合并参会人失败') }
  }
  const clearError = useCallback(() => setError(null), [])

  return { items: meetingId == null ? NO_ITEMS : items, loading, busy, error, reload, extract, add, setState, rename, bind, confirmAll, editDetails, merge, clearError }
}
