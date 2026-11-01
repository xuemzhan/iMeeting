import { useQuery } from '@tanstack/react-query'
import { Loader2, Network, Sparkles } from 'lucide-react'

import { meetingApi } from '../api/client'
import { MindMap } from './MindMap'

/**
 * 脑图的「内嵌版」：与 `MindMapDialog` 共用同一份查询与同一个 `MindMap` 组件，
 * 区别只在于不套 Sheet 弹层 —— 详情页 compact 档把它当第四个标签页用。
 *
 * 为什么要单独一个组件而不是给弹窗加 `inline` 开关：弹窗里有生成、重试、
 * 版本提示等交互，内嵌版只需要「有就画、没有就说一句」，拆开比加开关更清楚。
 */
export function MindMapPanel({
  meetingId,
  onSeek,
  onGenerate,
  generating = false,
}: {
  meetingId: number
  onSeek?: (ms: number) => void
  /** 传了就显示「生成纪要并创建脑图」按钮（详情页会复用它的生成逻辑） */
  onGenerate?: () => void
  generating?: boolean
}) {
  const { data, isLoading, error } = useQuery({
    queryKey: ['mindmap', meetingId],
    queryFn: () => meetingApi.mindmap(meetingId),
  })

  // 防御：接口异常时会返回 `[]` 之类的非对象（e2e 的兜底路由就是这样），
  // 直接塞给 MindMap 会在访问 `root.label` 时抛错，让整页掉进错误边界。
  const mindmap = data && !Array.isArray(data) && data.root ? data : null

  if (isLoading) {
    return (
      <div className="flex min-h-40 items-center justify-center gap-2 text-ui text-tertiary">
        <Loader2 size={16} className="animate-spin" />
        正在生成脑图…
      </div>
    )
  }

  if (mindmap) {
    return (
      <div className="panel flex min-h-0 flex-col overflow-hidden">
        <div className="panel-header">
          <Network size={14} style={{ color: 'var(--text-tertiary)' }} />
          <h2>会议纪要脑图</h2>
          <span className="text-meta text-tertiary">
            第 {mindmap.version} 版{mindmap.is_draft ? '实时草稿' : '纪要'}
          </span>
        </div>
        <div className="min-h-0 flex-1">
          <MindMap data={mindmap} onSeek={onSeek} />
        </div>
      </div>
    )
  }

  return (
    <div className="panel flex flex-col items-center gap-2 px-4 py-10 text-center">
      <Network size={20} style={{ color: 'var(--text-tertiary)' }} />
      <p className="text-ui text-secondary">{error ? '还没有可用的纪要' : '脑图暂时为空'}</p>
      <p className="max-w-[320px] text-meta text-tertiary">
        会中草稿生成后即可在这里查看主题与结论脑图；也可以现在基于完整转写生成一版。
      </p>
      {onGenerate && (
        <button
          type="button"
          onClick={onGenerate}
          disabled={generating}
          className="btn btn-primary mt-1 text-ui"
        >
          {generating ? <Loader2 size={13} className="animate-spin" /> : <Sparkles size={13} />}
          {generating ? '正在生成纪要…' : '生成纪要并创建脑图'}
        </button>
      )}
    </div>
  )
}
