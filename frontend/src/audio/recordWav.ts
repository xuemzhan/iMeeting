/**
 * 录制一段固定时长的语音并编码成 16kHz 单声道 16bit WAV。
 *
 * 用途：设置里的「声纹录入」。复用主采集链路（MicrophoneRecorder）而不是
 * 另写一套 getUserMedia，这样：
 *  - 采样率、声道、自动降级策略与正式转写完全一致，录出来的声纹才具有
 *    可比性（声纹识别最怕录入与识别走两条不同的音频处理链）；
 *  - 少维护一份设备/权限处理逻辑。
 */

import { MicrophoneRecorder } from './recorder'

/** 把若干块 16kHz float32 PCM 编码为 16bit 单声道 WAV。 */
export function encodeWav16k(chunks: Float32Array[]): Blob {
  const total = chunks.reduce((n, c) => n + c.length, 0)
  const samples = new Float32Array(total)
  let offset = 0
  for (const c of chunks) {
    samples.set(c, offset)
    offset += c.length
  }

  const dataBytes = total * 2
  const buffer = new ArrayBuffer(44 + dataBytes)
  const view = new DataView(buffer)

  const writeStr = (pos: number, s: string) => {
    for (let i = 0; i < s.length; i++) view.setUint8(pos + i, s.charCodeAt(i))
  }

  writeStr(0, 'RIFF')
  view.setUint32(4, 36 + dataBytes, true)
  writeStr(8, 'WAVE')
  writeStr(12, 'fmt ')
  view.setUint32(16, 16, true) // PCM 头长度
  view.setUint16(20, 1, true) // PCM
  view.setUint16(22, 1, true) // 单声道
  view.setUint32(24, 16000, true)
  view.setUint32(28, 16000 * 2, true) // 字节率
  view.setUint16(32, 2, true) // 块对齐
  view.setUint16(34, 16, true) // 位深
  writeStr(36, 'data')
  view.setUint32(40, dataBytes, true)

  for (let i = 0; i < total; i++) {
    // 限幅后再量化，避免爆音削顶产生刺耳噪声
    const s = Math.max(-1, Math.min(1, samples[i]))
    view.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true)
  }

  return new Blob([buffer], { type: 'audio/wav' })
}

export interface RecordWavResult {
  blob: Blob
  /** 实际录到的有效音频时长（毫秒） */
  ms: number
  /** 录制期间的峰值电平，用于提示「没录到声音」 */
  peak: number
}

/**
 * 录制固定时长。`onTick` 可用于倒计时显示。
 *
 * 会检查是否真的录到了声音：全零（死麦）时直接抛错，
 * 否则用户会得到一个「录了 4 秒但全是静音」的坏声纹，
 * 之后识别永远匹配不上，且完全不知道原因。
 */
export async function recordWav(
  seconds: number,
  opts: { deviceId?: string; onTick?: (remain: number, level: number) => void } = {},
): Promise<RecordWavResult> {
  const chunks: Float32Array[] = []
  let peak = 0

  /**
   * 起录时刻，倒计时的**唯一**基准。
   *
   * 电平回调与主循环都必须用它算剩余时间。以前电平回调直接把入参 `seconds`
   * （总时长这个常量）当成剩余时间回传，于是每次刷新音量都会把倒计时重置回
   * 起始值——界面上表现为「一直显示还剩 5 秒」，用户以为没在录。
   * 未开始前（null）按「还剩整段」处理，避免首帧闪一个 0。
   */
  let started: number | null = null
  const remainSeconds = () =>
    started === null ? seconds : seconds - (Date.now() - started) / 1000

  const recorder = new MicrophoneRecorder({
    deviceId: opts.deviceId,
    onChunk: (pcm) => {
      chunks.push(pcm)
      for (let i = 0; i < pcm.length; i++) {
        const v = Math.abs(pcm[i])
        if (v > peak) peak = v
      }
    },
    // 只负责刷新音量条；剩余时间同样按真实剩余算，不要回传常量
    onLevel: (level) => opts.onTick?.(Math.max(0, remainSeconds()), level),
    // 录音期间不弹全局错误，由本函数统一抛给调用方处理
    onError: () => {},
  })

  await recorder.start()
  started = Date.now()

  try {
    await new Promise<void>((resolve) => {
      const tick = window.setInterval(() => {
        const remain = remainSeconds()
        opts.onTick?.(Math.max(0, remain), 0)
        if (remain <= 0) {
          window.clearInterval(tick)
          resolve()
        }
      }, 100)
    })
  } finally {
    await recorder.stop()
  }

  if (peak === 0) {
    throw new Error('没有采集到声音。请检查麦克风设备与系统音量后重试。')
  }

  const total = chunks.reduce((n, c) => n + c.length, 0)
  return { blob: encodeWav16k(chunks), ms: Math.round((total / 16000) * 1000), peak }
}
