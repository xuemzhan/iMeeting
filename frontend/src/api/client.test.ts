// @vitest-environment jsdom
/**
 * 请求层（client.ts 的 request / ApiError / 鉴权事件 / relabel 分流）。
 *
 * 这个文件是所有后端调用的唯一入口，之前只有 7% 覆盖。它最容易出错的地方恰恰是
 * 「出错时怎么办」：detail 形状、401 的两种含义、读不到 JSON 时的兜底文案。
 * 这里把这几条都钉住，避免以后改动悄悄改变用户看到的错误。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { askVoiceConfirmation } from '../lib/voiceConfirmation'
import { AUTH_EXPIRED_EVENT, ApiError, getToken, setToken, glossaryApi, voiceprintApi } from './client'

vi.mock('../lib/voiceConfirmation', () => ({
  askVoiceConfirmation: vi.fn(),
  VOICE_CONFIRMATION_EVENT: 'imeeting-voice-confirmation',
}))

const mockedAsk = vi.mocked(askVoiceConfirmation)
const fetchMock = vi.fn()

function reply(body: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    text: async () => (body === undefined ? '' : JSON.stringify(body)),
  } as unknown as Response
}

function lastInit(): RequestInit {
  return fetchMock.mock.calls[fetchMock.mock.calls.length - 1][1] as RequestInit
}

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  fetchMock.mockReset()
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('令牌', () => {
  it('写入、读取与清除', () => {
    expect(getToken()).toBeNull()
    setToken('abc')
    expect(getToken()).toBe('abc')
    setToken(null)
    expect(getToken()).toBeNull()
  })

  it('有令牌时自动带 Bearer 头', async () => {
    setToken('t-1')
    fetchMock.mockResolvedValueOnce(reply([]))

    await voiceprintApi.list()

    const headers = lastInit().headers as Headers
    expect(headers.get('Authorization')).toBe('Bearer t-1')
  })

  it('无令牌时不带 Authorization 头', async () => {
    fetchMock.mockResolvedValueOnce(reply([]))

    await voiceprintApi.list()

    expect((lastInit().headers as Headers).get('Authorization')).toBeNull()
  })
})

describe('请求体与响应解析', () => {
  it('JSON 请求体自动带 Content-Type', async () => {
    fetchMock.mockResolvedValueOnce(reply({ ok: true }))

    await voiceprintApi.rename(1, '张三')

    const init = lastInit()
    expect((init.headers as Headers).get('Content-Type')).toBe('application/json')
    expect(init.body).toBe(JSON.stringify({ name: '张三' }))
  })

  it('FormData 不设 Content-Type，交给浏览器补 boundary', async () => {
    fetchMock.mockResolvedValueOnce(reply({ voiceprint_id: 1, template_id: 2, slot: 0, sample_ms: 3000 }))

    await voiceprintApi.enrollSample(1, new Blob(['x']), 'phone')

    const init = lastInit()
    expect((init.headers as Headers).get('Content-Type')).toBeNull()
    expect(init.body instanceof FormData).toBe(true)
    expect((init.body as FormData).get('device')).toBe('phone')
  })

  it('204 返回 undefined，不尝试解析 JSON', async () => {
    fetchMock.mockResolvedValueOnce(reply(undefined, 204))

    await expect(voiceprintApi.remove(9)).resolves.toBeUndefined()
  })

  it('非 JSON 响应体按纯文本返回', async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      status: 200,
      text: async () => 'not-json',
    } as unknown as Response)

    await expect(voiceprintApi.list()).resolves.toBe('not-json')
  })
})

describe('错误映射', () => {
  it('detail 是字符串时用它做消息', async () => {
    fetchMock.mockResolvedValueOnce(reply({ detail: '会议不存在' }, 404))

    const error = await voiceprintApi.remove(9).catch((err) => err)

    expect(error).toBeInstanceOf(ApiError)
    expect(error.status).toBe(404)
    expect(error.message).toBe('会议不存在')
    expect(error.detail).toBe('会议不存在')
  })

  it('FastAPI 校验错误（detail 是数组）取第一条 msg', async () => {
    fetchMock.mockResolvedValueOnce(reply({ detail: [{ msg: '姓名过长' }] }, 422))

    const error = await voiceprintApi.list().catch((err) => err)

    expect(error.message).toBe('姓名过长')
  })

  it('detail 形状不认识时退回通用文案', async () => {
    fetchMock.mockResolvedValueOnce(reply({}, 500))

    const error = await voiceprintApi.list().catch((err) => err)

    expect(error.message).toBe('请求失败 (500)')
  })
})

describe('401 的两种含义', () => {
  it('带着令牌访问业务接口：清令牌并广播会话失效', async () => {
    setToken('t-1')
    const heard = vi.fn()
    window.addEventListener(AUTH_EXPIRED_EVENT, heard)
    fetchMock.mockResolvedValueOnce(reply({ detail: '令牌已失效' }, 401))

    await voiceprintApi.list().catch(() => undefined)

    expect(getToken()).toBeNull()
    expect(heard).toHaveBeenCalledTimes(1)
    window.removeEventListener(AUTH_EXPIRED_EVENT, heard)
  })

  it('登录接口的 401 只表示密码错，不能清掉登录态', async () => {
    setToken('t-old')
    const heard = vi.fn()
    window.addEventListener(AUTH_EXPIRED_EVENT, heard)
    fetchMock.mockResolvedValueOnce(reply({ detail: '用户名或密码错误' }, 401))

    const { authApi } = await import('./client')
    await authApi.login('admin', 'wrong').catch(() => undefined)

    expect(getToken()).toBe('t-old')
    expect(heard).not.toHaveBeenCalled()
    window.removeEventListener(AUTH_EXPIRED_EVENT, heard)
  })

  it('匿名请求（本来就没有令牌）不广播会话失效', async () => {
    const heard = vi.fn()
    window.addEventListener(AUTH_EXPIRED_EVENT, heard)
    fetchMock.mockResolvedValueOnce(reply({ detail: '未提供访问令牌' }, 401))

    await voiceprintApi.list().catch(() => undefined)

    expect(heard).not.toHaveBeenCalled()
    window.removeEventListener(AUTH_EXPIRED_EVENT, heard)
  })
})

describe('relabel 的冲突分流', () => {
  const conflict = {
    detail: {
      kind: 'voiceprint_confirmation',
      assessment: { conflict_id: 3, self_score: 0.7, other: null, level: 'high' },
      voiceprint_id: 1,
      name: '张三',
      meeting_id: 13,
      segment_id: null,
      start_ms: null,
      requires_slot: false,
      templates: [],
    },
  }

  it('选择「只改本场显示名」时关掉三个开关，且不透传 decision', async () => {
    mockedAsk.mockResolvedValueOnce({ decision: 'label_only' })
    fetchMock
      .mockResolvedValueOnce(reply(conflict, 409))
      .mockResolvedValueOnce(reply({ renamed_voiceprint: false, updated_segments: 2, name: '张三' }))

    await voiceprintApi.relabel(13, '说话人 1', '张三')

    const body = JSON.parse(lastInit().body as string)
    expect(body).toMatchObject({
      meeting_id: 13,
      from_label: '说话人 1',
      to_name: '张三',
      learn: false,
      rename_identity: false,
      bind_person: false,
    })
    expect(body.decision).toBeUndefined()
    expect(body.conflict_id).toBeUndefined()
  })

  it('选择取消时立刻抛错，不再发第二个请求', async () => {
    mockedAsk.mockResolvedValueOnce({ decision: 'cancel' })
    fetchMock.mockResolvedValueOnce(reply(conflict, 409))

    await expect(voiceprintApi.relabel(13, '说话人 1', '张三')).rejects.toThrow('已取消声纹确认')

    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('确认为本人时带上 decision 与 conflict_id', async () => {
    mockedAsk.mockResolvedValueOnce({ decision: 'confirm_self', slot: 2 })
    fetchMock
      .mockResolvedValueOnce(reply(conflict, 409))
      .mockResolvedValueOnce(reply({ renamed_voiceprint: true, updated_segments: 2, name: '张三' }))

    await voiceprintApi.relabel(13, '说话人 1', '张三')

    const body = JSON.parse(lastInit().body as string)
    expect(body.decision).toBe('confirm_self')
    expect(body.conflict_id).toBe(3)
    expect(body.slot).toBe(2)
  })

  it('非声纹冲突的 409 原样抛出，不弹确认框', async () => {
    fetchMock.mockResolvedValueOnce(reply({ detail: '已存在名为「张三」的声纹' }, 409))

    await expect(voiceprintApi.relabel(13, '说话人 1', '张三')).rejects.toBeInstanceOf(ApiError)
    expect(mockedAsk).not.toHaveBeenCalled()
  })
})

describe('词表 CSV 导入', () => {
  it('以 FormData 提交，不手写 Content-Type，并带上令牌', async () => {
    setToken('t-import')
    fetchMock.mockResolvedValueOnce(reply({ imported: 3 }))

    const file = new File(['term,alias\n'], 'glossary.csv', { type: 'text/csv' })
    await expect(glossaryApi.importCsv(file)).resolves.toEqual({ imported: 3 })

    const init = lastInit()
    expect(init.method).toBe('POST')
    expect(init.body).toBeInstanceOf(FormData)
    expect((init.body as FormData).get('file')).toBe(file)

    const headers = new Headers(init.headers)
    // 手写 Content-Type 会丢掉 multipart boundary，后端直接解析失败
    expect(headers.get('Content-Type')).toBeNull()
    expect(headers.get('Authorization')).toBe('Bearer t-import')
  })

  it('被拒时抛出后端文案，不再静默成功', async () => {
    fetchMock.mockResolvedValueOnce(reply({ detail: '需要管理员权限' }, 403))

    const file = new File(['x'], 'glossary.csv', { type: 'text/csv' })
    await expect(glossaryApi.importCsv(file)).rejects.toThrow('需要管理员权限')
  })
})
