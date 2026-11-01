import { useState } from 'react'
import { Save } from 'lucide-react'

import type { ApiSegment } from '../api/types'
import { formatTimestamp } from '../store/session'
import { Sheet } from './Sheet'

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
    <Sheet onClose={onClose} title="修订转写文字" ariaLabel="修订转写文字" maxWidth={580}>
      <p className="mb-1 text-[11px] text-tertiary">
        原始识别时间 {formatTimestamp(segment.start_ms)} · 修订会同步更新全文检索
      </p>
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={7}
        autoFocus
        className="w-full rounded px-2.5 py-2 text-[13px] leading-relaxed outline-none max-md:text-[16px]"
        style={{
          background: 'var(--bg-surface)',
          border: '1px solid var(--border-default)',
          color: 'var(--text-primary)',
        }}
      />
      {segment.original_text && segment.original_text !== text && (
        <div
          className="mt-2 rounded-lg px-3 py-2 text-[11px] text-tertiary max-md:text-[12px]"
          style={{ background: 'var(--bg-subtle)' }}
        >
          <span className="font-medium">原始识别：</span>
          {segment.original_text}
        </div>
      )}
      <div className="mt-3 flex justify-end gap-2">
        <button type="button" onClick={onClose} className="btn text-[12px] max-md:text-[14px]">
          取消
        </button>
        <button
          type="button"
          onClick={() => onSave(text.trim())}
          disabled={!text.trim()}
          className="btn btn-primary text-[12px] max-md:text-[14px]"
        >
          <Save size={12} /> 保存修订
        </button>
      </div>
    </Sheet>
  )
}
