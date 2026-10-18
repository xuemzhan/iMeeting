/** TranscriptSocket 重连语义测试。
 *
 * 核心约定：**重连必须复用同一场会议**。
 * 服务端在 `ready` 里下发 meeting_id；若重连时不回传，服务端会新建会议，
 * 一场长会议会被网络抖动切成多条互不相干的记录（导出、纪要全部对不上）。
 *
 * 运行：npx vitest run
 */

import { describe, expect, it, beforeEach, afterEach, vi } from 'vitest'
import { TranscriptSocket } from './ws'

/** 可手动驱动的假 WebSocket，用来精确模拟"连上 → 断开 → 重连"。 */
class FakeWebSocket {
  static instances: FakeWebSocket[] = []
  static OPEN = 1
  static CONNECTING = 0
  static CLOSED = 3

  readyState = FakeWebSocket.CONNECTING
  binaryType = ''
  sent: Array<string | ArrayBuffer> = []

  onopen: (() => void) | null = null
  onmessage: ((ev: { data: string }) => void) | null = null
  onerror: (() => void) | null = null
  onclose: (() => void) | null = null

  constructor(public url: string) {
    FakeWebSocket.instances.push(this)
  }

  send(data: string | ArrayBuffer): void {
    this.sent.push(data)
  }

  close(): void {
    this.readyState = FakeWebSocket.CLOSED
    this.onclose?.()
  }

  // ---- 测试驱动 ----
  open(): void {
    this.readyState = FakeWebSocket.OPEN
    this.onopen?.()
  }

  /** 模拟服务端下发消息 */
  serverPush(msg: unknown): void {
    this.onmessage?.({ data: JSON.stringify(msg) })
  }

  /** 模拟网络中断（非主动关闭） */
  dropConnection(): void {
    this.readyState = FakeWebSocket.CLOSED
    this.onclose?.()
  }

  /** 取出第一条 start 消息的 payload */
  startPayload(): Record<string, unknown> | null {
    const first = this.sent.find((d) => typeof d === 'string' && d.includes('"start"'))
    return first ? (JSON.parse(first as string) as Record<string, unknown>) : null
  }
}

beforeEach(() => {
  FakeWebSocket.instances = []
  vi.stubGlobal('WebSocket', FakeWebSocket)
  vi.stubGlobal('location', { protocol: 'http:', host: 'localhost:8000' })
  vi.stubGlobal('localStorage', {
    getItem: () => null,
    setItem: () => {},
    removeItem: () => {},
  })
  // 把重连定时器改成同步立即执行，避免测试等待退避时间
  vi.stubGlobal('window', {
    setTimeout: (fn: () => void) => {
      fn()
      return 0
    },
    setInterval: () => 0,
    clearInterval: () => {},
    clearTimeout: () => {},
  })
})

afterEach(() => {
  vi.unstubAllGlobals()
})

function makeSocket() {
  return new TranscriptSocket({ onMessage: () => {}, onState: () => {} })
}

describe('TranscriptSocket 重连', () => {
  it('首次连接不带 meeting_id', () => {
    const sock = makeSocket()
    sock.connect({ type: 'start', title: '测试会议' })
    const ws = FakeWebSocket.instances[0]
    ws.open()

    const payload = ws.startPayload()
    expect(payload).not.toBeNull()
    expect(payload!.title).toBe('测试会议')
    expect(payload).not.toHaveProperty('meeting_id')
  })

  it('记住 ready 下发的 meeting_id', () => {
    const sock = makeSocket()
    sock.connect({ type: 'start', title: '测试会议' })
    const ws = FakeWebSocket.instances[0]
    ws.open()
    ws.serverPush({ type: 'ready', meeting_id: 42, session_id: 's1', sample_rate: 16000 })

    expect(sock.currentMeetingId).toBe(42)
  })

  it('重连时回传 meeting_id，复用同一场会议', () => {
    const sock = makeSocket()
    sock.connect({ type: 'start', title: '长会议' })

    const first = FakeWebSocket.instances[0]
    first.open()
    first.serverPush({ type: 'ready', meeting_id: 144, session_id: 's1', sample_rate: 16000 })

    // 网络中断，触发重连
    sock.sendAudio(new ArrayBuffer(8))
    first.dropConnection()

    // 重连产生的新连接
    expect(FakeWebSocket.instances.length).toBeGreaterThanOrEqual(2)
    const second = FakeWebSocket.instances[FakeWebSocket.instances.length - 1]
    second.open()

    const payload = second.startPayload()
    expect(payload).not.toBeNull()
    expect(payload!.meeting_id).toBe(144)
  })

  it('多次重连始终复用同一个 meeting_id', () => {
    const sock = makeSocket()
    sock.connect({ type: 'start', title: '长会议' })

    FakeWebSocket.instances[0].open()
    FakeWebSocket.instances[0].serverPush({
      type: 'ready',
      meeting_id: 7,
      session_id: 's1',
      sample_rate: 16000,
    })

    for (let i = 0; i < 3; i++) {
      const cur = FakeWebSocket.instances[FakeWebSocket.instances.length - 1]
      cur.dropConnection()
      const next = FakeWebSocket.instances[FakeWebSocket.instances.length - 1]
      next.open()
      expect(next.startPayload()!.meeting_id).toBe(7)
    }
  })

  it('主动 close 后清空绑定，下一次是新会议', () => {
    const sock = makeSocket()
    sock.connect({ type: 'start', title: '会议A' })
    FakeWebSocket.instances[0].open()
    FakeWebSocket.instances[0].serverPush({
      type: 'ready',
      meeting_id: 9,
      session_id: 's1',
      sample_rate: 16000,
    })
    expect(sock.currentMeetingId).toBe(9)

    sock.close()
    expect(sock.currentMeetingId).toBeNull()

    // 重新开始一次，不应带上旧 id
    sock.connect({ type: 'start', title: '会议B' })
    const ws = FakeWebSocket.instances[FakeWebSocket.instances.length - 1]
    ws.open()
    expect(ws.startPayload()).not.toHaveProperty('meeting_id')
  })

  it('显式传入 meetingId 可继续已有会议（刷新恢复场景）', () => {
    const sock = makeSocket()
    sock.connect({ type: 'start', title: '恢复' }, 2024)
    const ws = FakeWebSocket.instances[0]
    ws.open()
    expect(ws.startPayload()!.meeting_id).toBe(2024)
  })

  it('ready 之前断线，重连不带 meeting_id（尚未分配）', () => {
    const sock = makeSocket()
    sock.connect({ type: 'start', title: '早断' })
    FakeWebSocket.instances[0].open()
    // 还没收到 ready 就断了
    FakeWebSocket.instances[0].dropConnection()

    const second = FakeWebSocket.instances[FakeWebSocket.instances.length - 1]
    second.open()
    expect(second.startPayload()).not.toHaveProperty('meeting_id')
  })
})
