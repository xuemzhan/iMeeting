import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, Loader2, RefreshCw, X } from 'lucide-react'
import { meetingApi } from '../api/client'

export function ReviewPanel({
  meetingId,
  onSeek,
}: {
  meetingId: number
  onSeek?: (ms: number) => void
}) {
  const qc = useQueryClient()
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const reviews = useQuery({
    queryKey: ['reviews', meetingId],
    queryFn: () => meetingApi.reviews(meetingId),
  })

  const run = async () => {
    setBusy(true)
    setMessage('已提交会后音频复核任务，完成后会自动刷新。')
    try {
      await meetingApi.reviewAudio(meetingId)
      window.setTimeout(() => {
        void qc.invalidateQueries({ queryKey: ['reviews', meetingId] })
      }, 5000)
    } catch (e) {
      setMessage(e instanceof Error ? e.message : '提交失败')
    } finally {
      setBusy(false)
    }
  }

  const action = async (id: number, accept: boolean) => {
    await (accept ? meetingApi.acceptReview(id) : meetingApi.rejectReview(id))
    await qc.invalidateQueries({ queryKey: ['reviews', meetingId] })
    if (accept) {
      await qc.invalidateQueries({ queryKey: ['meeting', meetingId] })
    }
  }

  return (
    <section className="panel mt-3 overflow-hidden">
      <div className="panel-header">
        <RefreshCw size={14} />
        <h2>会后音频复核</h2>
        <span className="ml-auto text-[10px] text-tertiary">
          只生成建议，不直接覆盖人工转写
        </span>
        <button type="button" onClick={() => void run()} disabled={busy} className="btn btn-ghost text-[11px]">
          {busy ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
          运行复核
        </button>
      </div>
      <div className="space-y-2 p-3">
        {message && <p className="text-[11px] text-tertiary">{message}</p>}
        {reviews.isLoading ? (
          <div className="flex justify-center py-5">
            <Loader2 size={16} className="animate-spin" />
          </div>
        ) : (reviews.data?.length ?? 0) === 0 ? (
          <p className="py-5 text-center text-[11px] text-tertiary">
            暂无待确认的复核建议
          </p>
        ) : (
          reviews.data?.map((review) => (
            <div key={review.id} className="rounded-lg p-2.5" style={{ background: 'var(--bg-subtle)' }}>
              <button
                type="button"
                onClick={() => onSeek?.(review.start_ms)}
                className="font-mono text-[10px] text-tertiary"
              >
                {Math.floor(review.start_ms / 60000)}:
                {String(Math.floor((review.start_ms % 60000) / 1000)).padStart(2, '0')}
              </button>
              <p className="mt-1 text-[11px] text-tertiary line-through">{review.original_text}</p>
              <p className="mt-1 text-[12px]">{review.suggested_text}</p>
              <div className="mt-1 flex items-center gap-2">
                <span className="text-[10px] text-tertiary">
                  相似度 {Math.round(review.similarity * 100)}%
                </span>
                <button type="button" onClick={() => void action(review.id, true)} className="btn btn-ghost text-[10px]">
                  <Check size={11} /> 采用
                </button>
                <button type="button" onClick={() => void action(review.id, false)} className="btn btn-ghost text-[10px]">
                  <X size={11} /> 忽略
                </button>
              </div>
            </div>
          ))
        )}
      </div>
    </section>
  )
}
