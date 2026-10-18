import { useState } from 'react'
import { Save, X } from 'lucide-react'

import type { ApiSegment } from '../api/types'
import { formatTimestamp } from '../store/session'

export function SegmentEditDialog({
  segment,
  onSave,
  onClose,
}: {
  segment: ApiSegment
  onSave: (text: string) => void
  onClose: () => void
}) {
  const [text, setText] = useState(segment.text)
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4">
      <div className="panel w-full max-w-[580px] p-4">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-[14px] font-medium">修订转写文字</h2>
          <button type="button" onClick={onClose} className="p-1 text-tertiary" aria-label="关闭">
            <X size={15} />
          </button>
        </div>
        <p className="mb-1 text-[11px] text-tertiary">
          原始识别时间 {formatTimestamp(segment.start_ms)} · 修订会同步更新全文检索
        </p>
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={7}
          autoFocus
          className="w-full rounded px-2.5 py-2 text-[13px] leading-relaxed outline-none"
          style={{
            background: 'var(--bg-surface)',
            border: '1px solid var(--border-default)',
            color: 'var(--text-primary)',
          }}
        />
        {segment.original_text && segment.original_text !== text && (
          <div
            className="mt-2 rounded-lg px-3 py-2 text-[11px] text-tertiary"
            style={{ background: 'var(--bg-subtle)' }}
          >
            <span className="font-medium">原始识别：</span>
            {segment.original_text}
          </div>
        )}
        <div className="mt-3 flex justify-end gap-2">
          <button type="button" onClick={onClose} className="btn text-[12px]">
            取消
          </button>
          <button
            type="button"
            onClick={() => onSave(text.trim())}
            disabled={!text.trim()}
            className="btn btn-primary text-[12px]"
          >
            <Save size={12} /> 保存修订
          </button>
        </div>
      </div>
    </div>
  )
}

