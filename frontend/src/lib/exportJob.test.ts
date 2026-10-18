import { describe, expect, it } from 'vitest'

import type { Job } from '../api/types'
import { runExportJob } from './exportJob'

function job(status: Job['status'], overrides: Partial<Job> = {}): Job {
  return {
    id: 7,
    meeting_id: 1,
    owner_id: 1,
    kind: 'meeting_export',
    status,
    priority: 0,
    attempts: 0,
    max_attempts: 3,
    run_after: '2026-01-01T00:00:00Z',
    last_error: null,
    result: '',
    result_path: null,
    download_name: null,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    ...overrides,
  }
}

describe('runExportJob', () => {
  it('downloads an already completed job without polling', async () => {
    const progress: Job['status'][] = []
    const saved: string[] = []

    await runExportJob({
      createJob: async () => job('succeeded', { download_name: 'meeting.md' }),
      getJob: async () => {
        throw new Error('completed jobs must not be polled')
      },
      downloadJob: async () => new Blob(['ready']),
      fallbackFilename: 'fallback.md',
      onProgress: (current) => progress.push(current.status),
      save: (_blob, filename) => saved.push(filename),
      sleep: async () => {
        throw new Error('completed jobs must not sleep')
      },
    })

    expect(progress).toEqual([])
    expect(saved).toEqual(['meeting.md'])
  })

  it('polls until completion and reports each unfinished state', async () => {
    const states = [
      job('pending'),
      job('running'),
      job('succeeded', { download_name: 'result.docx' }),
    ]
    const progress: Job['status'][] = []
    const sleeps: number[] = []
    const saved: string[] = []
    let getCalls = 0

    await runExportJob({
      createJob: async () => states[0],
      getJob: async () => states[++getCalls],
      downloadJob: async () => new Blob(['ready']),
      fallbackFilename: 'fallback.docx',
      onProgress: (current) => progress.push(current.status),
      save: (_blob, filename) => saved.push(filename),
      sleep: async (milliseconds) => {
        sleeps.push(milliseconds)
      },
    })

    expect(progress).toEqual(['pending', 'running'])
    expect(sleeps).toEqual([1000, 1000])
    expect(saved).toEqual(['result.docx'])
  })

  it('uses the fallback name only after the task completes', async () => {
    let suffix = 'before'
    const saved: string[] = []

    await runExportJob({
      createJob: async () => job('pending'),
      getJob: async () => {
        suffix = 'after'
        return job('succeeded')
      },
      downloadJob: async () => new Blob(['ready']),
      fallbackFilename: () => `meetings-${suffix}.zip`,
      save: (_blob, filename) => saved.push(filename),
      sleep: async () => undefined,
    })

    expect(saved).toEqual(['meetings-after.zip'])
  })

  it('surfaces the server error for a failed job', async () => {
    await expect(
      runExportJob({
        createJob: async () => job('failed', { last_error: '磁盘空间不足' }),
        getJob: async () => job('succeeded'),
        downloadJob: async () => new Blob(['unused']),
        fallbackFilename: 'unused.md',
        save: () => undefined,
        sleep: async () => undefined,
      }),
    ).rejects.toThrow('磁盘空间不足')
  })

  it('stops after the polling budget', async () => {
    let sleeps = 0
    let polls = 0

    await expect(
      runExportJob({
        createJob: async () => job('pending'),
        getJob: async () => {
          polls++
          return job('pending')
        },
        downloadJob: async () => new Blob(['unused']),
        fallbackFilename: 'unused.md',
        timeoutMessage: '导出任务仍未完成',
        maxPolls: 3,
        onProgress: () => undefined,
        save: () => undefined,
        sleep: async () => {
          sleeps++
        },
      }),
    ).rejects.toThrow('导出任务仍未完成')

    expect(sleeps).toBe(3)
    expect(polls).toBe(3)
  })
})
