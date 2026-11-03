// @vitest-environment jsdom
/**
 * 分栏拖动条：aria 暴露的上界、键盘微调、禁用态。
 *
 * 这个组件此前零测试。上界（aria-valuemax）最近刚改过——从「渲染期读容器 ref」
 * 改成「ResizeObserver 量进 state」，改动的正是这里锁住的行为：容器宽度变了，
 * 上界必须跟着变，而读不到时不能给出小于下限的值。
 */

import { fireEvent, render, screen } from '@testing-library/react'
import { createRef } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { PANE_WIDTH_MIN, maxPaneWidth } from '../lib/readerPrefs'
import { PaneSplitter } from './PaneSplitter'

/** jsdom 没有 ResizeObserver；用可控替身在测试里主动触发一次「容器宽度变化」。 */
let triggerResize: (width: number) => void
class FakeResizeObserver {
  private callback: ResizeObserverCallback

  constructor(callback: ResizeObserverCallback) {
    this.callback = callback
    triggerResize = (width: number) => {
      this.callback(
        [{ contentRect: { width } } as ResizeObserverEntry],
        this as unknown as ResizeObserver,
      )
    }
  }

  observe() {}
  unobserve() {}
  disconnect() {}
}

function setup(overrides: Partial<Parameters<typeof PaneSplitter>[0]> = {}) {
  const container = document.createElement('div')
  // jsdom 不做布局，getBoundingClientRect 恒为 0；给一个真实宽度，
  // 让 clampPaneWidth 的上界换算（容器 60%）在测试里有意义。
  container.getBoundingClientRect = () =>
    ({
      width: 1000,
      height: 800,
      top: 0,
      left: 0,
      right: 1000,
      bottom: 800,
      x: 0,
      y: 0,
      toJSON: () => ({}),
    }) as DOMRect
  const containerRef = createRef<HTMLElement>() as React.RefObject<HTMLElement | null>
  ;(containerRef as { current: HTMLElement | null }).current = container

  const onChange = vi.fn()
  const onReset = vi.fn()
  render(
    <PaneSplitter
      containerRef={containerRef}
      width={320}
      onChange={onChange}
      onReset={onReset}
      {...overrides}
    />,
  )
  return { onChange, onReset, separator: screen.getByRole('separator') }
}

beforeEach(() => {
  vi.stubGlobal('ResizeObserver', FakeResizeObserver)
  // jsdom 没实现 pointer capture，组件按下时会调用
  Element.prototype.setPointerCapture = vi.fn()
  Element.prototype.releasePointerCapture = vi.fn()
  Element.prototype.hasPointerCapture = vi.fn(() => false)
})

describe('PaneSplitter', () => {
  it('暴露 aria 的当前值与下限；未量到宽度时上界不小于下限', () => {
    const { separator } = setup()

    expect(separator.getAttribute('aria-valuenow')).toBe('320')
    expect(separator.getAttribute('aria-valuemin')).toBe(String(PANE_WIDTH_MIN))
    expect(Number(separator.getAttribute('aria-valuemax'))).toBeGreaterThanOrEqual(PANE_WIDTH_MIN)
  })

  it('容器宽度变化后上界跟着变', async () => {
    const { separator } = setup()

    triggerResize(1000)

    expect(await screen.findByRole('separator')).toBeTruthy()
    expect(Number(separator.getAttribute('aria-valuemax'))).toBe(maxPaneWidth(1000))
  })

  it('容器很窄时上界收敛到下限而不是越界', async () => {
    const { separator } = setup()

    triggerResize(300)

    expect(await screen.findByRole('separator')).toBeTruthy()
    expect(Number(separator.getAttribute('aria-valuemax'))).toBe(PANE_WIDTH_MIN)
  })

  it('ArrowLeft 让纪要更宽、ArrowRight 更窄、Home 复位', () => {
    const { onChange, onReset, separator } = setup()

    fireEvent.keyDown(separator, { key: 'ArrowLeft' })
    const wider = onChange.mock.calls.at(-1)?.[0]
    expect(wider).toBeGreaterThan(320)

    fireEvent.keyDown(separator, { key: 'ArrowRight' })
    const narrower = onChange.mock.calls.at(-1)?.[0]
    expect(narrower).toBeLessThan(320)

    fireEvent.keyDown(separator, { key: 'Home' })
    expect(onReset).toHaveBeenCalled()
  })

  it('禁用时不响应键盘，也不进 Tab 序列', () => {
    const { onChange, onReset, separator } = setup({ disabled: true })

    fireEvent.keyDown(separator, { key: 'ArrowLeft' })
    fireEvent.keyDown(separator, { key: 'Home' })

    expect(onChange).not.toHaveBeenCalled()
    expect(onReset).not.toHaveBeenCalled()
    expect(separator.getAttribute('tabindex')).toBe('-1')
  })

  it('双击复位', () => {
    const { onReset, separator } = setup()

    fireEvent.doubleClick(separator)

    expect(onReset).toHaveBeenCalledTimes(1)
  })

  it('拖动：左移变宽、右移变窄，并夹在容器允许的范围内', () => {
    const { onChange, separator } = setup()

    fireEvent.pointerDown(separator, { clientX: 800, button: 0, pointerId: 1 })
    // 以「按下时的宽度 + 指针位移」为准：左移 300px → 320 + 300，
    // 但上界是容器的 60%（600），所以应停在上界。
    fireEvent.pointerMove(separator, { clientX: 500, pointerId: 1 })
    expect(onChange.mock.calls.at(-1)?.[0]).toBe(maxPaneWidth(1000))

    // 往右拖到远离容器处 → 收敛到下限，而不是负数
    fireEvent.pointerMove(separator, { clientX: 2000, pointerId: 1 })
    expect(onChange.mock.calls.at(-1)?.[0]).toBe(PANE_WIDTH_MIN)

    fireEvent.pointerUp(separator, { clientX: 2000, pointerId: 1 })
  })
})
