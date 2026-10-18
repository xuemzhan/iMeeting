/** 转写流滚动状态的纯函数推导。
 *
 * 抽出来是为了能脱离 DOM 单测：这段状态机踩过坑——
 *  - 内容没溢出时也接管拖动，把「点一下」吃掉；
 *  - 用户往上翻看历史，新内容到达时被强行拽回底部；
 *  - 溢出量小于贴底容差时，「回到最新」永远不出现（已知取舍，见下）。
 * jsdom 拿不到真实布局（scrollHeight 恒为 0），所以这里用纯函数锁规则，
 * 真实滚动行为另由 `scripts/probe-transcript-drag.mjs` 在浏览器里实测。
 */

/** 判定「贴底」的容差（px），避免亚像素滚动导致误判 */
export const BOTTOM_THRESHOLD = 80

export interface ScrollState {
  /** 距底部的像素距离 */
  distance: number
  /** 是否贴底：贴底时新内容到达会自动跟随（旧内容整体上移） */
  pinned: boolean
  /** 内容是否长过一屏：决定是否显示 grab 光标、是否接管拖动 */
  scrollable: boolean
  /** 是否显示「回到最新」按钮：内容溢出且用户已离开底部 */
  showJumpToLatest: boolean
}

/**
 * 由滚动几何量推导出三个界面状态。
 *
 * 注意一个有意为之的取舍：`pinned` 用固定容差 80px 判定，所以当内容
 * 溢出量不足 80px 时 `pinned` 恒为真、按钮永不出现。这是刻意的——
 * 只溢出几十像素时本来就没什么历史可翻，硬给一个「回到最新」反而多余。
 */
export function deriveScrollState(
  scrollTop: number,
  scrollHeight: number,
  clientHeight: number,
): ScrollState {
  const distance = scrollHeight - scrollTop - clientHeight
  const pinned = distance < BOTTOM_THRESHOLD
  const scrollable = isScrollable(scrollHeight, clientHeight)
  return { distance, pinned, scrollable, showJumpToLatest: scrollable && !pinned }
}

/** 内容是否长过一屏（+1 容忍亚像素误差） */
export function isScrollable(scrollHeight: number, clientHeight: number): boolean {
  return scrollHeight > clientHeight + 1
}

/**
 * 拖动平移：由「按下时的 scrollTop + 指针纵向位移」换算新的 scrollTop。
 *
 * 刻意用**绝对换算**而不是「每次 move 累加增量」：
 *  - 浏览器会把连续的 `pointermove` 合并成每帧一个，累加会漏掉位移；
 *  - 绝对换算天然幂等，同一个坐标算几次结果都一样，因此可以在
 *    `pointerup` 时用**松手坐标**再算一次来收口 —— 否则主线程繁忙时
 *    最后一次 move 可能还没投递就收到 up，内容会停在松手位置之前
 *    （实测差过 70px，且只在负载下复现）。
 *
 * 该函数返回的是**未夹取**的值，实际写入 `scrollTop` 时浏览器会自动
 * 夹到 `[0, scrollHeight - clientHeight]`。
 */
export function panScrollTop(startTop: number, startY: number, currentY: number): number {
  return startTop - (currentY - startY)
}
