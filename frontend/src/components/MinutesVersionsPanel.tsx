import { useState } from 'react'
import { History } from 'lucide-react'

import type { Minutes } from '../api/types'

interface MinutesVersionsPanelProps {
  versions: Minutes[]
  onEdit: (minutes: Minutes) => void
}

export function MinutesVersionsPanel({
  versions,
  onEdit,
}: MinutesVersionsPanelProps) {
  const [compareA, setCompareA] = useState<number | null>(null)
  const [compareB, setCompareB] = useState<number | null>(null)
  const versionA = versions.find(
    (item) => item.version === (compareA ?? versions[0]?.version),
  )
  const versionB = versions.find(
    (item) => item.version === (compareB ?? versions[1]?.version),
  )

  return (
    <section className="panel mt-3 overflow-hidden">
      <div className="panel-header">
        <History size={14} style={{ color: 'var(--text-tertiary)' }} />
        <h2>纪要版本与对比</h2>
        <span className="ml-auto text-[11px] text-tertiary">
          自动草稿、终稿与人工修订均保留
        </span>
      </div>
      <div className="grid gap-3 p-4 lg:grid-cols-[280px_1fr]">
        <div className="space-y-1.5">
          {versions.length === 0 && (
            <p className="py-6 text-center text-[11px] text-tertiary">
              还没有纪要版本
            </p>
          )}
          {versions.map((row) => (
            <div
              key={row.id}
              className="rounded-lg px-2.5 py-2"
              style={{ background: 'var(--bg-subtle)' }}
            >
              <div className="flex items-center gap-1.5">
                <span className="text-[11px] font-medium">v{row.version}</span>
                <span className="chip" style={{ height: 18 }}>
                  {row.edited ? '人工修订' : row.is_draft ? '会中草稿' : '自动终稿'}
                </span>
                <span className="ml-auto text-[10px] text-tertiary">
                  {new Date(row.created_at).toLocaleTimeString('zh-CN', {
                    hour: '2-digit',
                    minute: '2-digit',
                  })}
                </span>
              </div>
              <p className="mt-1 line-clamp-2 text-[10px] leading-relaxed text-tertiary">
                {row.summary || '无摘要'}
              </p>
              <button
                type="button"
                onClick={() => onEdit(row)}
                className="mt-1 text-[10px] underline-offset-2 hover:underline"
                style={{ color: 'var(--accent-text)' }}
              >
                基于此版修订
              </button>
            </div>
          ))}
        </div>
        <div>
          {versions.length >= 2 ? (
            <>
              <div className="mb-2 flex items-center gap-2 text-[11px] text-tertiary">
                <select
                  value={versionA?.version ?? ''}
                  onChange={(event) => setCompareA(Number(event.target.value))}
                  className="rounded px-1.5 py-1"
                  style={{
                    background: 'var(--bg-surface)',
                    border: '1px solid var(--border-subtle)',
                  }}
                >
                  {versions.map((row) => (
                    <option key={row.id} value={row.version}>
                      v{row.version} · {row.edited ? '人工' : row.is_draft ? '草稿' : '终稿'}
                    </option>
                  ))}
                </select>
                <span>对比</span>
                <select
                  value={versionB?.version ?? ''}
                  onChange={(event) => setCompareB(Number(event.target.value))}
                  className="rounded px-1.5 py-1"
                  style={{
                    background: 'var(--bg-surface)',
                    border: '1px solid var(--border-subtle)',
                  }}
                >
                  {versions.map((row) => (
                    <option key={row.id} value={row.version}>
                      v{row.version} · {row.edited ? '人工' : row.is_draft ? '草稿' : '终稿'}
                    </option>
                  ))}
                </select>
              </div>
              <div className="grid gap-2 sm:grid-cols-2">
                {[versionA, versionB].map((row, index) => (
                  <div
                    key={index}
                    className="max-h-[320px] overflow-y-auto rounded-lg p-3"
                    style={{ background: 'var(--bg-subtle)' }}
                  >
                    <p className="mb-2 text-[11px] font-medium">
                      v{row?.version ?? '—'} ·{' '}
                      {row?.edited ? '人工修订' : row?.is_draft ? '草稿' : '终稿'}
                    </p>
                    <p className="whitespace-pre-wrap text-[11px] leading-relaxed text-secondary">
                      {row?.summary || '无摘要'}
                    </p>
                    {row?.topics.map((topic, topicIndex) => (
                      <p key={topicIndex} className="mt-2 text-[11px] text-tertiary">
                        · {topic.title}
                        {topic.conclusion ? `：${topic.conclusion}` : ''}
                      </p>
                    ))}
                  </div>
                ))}
              </div>
            </>
          ) : (
            <p className="py-8 text-center text-[11px] text-tertiary">
              至少需要两个版本才能对比
            </p>
          )}
        </div>
      </div>
    </section>
  )
}

