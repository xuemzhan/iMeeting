import { useMemo } from 'react'
import type { KnowledgeTopic } from '../api/types'

/**
 * 轻量知识图谱：中心为知识库，外圈为主题，主题大小代表出现频次。
 * 不引入图形库，避免为一张总览图增加数百 KB 依赖。
 */
export function TopicMap({
  topics,
  onSelect,
}: {
  topics: KnowledgeTopic[]
  onSelect: (topic: string) => void
}) {
  const top = useMemo(() => topics.slice(0, 12), [topics])
  const width = 900
  const height = 360
  const cx = width / 2
  const cy = height / 2
  const max = Math.max(1, ...top.map((item) => item.count))

  return (
    <div className="overflow-x-auto">
      <svg
        viewBox={`0 0 ${width} ${height}`}
        style={{ minWidth: 680, width: '100%', height: 360 }}
        role="img"
        aria-label="会议主题关系图"
      >
        {top.map((item, index) => {
          const angle = (Math.PI * 2 * index) / Math.max(1, top.length) - Math.PI / 2
          const radiusX = 300
          const radiusY = 125
          const x = cx + Math.cos(angle) * radiusX
          const y = cy + Math.sin(angle) * radiusY
          const r = 15 + (item.count / max) * 13
          return (
            <g
              key={item.topic}
              role="button"
              tabIndex={0}
              onClick={() => onSelect(item.topic)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') onSelect(item.topic)
              }}
              style={{ cursor: 'pointer' }}
            >
              <line
                x1={cx}
                y1={cy}
                x2={x}
                y2={y}
                stroke="var(--border-default)"
                strokeWidth={1}
              />
              <circle
                cx={x}
                cy={y}
                r={r}
                fill={index % 3 === 0 ? 'var(--accent-soft)' : 'var(--bg-subtle)'}
                stroke={index % 3 === 0 ? 'var(--accent)' : 'var(--border-default)'}
              />
              <text
                x={x}
                y={y + 4}
                textAnchor="middle"
                fontSize={10.5}
                fill="var(--text-secondary)"
              >
                {item.count}
              </text>
              <text
                x={x}
                y={y + r + 15}
                textAnchor="middle"
                fontSize={11}
                fill="var(--text-primary)"
              >
                {item.topic.length > 12 ? item.topic.slice(0, 12) + '…' : item.topic}
              </text>
              <title>
                {item.topic} · {item.count} 场会议
              </title>
            </g>
          )
        })}
        <circle cx={cx} cy={cy} r={40} fill="var(--accent)" />
        <text
          x={cx}
          y={cy + 4}
          textAnchor="middle"
          fontSize={13}
          fontWeight={600}
          fill="#fff"
        >
          知识库
        </text>
      </svg>
    </div>
  )
}
