import { Loader2, Network } from 'lucide-react'

import type { MindMapResponse } from '../api/types'
import { MindMap } from './MindMap'

interface MinutesMindMapPanelProps {
  data: MindMapResponse | undefined
  loading: boolean
  onSeek: (ms: number) => void
  onClose: () => void
}

export function MinutesMindMapPanel({
  data,
  loading,
  onSeek,
  onClose,
}: MinutesMindMapPanelProps) {
  return (
    <section className="panel mt-3 overflow-hidden">
      <div className="panel-header">
        <Network size={14} style={{ color: 'var(--text-tertiary)' }} />
        <h2>纪要思维导图</h2>
        <button
          type="button"
          onClick={onClose}
          className="btn btn-ghost ml-auto px-1.5 text-[11px]"
        >
          收起
        </button>
      </div>
      <div style={{ height: 420 }}>
        {loading ? (
          <div className="flex h-full items-center justify-center gap-2 text-[12px] text-tertiary">
            <Loader2 size={14} className="animate-spin" />
            正在生成导图…
          </div>
        ) : data ? (
          <MindMap data={data} onSeek={onSeek} />
        ) : (
          <p className="py-10 text-center text-[12px] text-tertiary">
            还没有可用的纪要
          </p>
        )}
      </div>
    </section>
  )
}

