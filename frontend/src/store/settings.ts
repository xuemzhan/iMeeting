/** 本地偏好设置（外观等），持久化到 localStorage。 */

import { create } from 'zustand'

export type ThemeMode = 'light' | 'dark' | 'system'

const STORAGE_KEY = 'imeeting-settings'

interface SettingsState {
  theme: ThemeMode
  setTheme: (theme: ThemeMode) => void
}

function readTheme(): ThemeMode {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (raw) {
      const v = (JSON.parse(raw) as { theme?: unknown }).theme
      if (v === 'light' || v === 'dark' || v === 'system') return v
    }
  } catch {
    // localStorage 不可用（隐私模式等）时用默认值
  }
  return 'light'
}

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

export const useSettings = create<SettingsState>((set) => ({
  theme: readTheme(),
  setTheme: (theme) => {
    applyTheme(theme)
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify({ theme }))
    } catch {
      // 写不进去也不该影响使用
    }
    set({ theme })
  },
}))

// 首次加载立即生效，避免闪一下浅色再变深色
applyTheme(useSettings.getState().theme)

// 选「跟随系统」时，系统主题变化要能实时跟上
if (typeof window !== 'undefined') {
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
    if (useSettings.getState().theme === 'system') applyTheme('system')
  })
}
