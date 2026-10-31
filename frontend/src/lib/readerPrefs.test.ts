import { describe, expect, it } from 'vitest'

import {
  FONT_SCALE_DEFAULT,
  FONT_SCALE_MAX,
  FONT_SCALE_MIN,
  FONT_SCALE_STEP,
  PANE_WIDTH_DEFAULT,
  PANE_WIDTH_MAX,
  PANE_WIDTH_MIN,
  clampFontScale,
  clampPaneWidth,
  formatFontScale,
  maxPaneWidth,
  nextFontScale,
  readerScaleStyle,
  stepPaneWidth,
} from './readerPrefs'

describe('字号缩放', () => {
  it('非法值回到默认，而不是 NaN 污染 CSS', () => {
    for (const bad of [undefined, null, 'abc', Number.NaN, Infinity]) {
      expect(clampFontScale(bad)).toBe(FONT_SCALE_DEFAULT)
    }
  })

  it('越界时夹到上下限', () => {
    expect(clampFontScale(0)).toBe(FONT_SCALE_MIN)
    expect(clampFontScale(-5)).toBe(FONT_SCALE_MIN)
    expect(clampFontScale(9)).toBe(FONT_SCALE_MAX)
    expect(clampFontScale('1.25')).toBe(1.25)
  })

  it('保留两位小数，避免浮点累加出现 1.1500000000000001', () => {
    expect(clampFontScale(1.1500000000000001)).toBe(1.15)
  })

  it('按 5% 一档加减，到边界后不再变化', () => {
    expect(nextFontScale(1, 1)).toBe(1 + FONT_SCALE_STEP)
    expect(nextFontScale(1, -1)).toBe(1 - FONT_SCALE_STEP)
    expect(nextFontScale(FONT_SCALE_MAX, 1)).toBe(FONT_SCALE_MAX)
    expect(nextFontScale(FONT_SCALE_MIN, -1)).toBe(FONT_SCALE_MIN)
  })

  it('百分比展示与缩放值一致', () => {
    expect(formatFontScale(1)).toBe('100%')
    expect(formatFontScale(1.25)).toBe('125%')
    expect(formatFontScale(99)).toBe('150%')
  })

  it('readerScaleStyle 输出 CSS 变量', () => {
    expect(readerScaleStyle(1.2)).toEqual({ '--reader-scale': '1.2' })
    expect(readerScaleStyle('bad')).toEqual({ '--reader-scale': '1' })
  })
})

describe('分栏宽度', () => {
  it('非法值用默认宽度', () => {
    expect(clampPaneWidth(undefined, 1200)).toBe(PANE_WIDTH_DEFAULT)
    expect(clampPaneWidth('abc', 1200)).toBe(PANE_WIDTH_DEFAULT)
  })

  it('夹在最小/最大之间', () => {
    expect(clampPaneWidth(10, 1600)).toBe(PANE_WIDTH_MIN)
    expect(clampPaneWidth(10_000, 1600)).toBe(PANE_WIDTH_MAX)
  })

  it('窗口较窄时不能把转写挤没（最多占 60%，且给转写留 240px）', () => {
    // 1100 * 0.6 = 660，1100-240=860 → 上限 660
    expect(maxPaneWidth(1100)).toBe(660)
    // 700 → 420 与 460 取小 = 420
    expect(maxPaneWidth(700)).toBe(420)
    expect(clampPaneWidth(600, 700)).toBe(420)
    // 极小容器：仍保证最小宽度，不会算出比 MIN 还小的上限
    expect(maxPaneWidth(300)).toBe(PANE_WIDTH_MIN)
  })

  it('容器宽度未知时给出安全上限', () => {
    // 默认宽度 / 0.6 = 533
    expect(maxPaneWidth(Number.NaN)).toBe(533)
    expect(maxPaneWidth(0)).toBe(533)
  })

  it('键盘微调一次 16px，并在边界处收敛', () => {
    expect(stepPaneWidth(320, 1, 1600)).toBe(336)
    expect(stepPaneWidth(320, -1, 1600)).toBe(304)
    expect(stepPaneWidth(PANE_WIDTH_MIN, -1, 1600)).toBe(PANE_WIDTH_MIN)
    expect(stepPaneWidth(PANE_WIDTH_MAX, 1, 1600)).toBe(PANE_WIDTH_MAX)
  })
})
