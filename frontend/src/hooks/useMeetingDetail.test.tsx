// @vitest-environment jsdom
/**
 * 详情页的动作层：改名、改正文、确认说话人、生成纪要、导出。
 *
 * 这些方法是「谁改了哪条转写」的实际执行者，也是前几轮身份语义改动的落点。
 * 之前完全没有测试，覆盖的是三件容易退化的约定：
 * - 每次改动后必须让相关 query 失效（否则页面显示的还是旧数据）；
 * - 失败要落到 genError 上，不能静默；
 * - 没有转写时不允许触发生成纪要。
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { meetingApi, voiceprintApi } from '../api/client'
import type { ApiSegment, MeetingDetail, Minutes } from '../api/types'
import { runExportJob } from '../lib/exportJob'
import { useMeetingDetail } from './useMeetingDetail'

vi.mock('../api/client', () => ({
  meetingApi: {
    get: vi.fn(),
    getMinutes: vi.fn(),
    minutesVersions: vi.fn(),
    editSegment: vi.fn(),
    generateMinutes: vi.fn(),
    createExportJob: vi.fn(),
    exportJob: vi.fn(),
    downloadExportJob: vi.fn(),
  },
  voiceprintApi: { relabel: vi.fn() },
}))

vi.mock('../lib/exportJob', () => ({ runExportJob: vi.fn() }))

const mockedMeeting = vi.mocked(meetingApi)
const mockedVoiceprint = vi.mocked(voiceprintApi)
const mockedExport = vi.mocked(runExportJob)

function detail(overrides: Partial<MeetingDetail> = {}): MeetingDetail {
  return {
    id: 13,
    title: '两日会议',
    has_audio: false,
    segment_count: 3,
    ...overrides,
  } as MeetingDetail
}

function minutes(): Minutes {
  return { id: 1, meeting_id: 13, summary: '既有纪要', topics: [], todos: [] } as unknown as Minutes
}

function segment(overrides: Partial<ApiSegment> = {}): ApiSegment {
  return {
    id: 1,
    segment_id: 'seg-1',
    text: '原句',
    is_final: true,
    start_ms: 0,
    end_ms: 1000,
    speaker: '说话人 1',
    seq: 0,
    ...overrides,
  } as ApiSegment
}

function setup(search = '') {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  const invalidate = vi.spyOn(client, 'invalidateQueries')
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  )
  const view = renderHook(() => useMeetingDetail(13, new URLSearchParams(search)), { wrapper })
  return { ...view, invalidate }
}

beforeEach(() => {
  vi.clearAllMocks()
  mockedMeeting.get.mockResolvedValue(detail())
  mockedMeeting.getMinutes.mockResolvedValue(null as never)
  mockedMeeting.minutesVersions.mockResolvedValue([] as never)
  mockedVoiceprint.relabel.mockResolvedValue({ renamed_voiceprint: true, updated_segments: 1, name: '张三' })
})

describe('useMeetingDetail', () => {
  it('既有纪要与转写状态来自后端', async () => {
    mockedMeeting.getMinutes.mockResolvedValue(minutes())
    const { result } = setup()

    await waitFor(() => expect(result.current.activeMinutes).not.toBeNull())
    expect(result.current.activeMinutes?.summary).toBe('既有纪要')
    expect(result.current.hasTranscript).toBe(true)
  })

  it('没有片段时 hasTranscript 为 false', async () => {
    mockedMeeting.get.mockResolvedValue(detail({ segment_count: 0 }))
    const { result } = setup()

    await waitFor(() => expect(result.current.data).toBeDefined())
    expect(result.current.hasTranscript).toBe(false)
  })

  it('改名成功后让会议与纪要失效', async () => {
    const { result, invalidate } = setup()
    await waitFor(() => expect(result.current.data).toBeDefined())
    invalidate.mockClear()

    await act(async () => {
      await result.current.renameSpeaker('说话人 1', '张三')
    })

    expect(mockedVoiceprint.relabel).toHaveBeenCalledWith(13, '说话人 1', '张三')
    const keys = invalidate.mock.calls.map((call) => JSON.stringify(call[0]?.queryKey))
    expect(keys).toContain(JSON.stringify(['meeting', 13]))
    expect(keys).toContain(JSON.stringify(['minutes', 13]))
    expect(result.current.genError).toBeNull()
  })

  it('改名失败落到 genError', async () => {
    mockedVoiceprint.relabel.mockRejectedValueOnce(new Error('已取消声纹确认，原说话人保持不变'))
    const { result } = setup()
    await waitFor(() => expect(result.current.data).toBeDefined())

    await act(async () => {
      await result.current.renameSpeaker('说话人 1', '张三')
    })

    expect(result.current.genError).toContain('已取消声纹确认')
  })

  it('保存正文后刷新会议并关闭编辑器', async () => {
    mockedMeeting.editSegment.mockResolvedValueOnce({ segment_id: 'seg-1' } as never)
    const { result } = setup()
    await waitFor(() => expect(result.current.data).toBeDefined())

    act(() => {
      result.current.setEditingSegment(segment())
    })
    await act(async () => {
      await result.current.saveSegment(segment(), '改好的句子')
    })

    expect(mockedMeeting.editSegment).toHaveBeenCalledWith(13, 'seg-1', { text: '改好的句子' })
    expect(result.current.editingSegment).toBeNull()
  })

  it('保存正文失败时保留编辑器并报错', async () => {
    mockedMeeting.editSegment.mockRejectedValueOnce(new Error('权限不足'))
    const { result } = setup()
    await waitFor(() => expect(result.current.data).toBeDefined())

    const target = segment()
    act(() => {
      result.current.setEditingSegment(target)
    })
    await act(async () => {
      await result.current.saveSegment(target, 'x')
    })

    expect(result.current.genError).toBe('权限不足')
    expect(result.current.editingSegment).not.toBeNull()
  })

  it('确认说话人是同名提交，没有 speaker 时什么都不做', async () => {
    const { result } = setup()
    await waitFor(() => expect(result.current.data).toBeDefined())

    await act(async () => {
      await result.current.confirmSpeaker(segment({ speaker: null }))
    })
    expect(mockedVoiceprint.relabel).not.toHaveBeenCalled()

    await act(async () => {
      await result.current.confirmSpeaker(segment({ speaker: '说话人 1' }))
    })
    expect(mockedVoiceprint.relabel).toHaveBeenCalledWith(13, '说话人 1', '说话人 1')
  })

  it('没有转写时不允许生成纪要', async () => {
    mockedMeeting.get.mockResolvedValue(detail({ segment_count: 0 }))
    const { result } = setup()
    await waitFor(() => expect(result.current.data).toBeDefined())

    await act(async () => {
      await result.current.handleGenerate()
    })

    expect(mockedMeeting.generateMinutes).not.toHaveBeenCalled()
    expect(result.current.generating).toBe(false)
  })

  it('生成纪要成功后采用返回的纪要', async () => {
    mockedMeeting.generateMinutes.mockResolvedValueOnce(minutes())
    const { result } = setup()
    await waitFor(() => expect(result.current.hasTranscript).toBe(true))

    await act(async () => {
      await result.current.handleGenerate()
    })

    expect(mockedMeeting.generateMinutes).toHaveBeenCalled()
    expect(result.current.activeMinutes?.summary).toBe('既有纪要')
    expect(result.current.generating).toBe(false)
  })

  it('生成失败把错误留在 genError', async () => {
    mockedMeeting.generateMinutes.mockRejectedValueOnce(new Error('模型未就绪'))
    const { result } = setup()
    await waitFor(() => expect(result.current.hasTranscript).toBe(true))

    await act(async () => {
      await result.current.handleGenerate()
    })

    expect(result.current.genError).toBe('模型未就绪')
  })

  it('导出走统一作业流程，并把文件名兜底成会议标题', async () => {
    mockedExport.mockResolvedValueOnce(undefined as never)
    const { result } = setup()
    await waitFor(() => expect(result.current.data?.title).toBe('两日会议'))

    await act(async () => {
      await result.current.handleExport('docx')
    })

    const options = mockedExport.mock.calls[0][0]
    expect(options.fallbackFilename).toBe('两日会议.docx')
    expect(result.current.exportStatus).toBeNull()
    expect(result.current.showExport).toBe(false)
  })

  it('导出失败把错误显示在导出状态里', async () => {
    mockedExport.mockRejectedValueOnce(new Error('导出超时'))
    const { result } = setup()
    await waitFor(() => expect(result.current.data).toBeDefined())

    await act(async () => {
      await result.current.handleExport('pdf')
    })

    expect(result.current.exportStatus).toBe('导出超时')
  })

  it('点击时间戳通过事件通知播放器跳转', async () => {
    const heard = vi.fn()
    window.addEventListener('audio-player-seek', heard)
    const { result } = setup()
    await waitFor(() => expect(result.current.data).toBeDefined())

    act(() => {
      result.current.handleSeek(4200)
    })

    expect(heard).toHaveBeenCalledTimes(1)
    expect((heard.mock.calls[0][0] as CustomEvent).detail).toBe(4200)
    window.removeEventListener('audio-player-seek', heard)
  })

  it('URL 带时间戳且有音频时自动跳转一次', async () => {
    mockedMeeting.get.mockResolvedValue(detail({ has_audio: true }))
    const heard = vi.fn()
    window.addEventListener('audio-player-seek', heard)
    setup('t=42')

    await waitFor(() => expect(heard).toHaveBeenCalledTimes(1), { timeout: 3000 })
    expect((heard.mock.calls[0][0] as CustomEvent).detail).toBe(42000)
    window.removeEventListener('audio-player-seek', heard)
  })
})
