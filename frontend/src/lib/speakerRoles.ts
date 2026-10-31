/** 说话人重要程度：把接口 / 片段两种来源整理成同一份可渲染的行。 */

import type { MeetingSpeaker, Segment } from '../api/types'

/** 重要程度：1 关键（最高）| 2 次要 | 3 一般 */
export const IMPORTANCE_KEY = 1
export const IMPORTANCE_SECONDARY = 2
export const IMPORTANCE_NORMAL = 3

/** 参会人处理状态：0 未处理（面板显示）| 1 已确认 | 2 已删除（后两者不显示） */
export const STATE_VISIBLE = 0
export const STATE_CONFIRMED = 1
export const STATE_DELETED = 2

export interface ImportanceOption {
  value: number
  label: string
  hint: string
}

export const IMPORTANCE_OPTIONS: ImportanceOption[] = [
  {
    value: IMPORTANCE_KEY,
    label: '关键',
    hint: '关键参会人（1，最高）：纪要主干按此人总结',
  },
  {
    value: IMPORTANCE_SECONDARY,
    label: '次要',
    hint: '次要参会人（2）：作为关键参会人的补充',
  },
  {
    value: IMPORTANCE_NORMAL,
    label: '一般',
    hint: '一般参会人（3）：只作背景与旁证',
  },
]

export interface SpeakerRow {
  /** 稳定键：优先 speaker_key，历史数据退化为 label:<显示名> */
  key: string
  speaker: string
  speakerKey: string | null
  segments: number
  speech_ms: number
  importance: number
  /** 处理状态：0 未处理（显示）| 1 已确认 | 2 已删除（后两者不显示） */
  state: number
}

export function rowKey(
  speakerKey: string | null | undefined,
  speaker: string | null | undefined,
): string {
  if (speakerKey) return speakerKey
  const label = (speaker ?? '').trim()
  return label ? `label:${label}` : ''
}

export function normalizeImportance(value: unknown): number {
  const num = typeof value === 'number' ? value : Number(value)
  return num === 1 || num === 2 || num === 3 ? num : IMPORTANCE_NORMAL
}

/** 把任意输入收敛到 0/1/2；非法值按「未处理」处理。 */
export function normalizeState(value: unknown): number {
  const num = typeof value === 'number' ? value : Number(value)
  return num === STATE_CONFIRMED || num === STATE_DELETED
    ? num
    : STATE_VISIBLE
}

/** 接口返回的说话人清单 → 重要程度映射（键与 rowKey 保持一致）。 */
export function importanceMapFromApi(
  speakers: MeetingSpeaker[] | null | undefined,
): Record<string, number> {
  const map: Record<string, number> = {}
  for (const item of speakers ?? []) {
    const key = rowKey(item.speaker_key, item.speaker)
    if (key) map[key] = normalizeImportance(item.importance)
  }
  return map
}

/** 接口返回的说话人清单 → 处理状态映射（0 未处理 / 1 已确认 / 2 已删除）。 */
export function stateMapFromApi(
  speakers: MeetingSpeaker[] | null | undefined,
): Record<string, number> {
  const map: Record<string, number> = {}
  for (const item of speakers ?? []) {
    const key = rowKey(item.speaker_key, item.speaker)
    if (key) map[key] = normalizeState(item.state ?? STATE_VISIBLE)
  }
  return map
}

export function speakerRowsFromApi(
  speakers: MeetingSpeaker[] | null | undefined,
  overrides: Record<string, number> = {},
  stateByKey: Record<string, number> = {},
): SpeakerRow[] {
  const rows: SpeakerRow[] = []
  for (const item of speakers ?? []) {
    const key = rowKey(item.speaker_key, item.speaker)
    if (!key) continue
    const state = normalizeState(
      stateByKey[key] ?? item.state ?? STATE_VISIBLE,
    )
    if (state !== STATE_VISIBLE) continue
    rows.push({
      key,
      speaker: item.speaker || item.speaker_key || key,
      speakerKey: item.speaker_key ?? null,
      segments: item.segments ?? 0,
      speech_ms: item.speech_ms ?? 0,
      importance: normalizeImportance(
        overrides[key] ?? item.importance ?? IMPORTANCE_NORMAL,
      ),
      state,
    })
  }
  return sortSpeakerRows(rows)
}

/** 实时会话里按片段聚合说话人；重要程度与处理状态来自接口映射。 */
export function speakerRowsFromSegments(
  segments: Segment[],
  importanceByKey: Record<string, number> = {},
  stateByKey: Record<string, number> = {},
): SpeakerRow[] {
  const map = new Map<string, SpeakerRow>()
  for (const segment of segments) {
    if (!segment.isFinal) continue
    if (!segment.speaker && !segment.speakerKey) continue
    const key = rowKey(segment.speakerKey, segment.speaker)
    if (!key) continue
    const existing = map.get(key)
    const speech = Math.max(0, (segment.endMs ?? 0) - (segment.startMs ?? 0))
    if (existing) {
      existing.segments += 1
      existing.speech_ms += speech
      if (segment.speaker) existing.speaker = segment.speaker
    } else {
      map.set(key, {
        key,
        speaker: segment.speaker || key,
        speakerKey: segment.speakerKey ?? null,
        segments: 1,
        speech_ms: speech,
        importance: IMPORTANCE_NORMAL,
        state: STATE_VISIBLE,
      })
    }
  }
  const rows = [...map.values()]
  for (const row of rows) {
    row.importance = normalizeImportance(
      importanceByKey[row.key] ?? IMPORTANCE_NORMAL,
    )
    row.state = normalizeState(stateByKey[row.key] ?? STATE_VISIBLE)
  }
  // 确认 / 删除过的说话人不再出现在参会人面板里
  return sortSpeakerRows(rows.filter((row) => row.state === STATE_VISIBLE))
}

/** 重要程度升序（1 关键在前）→ 发言时长降序 → 姓名。 */
export function sortSpeakerRows(rows: SpeakerRow[]): SpeakerRow[] {
  return [...rows].sort(
    (a, b) =>
      a.importance - b.importance ||
      b.speech_ms - a.speech_ms ||
      a.speaker.localeCompare(b.speaker),
  )
}
