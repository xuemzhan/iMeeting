import { describe, expect, it } from 'vitest'

import type { MeetingSpeaker, Segment } from '../api/types'
import {
  IMPORTANCE_KEY,
  IMPORTANCE_NORMAL,
  IMPORTANCE_SECONDARY,
  STATE_CONFIRMED,
  STATE_DELETED,
  STATE_VISIBLE,
  importanceMapFromApi,
  normalizeImportance,
  normalizeState,
  rowKey,
  speakerRowsFromApi,
  speakerRowsFromSegments,
  sortSpeakerRows,
  stateMapFromApi,
} from './speakerRoles'

function segment(overrides: Partial<Segment>): Segment {
  return {
    segmentId: 's1',
    text: '内容',
    isFinal: true,
    startMs: 0,
    endMs: 1000,
    speaker: '张三',
    seq: 0,
    ...overrides,
  }
}

describe('rowKey', () => {
  it('优先用 speaker_key（改名不变）', () => {
    expect(rowKey('spk_a', '张三')).toBe('spk_a')
    expect(rowKey(null, '张三')).toBe('label:张三')
    expect(rowKey(undefined, '  ')).toBe('')
  })
})

describe('normalizeImportance', () => {
  it('只接受 1/2/3，其余按一般处理', () => {
    expect(normalizeImportance(1)).toBe(IMPORTANCE_KEY)
    expect(normalizeImportance('2')).toBe(IMPORTANCE_SECONDARY)
    expect(normalizeImportance(3)).toBe(IMPORTANCE_NORMAL)
    for (const bad of [0, 4, -1, null, undefined, 'x']) {
      expect(normalizeImportance(bad)).toBe(IMPORTANCE_NORMAL)
    }
  })
})

describe('importanceMapFromApi', () => {
  it('键与 rowKey 一致，缺省值按一般', () => {
    const list: MeetingSpeaker[] = [
      { speaker: '张三', speaker_key: 'spk_a', importance: 1, segments: 2, speech_ms: 5000 },
      { speaker: '李四', speaker_key: 'label:李四', importance: 2, segments: 1, speech_ms: 1000 },
      { speaker: '王五', speaker_key: 'spk_c', importance: 99, segments: 1, speech_ms: 1000 },
    ]
    expect(importanceMapFromApi(list)).toEqual({
      spk_a: 1,
      'label:李四': 2,
      spk_c: 3,
    })
  })
})

describe('speakerRowsFromSegments', () => {
  it('按说话人聚合片段数与发言时长', () => {
    const rows = speakerRowsFromSegments([
      segment({ segmentId: 'a', startMs: 0, endMs: 2000 }),
      segment({ segmentId: 'b', startMs: 2000, endMs: 5000 }),
      segment({ segmentId: 'c', speaker: '李四', startMs: 5000, endMs: 6000 }),
    ])
    expect(rows.map((r) => [r.speaker, r.segments, r.speech_ms])).toEqual([
      ['张三', 2, 5000],
      ['李四', 1, 1000],
    ])
  })

  it('忽略草稿片段与无说话人片段', () => {
    const rows = speakerRowsFromSegments([
      segment({ isFinal: false }),
      segment({ speaker: null }),
    ])
    expect(rows).toEqual([])
  })

  it('重要程度来自映射：关键排最前，即使发言更短', () => {
    const rows = speakerRowsFromSegments(
      [
        segment({ segmentId: 'a', speaker: '张三', endMs: 9000 }),
        segment({ segmentId: 'b', speaker: '李四', endMs: 1000 }),
      ],
      { 'label:李四': IMPORTANCE_KEY },
    )
    expect(rows[0]).toMatchObject({ speaker: '李四', importance: IMPORTANCE_KEY })
    expect(rows[1]).toMatchObject({ speaker: '张三', importance: IMPORTANCE_NORMAL })
  })

  it('同名说话人在 speaker_key 缺省时合并为一行', () => {
    const rows = speakerRowsFromSegments([
      segment({ segmentId: 'a', speaker: '张三' }),
      segment({ segmentId: 'b', speaker: '张三' }),
    ])
    expect(rows).toHaveLength(1)
    expect(rows[0].segments).toBe(2)
  })
})

describe('speakerRowsFromApi', () => {
  it('覆盖值优先于接口值，并按重要程度排序', () => {
    const api: MeetingSpeaker[] = [
      { speaker: '张三', speaker_key: 'spk_a', importance: 3, segments: 1, speech_ms: 9000 },
      { speaker: '李四', speaker_key: 'spk_b', importance: 2, segments: 1, speech_ms: 1000 },
    ]
    const rows = speakerRowsFromApi(api, { spk_a: IMPORTANCE_KEY })
    expect(rows.map((r) => [r.speaker, r.importance])).toEqual([
      ['张三', IMPORTANCE_KEY],
      ['李四', IMPORTANCE_SECONDARY],
    ])
  })

  it('接口缺字段时不崩，按默认值补齐', () => {
    const rows = speakerRowsFromApi([
      { speaker: '张三', speaker_key: '', importance: 1 } as MeetingSpeaker,
    ])
    expect(rows).toHaveLength(1)
    expect(rows[0]).toMatchObject({
      key: 'label:张三',
      segments: 0,
      speech_ms: 0,
    })
  })
})

describe('sortSpeakerRows', () => {
  it('重要性相同时按时长降序，再按姓名', () => {
    const rows = sortSpeakerRows([
      { key: 'b', speaker: '李四', speakerKey: null, segments: 1, speech_ms: 1000, importance: 3, state: STATE_VISIBLE },
      { key: 'a', speaker: '张三', speakerKey: null, segments: 1, speech_ms: 5000, importance: 3, state: STATE_VISIBLE },
      { key: 'c', speaker: '王五', speakerKey: null, segments: 1, speech_ms: 1000, importance: 1, state: STATE_VISIBLE },
    ])
    expect(rows.map((r) => r.speaker)).toEqual(['王五', '张三', '李四'])
  })
})

describe('normalizeState', () => {
  it('只接受 1/2，其余按未处理', () => {
    expect(normalizeState(STATE_CONFIRMED)).toBe(STATE_CONFIRMED)
    expect(normalizeState('2')).toBe(STATE_DELETED)
    expect(normalizeState(0)).toBe(STATE_VISIBLE)
    expect(normalizeState('x')).toBe(STATE_VISIBLE)
    expect(normalizeState(null)).toBe(STATE_VISIBLE)
  })
})

describe('参会人处理状态', () => {
  it('确认过的说话人不再出现在片段聚合的行里', () => {
    const rows = speakerRowsFromSegments(
      [
        segment({ segmentId: 'a', speaker: '张三' }),
        segment({ segmentId: 'b', speaker: '李四' }),
      ],
      {},
      { 'label:张三': STATE_CONFIRMED },
    )
    expect(rows.map((r) => r.speaker)).toEqual(['李四'])
  })

  it('删除过的说话人不再出现在接口清单里', () => {
    const api: MeetingSpeaker[] = [
      {
        speaker: '张三',
        speaker_key: 'spk_a',
        importance: 3,
        state: STATE_DELETED,
        segments: 1,
        speech_ms: 9000,
      },
      {
        speaker: '李四',
        speaker_key: 'spk_b',
        importance: 3,
        state: STATE_VISIBLE,
        segments: 1,
        speech_ms: 1000,
      },
    ]
    expect(speakerRowsFromApi(api).map((r) => r.speaker)).toEqual(['李四'])
  })

  it('stateMapFromApi 按 rowKey 归一，缺字段按未处理', () => {
    expect(
      stateMapFromApi([
        {
          speaker: '张三',
          speaker_key: 'spk_a',
          importance: 3,
          state: STATE_CONFIRMED,
          segments: 1,
          speech_ms: 1,
        },
        {
          speaker: '李四',
          speaker_key: '',
          importance: 3,
          segments: 1,
          speech_ms: 1,
        } as MeetingSpeaker,
      ]),
    ).toEqual({ spk_a: STATE_CONFIRMED, 'label:李四': STATE_VISIBLE })
  })
})
