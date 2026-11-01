import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Loader2, Network, Sparkles } from 'lucide-react'
import { meetingApi } from '../api/client'
import { MindMap } from './MindMap'
import { Sheet } from './Sheet'

/**
 * 会中/会后共用的脑图查看器。
 * 后端始终返回最新一版纪要（含会中草稿），因此渐进纪要更新后重新打开
 * 即可看到新主题，不需要单独维护另一份脑图数据。
 */
export function MindMapDialog({
  meetingId,
  onClose,
  onSeek,
}: {
  meetingId: number
  onClose: () => void
  /** 点导图节点跳到音频对应位置（详情页有播放器时用） */
  onSeek?: (ms: number) => void
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
    <Sheet
      onClose={onClose}
      title={
        <>
          <Network size={14} style={{ color: 'var(--text-tertiary)' }} />
          会议纪要脑图
        </>
      }
      actions={
        data ? (
          <span className="text-meta text-tertiary">
            第 {data.version} 版{data.is_draft ? '实时草稿' : '纪要'}
          </span>
        ) : undefined
      }
      ariaLabel="会议纪要脑图"
      maxWidth={1120}
      desktopHeight="82vh"
      fullScreen
    >
      <div className="flex h-full min-h-0 flex-col">
        {isLoading ? (
          <div className="flex flex-1 items-center justify-center gap-2 text-ui text-tertiary">
            <Loader2 size={16} className="animate-spin" />
            正在生成脑图…
          </div>
        ) : data ? (
          <MindMap data={data} onSeek={onSeek} />
        ) : error ? (
          <div className="flex flex-1 flex-col items-center justify-center gap-2 text-center">
            <Network size={20} style={{ color: 'var(--text-tertiary)' }} />
            <p className="text-ui text-secondary">还没有可用的纪要</p>
            <p className="max-w-[360px] text-meta text-tertiary">
              会中草稿生成后即可在这里查看主题与结论脑图；也可以现在基于完整转写生成一版。
            </p>
            <button
              type="button"
              onClick={() => void generate()}
              disabled={generating}
              className="btn btn-primary mt-1 text-ui"
            >
              {generating ? (
                <Loader2 size={13} className="animate-spin" />
              ) : (
                <Sparkles size={13} />
              )}
              {generating ? '正在生成纪要…' : '生成纪要并创建脑图'}
            </button>
            {actionError && (
              <p className="max-w-[420px] text-meta" style={{ color: 'var(--danger-text)' }}>
                {actionError}
              </p>
            )}
          </div>
        ) : (
          <div className="flex flex-1 flex-col items-center justify-center gap-2 text-center">
            <Network size={20} style={{ color: 'var(--text-tertiary)' }} />
            <p className="text-ui text-secondary">脑图暂时为空</p>
          </div>
        )}
      </div>
    </Sheet>
  )
}
