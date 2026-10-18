/**
 * 声纹录入录音的回归用例。
 *
 * 背景：`recordWav` 里有两个已修的真实故障，都很隐蔽，必须锁住——
 *
 * 1. 倒计时停在起始值。电平回调曾经把入参 `seconds`（总时长常量）当成剩余
 *    时间回传给界面，每 80ms 覆盖一次真实倒计时，表现为「一直显示还剩 5 秒」，
 *    用户以为没在录。
 * 2. 录完不进入下一步。`VoiceprintEnroll` 用 `useRef(false)` + 只在清理函数里
 *    置真来判断「已卸载」，而 React StrictMode（开发模式）挂载时会跑
 *    setup → cleanup → setup，cleanup 立刻把标志置真且再无机会复位，于是
 *    录音正常结束后被误判为已卸载而提前 return。该用例覆盖第 1 点；
 *    第 2 点由 `VoiceprintEnroll` 自身的 effect 复位保证。
 */

import { describe, expect, it, vi } from 'vitest'

/** 捕获 recordWav 内部创建的录音器实例，供测试驱动其回调。 */
const h = vi.hoisted(() => ({ last: null as { opts: Record<string, unknown> } | null }))

vi.mock('./recorder', () => ({
  MicrophoneRecorder: class {
    opts: Record<string, unknown>
    constructor(opts: Record<string, unknown>) {
      this.opts = opts
      h.last = this
    }
    async start() {}
    async stop() {}
  },
}))

import { recordWav } from './recordWav'

// recordWav 用 window.setInterval/setTimeout；node 环境下把 window 指到 globalThis，
// 这样 vi.useFakeTimers() 接管定时器后 window.* 也会一并被接管。
vi.stubGlobal('window', globalThis)

describe('recordWav', () => {
  it('倒计时按真实剩余递减，电平回调不会把它重置回起始值', async () => {
    vi.useFakeTimers()
    try {
      const seen: number[] = []
      const p = recordWav(3, { onTick: (remain) => seen.push(remain) })

      // 先让 await recorder.start() 落地，started 才有值
      await vi.advanceTimersByTimeAsync(0)

      // 录到第 2 秒时来一次电平回调（真实链路约每 80ms 一次）
      await vi.advanceTimersByTimeAsync(2000)
      const before = seen[seen.length - 1]
      expect(before).toBeLessThan(2)

      ;(h.last!.opts.onLevel as (l: number) => void)(0.9)
      const after = seen[seen.length - 1]
      // 关键断言：电平回调之后剩余时间仍是「真实剩余」，而不是跳回 3
      expect(after).toBeLessThan(2)
      expect(after).toBeLessThanOrEqual(before)

      // 收满一段有声音的 PCM，让 peak > 0（否则会被判为死麦抛错）
      ;(h.last!.opts.onChunk as (p: Float32Array) => void)(new Float32Array(1600).fill(0.5))

      await vi.advanceTimersByTimeAsync(1500)
      const res = await p
      expect(res.peak).toBeGreaterThan(0)
      expect(res.ms).toBe(100) // 1600 样本 @16k
      expect(res.blob.size).toBeGreaterThan(44) // 44 字节 WAV 头 + 数据
    } finally {
      vi.useRealTimers()
    }
  })

  it('全程没采到声音时抛错，而不是返回一段静音声纹', async () => {
    vi.useFakeTimers()
    try {
      const p = recordWav(1)
      // 必须先挂上拒绝处理器，否则 advanceTimers 期间会报 unhandled rejection
      const guarded = p.catch((e: Error) => e)
      await vi.advanceTimersByTimeAsync(0)
      ;(h.last!.opts.onChunk as (p: Float32Array) => void)(new Float32Array(1600)) // 全零
      await vi.advanceTimersByTimeAsync(1200)
      const err = await guarded
      expect(err).toBeInstanceOf(Error)
      expect((err as Error).message).toContain('没有采集到声音')
    } finally {
      vi.useRealTimers()
    }
  })
})
