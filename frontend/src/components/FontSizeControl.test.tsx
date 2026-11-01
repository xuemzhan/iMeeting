// @vitest-environment jsdom
/**
 * FontSizeControl：面板级字号调节的交互与边界。
 *
 * 这是项目里第一个组件级测试。此前 11 个测试文件全部落在 lib/store/api，
 * 组件只能靠 e2e 兜底（覆盖审查 P3-5）。
 */

import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { FontSizeControl } from './FontSizeControl'
import { FONT_SCALE_MAX, FONT_SCALE_MIN } from '../lib/readerPrefs'

describe('FontSizeControl', () => {
  it('把当前字号显示为百分比，并用无障碍名称区分面板', () => {
    render(<FontSizeControl value={1.25} onChange={() => {}} label="转写" />)
    expect(screen.getByRole('group', { name: '转写字号' })).toBeTruthy()
    expect(screen.getByText('125%')).toBeTruthy()
  })

  it('点击加号/减号按步长回调', () => {
    const onChange = vi.fn()
    render(<FontSizeControl value={1} onChange={onChange} label="纪要" />)

    fireEvent.click(screen.getByLabelText('纪要字号调大'))
    expect(onChange).toHaveBeenCalledTimes(1)
    expect(onChange.mock.calls[0][0]).toBeGreaterThan(1)

    fireEvent.click(screen.getByLabelText('纪要字号调小'))
    expect(onChange).toHaveBeenCalledTimes(2)
    expect(onChange.mock.calls[1][0]).toBeLessThan(1)
  })

  it('到达上下限后对应按钮禁用', () => {
    const { rerender } = render(
      <FontSizeControl value={FONT_SCALE_MIN} onChange={() => {}} label="转写" />,
    )
    expect((screen.getByLabelText('转写字号调小') as HTMLButtonElement).disabled).toBe(true)

    rerender(<FontSizeControl value={FONT_SCALE_MAX} onChange={() => {}} label="转写" />)
    expect((screen.getByLabelText('转写字号调大') as HTMLButtonElement).disabled).toBe(true)
  })

  it('非 100% 时百分比按钮可一键复位，100% 时禁用', () => {
    const onChange = vi.fn()
    const { rerender } = render(
      <FontSizeControl value={1.15} onChange={onChange} label="转写" />,
    )
    fireEvent.click(screen.getByText('115%'))
    expect(onChange).toHaveBeenCalledWith(1)

    rerender(<FontSizeControl value={1} onChange={onChange} label="转写" />)
    expect((screen.getByText('100%') as HTMLButtonElement).disabled).toBe(true)
  })

  it('disabled 时两个按钮都不可用', () => {
    render(<FontSizeControl value={1.1} onChange={() => {}} label="纪要" disabled />)
    expect((screen.getByLabelText('纪要字号调大') as HTMLButtonElement).disabled).toBe(true)
    expect((screen.getByLabelText('纪要字号调小') as HTMLButtonElement).disabled).toBe(true)
  })
})
