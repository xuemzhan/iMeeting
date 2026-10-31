/** 本地偏好设置（外观 / 阅读字号 / 分栏宽度），持久化到 localStorage。 */

import { create } from 'zustand'

import {
  FONT_SCALE_DEFAULT,
  PANE_WIDTH_DEFAULT,
  clampFontScale,
  clampPaneWidth,
} from '../lib/readerPrefs'

export type ThemeMode = 'light' | 'dark' | 'system'

const STORAGE_KEY = 'imeeting-settings'

export interface SettingsState {
  theme: ThemeMode
  setTheme: (theme: ThemeMode) => void
  /** 实时转写字号缩放（0.85 ~ 1.5，默认 1） */
  transcriptScale: number
  setTranscriptScale: (scale: number) => void
  /** 会议纪要字号缩放（0.85 ~ 1.5，默认 1） */
  minutesScale: number
  setMinutesScale: (scale: number) => void
  /** 桌面端纪要面板宽度（px），可拖动分栏调整 */
  minutesPaneWidth: number
  setMinutesPaneWidth: (width: number) => void
}

interface Persisted {
  theme?: unknown
  transcriptScale?: unknown
  minutesScale?: unknown
  minutesPaneWidth?: unknown
}

function readPersisted(): Persisted {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return {}
    const parsed: unknown = JSON.parse(raw)
    return parsed && typeof parsed === 'object' ? (parsed as Persisted) : {}
  } catch {
    // localStorage 不可用（隐私模式等）或旧数据损坏时用默认值
    return {}
  }
}

function readTheme(): ThemeMode {
  const value = readPersisted().theme
  return value === 'light' || value === 'dark' || value === 'system'
    ? value
    : 'light'
}

const initial = readPersisted()

/**
 * 把主题应用到 <html>。
 *
 * `.dark` 这套深色变量在 index.css 里早已定义完备，但此前没有任何代码去
 * 挂载这个 class —— 等于深色主题一直不可达。这里补上最后一环。
 */
function applyTheme(mode: ThemeMode): void {
  if (typeof document === 'undefined') return
  const dark =
    mode === 'dark' ||
    (mode === 'system' && window.matchMedia('(prefers-color-scheme: dark)').matches)
  document.documentElement.classList.toggle('dark', dark)
}

function persist(state: SettingsState): void {
  try {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        theme: state.theme,
        transcriptScale: state.transcriptScale,
        minutesScale: state.minutesScale,
        minutesPaneWidth: state.minutesPaneWidth,
      }),
    )
  } catch {
    // 写不进去也不该影响使用
  }
}

/**
 * 拖动分栏时 setState 会每帧触发一次，直接写 localStorage 会卡：
 * 这里合并到一次 200ms 后的写入，页面关闭前最后一次也会落盘（拖动结束后）。
 */
let persistTimer: ReturnType<typeof setTimeout> | null = null
function persistSoon(): void {
  if (typeof window === 'undefined') return
  if (persistTimer !== null) return
  persistTimer = setTimeout(() => {
    persistTimer = null
    persist(useSettings.getState())
  }, 200)
}

export const useSettings = create<SettingsState>((set) => ({
  theme: readTheme(),
  setTheme: (theme) => {
    applyTheme(theme)
    set({ theme })
    persistSoon()
  },

  transcriptScale: clampFontScale(initial.transcriptScale ?? FONT_SCALE_DEFAULT),
  setTranscriptScale: (transcriptScale) => {
    set({ transcriptScale: clampFontScale(transcriptScale) })
    persistSoon()
  },

  minutesScale: clampFontScale(initial.minutesScale ?? FONT_SCALE_DEFAULT),
  setMinutesScale: (minutesScale) => {
    set({ minutesScale: clampFontScale(minutesScale) })
    persistSoon()
  },

  minutesPaneWidth: clampPaneWidth(
    initial.minutesPaneWidth ?? PANE_WIDTH_DEFAULT,
    1400,
  ),
  setMinutesPaneWidth: (width) => {
    // 这里不做容器夹取：拖动时组件会用真实容器宽度调用 clampPaneWidth
    set({ minutesPaneWidth: clampPaneWidth(width, 1400) })
    persistSoon()
  },
}))

// 首次加载立即生效，避免闪一下浅色再变深色
applyTheme(useSettings.getState().theme)

// 选「跟随系统」时，系统主题变化要能实时跟上
if (typeof window !== 'undefined') {
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
    if (useSettings.getState().theme === 'system') applyTheme('system')
  })

  // 防抖窗口内离开页面（改完字号立刻跳转/关标签）也要落盘，否则偏好会丢。
  const flush = () => persist(useSettings.getState())
  window.addEventListener('pagehide', flush)
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') flush()
  })
}

/** 测试 / 重置用：把偏好恢复到默认值并清掉本地存储。 */
export function resetPreferences(): void {
  if (typeof localStorage !== 'undefined') {
    try {
      localStorage.removeItem(STORAGE_KEY)
    } catch {
      // ignore
    }
  }
  useSettings.setState({
    transcriptScale: FONT_SCALE_DEFAULT,
    minutesScale: FONT_SCALE_DEFAULT,
    minutesPaneWidth: PANE_WIDTH_DEFAULT,
  })
}
