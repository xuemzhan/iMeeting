import { describe, expect, it } from 'vitest'

import type { MeetingParticipantItem } from '../api/types'
import {
  STATE_LABELS,
  conflictLevelText,
  pendingCount,
  summarizeAttributes,
  visibleParticipants,
} from './participants'

function participant(overrides: Partial<MeetingParticipantItem>): MeetingParticipantItem {
  return {
    participant_id: 1,
    person_id: 1,
    speaker: '张三',
    speaker_key: null,
    role: '',
    importance: 3,
    state: 'pending',
    confidence: 0.8,
    ambiguous: false,
    evidence: [],
    attributes: [],
    ...overrides,
  }
}

describe('visibleParticipants', () => {
  it('隐藏已删除记录', () => {
    const rows = visibleParticipants([
      participant({ person_id: 1 }),
      participant({ person_id: 2, state: 'rejected', speaker: '李四' }),
    ])
    expect(rows.map((row) => row.person_id)).toEqual([1])
  })

  it('重要程度优先，其次待确认在前', () => {
    const rows = visibleParticipants([
      participant({ person_id: 1, speaker: '王五', importance: 3 }),
      participant({ person_id: 2, speaker: '李四', importance: 1 }),
      participant({ person_id: 3, speaker: '赵六', importance: 3, state: 'confirmed' }),
    ])
    expect(rows.map((row) => row.person_id)).toEqual([2, 1, 3])
  })

  it('空输入不崩', () => {
    expect(visibleParticipants(null)).toEqual([])
    expect(visibleParticipants(undefined)).toEqual([])
  })
})

describe('summarizeAttributes', () => {
  it('单位与职务拼一行', () => {
    expect(
      summarizeAttributes([
        { category: 'org', key: 'unit', value: '总体院' },
        { category: 'role', key: 'title', value: '主任' },
      ]),
    ).toBe('总体院 · 主任')
  })

  it('缺职务时退到部门', () => {
    expect(
      summarizeAttributes([
        { category: 'org', key: 'unit', value: '十四所' },
        { category: 'org', key: 'department', value: '总体部' },
      ]),
    ).toBe('十四所 · 总体部')
  })

  it('没有属性返回空串', () => {
    expect(summarizeAttributes([])).toBe('')
    expect(summarizeAttributes(undefined)).toBe('')
  })
})

describe('labels', () => {
  it('默认隐藏低可信语义候选，仍显示讲话人占位，可展开核对', () => {
    const rows = [participant({ person_id: 1, hidden_by_confidence: true }), participant({ person_id: 2, speaker: '说话人 1', hidden_by_confidence: false })]
    expect(visibleParticipants(rows).map((row) => row.person_id)).toEqual([2])
    expect(visibleParticipants(rows, true)).toHaveLength(2)
  })
  it('状态文案齐全', () => {
    expect(STATE_LABELS.pending).toBe('待确认')
    expect(STATE_LABELS.confirmed).toBe('已确认')
    expect(STATE_LABELS.rejected).toBe('已删除')
  })

  it('冲突等级文案不暴露阈值术语', () => {
    expect(conflictLevelText('high')).toBe('高冲突：更像别人')
    expect(conflictLevelText('medium')).toBe('建议核对')
    expect(conflictLevelText('none')).toBe('一致')
  })

  it('待确认计数排除已确认与已删除', () => {
    expect(
      pendingCount([
        participant({ person_id: 1, state: 'pending' }),
        participant({ person_id: 2, state: 'confirmed' }),
        participant({ person_id: 3, state: 'rejected' }),
      ]),
    ).toBe(1)
  })
})
