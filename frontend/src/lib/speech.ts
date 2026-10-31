/** 语音输入（会中「说一句就加参会人」用）。
 *
 * 只做「说 → 文字」这一步，识别结果放进输入框让用户看一眼再提交：
 * 直接拿 ASR 结果建人员，念错一个音就会多出一个不存在的人。
 *
 * 用浏览器自带的 SpeechRecognition（Chrome/Edge 支持，且需要 HTTPS 或
 * localhost）；不支持时 ``supported=false``，调用方隐藏麦克风按钮即可，
 * 不能因此让面板报错。
 */

export interface SpeechResult {
  transcript: string
  isFinal: boolean
}

export interface SpeechInput {
  supported: boolean
  listening: boolean
  error: string | null
  start: () => void
  stop: () => void
}

interface RecognitionLike {
  lang: string
  continuous: boolean
  interimResults: boolean
  maxAlternatives: number
  start: () => void
  stop: () => void
  abort: () => void
  onresult: ((event: RecognitionEventLike) => void) | null
  onerror: ((event: { error?: string }) => void) | null
  onend: (() => void) | null
}

interface RecognitionEventLike {
  resultIndex: number
  results: {
    length: number
    [index: number]: { isFinal: boolean; 0: { transcript: string } }
  }
}

type RecognitionCtor = new () => RecognitionLike

function ctor(): RecognitionCtor | null {
  if (typeof window === 'undefined') return null
  const win = window as unknown as {
    SpeechRecognition?: RecognitionCtor
    webkitSpeechRecognition?: RecognitionCtor
  }
  return win.SpeechRecognition ?? win.webkitSpeechRecognition ?? null
}

/** 是否支持语音输入（用于决定要不要显示麦克风按钮）。 */
export function speechSupported(): boolean {
  return ctor() !== null
}

/** 把识别结果里多余的空格和标点收拾干净：只保留姓名需要的字符。 */
export function normalizeSpokenName(raw: string): string {
  return (raw || '')
    .replace(/[\s\u3000]+/g, '')
    .replace(/[。，、,.!?！？；;：:]+/g, '')
    .slice(0, 32)
}

/** 一次性识别（说一句就停）。返回 disposer，用来在组件卸载时收尾。 */
export function listenOnce(
  onResult: (result: SpeechResult) => void,
  onError: (message: string) => void,
  onEnd?: () => void,
): { stop: () => void } {
  const Ctor = ctor()
  if (Ctor === null) {
    onError('当前浏览器不支持语音输入，请用 Chrome/Edge 或手动输入')
    return { stop: () => {} }
  }
  const recognition = new Ctor()
  recognition.lang = 'zh-CN'
  recognition.continuous = false
  recognition.interimResults = false
  recognition.maxAlternatives = 1

  let stopped = false
  recognition.onresult = (event) => {
    const index = event.resultIndex ?? 0
    const result = event.results?.[index]
    if (!result) return
    onResult({ transcript: result[0]?.transcript ?? '', isFinal: result.isFinal })
  }
  recognition.onerror = (event) => {
    const code = event?.error ?? 'unknown'
    onError(
      code === 'not-allowed'
        ? '麦克风权限被拒绝，请在浏览器地址栏允许后重试'
        : code === 'no-speech'
          ? '没有听到声音，请再说一次'
          : `语音输入失败：${code}`,
    )
  }
  recognition.onend = () => {
    if (!stopped) onEnd?.()
  }

  try {
    recognition.start()
  } catch (err) {
    onError(err instanceof Error ? err.message : '语音输入启动失败')
  }

  return {
    stop: () => {
      stopped = true
      try {
        recognition.abort()
      } catch {
        /* 已经结束就忽略 */
      }
    },
  }
}
