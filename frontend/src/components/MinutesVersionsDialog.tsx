import { History, X } from 'lucide-react'

import type { Minutes } from '../api/types'
import { MinutesVersionsPanel } from './MinutesVersionsPanel'

/**
 * 纪要历史版本弹窗。
 *
 * 为什么要做成弹窗：详情页左栏是整篇转写（长会议能到几万像素高），
 * 把版本面板挂在页面底部等于「点了没反应」。这里改成浮层，点一下就在
 * 当前视口出现，关闭即回到原处。
 */
export function MinutesVersionsDialog({
  versions,
  onEdit,
  onClose,
}: {
  versions: Minutes[]
  onEdit: (minutes: Minutes) => void
  onClose: () => void
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-3">
      <div
        className="panel flex max-h-[86vh] w-full max-w-[1120px] flex-col overflow-hidden"
        role="dialog"
        aria-label="纪要历史版本"
      >
        <div className="panel-header">
          <History size={14} style={{ color: 'var(--text-tertiary)' }} />
          <h2>纪要历史版本</h2>
          <span className="text-[11px] text-tertiary">
            {versions.length > 0 ? `共 ${versions.length} 版` : '暂无版本'}
          </span>
          <button
            type="button"
            onClick={onClose}
            className="btn btn-ghost ml-auto p-1"
            aria-label="关闭"
          >
            <X size={15} />
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto">
          <MinutesVersionsPanel
            versions={versions}
            embedded
            onEdit={(row) => {
              onEdit(row)
              onClose()
            }}
          />
        </div>
      </div>
    </div>
  )
}
