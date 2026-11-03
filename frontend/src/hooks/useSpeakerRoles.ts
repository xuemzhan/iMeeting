/** 参会人重要程度：读取、乐观更新与提交。 */

import { useCallback, useEffect, useMemo, useState } from 'react'

import { meetingApi } from '../api/client'
import type { MeetingSpeaker, Segment } from '../api/types'
import {
  IMPORTANCE_NORMAL,
  importanceMapFromApi,
  normalizeState,
  speakerRowsFromApi,
  speakerRowsFromSegments,
  stateMapFromApi,
  type SpeakerRow,
} from '../lib/speakerRoles'

/** 空数组常量：`initial ?? []` 每次渲染都会新建引用，会破坏下游 memo。 */
const NO_SPEAKERS: MeetingSpeaker[] = []

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
    initial ?? NO_SPEAKERS,
  )
  // 只存「用户改过的键」：基线来自接口返回值或详情页数据，覆盖层单独记。
  // 详情数据晚到时因此不需要在 effect 里补种（react-hooks/set-state-in-effect）。
  const [importanceOverrides, setImportanceOverrides] = useState<
    Record<string, number>
  >({})
  const [stateOverrides, setStateOverrides] = useState<Record<string, number>>(
    {},
  )
  const [error, setError] = useState<string | null>(null)

  /** 接口结果为空时退回详情页给的 initial，避免首屏没有行。 */
  const baseSpeakers =
    apiSpeakers.length > 0 ? apiSpeakers : (initial ?? NO_SPEAKERS)
  const baseImportance = useMemo(
    () => importanceMapFromApi(baseSpeakers),
    [baseSpeakers],
  )
  const baseState = useMemo(
    () => stateMapFromApi(baseSpeakers),
    [baseSpeakers],
  )
  // 覆盖层在后展开：界面上的改动优先于服务端基线
  const importanceByKey = useMemo(
    () => ({ ...baseImportance, ...importanceOverrides }),
    [baseImportance, importanceOverrides],
  )
  const stateByKey = useMemo(
    () => ({ ...baseState, ...stateOverrides }),
    [baseState, stateOverrides],
  )

  const adopt = useCallback((list: MeetingSpeaker[]) => {
    // 服务端返回值即权威：换基线，同时清空覆盖层
    setApiSpeakers(list)
    setImportanceOverrides({})
    setStateOverrides({})
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


  const speakers = useMemo(
    () =>
      segments
        ? speakerRowsFromSegments(segments, importanceByKey, stateByKey)
        : speakerRowsFromApi(baseSpeakers, importanceByKey, stateByKey),
    [segments, baseSpeakers, importanceByKey, stateByKey],
  )

  const setImportance = useCallback(
    async (row: SpeakerRow, importance: number) => {
      if (meetingId == null) return
      const value =
        importance === 1 || importance === 2 || importance === 3
          ? importance
          : IMPORTANCE_NORMAL
      // 先改界面，不等网络往返；失败就删掉覆盖值，回到服务端基线
      setImportanceOverrides((map) => ({ ...map, [row.key]: value }))
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
        setImportanceOverrides((map) => {
          const next = { ...map }
          delete next[row.key]
          return next
        })
        setError(err instanceof Error ? err.message : '设置参会人重要程度失败')
      }
    },
    [meetingId, adopt],
  )

  const setState = useCallback(
    async (row: SpeakerRow, state: number) => {
      if (meetingId == null) return
      const value = normalizeState(state)
      // 乐观：该行立刻从面板消失；失败同样靠删覆盖值放回来
      setStateOverrides((map) => ({ ...map, [row.key]: value }))
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
        setStateOverrides((map) => {
          const next = { ...map }
          delete next[row.key]
          return next
        })
        setError(err instanceof Error ? err.message : '参会人操作失败')
      }
    },
    [meetingId, adopt],
  )

  return { speakers, importanceByKey, setImportance, setState, error }
}
