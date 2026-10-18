/** 转写会话状态：字幕流、说话人、连接状态、录音控制。 */

import { create } from 'zustand'
import { MicrophoneRecorder, listAudioInputs, CAPTURE_PROFILES } from '../audio/recorder'
import { meetingApi, voiceprintApi } from '../api/client'
import type { AudioInputDevice } from '../audio/recorder'
import { TranscriptSocket } from '../api/ws'
import type {
  Minutes,
  Segment,
  SpeakerRatio,
  ServerMessage,
  SessionStatus,
  WsState,
} from '../api/types'

interface SessionState {
  // 状态
  status: SessionStatus
  wsState: WsState
  meetingId: number | null
  sessionId: string | null
  title: string
  meetingInfo: {
    participants: string
    agenda: string
    meetingType: string
    description?: string
  } | null
  metadataCaptureMinutes: number
  /** 低于该相似度的姓名显示“低置信”，由后端配置下发。 */
  speakerLowConfidenceThreshold: number
  error: string | null
  /**
   * 非阻塞提示（与 error 分开）。
   *
   * 采集参数自动降级属于「正在自愈」，不是错误——用 error 会导致界面切成
   * 错误态、打断会议，所以单独用一个中性通道展示。
   */
  notice: string | null
  elapsedMs: number
  level: number

  // 数据
  segments: Segment[]
  /** segmentId → 数组下标，用于 O(1) 覆盖更新 */
  indexById: Map<string, number>
  speakers: SpeakerRatio[]
  /** 会中自动生成的阶段性纪要草稿（渐进式更新） */
  liveMinutes: Minutes | null

  // 麦克风设备
  /** 可选的输入设备列表（空数组表示未枚举或没有设备） */
  devices: AudioInputDevice[]
  /** 当前使用的设备 ID；null 表示跟随系统默认 */
  activeDeviceId: string | null
  /**
   * 采集是否健康。
   * - `true`：最近收到过有能量的音频
   * - `false`：已连续多秒收到全零 PCM（死麦）
   * - `null`：尚未开始采集
   */
  micAlive: boolean | null

  // 动作
  start: (title?: string, meetingId?: number) => Promise<void>
  stop: () => Promise<void>
  togglePause: () => void
  finalizeCurrent: () => void
  setTitle: (title: string) => void
  /** 顶栏标题输入框失焦后持久化，避免口语自动提取覆盖用户手输的标题。 */
  persistTitle: () => Promise<void>
  setMeetingInfo: (info: SessionState['meetingInfo']) => void
  /** 重新枚举输入设备（用户点开设备菜单时调用） */
  refreshDevices: () => Promise<void>
  /** 切换到指定输入设备；传 null 表示跟随系统默认 */
  selectDevice: (deviceId: string | null) => Promise<void>
  /**
   * 把某个说话人标签改成真实姓名。
   *
   * 会议进行中用户看到「说话人 1」并知道这是谁时用。同时做两件事：
   *  - 本地片段立刻改（界面即时响应，不等网络）
   *  - 调后端 relabel：改声纹库 + 回溯更新已落库的片段
   */
  renameSpeaker: (fromLabel: string, toName: string) => Promise<void>
  reset: () => void
}

let socket: TranscriptSocket | null = null
let recorder: MicrophoneRecorder | null = null
let timerHandle: number | null = null
let startedAt = 0
let pausedAccum = 0
let pausedAt = 0
let isPaused = false
/**
 * 采集参数是否仍在自动降级中。
 *
 * 降级期间前端会自行换配置重试，此时后端推来的 signal_warning 与本地的
 * onSilence 都**不该**升级成错误（否则界面会一边显示「正在尝试第 N 种配置」、
 * 一边又报「麦克风没声音」，把用户带去查硬件，而实际问题可能只是参数组合）。
 */
let captureRetrying = false
let reconnecting = false

/**
 * 说话人显示名。
 *
 * 后端的 speaker 现在有两种取值：
 *  - 已录入声纹命中 → 直接是姓名（如「张三」）
 *  - 未录入 → 会话内聚类得到的「说话人 N」
 * 而历史数据里存的是 FunASR 的裸下标（"0"）。这里兼容三种形态，
 * 避免出现「说话人 说话人 1」这种重复前缀。
 */
export function speakerLabel(speaker: string | null): string | null {
  if (!speaker) return null
  // 纯数字 = 旧数据（FunASR 裸下标）
  return /^\d+$/.test(speaker) ? `说话人 ${speaker}` : speaker
}

/** 说话人 → 稳定色板索引，保证同一说话人颜色不跳变 */
const SPEAKER_COLORS = 6
export function speakerColorIndex(speaker: string | null): number {
  if (!speaker) return 0
  let hash = 0
  for (let i = 0; i < speaker.length; i++) {
    hash = (hash * 31 + speaker.charCodeAt(i)) | 0
  }
  return Math.abs(hash) % SPEAKER_COLORS
}

export const useSession = create<SessionState>((set, get) => ({
  status: 'idle',
  wsState: 'closed',
  meetingId: null,
  sessionId: null,
  title: '',
  meetingInfo: null,
  metadataCaptureMinutes: 3,
  speakerLowConfidenceThreshold: 0.62,
  error: null,
  notice: null,
  elapsedMs: 0,
  level: 0,
  segments: [],
  indexById: new Map(),
  speakers: [],
  liveMinutes: null,

  devices: [],
  activeDeviceId: null,
  micAlive: null,

  setTitle: (title) => set({ title }),
  persistTitle: async () => {
    const { title, meetingId } = get()
    if (meetingId === null || !title.trim()) return
    try {
      await meetingApi.updateMetadata(meetingId, { title: title.trim() })
      set({ title: title.trim() })
    } catch (e) {
      set({ error: e instanceof Error ? e.message : '会议标题保存失败' })
    }
  },
  setMeetingInfo: (meetingInfo) => set({ meetingInfo }),

  start: async (title = '', existingMeetingId?: number) => {
    if (get().status !== 'idle' && get().status !== 'error') return

    captureRetrying = false
    reconnecting = false

    set({
      status: 'connecting',
      error: null,
      notice: null,
      segments: [],
      indexById: new Map(),
      speakers: [],
      liveMinutes: null,
      elapsedMs: 0,
      title: title || defaultTitle(),
      meetingInfo: null,
      metadataCaptureMinutes: 3,
      speakerLowConfidenceThreshold: 0.62,
      meetingId: null,
      micAlive: null,
    })

    // ---- 1. 建立 WebSocket ----
    socket = new TranscriptSocket({
      onMessage: handleServerMessage,
      onState: (state) => {
        set({ wsState: state })
        if (state === 'reconnecting') {
          reconnecting = true
          set({
            error: null,
            notice: '网络连接中断，正在重连…已定稿内容不受影响。',
          })
        } else if (state === 'open' && get().status === 'recording') {
          set({
            error: null,
            notice: reconnecting
              ? '连接已恢复并续录，已定稿内容不会丢失。'
              : get().notice,
          })
          reconnecting = false
        }
      },
      onError: (message) => set({ error: message }),
    })

    socket.connect({
      type: 'start',
      title: get().title,
      ...(existingMeetingId != null ? { meeting_id: existingMeetingId } : {}),
    })

    // ---- 2. 启动麦克风 ----
    recorder = new MicrophoneRecorder({
      deviceId: get().activeDeviceId ?? undefined,
      onChunk: (pcm) => {
        // 暂停时不上传，但保持采集链路（避免重开设备的延迟）
        if (isPaused) return
        socket?.sendAudio(pcm.buffer as ArrayBuffer)
      },
      onLevel: (level) => {
        // 采到声音就撤掉「正在尝试配置」的提示，避免它一直挂在界面上
        if (level > 0) captureRetrying = false
        set({
          level,
          micAlive: level > 0 ? true : get().micAlive,
          ...(level > 0 && get().notice?.includes('自动尝试')
            ? { notice: null }
            : {}),
        })
      },
      onError: (message) => {
        set({ error: message, status: 'error' })
      },
      // 死麦：getUserMedia 成功但送出的 PCM 全为 0。这不是致命错误（会话可以
      // 继续，用户也可能只是暂时没说话），但必须明确提示，否则表现为
      // 「一直在识别中、一个字都不出」，用户无从判断原因。
      onSilence: ({ seconds, suggestedDeviceId, retrying }) => {
        // 自动降级还在进行中时**不要**报错误：那只是「还没找到可用配置」，
        // 不是「麦克风坏了」。此时报死麦会让用户立刻去查硬件，
        // 而系统其实正在自己恢复——只会误导排查方向。
        if (retrying) {
          set({ micAlive: false })
          return
        }

        set({
          micAlive: false,
          error:
            `已有 ${seconds} 秒未采集到任何声音（已自动尝试全部 ` +
            `${CAPTURE_PROFILES.length} 种采集配置）。请检查：` +
            '① 系统输入设备是否选对；② 麦克风静音开关/物理静音键；' +
            '③ 是否被其他程序（会议软件、录音工具）独占；' +
            '④ 浏览器地址栏的麦克风权限。' +
            (suggestedDeviceId ? '（已在其它设备上检测到声音，正在自动切换…）' : ''),
        })
        // 若在其它设备上探测到了信号，直接给出「一键切换」的入口，
        // 比让用户自己去系统设置里翻要有效得多。
        if (suggestedDeviceId) {
          void get().refreshDevices()
          set({ activeDeviceId: suggestedDeviceId })
          void get().selectDevice(suggestedDeviceId)
        }
      },
      // 采集参数自动降级：让「自愈」过程可见，而不是静默地重试。
      // 用户看到这条提示就知道系统在自己找可用配置，不必立刻去查硬件。
      onCaptureProfile: ({ profile, attempt, total }) => {
        captureRetrying = attempt < total
        set({
          error: null,
          notice:
            `未采集到声音，正在自动尝试第 ${attempt}/${total} 种采集配置` +
            `（${profile}）…`,
        })
      },
    })

    try {
      await recorder.start()
    } catch {
      set({ status: 'error' })
      socket?.close()
      socket = null
      recorder = null
      return
    }

    // ---- 3. 计时 ----
    startedAt = Date.now()
    pausedAccum = 0
    isPaused = false
    set({ status: 'recording' })

    if (timerHandle !== null) clearInterval(timerHandle)
    timerHandle = window.setInterval(() => {
      const now = Date.now()
      const pausedTotal = pausedAccum + (isPaused ? now - pausedAt : 0)
      set({ elapsedMs: now - startedAt - pausedTotal })
    }, 200)
  },

  stop: async () => {
    const { status } = get()
    if (status === 'idle' || status === 'stopping') return

    set({ status: 'stopping' })

    if (timerHandle !== null) {
      clearInterval(timerHandle)
      timerHandle = null
    }

    // 先通知服务端收尾，让它把最后一句定稿推回来
    socket?.close()

    // 等一会再关麦克风，确保尾音已被采集
    await new Promise((r) => setTimeout(r, 900))

    await recorder?.stop()
    recorder = null
    socket = null
    isPaused = false

    set({ status: 'idle', level: 0, wsState: 'closed' })
  },

  togglePause: () => {
    const { status } = get()
    if (status !== 'recording' && status !== 'paused') return

    if (status === 'recording') {
      isPaused = true
      pausedAt = Date.now()
      recorder?.pause()
      set({ status: 'paused', level: 0 })
    } else {
      isPaused = false
      pausedAccum += Date.now() - pausedAt
      recorder?.resume()
      set({ status: 'recording' })
    }
  },

  finalizeCurrent: () => {
    socket?.requestFinalize()
  },

  refreshDevices: async () => {
    try {
      const devices = await listAudioInputs()
      set({ devices })
    } catch {
      set({ devices: [] })
    }
  },

  selectDevice: async (deviceId) => {
    set({ activeDeviceId: deviceId })
    if (!recorder) return
    try {
      if (deviceId === null) {
        // 跟随系统默认：重新枚举时把选择清掉即可，下次启动会走默认设备
        return
      }
      await recorder.switchDevice(deviceId)
      set({ error: null })
    } catch (e) {
      const name = (e as DOMException)?.name
      set({
        error:
          name === 'NotAllowedError'
            ? '切换麦克风失败：权限被拒绝。'
            : name === 'NotFoundError'
              ? '切换麦克风失败：该设备已不可用。'
              : '切换麦克风失败，请重试或选择其它设备。',
      })
    }
  },

  renameSpeaker: async (fromLabel, toName) => {
    const name = toName.trim()
    if (!name || name === fromLabel) return

    // 先改本地，界面立刻响应，不等网络往返
    set((s) => ({
      segments: s.segments.map((seg) =>
        seg.speaker === fromLabel ? { ...seg, speaker: name } : seg,
      ),
    }))

    try {
      const meetingId = useSession.getState().meetingId
      if (meetingId === null) {
        set({ error: '会议尚未开始，无法修改发言人' })
        return
      }
      await voiceprintApi.relabel(meetingId, fromLabel, name)
    } catch (e) {
      set({ error: e instanceof Error ? e.message : '说话人改名失败' })
    }
  },

  reset: () => {
    if (timerHandle !== null) {
      clearInterval(timerHandle)
      timerHandle = null
    }
    socket?.close()
    recorder?.stop()
    socket = null
    recorder = null
    isPaused = false
    captureRetrying = false
    reconnecting = false

    set({
      status: 'idle',
      wsState: 'closed',
      meetingId: null,
      sessionId: null,
      meetingInfo: null,
      metadataCaptureMinutes: 3,
      speakerLowConfidenceThreshold: 0.62,
      error: null,
      notice: null,
      elapsedMs: 0,
      level: 0,
      segments: [],
      indexById: new Map(),
      speakers: [],
      liveMinutes: null,
      devices: [],
      micAlive: null,
    })
  },
}))

// ---------------------------------------------------------------------------
// 服务端消息 → 状态更新
// ---------------------------------------------------------------------------

export function handleServerMessage(msg: ServerMessage): void {
  switch (msg.type) {
    case 'ready':
      useSession.setState({
        meetingId: msg.meeting_id,
        sessionId: msg.session_id,
        error: null,
        metadataCaptureMinutes: msg.metadata_capture_minutes ?? 3,
        speakerLowConfidenceThreshold:
          msg.speaker_low_confidence_threshold ?? 0.62,
      })
      break

    case 'partial': {
      // 流式草稿：用同一个 segment_id 覆盖
      upsertSegment({
        segmentId: msg.segment_id,
        text: msg.text,
        isFinal: false,
        startMs: msg.start_ms,
        endMs: msg.end_ms,
        speaker: null,
        seq: -1,
      })
      break
    }

    case 'final': {
      upsertSegment({
        segmentId: msg.segment_id,
        text: msg.text,
        isFinal: true,
        startMs: msg.start_ms,
        endMs: msg.end_ms,
        speaker: msg.speaker,
        speakerScore: msg.speaker_score,
        speakerSource: msg.speaker_source,
        lowConfidence: msg.speaker_low_confidence,
        asrSource: msg.asr_source,
        seq: msg.seq,
      })
      break
    }

    // 服务端确认改名（可能由另一个标签页/客户端发起）：
    // 把所有已显示的同名片段同步成新姓名。后续 final 也由服务端用新名发出。
    case 'speaker_update': {
      useSession.setState((s) => ({
        segments: s.segments.map((seg) =>
          seg.speaker === msg.from ? { ...seg, speaker: msg.to } : seg,
        ),
        speakers: s.speakers.map((sp) =>
          sp.id === msg.from ? { ...sp, id: msg.to } : sp,
        ),
      }))
      break
    }

    case 'speakers':
      useSession.setState({ speakers: msg.data })
      break

    case 'minutes_update':
      useSession.setState({ liveMinutes: msg.minutes })
      break

    case 'noise_warning':
      useSession.setState({ notice: msg.message })
      break

    case 'meeting_meta':
      useSession.setState({
        title: msg.title,
        meetingInfo: {
          participants: (msg.participants || []).join(', '),
          agenda: msg.agenda || '',
          meetingType: msg.meeting_type || '',
        },
        notice: '已通过开场介绍识别会议主题与参会人，可在“会议信息”中修改。',
      })
      break

    case 'translation':
      useSession.setState((s) => ({
        segments: s.segments.map((seg) =>
          seg.segmentId === msg.segment_id
            ? { ...seg, translation: msg.text }
            : seg,
        ),
      }))
      break

    // 服务端独立探测到采集端无音频。前端本地也有 onSilence，两者互为独立
    // 证据——任一方检出都要把状态点亮，避免单侧漏报时用户毫无察觉。
    case 'signal_warning': {
      // 采集参数还在自动降级时，服务端的这条告警不算结论——前端正在换配置
      // 重试，此时弹「没有音频」会把用户的排查方向带偏到硬件上。
      // 降级跑完仍无信号时才升级为错误。
      if (captureRetrying) {
        useSession.setState({ micAlive: false })
        break
      }
      useSession.setState((s) => ({
        micAlive: false,
        // 不覆盖已有的更具体错误（例如权限失败），只在没有错误时提示
        error: s.error ?? msg.message,
      }))
      break
    }

    case 'closed':
      break

    default:
      break
  }
}

/** 把片段写入状态：同 id 覆盖，且定稿后不会被迟到的 partial 回退。
 *
 * 导出以便单测覆盖——这是整个前端最关键的语义，
 * 一旦退化成「追加」会导致屏幕上文字重复。
 */
export function upsertSegment(seg: Segment): void {
  useSession.setState((state) => {
    const index = state.indexById.get(seg.segmentId)

    if (index === undefined) {
      // 新片段：按全局时间轴插入。有界重排可能让 final 晚于后面的片段到达，
      // 仅靠追加会让字幕顺序错乱。
      const segments = [...state.segments, seg].sort(
        (a, b) => a.startMs - b.startMs || a.seq - b.seq,
      )
      const indexById = new Map(
        segments.map((item, i) => [item.segmentId, i] as const),
      )
      return { segments, indexById }
    }

    // 已存在：覆盖。定稿后不再被 partial 回退。
    const existing = state.segments[index]
    if (existing.isFinal && !seg.isFinal) return state

    const segments = state.segments.slice()
    segments[index] = seg
    segments.sort((a, b) => a.startMs - b.startMs || a.seq - b.seq)
    const indexById = new Map(
      segments.map((item, i) => [item.segmentId, i] as const),
    )
    return { segments, indexById }
  })
}

function defaultTitle(): string {
  const d = new Date()
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getMonth() + 1}月${d.getDate()}日 ${pad(d.getHours())}:${pad(d.getMinutes())} 会议`
}

/** 格式化毫秒为 hh:mm:ss 或 mm:ss */
export function formatDuration(ms: number): string {
  const total = Math.floor(ms / 1000)
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  const pad = (n: number) => String(n).padStart(2, '0')
  return h > 0 ? `${pad(h)}:${pad(m)}:${pad(s)}` : `${pad(m)}:${pad(s)}`
}

/** 格式化毫秒为 mm:ss */
export function formatTimestamp(ms: number): string {
  const total = Math.floor(ms / 1000)
  const m = Math.floor(total / 60)
  const s = total % 60
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
}
