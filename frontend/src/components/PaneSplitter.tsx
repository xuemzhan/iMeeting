import { useRef, useState } from 'react'
import { GripVertical } from 'lucide-react'

import {
  PANE_WIDTH_MIN,
  clampPaneWidth,
  maxPaneWidth,
  stepPaneWidth,
} from '../lib/readerPrefs'

interface Props {
  /** 包含转写 + 分隔条 + 纪要的父容器，用于换算宽度 */
  containerRef: React.RefObject<HTMLElement | null>
  /** 当前纪要面板宽度（px） */
  width: number
  onChange: (width: number) => void
  /** 双击 / Home / 按钮复位 */
  onReset: () => void
  disabled?: boolean
}

/**
 * 转写与纪要之间的可拖动分隔条（仅桌面端显示）。
 *
 * - 拖动：以「父容器右边缘 - 指针 X」作为纪要宽度，天然是右栏宽度；
 * - 键盘：← 让纪要更宽、→ 更窄，Home 复位（无鼠标也能调）；
 * - 双击：恢复默认宽度；
 * - 使用 pointer capture，指针移出分隔条也不会丢事件。
 */
export function PaneSplitter({
  containerRef,
  width,
  onChange,
  onReset,
  disabled,
}: Props) {
  const draggingRef = useRef(false)
  const dragStartRef = useRef({ x: 0, width })
  const [dragging, setDragging] = useState(false)
  const [hover, setHover] = useState(false)

  const containerWidth = () => {
    const el = containerRef.current
    return el ? el.getBoundingClientRect().width : window.innerWidth
  }

  /** 宽度按「起点宽度 + 指针位移」算，而不是「容器右缘 - X」。 */
  const applyFromPointer = (clientX: number) => {
    const { x, width: startWidth } = dragStartRef.current
    // 指针左移（clientX 变小）→ 纪要变宽
    onChange(
      clampPaneWidth(startWidth + (x - clientX), containerWidth()),
    )
  }

  const onPointerDown = (event: React.PointerEvent<HTMLDivElement>) => {
    if (disabled || event.button !== 0) return
    event.preventDefault()
    draggingRef.current = true
    dragStartRef.current = { x: event.clientX, width }
    setDragging(true)
    event.currentTarget.setPointerCapture(event.pointerId)
  }

  const onPointerMove = (event: React.PointerEvent<HTMLDivElement>) => {
    if (!draggingRef.current) return
    applyFromPointer(event.clientX)
  }

  const finishDrag = (event: React.PointerEvent<HTMLDivElement>) => {
    if (!draggingRef.current) return
    draggingRef.current = false
    applyFromPointer(event.clientX)
    setDragging(false)
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId)
    }
  }

  const onKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (disabled) return
    if (event.key === 'ArrowLeft') {
      event.preventDefault()
      onChange(stepPaneWidth(width, 1, containerWidth()))
    } else if (event.key === 'ArrowRight') {
      event.preventDefault()
      onChange(stepPaneWidth(width, -1, containerWidth()))
    } else if (event.key === 'Home') {
      event.preventDefault()
      onReset()
    }
  }

  const active = dragging || hover
  const maxWidth = maxPaneWidth(containerWidth())

  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label="拖动调整会议纪要面板宽度"
      aria-valuenow={width}
      aria-valuemin={PANE_WIDTH_MIN}
      aria-valuemax={maxWidth}
      tabIndex={disabled ? -1 : 0}
      onKeyDown={onKeyDown}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={finishDrag}
      onPointerCancel={finishDrag}
      onDoubleClick={onReset}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      className="group relative hidden w-2 shrink-0 touch-none cursor-col-resize select-none lg:block"
      style={{ marginLeft: -3, marginRight: -3 }}
      title="拖动调整纪要宽度 · 双击复位"
    >
      <span
        className="absolute inset-y-2 left-1/2 w-px -translate-x-1/2 rounded-full transition-colors"
        style={{ background: active ? 'var(--accent)' : 'var(--border-default)' }}
        aria-hidden
      />
      <span
        className="pointer-events-none absolute left-1/2 top-1/2 flex h-9 w-3.5 -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full transition-opacity"
        style={{
          opacity: active ? 1 : 0,
          background: 'var(--bg-surface)',
          border: '1px solid var(--border-default)',
          boxShadow: 'var(--shadow-panel)',
        }}
        aria-hidden
      >
        <GripVertical size={10} style={{ color: 'var(--text-tertiary)' }} />
      </span>
    </div>
  )
}
