import { useEffect, useRef, type ReactNode } from 'react'
import { X } from 'lucide-react'
import { useLayoutMode } from '../hooks/useLayoutMode'

interface Props {
  onClose: () => void
  /** 标题行内容（图标 + 文字）。完整标题便于读屏，勿只放图标。 */
  title: ReactNode
  children: ReactNode
  /** 底部固定操作区（可选）。compact 下会贴在安全区之上。 */
  footer?: ReactNode
  /** 标题行右侧、关闭按钮之前的快捷操作（可选） */
  actions?: ReactNode
  /** 桌面最大宽度（px）。compact 下总是铺满屏幕宽度。 */
  maxWidth?: number
  /** 桌面高度（CSS 长度）。脑图这类「撑满容器」的内容需要显式高度。 */
  desktopHeight?: string
  /** compact 下全屏显示（设置、脑图这类内容多且需要滚动的） */
  fullScreen?: boolean
  /** 无障碍名称；缺省用 title 文本 */
  ariaLabel?: string
}

/**
 * 统一的弹层容器。
 *
 * 为什么需要它：改造前 9 个弹窗各自写了一遍 `fixed inset-0 flex items-center`，
 * 在 390px 宽的手机上都是「居中卡片 + 上下被裁」——卡片最大宽 560，可用宽 390，
 * 内容一多就溢出到视口外，底部按钮点不到。
 *
 * 现在的行为：
 *   - compact（<=767）：从底部升起的面板，圆角 16、顶部抓手、最高 92dvh，
 *     下拉可关闭，背景滚动锁；`fullScreen` 时铺满整屏（带安全区）。
 *   - md 以上：与改造前一致的居中卡片（同样的遮罩、同样的宽度与高度）。
 */
export function Sheet({
  onClose,
  title,
  children,
  footer,
  actions,
  maxWidth = 560,
  desktopHeight,
  fullScreen = false,
  ariaLabel,
}: Props) {
  const compact = useLayoutMode() === 'compact'
  const dragStart = useRef<{ y: number; t: number } | null>(null)

  // 背景滚动锁：手机上弹层开着还能滚动正文，是「感觉卡住」的头号来源
  useEffect(() => {
    const previous = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.body.style.overflow = previous
    }
  }, [])

  // Esc 关闭。与项目其它弹窗一致，不额外做焦点陷阱。
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  const handleTouchStart = (event: React.TouchEvent) => {
    if (!compact) return
    dragStart.current = { y: event.touches[0].clientY, t: Date.now() }
  }

  const handleTouchEnd = (event: React.TouchEvent) => {
    const start = dragStart.current
    dragStart.current = null
    if (!compact || !start) return
    const dy = event.changedTouches[0].clientY - start.y
    // 向下拖动超过 80px 且够快才关闭，避免误触
    if (dy > 80 && Date.now() - start.t < 800) onClose()
  }

  const panelStyle: React.CSSProperties = compact
    ? {
        maxHeight: fullScreen ? undefined : '92dvh',
        height: fullScreen ? '100%' : undefined,
        borderRadius: 0,
        borderTopLeftRadius: 16,
        borderTopRightRadius: 16,
        paddingBottom: 'var(--safe-bottom)',
      }
    : { maxWidth, height: desktopHeight }

  return (
    <div
      className={
        compact
          ? 'fixed inset-0 z-50 flex flex-col justify-end bg-black/30'
          : 'fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4'
      }
      onClick={(event) => {
        // 点遮罩关闭；点面板内部不关闭
        if (event.target === event.currentTarget) onClose()
      }}
    >
      <div
        className="panel flex min-h-0 w-full flex-col"
        style={panelStyle}
        role="dialog"
        aria-modal="true"
        aria-label={ariaLabel}
      >
        {compact && !fullScreen && (
          <div
            className="flex shrink-0 justify-center pt-2 pb-1"
            onTouchStart={handleTouchStart}
            onTouchEnd={handleTouchEnd}
            aria-hidden
          >
            <span className="h-1 w-10 rounded-full" style={{ background: 'var(--border-default)' }} />
          </div>
        )}

        <div
          className="flex shrink-0 items-center gap-2 px-4 py-3"
          style={{ borderBottom: '1px solid var(--border-subtle)' }}
          onTouchStart={handleTouchStart}
          onTouchEnd={handleTouchEnd}
        >
          <h2 className="flex min-w-0 items-center gap-2 truncate text-[14px] font-medium">{title}</h2>
          <div className="ml-auto flex shrink-0 items-center gap-2">
            {actions}
            <button
              type="button"
              onClick={onClose}
              aria-label="关闭"
              className="flex items-center justify-center rounded p-1 text-tertiary max-md:size-10"
              title="关闭"
            >
              <X size={16} />
            </button>
          </div>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-4 py-3">{children}</div>

        {footer && (
          <div
            className="shrink-0 px-4 py-3"
            style={{ borderTop: '1px solid var(--border-subtle)' }}
          >
            {footer}
          </div>
        )}
      </div>
    </div>
  )
}
