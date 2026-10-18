import { useEffect, useRef } from 'react'

interface Props {
  level: number
  active: boolean
  bars?: number
}

/** 实时音量波形。用 Canvas 渲染，避免频繁 DOM 操作。 */
export function Waveform({ level, active, bars = 28 }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const historyRef = useRef<number[]>(new Array(bars).fill(0))
  const levelRef = useRef(level)
  const rafRef = useRef<number | null>(null)

  levelRef.current = level

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return

    const ctx = canvas.getContext('2d')
    if (!ctx) return

    const dpr = window.devicePixelRatio || 1
    const width = canvas.clientWidth
    const height = canvas.clientHeight
    canvas.width = width * dpr
    canvas.height = height * dpr
    ctx.scale(dpr, dpr)

    const barWidth = 3
    const gap = (width - bars * barWidth) / Math.max(bars - 1, 1)

    const draw = () => {
      const history = historyRef.current
      history.push(active ? levelRef.current : 0)
      history.shift()

      ctx.clearRect(0, 0, width, height)

      const accent = getComputedStyle(document.documentElement).getPropertyValue('--live').trim()
      const muted = getComputedStyle(document.documentElement).getPropertyValue('--border-default').trim()

      history.forEach((v, i) => {
        const h = Math.max(2, v * height * 0.85)
        const x = i * (barWidth + gap)
        const y = (height - h) / 2
        ctx.fillStyle = v > 0.04 ? accent : muted
        ctx.beginPath()
        ctx.roundRect(x, y, barWidth, h, 1.5)
        ctx.fill()
      })

      rafRef.current = requestAnimationFrame(draw)
    }

    draw()

    return () => {
      if (rafRef.current !== null) cancelAnimationFrame(rafRef.current)
    }
  }, [active, bars])

  return (
    <canvas
      ref={canvasRef}
      className="h-5 w-[120px]"
      role="img"
      aria-label={active ? '当前有声音输入' : '当前无声音输入'}
    />
  )
}
