/** WebSocket 转写客户端：连接、发送音频、接收结果、断线重连。 */

import { getToken } from './client'
import type { ServerMessage, WsState } from './types'
import { deleteChunk, listChunks, putChunk } from '../audio/audioStore'

export type MessageHandler = (msg: ServerMessage) => void
export type StateHandler = (state: WsState) => void

interface Options {
  /** 收到服务端消息 */
  onMessage: MessageHandler
  /** 连接状态变化 */
  onState?: StateHandler
  /** 不可恢复的错误 */
  onError?: (message: string) => void
}

const MAX_RECONNECT_DELAY = 8000
const HEARTBEAT_INTERVAL = 20000

export class TranscriptSocket {
  private ws: WebSocket | null = null
  private state: WsState = 'closed'
  private reconnectAttempts = 0
  private reconnectTimer: number | null = null
  private heartbeatTimer: number | null = null
  private manualClose = false
  private pendingStart: Record<string, unknown> | null = null
  /**
   * 服务端在 `ready` 里下发的会议 id。
   *
   * 必须在重连时回传，否则服务端会当成一次全新会话另开会话记录，
   * 一场长会议会被网络抖动切成多条互相割裂的会议（导出/纪要都会对不上）。
   */
  private meetingId: number | null = null
  /** 会议 id 尚未由服务端返回前，先暂存音频。 */
  private preReady: ArrayBuffer[] = []
  private nextSeq = 0
  private sendChain: Promise<void> = Promise.resolve()

  constructor(private opts: Options) {}

  /** 当前会话绑定的会议 id（重连后保持不变）。 */
  get currentMeetingId(): number | null {
    return this.meetingId
  }

  get currentState(): WsState {
    return this.state
  }

  private setState(next: WsState): void {
    if (this.state === next) return
    this.state = next
    this.opts.onState?.(next)
  }

  /** 建立连接并发送 start 消息。
   *
   * `meetingId` 用于「继续一场已有会议」（例如页面刷新后恢复）。
   * 不传则视为新会议，由服务端创建后通过 `ready` 告知。
   */
  connect(startPayload: Record<string, unknown>, meetingId?: number | null): void {
    this.manualClose = false
    this.pendingStart = startPayload
    if (meetingId != null) this.meetingId = meetingId
    this.open()
  }

  private open(): void {
    if (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)) {
      return
    }

    this.setState(this.reconnectAttempts > 0 ? 'reconnecting' : 'connecting')

    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    const url = `${proto}://${location.host}/ws/stream`

    let ws: WebSocket
    try {
      ws = new WebSocket(url)
    } catch {
      this.opts.onError?.('无法建立 WebSocket 连接')
      this.scheduleReconnect()
      return
    }

    ws.binaryType = 'arraybuffer'
    this.ws = ws

    ws.onopen = () => {
      this.setState('open')
      this.reconnectAttempts = 0

      const token = getToken()
      // 重连时必须带上已知的 meeting_id，让服务端复用同一场会议，
      // 否则每次抖动都会新建一条会议记录，转写被割裂。
      const payload: Record<string, unknown> = {
        ...(this.pendingStart ?? {}),
        ...(this.meetingId != null ? { meeting_id: this.meetingId } : {}),
        ...(token ? { token } : {}),
      }
      ws.send(JSON.stringify(payload))

      this.startHeartbeat()
    }

    ws.onmessage = (ev) => {
      if (typeof ev.data !== 'string') return
      try {
        const msg = JSON.parse(ev.data) as ServerMessage
        if (msg.type === 'error') this.opts.onError?.(msg.message)
        // 记住服务端分配的会议 id，供后续重连复用
        if (msg.type === 'ready' && typeof msg.meeting_id === 'number') {
          this.meetingId = msg.meeting_id
          this.nextSeq = Math.max(
            this.nextSeq,
            (msg.audio_chunk_seq ?? 0) + 1,
          )
          const pending = this.preReady
          this.preReady = []
          this.sendChain = this.sendChain.then(() => this.flushStoredAudio())
          for (const buffer of pending) {
            void this.sendAudio(buffer)
          }
        }
        if (msg.type === 'audio_ack' && this.meetingId !== null) {
          void deleteChunk(this.meetingId, msg.seq)
        }
        this.opts.onMessage(msg)
      } catch {
        // 忽略非法 JSON
      }
    }

    ws.onerror = () => {
      // onerror 后必然触发 onclose，重连逻辑放在那里
    }

    ws.onclose = () => {
      this.stopHeartbeat()
      this.ws = null
      if (this.manualClose) {
        this.setState('closed')
        return
      }
      this.scheduleReconnect()
    }
  }

  private scheduleReconnect(): void {
    if (this.reconnectTimer !== null) return
    this.reconnectAttempts += 1

    // 指数退避，上限 8 秒
    const delay = Math.min(500 * 2 ** (this.reconnectAttempts - 1), MAX_RECONNECT_DELAY)
    this.setState('reconnecting')

    this.reconnectTimer = window.setTimeout(() => {
      this.reconnectTimer = null
      this.open()
    }, delay)
  }

  private startHeartbeat(): void {
    this.stopHeartbeat()
    this.heartbeatTimer = window.setInterval(() => {
      if (this.ws?.readyState === WebSocket.OPEN) {
        this.ws.send(JSON.stringify({ type: 'ping' }))
      }
    }, HEARTBEAT_INTERVAL)
  }

  private stopHeartbeat(): void {
    if (this.heartbeatTimer !== null) {
      clearInterval(this.heartbeatTimer)
      this.heartbeatTimer = null
    }
  }

  /** 发送音频块：先写本地库，服务端确认后才删除。 */
  sendAudio(buffer: ArrayBuffer): Promise<void> {
    if (this.meetingId === null) {
      this.preReady.push(buffer)
      return Promise.resolve()
    }
    const meetingId = this.meetingId
    this.sendChain = this.sendChain.then(async () => {
      const seq = this.nextSeq++
      await putChunk(meetingId, seq, buffer)
      this.sendChunk(seq, buffer)
    })
    return this.sendChain
  }

  private sendChunk(seq: number, buffer: ArrayBuffer): void {
    if (this.ws?.readyState !== WebSocket.OPEN) return
    this.ws.send(JSON.stringify({ type: 'audio_chunk', seq }))
    this.ws.send(buffer)
  }

  private async flushStoredAudio(): Promise<void> {
    if (this.meetingId === null) return
    const chunks = await listChunks(this.meetingId)
    for (const chunk of chunks) {
      this.nextSeq = Math.max(this.nextSeq, chunk.seq + 1)
      this.sendChunk(chunk.seq, chunk.data)
    }
  }

  /** 请求服务端立即定稿当前句。 */
  requestFinalize(): void {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ type: 'partial_hint' }))
    }
  }

  /** 优雅关闭：通知服务端收尾，等待最终结果。 */
  close(): void {
    this.manualClose = true
    this.stopHeartbeat()

    if (this.reconnectTimer !== null) {
      clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }

    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ type: 'stop' }))
      // 给服务端一点时间把最后一句定稿推回
      window.setTimeout(() => this.ws?.close(), 1200)
    } else {
      this.ws?.close()
    }

    // 主动结束会话后清空绑定，避免下一次「开始」被误当成继续旧会议
    this.meetingId = null
    this.setState('closed')
  }
}
