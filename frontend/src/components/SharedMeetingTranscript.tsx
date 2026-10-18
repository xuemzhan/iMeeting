import { FileText } from 'lucide-react'

import type { Paragraph } from '../store/paragraphs'
import { formatTimestamp, speakerLabel } from '../store/session'

export function SharedMeetingTranscript({
  paragraphs,
  visibleParagraphs,
  segmentCount,
  onLoadMore,
}: {
  paragraphs: Paragraph[]
  visibleParagraphs: Paragraph[]
  segmentCount: number
  onLoadMore: () => void
}) {
  return (
    <section className="panel overflow-hidden">
      <div className="panel-header">
        <FileText size={14} /> <h2>完整转写</h2>
        <span className="ml-auto text-[11px] text-tertiary">
          {segmentCount} 段
        </span>
      </div>
      <div className="space-y-3 p-4">
        {visibleParagraphs.map((paragraph) => (
          <div
            key={paragraph.key}
            className="flex gap-3"
            style={{
              contentVisibility: 'auto',
              containIntrinsicSize: '150px',
            }}
          >
            <span className="w-12 shrink-0 pt-0.5 font-mono text-[11px] text-tertiary">
              {formatTimestamp(paragraph.startMs)}
            </span>
            <div className="min-w-0 flex-1">
              {paragraph.speaker && (
                <p
                  className="mb-0.5 text-[11px] font-medium"
                  style={{ color: 'var(--accent-text)' }}
                >
                  {speakerLabel(paragraph.speaker)}
                </p>
              )}
              <p className="whitespace-pre-wrap text-[13px] leading-relaxed">
                {paragraph.text}
              </p>
            </div>
          </div>
        ))}
        {paragraphs.length > visibleParagraphs.length && (
          <div className="flex justify-center">
            <button type="button" className="btn text-[12px]" onClick={onLoadMore}>
              加载更多（剩余 {paragraphs.length - visibleParagraphs.length} 段）
            </button>
          </div>
        )}
      </div>
    </section>
  )
}
