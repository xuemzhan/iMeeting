import { Minus, Plus } from 'lucide-react'

import {
  FONT_SCALE_MAX,
  FONT_SCALE_MIN,
  formatFontScale,
  nextFontScale,
} from '../lib/readerPrefs'

interface Props {
  /** 当前缩放值（1 = 100%） */
  value: number
  onChange: (value: number) => void
  /** 无障碍名称：「转写」/「纪要」 */
  label: string
  disabled?: boolean
}

/**
 * 面板级字号调节：− / 百分比（点击复位）/ +。
 *
 * 百分比按钮同时是「恢复默认」入口：调过之后点一下即可回到 100%，
 * 不额外占一个按钮，窄面板里也不拥挤。
 */
export function FontSizeControl({ value, onChange, label, disabled }: Props) {
  const canDecrease = value > FONT_SCALE_MIN + 0.001
  const canIncrease = value < FONT_SCALE_MAX - 0.001
  const reset = Math.abs(value - 1) > 0.001

  return (
    <div
      className="flex shrink-0 items-center gap-0.5"
      role="group"
      aria-label={`${label}字号`}
    >
      <button
        type="button"
        onClick={() => onChange(nextFontScale(value, -1))}
        disabled={disabled || !canDecrease}
        className="btn btn-ghost px-1"
        style={{ height: 22, minWidth: 22 }}
        aria-label={`${label}字号调小`}
        title="字号调小"
      >
        <Minus size={11} />
      </button>
      <button
        type="button"
        onClick={() => onChange(1)}
        disabled={disabled || !reset}
        className="rounded px-1 py-0.5 font-mono text-[10px] tabular-nums transition-colors disabled:cursor-default"
        style={{
          color: reset ? 'var(--text-secondary)' : 'var(--text-tertiary)',
          minWidth: 34,
        }}
        aria-label={`${label}字号${formatFontScale(value)}，点击恢复默认`}
        title={reset ? '点击恢复默认字号' : '当前为默认字号'}
      >
        {formatFontScale(value)}
      </button>
      <button
        type="button"
        onClick={() => onChange(nextFontScale(value, 1))}
        disabled={disabled || !canIncrease}
        className="btn btn-ghost px-1"
        style={{ height: 22, minWidth: 22 }}
        aria-label={`${label}字号调大`}
        title="字号调大"
      >
        <Plus size={11} />
      </button>
    </div>
  )
}
