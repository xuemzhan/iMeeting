/** 会议参会人（REQ-001）：列表、抽取、确认/改名/绑定。
 *
 * 与声纹模板不同，这里的身份是「姓名」而非「声音」：抽取失败或未确认都不影响
 * 转写本身，只影响面板展示。
 *
 * 列表走 `useQuery`：轮询交给 `refetchInterval`，改动用 `setQueryData` 做乐观更新，
 * 因此 effect 体里不再有取数调用（`react-hooks/set-state-in-effect`）。
 */

import { useCallback, useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'

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
  const qc = useQueryClient()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const listQuery = useQuery({
    queryKey: ['participants', meetingId],
    queryFn: () => personApi.participants(meetingId as number),
    enabled: meetingId != null,
    // 轮询交给 query：不会像手写 interval 那样每来一次就把 loading 翻成 true
    refetchInterval: meetingId != null && pollMs > 0 ? pollMs : false,
  })

  const items =
    meetingId == null ? NO_ITEMS : (listQuery.data ?? NO_ITEMS)
  const loading = listQuery.isLoading
  // 取数失败也走同一个 error 出口，避免「加载失败但界面没提示」
  const queryError =
    listQuery.isError && listQuery.error instanceof Error
      ? listQuery.error.message
      : listQuery.isError
        ? '读取参会人失败'
        : null

  const reload = useCallback(async () => {
    if (meetingId == null) return
    await qc.invalidateQueries({ queryKey: ['participants', meetingId] })
  }, [meetingId, qc])
  // 订阅其它组件广播的变更（这是订阅：setState 在事件回调里，不是 effect 体）
  useEffect(() => {
    const changed = (event: Event) => {
      if ((event as CustomEvent<number>).detail === meetingId) void reload()
    }
    window.addEventListener('imeeting-participants-changed', changed)
    return () =>
      window.removeEventListener('imeeting-participants-changed', changed)
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
      if (skipped) setError(skipped)
    } catch (err) {
      setError(err instanceof Error ? err.message : '抽取参会人失败')
    } finally {
      setBusy(false)
    }
  }, [meetingId, busy, reload])

  const patch = useCallback(
    async (
      personId: number,
      body: Parameters<typeof personApi.updateParticipant>[2],
      optimistic?: (item: MeetingParticipantItem) => MeetingParticipantItem,
    ) => {
      if (meetingId == null) return
      const key = ['participants', meetingId]
      const snapshot = qc.getQueryData<MeetingParticipantItem[]>(key)
      if (optimistic && snapshot) {
        qc.setQueryData(
          key,
          snapshot.map((item) =>
            item.person_id === personId ? optimistic(item) : item,
          ),
        )
      }
      try {
        const updated = await personApi.updateParticipant(
          meetingId,
          personId,
          body,
        )
        qc.setQueryData<MeetingParticipantItem[]>(key, (current) =>
          (current ?? NO_ITEMS).map((item) =>
            item.person_id === personId ? updated : item,
          ),
        )
        setError(null)
        void qc.invalidateQueries({ queryKey: ['meeting', meetingId] })
      } catch (err) {
        if (snapshot) qc.setQueryData(key, snapshot)
        setError(err instanceof Error ? err.message : '修改参会人失败')
      }
    },
    [meetingId, qc],
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
        await qc.invalidateQueries({ queryKey: ['meeting', meetingId] })
      } catch (err) {
        setError(err instanceof Error ? err.message : '绑定说话人失败')
      }
    },
    [meetingId, reload, qc],
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
        qc.setQueryData<MeetingParticipantItem[]>(
          ['participants', meetingId],
          (current) => [
            ...(current ?? NO_ITEMS).filter(
              (item) => item.person_id !== created.person_id,
            ),
            created,
          ],
        )
        setError(null)
      } catch (err) {
        setError(err instanceof Error ? err.message : '添加参会人失败')
        throw err
      }
    },
    [meetingId, qc],
  )

  const confirmAll = async () => {
    if (meetingId == null) return
    try {
      const list = await personApi.confirmAll(meetingId)
      qc.setQueryData(['participants', meetingId], list)
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : '批量确认失败')
    }
  }
  const editDetails = async (personId: number, unit: string, title: string) => {
    await patch(personId, {
      attributes: [
        { category: 'org', key: 'unit', value: unit.trim() },
        { category: 'role', key: 'title', value: title.trim() },
      ],
    })
  }
  const merge = async (targetId: number, sourceId: number) => {
    try {
      await personApi.merge(targetId, sourceId)
      await reload()
      void qc.invalidateQueries({ queryKey: ['meeting', meetingId] })
    } catch (err) {
      setError(err instanceof Error ? err.message : '合并参会人失败')
    }
  }
  const clearError = useCallback(() => setError(null), [])

  return {
    items,
    loading,
    busy,
    error: error ?? queryError,
    reload,
    extract,
    add,
    setState,
    rename,
    bind,
    confirmAll,
    editDetails,
    merge,
    clearError,
  }
}
