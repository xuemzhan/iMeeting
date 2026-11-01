import { History } from 'lucide-react'

import type { Minutes } from '../api/types'
import { MinutesVersionsPanel } from './MinutesVersionsPanel'
import { Sheet } from './Sheet'

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
    <Sheet
      onClose={onClose}
      title={
        <>
          <History size={14} style={{ color: 'var(--text-tertiary)' }} />
          纪要历史版本
        </>
      }
      actions={
        <span className="text-meta text-tertiary">
          {versions.length > 0 ? `共 ${versions.length} 版` : '暂无版本'}
        </span>
      }
      ariaLabel="纪要历史版本"
      maxWidth={1120}
      desktopHeight="86vh"
      fullScreen
    >
          <MinutesVersionsPanel
            versions={versions}
            embedded
            onEdit={(row) => {
              onEdit(row)
              onClose()
            }}
          />
    </Sheet>
  )
}
