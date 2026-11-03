// @vitest-environment jsdom
/**
 * 「可能重复的声纹」面板：预览、批量计划与执行。
 *
 * 这里锁的是安全性而不是外观：合并会**删除**被并的那一条，所以
 * - 必须先出计划（dry-run）才允许执行；
 * - 每一对都能展开看到「删谁、留谁」；
 * - 执行时把计划里的阈值和条数原样传回去，不偷偷放宽。
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { voiceprintApi } from '../api/client'
import type { Voiceprint } from '../api/types'
import { VoiceprintMergePanel } from './VoiceprintMergePanel'

vi.mock('../api/client', () => ({
  voiceprintApi: { applyMergeSuggestions: vi.fn() },
}))

const mocked = vi.mocked(voiceprintApi)

function print(overrides: Partial<Voiceprint> = {}): Voiceprint {
  return {
    id: 1,
    name: '说话人 1',
    sample_ms: 2400,
    created_at: '2026-11-01T00:00:00Z',
    provisional: true,
    source_meeting_id: 13,
    sample_count: 3,
    quality_score: 0.4,
    disabled: false,
    last_seen_at: null,
    ...overrides,
  }
}

const suggestion = {
  score: 0.973,
  source: print(),
  target: print({ id: 17, name: '周YJ', provisional: false, sample_count: 12, sample_ms: 48000 }),
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('VoiceprintMergePanel', () => {
  it('列出候选对与相似度', () => {
    render(<VoiceprintMergePanel suggestions={[suggestion]} onMerge={vi.fn()} onReload={vi.fn()} />)

    expect(screen.getByText(/说话人 1 → 周YJ/)).toBeTruthy()
    expect(screen.getByText('97%')).toBeTruthy()
    expect(screen.getByText('1 对')).toBeTruthy()
  })

  it('展开预览会写明删谁、留谁', () => {
    render(<VoiceprintMergePanel suggestions={[suggestion]} onMerge={vi.fn()} onReload={vi.fn()} />)

    expect(screen.queryByText(/不可撤销/)).toBeNull()
    fireEvent.click(screen.getByText('预览'))

    expect(screen.getByText(/删除：/)).toBeTruthy()
    expect(screen.getByText(/保留：/)).toBeTruthy()
    expect(screen.getByText(/不可撤销/)).toBeTruthy()
    expect(screen.getByText('确认合并')).toBeTruthy()
  })

  it('确认合并只走这一对', async () => {
    const onMerge = vi.fn(async () => undefined)
    render(<VoiceprintMergePanel suggestions={[suggestion]} onMerge={onMerge} onReload={vi.fn()} />)

    fireEvent.click(screen.getByText('预览'))
    fireEvent.click(screen.getByText('确认合并'))

    await waitFor(() => expect(onMerge).toHaveBeenCalledWith(suggestion.source, 17))
    expect(await screen.findByText(/已把「说话人 1」合并进「周YJ」/)).toBeTruthy()
    expect(mocked.applyMergeSuggestions).not.toHaveBeenCalled()
  })

  it('生成计划只是 dry-run，不会动数据', async () => {
    mocked.applyMergeSuggestions.mockResolvedValueOnce({
      dry_run: true,
      threshold: 0.92,
      candidates: 4,
      plan: [suggestion],
      applied: [],
      failed: [],
    })
    render(<VoiceprintMergePanel suggestions={[suggestion]} onMerge={vi.fn()} onReload={vi.fn()} />)

    fireEvent.click(screen.getByText('生成合并计划…'))

    expect(await screen.findByText(/计划合并 1 对/)).toBeTruthy()
    expect(mocked.applyMergeSuggestions).toHaveBeenCalledWith({ dry_run: true, threshold: 0.92 })
    expect(screen.getByText('执行这 1 对')).toBeTruthy()
  })

  it('执行时按计划里的阈值与条数提交，完成后刷新列表', async () => {
    const onReload = vi.fn()
    mocked.applyMergeSuggestions
      .mockResolvedValueOnce({
        dry_run: true,
        threshold: 0.92,
        candidates: 1,
        plan: [suggestion],
        applied: [],
        failed: [],
      })
      .mockResolvedValueOnce({
        dry_run: false,
        threshold: 0.92,
        candidates: 1,
        plan: [suggestion],
        applied: [
          {
            source_id: 1,
            source_name: '说话人 1',
            target_id: 17,
            target_name: '周YJ',
            score: 0.973,
            updated_segments: 151,
          },
        ],
        failed: [],
      })
    render(<VoiceprintMergePanel suggestions={[suggestion]} onMerge={vi.fn()} onReload={onReload} />)

    fireEvent.click(screen.getByText('生成合并计划…'))
    fireEvent.click(await screen.findByText('执行这 1 对'))

    await waitFor(() =>
      expect(mocked.applyMergeSuggestions).toHaveBeenLastCalledWith({
        dry_run: false,
        threshold: 0.92,
        limit: 1,
      }),
    )
    expect(onReload).toHaveBeenCalled()
    expect(await screen.findByText(/已合并 1 对/)).toBeTruthy()
  })

  it('没有可自动执行的计划时说明原因，而不是静默', async () => {
    mocked.applyMergeSuggestions.mockResolvedValueOnce({
      dry_run: true,
      threshold: 0.92,
      candidates: 2,
      plan: [],
      applied: [],
      failed: [],
    })
    render(<VoiceprintMergePanel suggestions={[suggestion]} onMerge={vi.fn()} onReload={vi.fn()} />)

    fireEvent.click(screen.getByText('生成合并计划…'))

    expect(await screen.findByText(/没有可自动执行的合并/)).toBeTruthy()
    expect(screen.queryByText(/执行这/)).toBeNull()
  })
})
