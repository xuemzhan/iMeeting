/** 阅读偏好：字号缩放与分栏宽度。纯函数，便于单测（不依赖 DOM）。 */

export const FONT_SCALE_MIN = 0.85
export const FONT_SCALE_MAX = 1.5
export const FONT_SCALE_STEP = 0.05
export const FONT_SCALE_DEFAULT = 1

export const PANE_WIDTH_MIN = 260
export const PANE_WIDTH_MAX = 720
export const PANE_WIDTH_DEFAULT = 320
/** 纪要面板最多占容器宽度的比例，避免把转写挤没 */
export const PANE_MAX_CONTAINER_RATIO = 0.6
/** 转写面板至少要留出的宽度（桌面） */
export const TRANSCRIPT_MIN_WIDTH = 240

/** 收敛字号缩放：非法值回到默认，越界夹到区间内，保留两位小数。 */
export function clampFontScale(value: unknown): number {
  // null / 空串是「没设置」，不能当成 0 再被夹到最小字号
  if (value === null || value === undefined || value === '') {
    return FONT_SCALE_DEFAULT
  }
  const num = typeof value === 'number' ? value : Number(value)
  if (!Number.isFinite(num)) return FONT_SCALE_DEFAULT
  const clamped = Math.min(FONT_SCALE_MAX, Math.max(FONT_SCALE_MIN, num))
  return Math.round(clamped * 100) / 100
}

/** 加减一档；已在边界时返回原值（调用方可据此禁用按钮，避免多余渲染）。 */
export function nextFontScale(current: unknown, direction: 1 | -1): number {
  const scale = clampFontScale(current)
  return clampFontScale(scale + direction * FONT_SCALE_STEP)
}

export function formatFontScale(scale: unknown): string {
  return `${Math.round(clampFontScale(scale) * 100)}%`
}

/** 根据容器宽度算出纪要面板允许的最大宽度。 */
export function maxPaneWidth(containerWidth: unknown): number {
  const width =
    typeof containerWidth === 'number' ? containerWidth : Number(containerWidth)
  if (!Number.isFinite(width) || width <= 0) {
    return Math.min(
      PANE_WIDTH_MAX,
      Math.round(PANE_WIDTH_DEFAULT / PANE_MAX_CONTAINER_RATIO),
    )
  }
  return Math.max(
    PANE_WIDTH_MIN,
    Math.min(
      PANE_WIDTH_MAX,
      Math.round(width * PANE_MAX_CONTAINER_RATIO),
      Math.round(width - TRANSCRIPT_MIN_WIDTH),
    ),
  )
}

/** 收敛分栏宽度：非法值回到默认，越界按容器宽度夹住。 */
export function clampPaneWidth(desired: unknown, containerWidth: unknown): number {
  if (desired === null || desired === undefined || desired === '') {
    return PANE_WIDTH_DEFAULT
  }
  const num = typeof desired === 'number' ? desired : Number(desired)
  const safe = Number.isFinite(num) ? num : PANE_WIDTH_DEFAULT
  const max = maxPaneWidth(containerWidth)
  return Math.round(Math.min(max, Math.max(PANE_WIDTH_MIN, safe)))
}

/** 键盘 / 按钮微调分栏宽度。 */
export function stepPaneWidth(
  current: unknown,
  direction: 1 | -1,
  containerWidth: unknown,
  step = 16,
): number {
  const width = clampPaneWidth(current, containerWidth)
  return clampPaneWidth(width + direction * step, containerWidth)
}

/** 把缩放值变成 CSS 变量，供 .reader-* 类消费。 */
export function readerScaleStyle(scale: unknown): Record<string, string> {
  return { '--reader-scale': String(clampFontScale(scale)) }
}
