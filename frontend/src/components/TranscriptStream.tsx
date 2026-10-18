import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { ArrowDown } from 'lucide-react'
import { SegmentCard } from './SegmentCard'
import { deriveScrollState, isScrollable, panScrollTop } from './transcriptScroll'
import type { Segment } from '../api/types'

interface Props {
  segments: Segment[]
  onSeek?: (ms: number) => void
  /** 点说话人标签改名 */
  onRenameSpeaker?: (fromLabel: string, toName: string) => void
  /** 低于该相似度的说话人姓名显示“低置信” */
  lowConfidenceThreshold?: number
}

/** 位移超过该像素才算「拖动」，否则按点击处理 */
const DRAG_THRESHOLD = 4

export function TranscriptStream({
  segments,
  onSeek,
  onRenameSpeaker,
  lowConfidenceThreshold,
}: Props) {
  const scrollRef = useRef<HTMLDivElement>(null)
  const [pinned, setPinned] = useState(true)
  const [scrollable, setScrollable] = useState(false)

  // 贴底时自动跟随最新内容（新内容出现 → 旧内容整体上移）。
  // 用 useLayoutEffect 避免看到跳动。
  useLayoutEffect(() => {
    const el = scrollRef.current
    if (!el) return

    // 内容是否溢出决定要不要显示「可拖动」光标
    setScrollable(isScrollable(el.scrollHeight, el.clientHeight))

    // 只有「贴底」时才跟随；用户手动翻看历史时绝不强行拽回底部。
    if (pinned) el.scrollTop = el.scrollHeight
  }, [segments, pinned])

  // 用户滚动时更新跟随状态
  useEffect(() => {
    const el = scrollRef.current
    if (!el) return

    const onScroll = () => {
      const next = deriveScrollState(el.scrollTop, el.scrollHeight, el.clientHeight)
      setPinned(next.pinned)
      setScrollable(next.scrollable)
    }

    el.addEventListener('scroll', onScroll, { passive: true })
    return () => el.removeEventListener('scroll', onScroll)
  }, [])

  /**
   * 按住拖动平移内容。
   *
   * 转写常比一屏长得多，滚轮只能一段段地滚；直接拖拽更接近「翻看」的手感。
   * 几处克制，避免把既有交互弄坏：
   *  - 内容没溢出时不接管，否则会把「点一下」也吃掉；
   *  - 起点落在按钮/输入框上不接管（说话人改名、时间戳跳转必须能点）；
   *  - 位移超过 DRAG_THRESHOLD 才算拖动，轻点仍是点击；
   *  - 真正进入拖动时才清掉可能已产生的文本选区并禁用选中，
   *    否则会出现「拖出一片蓝色高亮」的怪异观感。
   */
  useEffect(() => {
    const el = scrollRef.current
    if (!el) return

    let active = false
    let panning = false
    let startY = 0
    let startTop = 0

    const onPointerDown = (e: PointerEvent) => {
      if (e.button !== 0) return
      if (el.scrollHeight <= el.clientHeight) return
      const target = e.target as HTMLElement | null
      if (target?.closest('button, a, input, textarea, select, [role="button"]')) return
      active = true
      panning = false
      startY = e.clientY
      startTop = el.scrollTop
    }

    const onPointerMove = (e: PointerEvent) => {
      if (!active) return
      const dy = e.clientY - startY
      if (!panning) {
        if (Math.abs(dy) < DRAG_THRESHOLD) return
        panning = true
        el.style.cursor = 'grabbing'
        el.style.userSelect = 'none'
        window.getSelection()?.removeAllRanges()
        el.setPointerCapture(e.pointerId)
      }
      e.preventDefault()
      el.scrollTop = panScrollTop(startTop, startY, e.clientY)
    }

    const finish = (e: PointerEvent) => {
      if (!active) return
      active = false
      if (!panning) return
      panning = false
      // 用松手坐标再算一次收口。浏览器会把连续的 pointermove 合并成每帧一个，
      // 主线程繁忙（或高刷新率）时最后一次移动可能还没投递就收到了 pointerup，
      // 内容就会停在松手位置之前 —— 实测能差 70px 以上，且只在负载下复现。
      // panScrollTop 是绝对换算、幂等，所以这次重算不会引入累积误差。
      el.scrollTop = panScrollTop(startTop, startY, e.clientY)
      el.style.cursor = ''
      el.style.userSelect = ''
      if (el.hasPointerCapture(e.pointerId)) el.releasePointerCapture(e.pointerId)
    }

    el.addEventListener('pointerdown', onPointerDown)
    el.addEventListener('pointermove', onPointerMove)
    el.addEventListener('pointerup', finish)
    el.addEventListener('pointercancel', finish)
    return () => {
      el.removeEventListener('pointerdown', onPointerDown)
      el.removeEventListener('pointermove', onPointerMove)
      el.removeEventListener('pointerup', finish)
      el.removeEventListener('pointercancel', finish)
      el.style.cursor = ''
      el.style.userSelect = ''
    }
  }, [])

  const scrollToBottom = useCallback(() => {
    const el = scrollRef.current
    if (!el) return
    el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' })
    setPinned(true)
  }, [])

  // 只要「内容溢出」且「用户已离开底部」，就给一个一键回到底部的入口。
  // 拖动翻看历史后，这是最省事的返回方式。
  // 规则与 deriveScrollState().showJumpToLatest 保持一致：溢出 + 离开底部。
  const showJumpToLatest = scrollable && !pinned

  return (
    <div className="relative flex min-h-0 flex-1 flex-col">
      <div
        ref={scrollRef}
        className="min-h-0 flex-1 overflow-y-auto overscroll-contain pt-3"
        style={scrollable ? { cursor: 'grab' } : undefined}
        role="log"
        aria-live="polite"
        aria-label="实时转写内容"
      >
        {segments.length === 0 ? (
          <EmptyState />
        ) : (
          <>
            {segments.map((seg) => (
              <SegmentCard
                key={seg.segmentId}
                segment={seg}
                onSeek={onSeek}
                onRenameSpeaker={onRenameSpeaker}
                lowConfidenceThreshold={lowConfidenceThreshold}
              />
            ))}
            <div className="h-4" />
          </>
        )}
      </div>

      {showJumpToLatest && (
        <button
          type="button"
          onClick={scrollToBottom}
          className="btn absolute bottom-4 left-1/2 -translate-x-1/2 shadow-lg"
          style={{
            background: 'var(--accent)',
            borderColor: 'var(--accent)',
            color: '#fff',
            height: 30,
          }}
        >
          <ArrowDown size={14} />
          回到最新
        </button>
      )}
    </div>
  )
}

function EmptyState() {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 px-8 text-center">
      <div
        className="flex size-12 items-center justify-center rounded-full"
        style={{ background: 'var(--bg-subtle)' }}
      >
        <div className="flex items-end gap-[3px]" aria-hidden>
          {[10, 18, 13, 20, 11].map((h, i) => (
            <span
              key={i}
              className="animate-breathe w-[3px] rounded-full"
              style={{
                height: h,
                background: 'var(--text-tertiary)',
                animationDelay: `${i * 160}ms`,
              }}
            />
          ))}
        </div>
      </div>
      <p className="text-[13px] font-medium text-secondary">正在聆听…</p>
      <p className="max-w-[300px] text-[12px] text-tertiary">
        字幕将随说话实时出现。系统会自动断句、区分说话人并补全标点
      </p>
    </div>
  )
}
