import { useMemo, useRef, useState } from 'react'
import { Download, Minus, Plus } from 'lucide-react'
import type { MindMapNode, MindMapResponse } from '../api/types'

interface LayoutNode {
  node: MindMapNode
  x: number
  y: number
  w: number
  h: number
  lines: string[]
  children: LayoutNode[]
}

const WIDTHS = [210, 230, 300]
const GAP_X = 90
const GAP_Y = 18
const LINE_H = 16
const PAD_Y = 12

function wrap(text: string, maxChars: number, maxLines: number): string[] {
  const chars = Array.from(text)
  const lines: string[] = []
  let cur = ''
  for (let i = 0; i < chars.length; i++) {
    if ((cur + chars[i]).length > maxChars) {
      lines.push(cur)
      cur = chars[i]
      if (lines.length === maxLines) {
        lines[maxLines - 1] = lines[maxLines - 1].slice(0, maxChars - 1) + '…'
        return lines
      }
    } else {
      cur += chars[i]
    }
  }
  if (cur) lines.push(cur)
  return lines
}

function buildLayout(root: MindMapNode): { root: LayoutNode; width: number; height: number } {
  let cursor = 0
  const xAt = (depth: number) =>
    WIDTHS.slice(0, depth).reduce((sum, w) => sum + w + GAP_X, 0)

  const walk = (node: MindMapNode, depth: number): LayoutNode => {
    const w = WIDTHS[Math.min(depth, WIDTHS.length - 1)]
    const lines = wrap(node.label, depth === 0 ? 12 : depth === 1 ? 14 : 22, depth === 2 ? 5 : 3)
    const h = PAD_Y * 2 + lines.length * LINE_H + (node.meta ? 14 : 0)
    const children = (node.children ?? []).map((child) => walk(child, depth + 1))
    let y: number
    if (children.length === 0) {
      y = cursor
      cursor += h + GAP_Y
    } else {
      y = (children[0].y + children[children.length - 1].y) / 2
    }
    return { node, x: xAt(depth), y, w, h, lines, children }
  }

  const laid = walk(root, 0)
  const width = WIDTHS.slice(0, 3).reduce((a, b) => a + b, 0) + GAP_X * 2 + 40
  return { root: laid, width, height: Math.max(cursor, 120) }
}

function flatten(node: LayoutNode, out: LayoutNode[] = []): LayoutNode[] {
  out.push(node)
  node.children.forEach((child) => flatten(child, out))
  return out
}

export function MindMap({
  data,
  onSeek,
}: {
  data: MindMapResponse
  onSeek?: (ms: number) => void
}) {
  const svgRef = useRef<SVGSVGElement>(null)
  const [scale, setScale] = useState(1)
  const layout = useMemo(() => buildLayout(data.root), [data.root])
  const nodes = useMemo(() => flatten(layout.root), [layout])

  const exportSvg = () => {
    const svg = svgRef.current
    if (!svg) return
    const clone = svg.cloneNode(true) as SVGSVGElement
    clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg')
    const blob = new Blob([clone.outerHTML], { type: 'image/svg+xml;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `mindmap-${data.meeting_id}.svg`
    a.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div className="flex min-h-0 flex-col">
      <div className="flex items-center gap-1.5 px-3 py-2">
        <span className="mr-auto text-[11px] text-tertiary">
          基于纪要 v{data.version}
          {data.is_draft ? '（草稿）' : ''}
        </span>
        <button type="button" onClick={() => setScale((s) => Math.max(0.6, s - 0.1))} className="btn btn-ghost p-1" aria-label="缩小">
          <Minus size={13} />
        </button>
        <button type="button" onClick={() => setScale(1)} className="btn btn-ghost px-1.5 text-[11px]" aria-label="重置缩放">
          {Math.round(scale * 100)}%
        </button>
        <button type="button" onClick={() => setScale((s) => Math.min(1.6, s + 0.1))} className="btn btn-ghost p-1" aria-label="放大">
          <Plus size={13} />
        </button>
        <button type="button" onClick={exportSvg} className="btn btn-ghost p-1" title="导出 SVG" aria-label="导出 SVG">
          <Download size={13} />
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-auto rounded-lg border" style={{ borderColor: 'var(--border-subtle)' }}>
        <svg
          ref={svgRef}
          viewBox={`0 0 ${layout.width} ${layout.height + 20}`}
          style={{ width: layout.width * scale, height: (layout.height + 20) * scale }}
          role="img"
          aria-label="会议纪要思维导图"
        >
          <rect width="100%" height="100%" fill="var(--bg-surface)" />
          {nodes.flatMap((parent) =>
            parent.children.map((child) => {
              const x1 = parent.x + parent.w
              const y1 = parent.y + parent.h / 2
              const x2 = child.x
              const y2 = child.y + child.h / 2
              const mx = (x1 + x2) / 2
              return (
                <path
                  key={`${parent.node.id}-${child.node.id}`}
                  d={`M ${x1} ${y1} C ${mx} ${y1}, ${mx} ${y2}, ${x2} ${y2}`}
                  fill="none"
                  stroke="var(--border-subtle)"
                  strokeWidth={1.5}
                />
              )
            }),
          )}
          {nodes.map((item) => {
            const depth = item.x === 0 ? 0 : item.x < WIDTHS[0] + GAP_X + 1 ? 1 : 2
            const isTodo = item.node.id === 'todos' || item.node.id.startsWith('todo-')
            const fill =
              depth === 0
                ? 'var(--accent)'
                : isTodo
                  ? 'var(--warn-soft)'
                  : depth === 1
                    ? 'var(--accent-soft)'
                    : 'var(--bg-subtle)'
            const color =
              depth === 0
                ? 'var(--accent-contrast, #fff)'
                : isTodo
                  ? 'var(--warn-text)'
                  : depth === 1
                    ? 'var(--accent-text)'
                    : 'var(--text-secondary)'
            return (
              <g key={item.node.id}>
                <rect
                  x={item.x}
                  y={item.y}
                  width={item.w}
                  height={item.h}
                  rx={8}
                  fill={fill}
                  stroke={depth === 0 ? 'none' : 'var(--border-subtle)'}
                  style={{
                    cursor:
                      item.node.evidence?.[0] && onSeek ? 'pointer' : 'default',
                  }}
                  onClick={() => {
                    const evidence = item.node.evidence?.[0]
                    if (evidence && onSeek) onSeek(evidence.start_ms)
                  }}
                />
                {item.lines.map((line, i) => (
                  <text
                    key={i}
                    x={item.x + 12}
                    y={item.y + PAD_Y + 12 + i * LINE_H}
                    fontSize={depth === 0 ? 14 : depth === 1 ? 12.5 : 12}
                    fontWeight={depth <= 1 ? 600 : 400}
                    fill={color}
                  >
                    {line}
                  </text>
                ))}
                {item.node.meta && (
                  <text
                    x={item.x + 12}
                    y={item.y + PAD_Y + 12 + item.lines.length * LINE_H + 2}
                    fontSize={10.5}
                    fill={color}
                    opacity={0.8}
                  >
                    {item.node.meta}
                  </text>
                )}
                {item.node.evidence?.[0] && (
                  <text
                    x={item.x + item.w - 10}
                    y={item.y + item.h - 8}
                    textAnchor="end"
                    fontSize={9.5}
                    fill={color}
                    opacity={0.65}
                  >
                    原话 {formatEvidenceTime(item.node.evidence[0].start_ms)}
                  </text>
                )}
              </g>
            )
          })}
        </svg>
      </div>
    </div>
  )
}

function formatEvidenceTime(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000))
  return `${String(Math.floor(total / 60)).padStart(2, '0')}:${String(
    total % 60,
  ).padStart(2, '0')}`
}
