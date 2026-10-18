import { useEffect } from 'react'
import { useSession } from '../store/session'

/** 录音期间的键盘快捷键。
 *
 * - Space：暂停 / 继续
 * - F：切句（立即结束当前句，触发出稿）
 * - Esc：结束会议
 *
 * 只在 recording / paused 状态下生效，避免在输入框中触发。
 */
export function useKeyboardShortcuts() {
  const status = useSession((s) => s.status)
  const togglePause = useSession((s) => s.togglePause)
  const finalizeCurrent = useSession((s) => s.finalizeCurrent)
  const stop = useSession((s) => s.stop)

  useEffect(() => {
    if (status !== 'recording' && status !== 'paused') return

    const handler = (e: KeyboardEvent) => {
      // 在输入框中不拦截
      const target = e.target as HTMLElement
      if (
        target.tagName === 'INPUT' ||
        target.tagName === 'TEXTAREA' ||
        target.isContentEditable
      ) {
        return
      }

      switch (e.key) {
        case ' ':
        case 'Spacebar':
          e.preventDefault()
          togglePause()
          break
        case 'f':
        case 'F':
          e.preventDefault()
          finalizeCurrent()
          break
        case 'Escape':
          e.preventDefault()
          if (status === 'recording' || status === 'paused') {
            void stop()
          }
          break
      }
    }

    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [status, togglePause, finalizeCurrent, stop])
}
