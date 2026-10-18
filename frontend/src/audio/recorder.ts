/** 麦克风采集：getUserMedia → AudioWorklet → 16kHz PCM 分块回调。 */

/**
 * worklet 脚本放在 public/ 下，以固定 URL 提供。
 *
 * 不用 `?url` 导入的原因：Vite 会把小文件内联成 data:text/javascript URI，
 * 而 addModule() 对 data URI 的支持受 CSP 与 URL 长度限制，生产环境不可靠。
 * 放在 public/ 下能保证始终是一个真实的、可缓存的文件路径。
 *
 * 注意：src/audio/pcm-worklet.js 与 public/pcm-worklet.js 必须保持一致，
 * 改动前者后需同步复制到 public/。
 */
const WORKLET_URL = '/pcm-worklet.js'

export interface RecorderOptions {
  /** 每块 16kHz float32 PCM 到达时回调 */
  onChunk: (pcm: Float32Array) => void
  /** 实时音量 0~1，用于波形展示 */
  onLevel?: (level: number) => void
  /** 采集错误 */
  onError?: (message: string) => void
  /**
   * 采集到「持续全静音」时回调一次（不会重复触发）。
   *
   * 典型场景：系统选了不存在的输入设备、被其他程序独占、或静音开关打开，
   * 此时 getUserMedia 会成功、AudioContext 状态也是 running，但送出来的
   * PCM 全是 0。用户看到的只是「一直在识别中、一个字都不出」——极难自诊断。
   * 这里主动探测并提示，把黑盒变成明确的错误。
   */
  /**
   * 采集到「持续全静音」时回调一次（不会重复触发）。
   *
   * `retrying` 表示采集参数还有没试完的组合。为 true 时界面**不要**弹错误——
   * 自动降级仍在进行，此时报「麦克风没声音」是误报，会让用户去查硬件，
   * 而实际上系统正在自己恢复。
   */
  onSilence?: (detail: { seconds: number; suggestedDeviceId?: string; retrying: boolean }) => void
  /** 指定输入设备；不传则用浏览器默认设备 */
  deviceId?: string
  /**
   * 采集参数自动降级时回调。
   *
   * 「收到全零 PCM」时会自动换一组参数重试（见 CAPTURE_PROFILES），
   * 这里把「换成了哪一组、试到第几次」告知界面，让用户看得见自愈过程，
   * 而不是干等一个永远不来的字幕。
   */
  onCaptureProfile?: (detail: { profile: string; attempt: number; total: number }) => void
}

/** 一个可选音频输入设备。 */
export interface AudioInputDevice {
  deviceId: string
  label: string
}

/**
 * 一组采集参数。
 *
 * 为什么需要多组：真实机器上「麦克风有权限、AudioContext 在跑、但送出来
 * 的 PCM 全是 0」是极常见的故障，而它往往不是麦克风坏了，而是请求的音频
 * **格式与设备原生格式不一致**，被驱动的 APO（音频处理对象）整条链路静音。
 *
 * Windows 11 上录音设备默认的「混音格式」实测为 **48000 Hz / 32-bit / 2 声道**
 * （WAVEFORMATEXTENSIBLE）。实测矩阵（见同机 WASAPI 对照实验）显示：
 *   - 请求原生混音格式            → 有信号
 *   - 请求 IEEE float 48000/2     → 全零   ← 而 Web Audio 内部一律是 float32
 *   - 请求 48000/1 **单声道**      → 全零
 *   - 请求 44100 / 16-bit PCM      → 全零
 * 根因多为 Realtek / Intel Smart Sound 的「音频增强」「语音清晰度」等
 * 设备效果（APO）：只给匹配原生格式的客户端喂数据，其余一律静默返回空缓冲。
 *
 * 因此这里**默认就贴近原生格式**：不限声道、不限采样率、关闭浏览器处理。
 * 三个维度都会影响能否出声，但都不影响音质——pcm-worklet.js 内部本来就做
 * 多声道混音与盒式滤波降采样，拿 48k/2ch 照样输出正确的 16k 单声道 PCM。
 */
export interface CaptureProfile {
  /** AudioContext 采样率；null 表示不指定（跟随设备，通常 48k） */
  rate: number | null
  /** 是否开启浏览器音频处理（回声消除 / 降噪 / 自动增益） */
  apm: boolean
  /**
   * 请求的声道数；**null 表示不约束，用设备原生的（通常是 2）**。
   *
   * 这一项是关键：实测请求单声道（48000/1）会被驱动直接静音，
   * 所以绝不能默认写死 channelCount: 1。多声道由 worklet 混成单声道。
   */
  channels: number | null
  /** 用于日志与界面提示的说明 */
  label: string
}

/** 按「越靠前越贴近设备原生格式」排序：先保能出声，再考虑其它。 */
export const CAPTURE_PROFILES: CaptureProfile[] = [
  { rate: null, apm: false, channels: null, label: '原生格式·处理关' },
  { rate: null, apm: true, channels: null, label: '原生格式·处理开' },
  { rate: null, apm: false, channels: 1, label: '原生率·单声道·处理关' },
  { rate: 16000, apm: true, channels: 1, label: '16kHz·单声道·处理开' },
]

/**
 * 列出可用的音频输入设备。
 *
 * 注意：未授权前 `label` 是空串（浏览器隐私保护）。所以本函数会先尝试
 * 取一次权限，拿到 label 后再枚举——否则用户在下拉框里只会看到一串
 * 无意义的设备 ID，根本没法选。
 */
export async function listAudioInputs(): Promise<AudioInputDevice[]> {
  if (!navigator.mediaDevices?.enumerateDevices) return []

  // 先尝试拿权限，让 label 可读；失败也不影响后续枚举（只是标签为空）
  try {
    const probe = await navigator.mediaDevices.getUserMedia({ audio: true })
    probe.getTracks().forEach((t) => t.stop())
  } catch {
    // 用户拒绝或没有设备：仍然继续枚举，至少能拿到 deviceId
  }

  const devices = await navigator.mediaDevices.enumerateDevices()
  return devices
    .filter((d) => d.kind === 'audioinput')
    .map((d, i) => ({
      deviceId: d.deviceId,
      label: d.label || `麦克风 ${i + 1}`,
    }))
}

/**
 * 逐个打开设备并测量电平，返回第一个「有信号」的设备 ID。
 *
 * 这是解决「浏览器默认选中的设备是哑的」这一类故障的兜底手段：
 * 真实场景里机器常挂着 USB 麦克风、虚拟声卡、HDMI 音频等多个输入，
 * 默认设备未必是用户在说话的那一个。
 *
 * 每个设备探测 ~600ms；只测前 `maxProbe` 个，避免设备多时启动过慢。
 * 探测失败或超时都视为「无信号」，继续试下一个。
 */
export async function pickLoudestDevice(maxProbe = 5): Promise<string | null> {
  if (!navigator.mediaDevices?.enumerateDevices) return null

  let devices: MediaDeviceInfo[]
  try {
    devices = (await navigator.mediaDevices.enumerateDevices()).filter((d) => d.kind === 'audioinput')
  } catch {
    return null
  }
  if (devices.length <= 1) return null // 只有一个设备，没什么可挑的

  let best: { id: string; peak: number } | null = null

  for (const d of devices.slice(0, maxProbe)) {
    const peak = await probeDevicePeak(d.deviceId, 600)
    if (peak === null) continue
    if (!best || peak > best.peak) best = { id: d.deviceId, peak }
    // 已经足够响，不必再试其它设备
    if (peak > 0.01) break
  }

  // 全都无信号时返回 null，交由调用方走「死麦」提示路径
  return best && best.peak > 0.001 ? best.id : null
}

/** 打开指定设备测 600ms 的峰值电平；失败返回 null。 */
async function probeDevicePeak(deviceId: string, ms: number): Promise<number | null> {
  let stream: MediaStream | null = null
  let ctx: AudioContext | null = null
  try {
    // 不约束声道与采样率：请求非原生格式（尤其单声道）在部分 Windows 驱动上
    // 会拿到全零缓冲，探测结果会误判成「这个设备是哑的」。
    stream = await navigator.mediaDevices.getUserMedia({
      audio: { deviceId: { exact: deviceId } },
      video: false,
    })
    ctx = new AudioContext()
    const src = ctx.createMediaStreamSource(stream)
    const an = ctx.createAnalyser()
    an.fftSize = 1024
    src.connect(an)
    const buf = new Uint8Array(an.fftSize)

    let peak = 0
    const deadline = performance.now() + ms
    while (performance.now() < deadline) {
      an.getByteTimeDomainData(buf)
      for (let i = 0; i < buf.length; i++) {
        const v = Math.abs(buf[i] - 128) / 128
        if (v > peak) peak = v
      }
      await new Promise((r) => setTimeout(r, 40))
    }
    return peak
  } catch {
    return null
  } finally {
    stream?.getTracks().forEach((t) => t.stop())
    if (ctx) await ctx.close().catch(() => {})
  }
}

export class MicrophoneRecorder {
  private stream: MediaStream | null = null
  private ctx: AudioContext | null = null
  private source: MediaStreamAudioSourceNode | null = null
  private worklet: AudioWorkletNode | null = null
  private analyser: AnalyserNode | null = null
  /** 0 增益节点，仅用于把音频图接到 destination，保证采集节点被调度 */
  private silentGain: GainNode | null = null
  private levelTimer: number | null = null
  /** 「无数据」看门狗定时器 */
  private noDataTimer: number | null = null
  // 显式写成 Uint8Array<ArrayBuffer>：TS 5.7 起 Uint8Array 是泛型，
  // 而 Web Audio 的 getByteTimeDomainData 只接受底层为 ArrayBuffer 的视图
  private levelBuf: Uint8Array<ArrayBuffer> | null = null

  /** 已连续收到的「全零块」时长（毫秒），用于探测死麦 */
  private silentMs = 0
  /** 是否已经上报过静音告警，保证只提示一次 */
  private silenceReported = false
  /** 累计已收样本数，用于静音告警里给出「已采集多久」 */
  private totalSamples = 0
  /** 累计收到的音频块数，用于区分「没收到块」与「收到块但全零」 */
  private chunkCount = 0

  /** 当前使用的采集参数在 CAPTURE_PROFILES 中的下标 */
  private profileIndex = 0
  /** 是否正在切换采集参数（防止重入） */
  private switchingProfile = false
  /** 当前这组参数下已连续收到多少毫秒的全零音频 */
  private zeroMsInProfile = 0
  /** 是否处于暂停：暂停时音轨被禁用，PCM 必然全零，不能当成故障 */
  private paused = false

  /** 判定为「死麦」的连续静音时长阈值（毫秒） */
  private static readonly SILENCE_ALERT_MS = 3000
  /** 当前参数连续全零多久就自动换下一组参数（毫秒） */
  private static readonly PROBE_MS = 1500

  constructor(private opts: RecorderOptions) {}

  /** 请求麦克风并开始采集。 */
  async start(): Promise<void> {
    this.silentMs = 0
    this.silenceReported = false
    this.totalSamples = 0
    this.chunkCount = 0
    this.zeroMsInProfile = 0
    this.profileIndex = 0
    this.paused = false

    await this.buildGraph()
  }

  /**
   * 按当前 profile 建立整条采集链路（流 → 上下文 → worklet → 图）。
   *
   * 单独抽出来是因为「自动降级」需要把它整条重建一遍：换采样率意味着
   * AudioContext 也要换，没法像 switchDevice 那样只换源头。
   */
  private async buildGraph(): Promise<void> {
    const profile = CAPTURE_PROFILES[this.profileIndex]

    try {
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          // 指定设备时用 exact，否则用浏览器默认
          ...(this.opts.deviceId ? { deviceId: { exact: this.opts.deviceId } } : {}),
          // 会议场景默认开启回声消除/降噪/自动增益；但若这组参数采不到
          // 声音，会自动降级到关闭它们的组合（见 CAPTURE_PROFILES）
          echoCancellation: profile.apm,
          noiseSuppression: profile.apm,
          autoGainControl: profile.apm,
          // 关键：不约束声道时**必须整个键都不出现**。写成 channelCount:
          // undefined 有些实现仍会当成约束处理，从而触发驱动的静音行为。
          ...(profile.channels !== null ? { channelCount: profile.channels } : {}),
        },
        video: false,
      })
    } catch (err) {
      const name = (err as DOMException)?.name
      if (name === 'NotAllowedError') {
        this.opts.onError?.('麦克风权限被拒绝。请在浏览器地址栏的权限设置中允许麦克风访问后重试。')
      } else if (name === 'NotFoundError') {
        this.opts.onError?.('未检测到可用的麦克风设备。')
      } else if (name === 'OverconstrainedError') {
        // 指定的设备在页面加载后消失了（拔掉 USB 麦克风是最常见的场景）
        this.opts.onError?.('指定的麦克风已不可用（可能已拔出）。将回退到系统默认设备，请重新选择。')
      } else {
        this.opts.onError?.('麦克风启动失败，请检查设备与浏览器权限。')
      }
      throw err
    }

    this.ctx = this.createContext(profile.rate)

    // 浏览器可能因自动播放策略挂起音频上下文
    if (this.ctx.state === 'suspended') {
      await this.ctx.resume()
    }

    try {
      await this.ctx.audioWorklet.addModule(WORKLET_URL)
    } catch (err) {
      // worklet 脚本加载/解析失败在浏览器里没有明显提示，这里必须显式上报，
      // 否则表现为「点了开始但永远没有字幕」这种极难排查的现象
      console.error('[recorder] AudioWorklet 加载失败', err)
      this.opts.onError?.(
        '音频处理模块加载失败。若刚更新过代码请强制刷新页面（Ctrl/Cmd+Shift+R）后重试。',
      )
      await this.releaseContext()
      throw err
    }

    this.source = this.ctx.createMediaStreamSource(this.stream)
    this.worklet = new AudioWorkletNode(this.ctx, 'pcm-chunker', {
      // 显式声明：本节点只做采集，不产生任何输出。
      // 不声明 numberOfOutputs=0 时它会带一个默认输出，而我们的 process()
      // 从不写 outputs，浏览器有可能因为「输出没接到任何地方」而不稳定地
      // 调度这个节点——表现就是偶发地完全收不到 chunk。
      numberOfInputs: 1,
      numberOfOutputs: 0,
      channelCount: 1,
      channelCountMode: 'explicit',
      channelInterpretation: 'speakers',
    })

    this.worklet.port.onmessage = (ev: MessageEvent<{ type: string; pcm: ArrayBuffer }>) => {
      if (ev.data.type === 'chunk') {
        this.chunkCount += 1
        const pcm = new Float32Array(ev.data.pcm)
        this.watchSilence(pcm)
        this.opts.onChunk(pcm)
      }
    }

    // 音量分析走独立节点，不影响分块逻辑
    this.analyser = this.ctx.createAnalyser()
    this.analyser.fftSize = 512
    this.analyser.smoothingTimeConstant = 0.75
    this.levelBuf = new Uint8Array(this.analyser.frequencyBinCount)

    this.source.connect(this.worklet)
    this.source.connect(this.analyser)
    this.keepGraphAlive()

    // 两个循环都只在首次建图时启动：自动降级会重建音频图，但这两个定时器
    // 读的是实例字段（analyser / chunkCount），重建后照常工作，重复启动
    // 只会造成多条并行循环和音量回调抖动。
    this.startLevelLoop()
    this.startNoDataWatchdog()
  }

  /**
   * 自动降级：当前参数采到的全是 0，换下一组参数重建链路。
   *
   * 这是「收到块但全零」这类故障的**自愈**动作。此前只能靠用户自己打开
   * 自检页、手动换设备来判断；现在前端自己先试完所有已知可行的组合，
   * 试完仍然全零才把结论交给用户，那时就基本能确定是硬件/系统层面了。
   *
   * 只降级不升级：一旦某组参数采到过声音，就不再动它。
   */
  private async escalateProfile(): Promise<void> {
    if (this.switchingProfile) return
    if (this.profileIndex >= CAPTURE_PROFILES.length - 1) return

    this.switchingProfile = true
    const next = this.profileIndex + 1
    try {
      console.warn(
        `[recorder] 采集参数「${CAPTURE_PROFILES[this.profileIndex].label}」连续 ` +
          `${(this.zeroMsInProfile / 1000).toFixed(1)}s 全零，自动切换到「${CAPTURE_PROFILES[next].label}」`,
      )

      // 先拆旧链路（不停定时器：它们按实例字段取用，重建后自动生效）
      this.worklet?.port.close()
      this.worklet?.disconnect()
      this.source?.disconnect()
      this.analyser?.disconnect()
      this.silentGain?.disconnect()
      this.stream?.getTracks().forEach((t) => t.stop())
      await this.releaseContext()
      this.source = null
      this.worklet = null
      this.analyser = null
      this.silentGain = null

      this.profileIndex = next
      this.zeroMsInProfile = 0

      // 进入最后一组参数时重新武装静音告警。
      // 否则告警只会在第一组参数处触发一次（那时还在重试、只更新提示），
      // 之后 silenceReported 一直为真，即便所有参数都试完了也永远不会
      // 向用户报「死麦」——用户就只能干等着，得不到任何结论。
      if (next === CAPTURE_PROFILES.length - 1) {
        this.silenceReported = false
        this.silentMs = 0
      }

      await this.buildGraph()

      this.opts.onCaptureProfile?.({
        profile: CAPTURE_PROFILES[next].label,
        attempt: next + 1,
        total: CAPTURE_PROFILES.length,
      })
    } catch (err) {
      // 降级失败不该让整场会议挂掉：保持现状，交给静音告警去提示用户
      console.error('[recorder] 采集参数降级失败', err)
    } finally {
      this.switchingProfile = false
    }
  }

  /**
   * 把音频图挂到 destination 上，保证节点被持续调度。
   *
   * Chromium 的渲染图只调度「能连到 destination 的活跃链路」。采集节点虽然
   * 有 MediaStreamSource 在推数据，但如果整条链路的末端没有落到 destination，
   * 节点可能被判定为不可达而被回收/不调度，process() 就再也不被调用——
   * 结果就是「麦克风权限正常、波形也在动、但一块 PCM 都收不到」。
   * 这里用 0 增益节点接到 destination：链路可达，同时不会有任何声音被播出去
   * （把麦克风回放出来会形成啸叫）。
   */
  private keepGraphAlive(): void {
    if (!this.ctx || !this.worklet || !this.source) return
    this.silentGain = this.ctx.createGain()
    this.silentGain.gain.value = 0
    this.source.connect(this.silentGain)
    this.silentGain.connect(this.ctx.destination)
  }

  /**
   * 探测「死麦」：连续多个音频块全是 0 时判定采集链路无效。
   *
   * 为什么必须探测而不是等后端报错：全零 PCM 会被后端当成「一段静音」正常
   * 丢弃，整场会议落库 0 段、没有任何报错。用户只能看到「一直在识别中」，
   * 无法区分是设备问题、权限问题还是模型问题。这里在前端就把原因点明。
   */
  private watchSilence(pcm: Float32Array): void {
    if (pcm.length === 0) return
    this.totalSamples += pcm.length

    // 暂停时音轨被禁用，PCM 必然全零——这是预期状态，不是故障。
    // 若不在这里挡住，暂停 3 秒后就会误报「麦克风没声音」。
    if (this.paused) return

    // 逐样本求绝对值最大，比 RMS 更快且同样能判定「全零」
    let peak = 0
    for (let i = 0; i < pcm.length; i++) {
      const v = pcm[i] < 0 ? -pcm[i] : pcm[i]
      if (v > peak) peak = v
    }

    if (peak > 0) {
      this.silentMs = 0
      this.silenceReported = false
      // 这组参数确实采到了声音，后续无需再降级
      this.zeroMsInProfile = 0
      return
    }

    const chunkMs = (pcm.length / 16000) * 1000
    this.silentMs += chunkMs
    this.zeroMsInProfile += chunkMs

    // 自动降级：比 3 秒的静音告警更早介入，尽量在用户察觉前自愈
    if (this.zeroMsInProfile >= MicrophoneRecorder.PROBE_MS) {
      void this.escalateProfile()
    }

    if (!this.silenceReported && this.silentMs >= MicrophoneRecorder.SILENCE_ALERT_MS) {
      this.silenceReported = true
      // 静音告警里附带「换设备」的机会：真实场景中默认设备常常是哑的，
      // 直接给用户一个可切换的建议比让他自己去系统设置里翻要有效得多。
      void this.suggestAlternativeDevice().then((suggestedDeviceId) => {
        this.opts.onSilence?.({
          seconds: (this.totalSamples / 16000) | 0,
          suggestedDeviceId,
          retrying: this.profileIndex < CAPTURE_PROFILES.length - 1,
        })
      })
    }
  }

  /**
   * 静音时探测其它设备，找一个「有信号」的作为建议。
   *
   * 只探测一次（由 silenceReported 保证），且完全异步，不阻塞采集链路。
   */
  private async suggestAlternativeDevice(): Promise<string | undefined> {
    if (this.opts.deviceId) return undefined // 用户已显式指定，不再自作主张
    try {
      const found = await pickLoudestDevice()
      return found ?? undefined
    } catch {
      return undefined
    }
  }

  /**
   * 独立的「无数据」看门狗：启动 4 秒后仍一块都没收到，说明 worklet
   * 的 process() 根本没被调度（或节点被回收），这与「收到块但全零」
   * 是完全不同的两类故障，必须分开报，否则排查会一直跑偏。
   */
  private startNoDataWatchdog(): void {
    if (this.noDataTimer !== null) return
    this.noDataTimer = window.setTimeout(() => {
      if (this.chunkCount === 0 && this.stream) {
        this.opts.onError?.(
          '音频采集未启动：4 秒内未收到任何音频数据。' +
            `当前上下文采样率 ${this.ctx?.sampleRate ?? '未知'}Hz，状态 ${this.ctx?.state ?? '未知'}。` +
            '请强制刷新页面（Ctrl/Cmd+Shift+R）后重试；若仍无效请反馈此信息。',
        )
      }
    }, 4000)
  }

  /**
   * 创建 AudioContext。
   *
   * 16kHz 是 ASR 的目标采样率，按它建上下文时 worklet 走的是「直通快路径」。
   * 但强行指定采样率会迫使 MediaStreamAudioSourceNode 做一次硬件原生率
   * （通常 48k）→ 16k 的重采样，个别 Windows 驱动下这条路径会直接产出全零。
   *
   * 因此这里接受 profile 指定的采样率：传 null 时用设备原生采样率，
   * 降采样交给 pcm-worklet.js 内部的盒式滤波——实测那条路在 48k 下同样正确，
   * 而且完全绕开了驱动重采样的坑。
   */
  private createContext(rate: number | null): AudioContext {
    if (rate === null) return new AudioContext()
    try {
      return new AudioContext({ sampleRate: rate })
    } catch {
      // 部分设备/浏览器不支持指定采样率，回落到默认
      return new AudioContext()
    }
  }

  private startLevelLoop(): void {
    if (!this.opts.onLevel) return

    const tick = () => {
      if (!this.analyser || !this.levelBuf) return
      this.analyser.getByteTimeDomainData(this.levelBuf)

      // 计算 RMS
      let sum = 0
      for (let i = 0; i < this.levelBuf.length; i++) {
        const v = (this.levelBuf[i] - 128) / 128
        sum += v * v
      }
      const rms = Math.sqrt(sum / this.levelBuf.length)
      // 映射到 0~1，并做一点曲线拉伸让低音量也能看见
      this.opts.onLevel?.(Math.min(1, rms * 3.2))

      this.levelTimer = window.setTimeout(tick, 80)
    }

    tick()
  }

  /** 暂停采集（保留流，便于快速恢复）。 */
  pause(): void {
    this.paused = true
    this.stream?.getAudioTracks().forEach((t) => (t.enabled = false))
  }

  resume(): void {
    this.stream?.getAudioTracks().forEach((t) => (t.enabled = true))
    // 恢复后重新计时，避免把暂停期间的静音算进故障判定
    this.paused = false
    this.silentMs = 0
    this.silenceReported = false
    this.zeroMsInProfile = 0
  }

  /**
   * 热切换到另一个输入设备（会议进行中也能换）。
   *
   * 实现是「换流不换图」：只替换 MediaStreamAudioSourceNode 的源头，
   * AudioContext / worklet / 分析器全部复用，因此不会中断已经积累的
   * 转写状态，也不会有重开音频设备的爆音。
   */
  async switchDevice(deviceId: string): Promise<void> {
    if (!this.ctx || !this.worklet) throw new Error('录音尚未启动')

    // 沿用当前这组采集参数：用户手动换设备时，若当前参数是被自动降级过的，
    // 说明在这台机器上它才是能出声的那一组，换回去可能又变全零。
    const profile = CAPTURE_PROFILES[this.profileIndex]
    const newStream = await navigator.mediaDevices.getUserMedia({
      audio: {
        deviceId: { exact: deviceId },
        echoCancellation: profile.apm,
        noiseSuppression: profile.apm,
        autoGainControl: profile.apm,
        ...(profile.channels !== null ? { channelCount: profile.channels } : {}),
      },
      video: false,
    })

    // 先建新源再接图，成功后才拆旧源——失败时原链路仍然可用
    const newSource = this.ctx.createMediaStreamSource(newStream)
    newSource.connect(this.worklet)
    if (this.analyser) newSource.connect(this.analyser)
    if (this.silentGain) newSource.connect(this.silentGain)

    this.source?.disconnect()
    this.stream?.getTracks().forEach((t) => t.stop())

    this.source = newSource
    this.stream = newStream

    // 换设备后重新计时死麦检测：新设备从「还没听到声音」开始
    this.silentMs = 0
    this.silenceReported = false
    this.totalSamples = 0
    this.chunkCount = 0
    this.zeroMsInProfile = 0
  }

  /** 停止采集并释放全部资源。 */
  async stop(): Promise<void> {
    if (this.levelTimer !== null) {
      clearTimeout(this.levelTimer)
      this.levelTimer = null
    }
    if (this.noDataTimer !== null) {
      clearTimeout(this.noDataTimer)
      this.noDataTimer = null
    }

    this.worklet?.port.close()
    this.worklet?.disconnect()
    this.source?.disconnect()
    this.analyser?.disconnect()
    this.silentGain?.disconnect()

    this.stream?.getTracks().forEach((t) => t.stop())

    await this.releaseContext()

    this.stream = null
    this.source = null
    this.worklet = null
    this.analyser = null
    this.silentGain = null
    this.levelBuf = null
  }

  /** 关闭音频上下文。启动失败与正常停止都会用到。 */
  private async releaseContext(): Promise<void> {
    this.stream?.getTracks().forEach((t) => t.stop())
    const ctx = this.ctx
    this.ctx = null
    if (!ctx) return
    try {
      // close() 位于「停止录音」的必经路径上。个别浏览器/驱动组合下它可能
      // 长时间不 resolve，一旦挂住，界面就会永远停在「正在录音」——用户看到
      // 的只是「念完了但什么都不发生」。这里给个上限兜底，超时照常往下走，
      // 底层资源由浏览器在音轨停止后自行回收。
      await Promise.race([
        ctx.close(),
        new Promise<void>((resolve) => window.setTimeout(resolve, 1000)),
      ])
    } catch {
      // 忽略关闭异常
    }
  }
}

/** 检查浏览器是否支持所需的 API。 */
export function checkBrowserSupport(): { ok: boolean; reason?: string } {
  if (!navigator.mediaDevices?.getUserMedia) {
    return { ok: false, reason: '当前浏览器不支持麦克风采集，请使用 Chrome / Edge / Firefox 最新版。' }
  }
  if (typeof AudioWorkletNode === 'undefined') {
    return { ok: false, reason: '当前浏览器不支持 AudioWorklet，请升级浏览器。' }
  }
  if (!window.isSecureContext) {
    return { ok: false, reason: '麦克风需要安全上下文。请通过 https 或 localhost 访问本应用。' }
  }
  return { ok: true }
}
