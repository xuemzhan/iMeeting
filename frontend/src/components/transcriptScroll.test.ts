/**
 * 转写流滚动状态的回归用例。
 *
 * 背景：这段状态机修过三个真实故障——
 *  1. 翻看历史时被强行拽回底部：只判断「有新内容就贴底」，没判断用户是否已离开底部。
 *  2. 内容溢出很小时，「回到最新」永远不出现（容差 80px 大于溢出量）。
 *  3. 拖动收口缺失：只在 pointermove 里定位，浏览器合并 move 事件时最后一次
 *     可能没投递，内容停在松手位置之前（负载下才复现，实测差 70px+）。
 *
 * jsdom 里 scrollHeight/clientHeight 恒为 0，量不出真实布局，所以把规则抽成
 * 纯函数在这里锁住；真实滚动/拖动由 `scripts/probe-transcript-drag.mjs` 实测。
 */

import { describe, expect, it } from 'vitest'
import { BOTTOM_THRESHOLD, deriveScrollState, isScrollable, panScrollTop } from './transcriptScroll'

describe('isScrollable', () => {
  it('内容没长过一屏 → 不可滚动', () => {
    expect(isScrollable(280, 280)).toBe(false)
  })

  it('亚像素级差异（<=1px）不算可滚动，避免抖动出光标', () => {
    expect(isScrollable(281, 280)).toBe(false)
  })

  it('内容长过一屏 → 可滚动', () => {
    expect(isScrollable(4000, 280)).toBe(true)
  })
})

describe('deriveScrollState', () => {
  it('未溢出：不贴底判定为真（距离为负/零）、不可拖动、不显示按钮', () => {
    const s = deriveScrollState(0, 280, 280)
    expect(s.distance).toBe(0)
    expect(s.pinned).toBe(true)
    expect(s.scrollable).toBe(false)
    expect(s.showJumpToLatest).toBe(false)
  })

  it('溢出且贴底：pinned 为真、可拖动、不显示按钮', () => {
    const s = deriveScrollState(3720, 4000, 280)
    expect(s.distance).toBe(0)
    expect(s.pinned).toBe(true)
    expect(s.scrollable).toBe(true)
    expect(s.showJumpToLatest).toBe(false)
  })

  it('溢出且明显上滚：pinned 为假、显示「回到最新」', () => {
    const s = deriveScrollState(1000, 4000, 280)
    expect(s.distance).toBe(2720)
    expect(s.pinned).toBe(false)
    expect(s.scrollable).toBe(true)
    expect(s.showJumpToLatest).toBe(true)
  })

  it('上滚距离在容差内仍算贴底，不显示按钮', () => {
    // 距底 79px < BOTTOM_THRESHOLD(80)
    const s = deriveScrollState(4000 - 280 - (BOTTOM_THRESHOLD - 1), 4000, 280)
    expect(s.distance).toBe(BOTTOM_THRESHOLD - 1)
    expect(s.pinned).toBe(true)
    expect(s.showJumpToLatest).toBe(false)
  })

  it('恰好等于容差即算离开底部', () => {
    const s = deriveScrollState(4000 - 280 - BOTTOM_THRESHOLD, 4000, 280)
    expect(s.pinned).toBe(false)
    expect(s.showJumpToLatest).toBe(true)
  })

  it('溢出量不足容差时按钮永不出现（有意取舍）', () => {
    // 溢出 31px：滚到顶也仍在容差内 → 不算离开底部
    const s = deriveScrollState(0, 311, 280)
    expect(s.scrollable).toBe(true)
    expect(s.distance).toBe(31)
    expect(s.pinned).toBe(true)
    expect(s.showJumpToLatest).toBe(false)
  })
})

describe('panScrollTop', () => {
  it('往下拖 → 内容上移 → scrollTop 减小', () => {
    expect(panScrollTop(1000, 200, 360)).toBe(840)
  })

  it('往上拖 → scrollTop 增大', () => {
    expect(panScrollTop(1000, 200, 120)).toBe(1080)
  })

  it('位移为 0 → 位置不变', () => {
    expect(panScrollTop(1000, 200, 200)).toBe(1000)
  })

  it('是绝对换算而非累加：同一个坐标算多次结果相同（幂等）', () => {
    const once = panScrollTop(1000, 200, 360)
    // 模拟 pointerup 收口时用松手坐标再算一次
    expect(panScrollTop(1000, 200, 360)).toBe(once)
  })

  it('收口用松手坐标：只投递了前几次 move 也不会少滚一截', () => {
    // 按下在 y=200、scrollTop=1000；浏览器只投递到 y=284（dy=84）就收到了 up，
    // 但松手位置是 y=360。用松手坐标收口后总位移必须仍是 160，而不是 84。
    const delivered = panScrollTop(1000, 200, 284) // 最后一次投递到的 move
    const released = panScrollTop(1000, 200, 360) // pointerup 收口
    expect(1000 - delivered).toBe(84)
    expect(1000 - released).toBe(160)
  })

  it('不夹取边界，交给浏览器夹取（保证换算本身可预测）', () => {
    expect(panScrollTop(50, 200, 400)).toBe(-150)
    expect(panScrollTop(3000, 200, 0)).toBe(3200)
  })
})
