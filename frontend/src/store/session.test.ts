/** 转写片段的状态合并语义测试。
 *
 * 覆盖前端最关键的约定：**同 segment_id 覆盖而非追加**。
 * 双路 ASR 会先推 partial 草稿、后推 final 定稿，二者共用同一个 id；
 * 如果这里退化成 append，屏幕上就会出现重复文字。
 *
 * 运行：npx vitest run
 */

import { describe, expect, it, beforeEach } from 'vitest'
import type { Segment, ServerMessage } from '../api/types'
import { handleServerMessage, upsertSegment, useSession } from './session'

function seg(id: string, text: string, isFinal = false, seq = -1): Segment {
  return { segmentId: id, text, isFinal, startMs: 0, endMs: 600, speaker: null, seq }
}

beforeEach(() => {
  useSession.setState({ segments: [], indexById: new Map(), speakers: [] })
})

describe('upsertSegment', () => {
  it('新片段追加到末尾', () => {
    upsertSegment(seg('a', '你好'))
    upsertSegment(seg('b', '世界'))
    const { segments } = useSession.getState()
    expect(segments.map((s) => s.text)).toEqual(['你好', '世界'])
  })

  it('同 id 覆盖，不产生新行', () => {
    upsertSegment(seg('a', '你'))
    upsertSegment(seg('a', '你好'))
    upsertSegment(seg('a', '你好世'))
    const { segments } = useSession.getState()
    expect(segments).toHaveLength(1)
    expect(segments[0].text).toBe('你好世')
  })

  it('final 覆盖同 id 的 partial 草稿', () => {
    upsertSegment(seg('a', '草稿', false))
    upsertSegment(seg('a', '定稿文本', true, 0))
    const { segments } = useSession.getState()
    expect(segments).toHaveLength(1)
    expect(segments[0].isFinal).toBe(true)
    expect(segments[0].text).toBe('定稿文本')
  })

  it('已定稿的片段不会被迟到的 partial 回退', () => {
    upsertSegment(seg('a', '草稿', false))
    upsertSegment(seg('a', '定稿', true, 0))
    // 迟到的旧 partial（网络乱序）
    upsertSegment(seg('a', '更旧的草稿', false))
    const { segments } = useSession.getState()
    expect(segments).toHaveLength(1)
    expect(segments[0].text).toBe('定稿')
    expect(segments[0].isFinal).toBe(true)
  })

  it('indexById 与 segments 始终一致', () => {
    upsertSegment(seg('a', '1'))
    upsertSegment(seg('b', '2'))
    upsertSegment(seg('a', '1b'))
    const { segments, indexById } = useSession.getState()
    expect(indexById.size).toBe(segments.length)
    for (const [id, i] of indexById) {
      expect(segments[i].segmentId).toBe(id)
    }
  })

  it('覆盖中间元素不打乱其他片段的顺序', () => {
    upsertSegment(seg('a', 'A'))
    upsertSegment(seg('b', 'B'))
    upsertSegment(seg('c', 'C'))
    upsertSegment(seg('b', 'B2'))
    const { segments } = useSession.getState()
    expect(segments.map((s) => s.text)).toEqual(['A', 'B2', 'C'])
  })
})

describe('handleServerMessage', () => {
  it('ready 写入 meetingId 与 sessionId', () => {
    handleServerMessage({
      type: 'ready',
      meeting_id: 7,
      session_id: 'abc',
      sample_rate: 16000,
    } as ServerMessage)
    const s = useSession.getState()
    expect(s.meetingId).toBe(7)
    expect(s.sessionId).toBe('abc')
  })

  it('partial 与随后同 id 的 final 合成一条', () => {
    handleServerMessage({
      type: 'partial',
      segment_id: 'x1',
      text: '今天',
      start_ms: 0,
      end_ms: 600,
    })
    handleServerMessage({
      type: 'final',
      segment_id: 'x1',
      text: '今天开会。',
      start_ms: 0,
      end_ms: 1800,
      speaker: 'spk0',
      seq: 0,
    })
    const { segments } = useSession.getState()
    expect(segments).toHaveLength(1)
    expect(segments[0].text).toBe('今天开会。')
    expect(segments[0].isFinal).toBe(true)
    expect(segments[0].speaker).toBe('spk0')
  })

  it('speakers 消息更新占比数据', () => {
    handleServerMessage({
      type: 'speakers',
      data: [
        { id: 'spk0', ratio: 0.6 },
        { id: 'spk1', ratio: 0.4 },
      ],
    })
    expect(useSession.getState().speakers).toHaveLength(2)
  })

  it('speaker_update 同步已显示片段与占比', () => {
    handleServerMessage({
      type: 'final',
      segment_id: 'rename-1',
      text: '这句话是谁说的。',
      start_ms: 0,
      end_ms: 1200,
      speaker: '说话人 1',
      seq: 0,
    })
    handleServerMessage({
      type: 'speakers',
      data: [{ id: '说话人 1', ratio: 1 }],
    })

    handleServerMessage({
      type: 'speaker_update',
      meeting_id: 1,
      from: '说话人 1',
      to: '王工',
    })

    expect(useSession.getState().segments[0].speaker).toBe('王工')
    expect(useSession.getState().speakers[0].id).toBe('王工')
  })

  it('minutes_update 写入会中草稿', () => {
    handleServerMessage({
      type: 'minutes_update',
      meeting_id: 9,
      minutes: {
        id: 1,
        meeting_id: 9,
        summary: '阶段性摘要',
        topics: [],
        todos: [],
        raw_markdown: '',
        model_used: 'fake',
        status: 'ready',
        error: null,
        version: 2,
        is_draft: true,
        created_at: '2026-10-17T03:40:00Z',
      },
    })
    const live = useSession.getState().liveMinutes
    expect(live?.summary).toBe('阶段性摘要')
    expect(live?.is_draft).toBe(true)
    expect(live?.version).toBe(2)
  })
})
