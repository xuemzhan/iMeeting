import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Loader2, Network, Sparkles, X } from 'lucide-react'
import { meetingApi } from '../api/client'
import { MindMap } from './MindMap'

/**
 * 会中/会后共用的脑图查看器。
 * 后端始终返回最新一版纪要（含会中草稿），因此渐进纪要更新后重新打开
 * 即可看到新主题，不需要单独维护另一份脑图数据。
 */
export function MindMapDialog({
  meetingId,
  onClose,
}: {
  meetingId: number
  onClose: () => void
}) {
  const qc = useQueryClient()
  const [generating, setGenerating] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  const { data, isLoading, error } = useQuery({
    queryKey: ['mindmap', meetingId],
    queryFn: () => meetingApi.mindmap(meetingId),
  })

  const generate = async () => {
    setGenerating(true)
    setActionError(null)
    try {
      await meetingApi.generateMinutes(meetingId, false, null)
      await qc.invalidateQueries({ queryKey: ['mindmap', meetingId] })
      await qc.invalidateQueries({ queryKey: ['minutes', meetingId] })
      await qc.invalidateQueries({ queryKey: ['minutes-versions', meetingId] })
    } catch (e) {
      setActionError(e instanceof Error ? e.message : '生成纪要失败')
    } finally {
      setGenerating(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-3">
      <div
        className="panel flex h-[82vh] w-full max-w-[1120px] flex-col overflow-hidden"
        role="dialog"
        aria-label="会议纪要脑图"
      >
        <div className="panel-header">
          <Network size={14} style={{ color: 'var(--text-tertiary)' }} />
          <h2>会议纪要脑图</h2>
          {data && (
            <span className="text-[11px] text-tertiary">
              基于第 {data.version} 版{data.is_draft ? '实时草稿' : '纪要'}
            </span>
          )}
          <button
            type="button"
            onClick={onClose}
            className="btn btn-ghost ml-auto p-1"
            aria-label="关闭"
          >
            <X size={15} />
          </button>
        </div>
        <div className="min-h-0 flex-1">
          {isLoading ? (
            <div className="flex h-full items-center justify-center gap-2 text-[12px] text-tertiary">
              <Loader2 size={16} className="animate-spin" />
              正在生成脑图…
            </div>
          ) : data ? (
            <MindMap data={data} />
          ) : error ? (
            <div className="flex h-full flex-col items-center justify-center gap-2 text-center">
              <Network size={20} style={{ color: 'var(--text-tertiary)' }} />
              <p className="text-[12px] text-secondary">还没有可用的纪要</p>
              <p className="max-w-[360px] text-[11px] text-tertiary">
                会中草稿生成后即可在这里查看主题与结论脑图；也可以现在基于完整转写生成一版。
              </p>
              <button
                type="button"
                onClick={() => void generate()}
                disabled={generating}
                className="btn btn-primary mt-1 text-[12px]"
              >
                {generating ? (
                  <Loader2 size={13} className="animate-spin" />
                ) : (
                  <Sparkles size={13} />
                )}
                {generating ? '正在生成纪要…' : '生成纪要并创建脑图'}
              </button>
              {actionError && (
                <p className="max-w-[420px] text-[11px]" style={{ color: 'var(--danger-text)' }}>
                  {actionError}
                </p>
              )}
            </div>
          ) : (
            <div className="flex h-full flex-col items-center justify-center gap-2 text-center">
              <Network size={20} style={{ color: 'var(--text-tertiary)' }} />
              <p className="text-[12px] text-secondary">脑图暂时为空</p>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
