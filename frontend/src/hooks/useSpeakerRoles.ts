/** 参会人重要程度：读取、乐观更新与提交。 */

import { useCallback, useEffect, useMemo, useState } from 'react'

import { meetingApi } from '../api/client'
import type { MeetingSpeaker, Segment } from '../api/types'
import {
  IMPORTANCE_NORMAL,
  STATE_VISIBLE,
  importanceMapFromApi,
  normalizeState,
  speakerRowsFromApi,
  speakerRowsFromSegments,
  stateMapFromApi,
  type SpeakerRow,
} from '../lib/speakerRoles'

interface Options {
  /** 实时页传片段：说话人清单直接从字幕聚合，新说话人立刻出现 */
  segments?: Segment[]
  /** 详情页可先传接口返回的清单，避免首屏闪烁 */
  initial?: MeetingSpeaker[]
  enabled?: boolean
}

export interface SpeakerRoleController {
  speakers: SpeakerRow[]
  importanceByKey: Record<string, number>
  setImportance: (row: SpeakerRow, importance: number) => Promise<void>
  /**
   * 确认（1）/ 删除（2）一位说话人。
   *
   * 两者都只是标记：转写片段与重要程度都不动，只是该行立刻从面板消失
   * （刷新后依然不显示，状态落在 speaker_role.state 上）。
   */
  setState: (row: SpeakerRow, state: number) => Promise<void>
  error: string | null
}

export function useSpeakerRoles(
  meetingId: number | null,
  options: Options = {},
): SpeakerRoleController {
  const { segments, initial, enabled = true } = options
  const [apiSpeakers, setApiSpeakers] = useState<MeetingSpeaker[]>(
    initial ?? [],
  )
  const [importanceByKey, setImportanceByKey] = useState<
    Record<string, number>
  >(() => importanceMapFromApi(initial))
  const [stateByKey, setStateByKey] = useState<Record<string, number>>(() =>
    stateMapFromApi(initial),
  )
  const [error, setError] = useState<string | null>(null)

  const adopt = useCallback((list: MeetingSpeaker[]) => {
    setApiSpeakers(list)
    setImportanceByKey(importanceMapFromApi(list))
    setStateByKey(stateMapFromApi(list))
  }, [])

  // 拉一次接口：拿到重要程度、处理状态与发言统计（实时页的行由片段聚合）
  useEffect(() => {
    if (!enabled || meetingId == null) return
    let cancelled = false
    meetingApi
      .speakers(meetingId)
      .then((list) => {
        if (cancelled) return
        adopt(list)
      })
      .catch((err: unknown) => {
        if (cancelled) return
        setError(err instanceof Error ? err.message : '读取参会人失败')
      })
    return () => {
      cancelled = true
    }
  }, [meetingId, enabled, adopt])

  // 详情数据晚到：补种一次（同一引用只执行一次）
  useEffect(() => {
    if (!initial || initial.length === 0) return
    adopt(initial)
  }, [initial, adopt])

  const speakers = useMemo(
    () =>
      segments
        ? speakerRowsFromSegments(segments, importanceByKey, stateByKey)
        : speakerRowsFromApi(apiSpeakers, importanceByKey, stateByKey),
    [segments, apiSpeakers, importanceByKey, stateByKey],
  )

  const setImportance = useCallback(
    async (row: SpeakerRow, importance: number) => {
      if (meetingId == null) return
      const value =
        importance === 1 || importance === 2 || importance === 3
          ? importance
          : IMPORTANCE_NORMAL
      const previous = importanceByKey[row.key]
      // 先改界面，不等网络往返；失败再回滚
      setImportanceByKey((map) => ({ ...map, [row.key]: value }))
      setError(null)
      try {
        const list = await meetingApi.setSpeakerImportance(
          meetingId,
          row.speaker,
          value,
          row.speakerKey,
        )
        adopt(list)
      } catch (err) {
        setImportanceByKey((map) => ({
          ...map,
          [row.key]: previous ?? IMPORTANCE_NORMAL,
        }))
        setError(err instanceof Error ? err.message : '设置参会人重要程度失败')
      }
    },
    [meetingId, importanceByKey, adopt],
  )

  const setState = useCallback(
    async (row: SpeakerRow, state: number) => {
      if (meetingId == null) return
      const value = normalizeState(state)
      const previous = stateByKey[row.key] ?? STATE_VISIBLE
      // 乐观：该行立刻从面板消失；失败再放回来
      setStateByKey((map) => ({ ...map, [row.key]: value }))
      setError(null)
      try {
        const list = await meetingApi.setSpeakerState(
          meetingId,
          row.speaker,
          value,
          row.speakerKey,
        )
        adopt(list)
      } catch (err) {
        setStateByKey((map) => ({ ...map, [row.key]: previous }))
        setError(err instanceof Error ? err.message : '参会人操作失败')
      }
    },
    [meetingId, stateByKey, adopt],
  )

  return { speakers, importanceByKey, setImportance, setState, error }
}
