/**
 * AudioWorklet 处理器：把输入音频重采样到 16kHz 单声道，
 * 按固定步长切块后通过 postMessage 交给主线程。
 *
 * 为什么用 AudioWorklet 而不是 ScriptProcessorNode：
 * AudioWorklet 跑在独立的高优先级音频线程，不会被主线程的渲染阻塞，
 * 这是保证字幕不掉字的前提。
 *
 * 注意：本文件通过 audioWorklet.addModule() 以【原始脚本】方式加载，
 * 不会经过 TypeScript/Vite 的编译。因此这里必须是纯 JavaScript，
 * 不能出现类型注解。主线程侧的类型声明见 src/audio/recorder.ts。
 */

const TARGET_SAMPLE_RATE = 16000
/** 每块 600ms，与后端 chunk_size=[0,10,5] 对齐 */
const CHUNK_MS = 600
const CHUNK_SAMPLES = (TARGET_SAMPLE_RATE * CHUNK_MS) / 1000

class PCMChunker extends AudioWorkletProcessor {
  constructor() {
    super()
    this.buffer = new Float32Array(CHUNK_SAMPLES)
    this.offset = 0
    /** 当前抽取窗口内已累积的样本和 */
    this.acc = 0
    /** 当前抽取窗口内已累积的样本数 */
    this.accCount = 0
    /** 重采样相位累积器 */
    this.phase = 0

    // sampleRate 是 AudioWorkletGlobalScope 的全局量，代表本上下文的实际采样率。
    // 若拿不到（极少数环境）或不是有限正数，回落到目标采样率，避免 ratio
    // 变成 NaN/0 后整个循环静默退化成「一块都发不出」。
    var sr = typeof sampleRate === 'number' && isFinite(sampleRate) && sampleRate > 0
      ? sampleRate
      : TARGET_SAMPLE_RATE
    this.ratio = sr / TARGET_SAMPLE_RATE

    // 采集节点声明了 numberOfOutputs=0；但若宿主仍按 1 个输出调用，
    // process() 的第二个参数会带 outputs。这里做一次自证：真正收到过
    // 多少帧、发出过多少块，异常时可以据此定位，而不是无声无息。
    this._framesIn = 0
    this._chunksOut = 0
  }

  process(inputs) {
    const input = inputs[0]
    // 没有输入连接时 input 会是空数组——这是正常状态（例如链路还没建立），
    // 必须 return true 保持节点存活，绝不能 return false 把自己注销掉。
    if (!input || input.length === 0) return true

    const channels = input.length
    const frames = input[0].length
    if (frames === 0) return true

    this._framesIn += frames

    // 直通快路径：上下文已是 16kHz 时无需重采样，逐样本拷贝即可。
    // 走这条路可以完全绕开相位累积的浮点误差。
    if (this.ratio === 1) {
      const ch0 = input[0]
      for (let i = 0; i < frames; i++) {
        let sample = ch0[i]
        if (channels > 1) {
          let sum = 0
          for (let c = 0; c < channels; c++) sum += input[c][i]
          sample = sum / channels
        }
        this._emit(sample)
      }
      return true
    }

    const ratio = this.ratio

    for (let i = 0; i < frames; i++) {
      // 多声道混为单声道
      let sample = 0
      for (let c = 0; c < channels; c++) sample += input[c][i]
      sample /= channels

      // 降采样前先做盒式滤波（对窗口内样本取均值）再抽取。
      // 直接抽取会引入混叠：48kHz→16kHz 时 8kHz 以上的能量会折叠回可听频段，
      // 对 ASR 特征提取（fbank）是明显的噪声来源。
      this.acc += sample
      this.accCount += 1

      this.phase += 1
      if (this.phase < ratio) continue
      this.phase -= ratio

      const out = this.accCount > 0 ? this.acc / this.accCount : 0
      this.acc = 0
      this.accCount = 0

      this._emit(out)
    }

    return true
  }

  /** 写入输出缓冲，满一块就发给主线程。 */
  _emit(sample) {
    this.buffer[this.offset++] = sample

    if (this.offset >= CHUNK_SAMPLES) {
      // 拷贝后转移所有权，避免音频线程与主线程争用同一块内存
      const chunk = this.buffer.slice(0, CHUNK_SAMPLES)
      this._chunksOut++
      this.port.postMessage({ type: 'chunk', pcm: chunk.buffer }, [chunk.buffer])
      this.offset = 0
    }
  }
}

registerProcessor('pcm-chunker', PCMChunker)
