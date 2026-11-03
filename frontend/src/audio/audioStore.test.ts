// @vitest-environment jsdom
/**
 * 本地音频缓冲的降级行为。
 *
 * 这套缓冲是「断线/刷新后补传」的唯一依赖，但它在三种环境下必须
 * 安静退化成空操作，不能把异常甩给调用方：
 * - 浏览器没有 IndexedDB（隐私模式、旧内核、测试环境）；
 * - 存储配额已满，事务 abort；
 * - 会话在认证已失效的情况下被清理。
 *
 * 尤其是最后一点：`putChunk` 失败必须 resolve —— 它处在录音主链路上，
 * reject 会直接把一次正在进行的会议打断。
 */

import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  clearMeeting,
  clearStaleChunks,
  deleteChunk,
  listChunks,
  putChunk,
} from './audioStore'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('audioStore 无 IndexedDB 时降级', () => {
  it('写入 / 列取 / 删除 / 清理全部安静返回', async () => {
    vi.stubGlobal('window', {})

    await expect(putChunk(7, 0, new ArrayBuffer(8))).resolves.toBeUndefined()
    await expect(listChunks(7)).resolves.toEqual([])
    await expect(deleteChunk(7, 0)).resolves.toBeUndefined()
    await expect(clearMeeting(7)).resolves.toBeUndefined()
    await expect(clearStaleChunks()).resolves.toBeUndefined()
  })

  it('清理过期块可以指定更短的有效期', async () => {
    vi.stubGlobal('window', {})
    await expect(clearStaleChunks(0)).resolves.toBeUndefined()
  })
})
