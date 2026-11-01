import { useEffect, useState } from 'react'

/**
 * 三档布局模式：compact（<=767）/ medium（768–1023）/ wide（>=1024）。
 *
 * 与 `styles/index.css` 的令牌断点、以及
 * `docs/plans/2026-11-01-responsive-ui-adaptation.md` 的 4.1 保持一致。
 * 用 `matchMedia` 而不是读 `window.innerWidth` 是为了：
 *   - 只在跨档时触发一次重渲染，不在每次 resize 抖动；
 *   - 与 CSS 断点用同一套查询，不会出现「CSS 已切、JS 还没切」的半拍。
 */
export type LayoutMode = 'compact' | 'medium' | 'wide'

const QUERIES: { mode: LayoutMode; query: string }[] = [
  { mode: 'compact', query: '(max-width: 767px)' },
  { mode: 'medium', query: '(min-width: 768px) and (max-width: 1023px)' },
]

function detect(): LayoutMode {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
    return 'wide'
  }
  if (window.matchMedia(QUERIES[0].query).matches) return 'compact'
  if (window.matchMedia(QUERIES[1].query).matches) return 'medium'
  return 'wide'
}

export function useLayoutMode(): LayoutMode {
  const [mode, setMode] = useState<LayoutMode>(detect)

  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return
    const lists = QUERIES.map((item) => window.matchMedia(item.query))
    const sync = () => setMode(detect())
    // 先用真实值同步一次：首帧 SSR/预渲染与真实窗口不一致时立刻纠正
    sync()
    for (const list of lists) {
      list.addEventListener('change', sync)
    }
    return () => {
      for (const list of lists) list.removeEventListener('change', sync)
    }
  }, [])

  return mode
}

/** 便捷判断：手机（compact）档 */
export function useIsCompact(): boolean {
  return useLayoutMode() === 'compact'
}
