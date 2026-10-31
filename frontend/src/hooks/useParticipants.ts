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

export function useParticipants(
  meetingId: number | null,
  { pollMs = 0 }: Options = {},
): ParticipantsController {
  const [items, setItems] = useState<MeetingParticipantItem[]>([])
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const mounted = useRef(true)

  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
    }
  }, [])

  const reload = useCallback(async () => {
    if (meetingId == null) {
      setItems([])
      return
    }
    setLoading(true)
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
      if (mounted.current) setLoading(false)
    }
  }, [meetingId])

  useEffect(() => {
    void reload()
    if (meetingId == null || pollMs <= 0) return
    const timer = window.setInterval(() => void reload(), pollMs)
    return () => window.clearInterval(timer)
  }, [meetingId, pollMs, reload])

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
      if (result.skipped) {
        setError(
          result.skipped === 'auto_limit'
            ? '本场自动抽取次数已用完，稍后再试或手动确认现有参会人'
            : '转写内容不足，暂无可抽取的信息',
        )
      }
      await reload()
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

  return { items, loading, busy, error, reload, extract, add, setState, rename, bind, confirmAll, editDetails, merge, clearError }
}
