import { Mic, Square, Pause, Play, Scissors } from 'lucide-react'
import { useSession } from '../store/session'
import { DeviceSelector } from './DeviceSelector'

/** 会议控制栏。
 *
 * 设计：
 * - idle：一个大主按钮，居中
 * - busy：spinner + 文案
 * - recording/paused：左对齐主操作 + 右对齐危险操作，中间用分隔线
 * - 每个按钮显示键盘快捷键提示（kbd 元素）
 */
export function ControlBar() {
  const status = useSession((s) => s.status)
  const start = useSession((s) => s.start)
  const stop = useSession((s) => s.stop)
  const togglePause = useSession((s) => s.togglePause)
  const finalizeCurrent = useSession((s) => s.finalizeCurrent)

  const isRecording = status === 'recording'
  const isPaused = status === 'paused'
  const isIdle = status === 'idle' || status === 'error'
  const isBusy = status === 'connecting' || status === 'stopping'

  return (
    <div className="flex w-full flex-wrap items-center justify-center gap-3">
      {isIdle && (
        <>
          {/* 开始前也能选设备：避免「开始会议→发现没声音→退出重来」 */}
          <DeviceSelector />

          <button
            type="button"
            onClick={() => void start()}
            disabled={isBusy}
            className="group flex items-center gap-3 rounded-full py-2.5 pr-6 pl-2.5 transition-transform active:scale-[0.97] disabled:opacity-60"
            style={{ background: 'var(--accent)', color: '#fff' }}
          >
            <span className="flex size-9 items-center justify-center rounded-full bg-white/15">
              <Mic size={18} />
            </span>
            <span className="text-left">
              <span className="block text-[14px] font-medium">开始会议</span>
              <span className="block text-[11px] opacity-75">点击即可，无需任何配置</span>
            </span>
          </button>
        </>
      )}

      {isBusy && (
        <button
          type="button"
          disabled
          className="flex items-center gap-3 rounded-full py-2.5 pr-6 pl-2.5"
          style={{ background: 'var(--bg-subtle)', color: 'var(--text-secondary)' }}
        >
          <span className="flex size-9 items-center justify-center rounded-full bg-black/5">
            <span className="size-3 animate-spin rounded-full border-2 border-current border-t-transparent" />
          </span>
          <span className="text-[14px] font-medium">
            {status === 'connecting' ? '正在连接…' : '正在收尾…'}
          </span>
        </button>
      )}

      {(isRecording || isPaused) && (
        <>
          {/* 设备选择器：会议中也能切，选错设备时用户不必退出重来 */}
          <DeviceSelector />

          {/* 主操作组 */}
          <button
            type="button"
            onClick={togglePause}
            className="btn"
            style={{ height: 40, paddingInline: 18 }}
            title={isPaused ? '继续 (Space)' : '暂停 (Space)'}
          >
            {isPaused ? <Play size={15} /> : <Pause size={15} />}
            {isPaused ? '继续' : '暂停'}
            <span className="ml-1 kbd" style={{ border: 'none', background: 'transparent', padding: 0 }}>
              Space
            </span>
          </button>

          <button
            type="button"
            onClick={finalizeCurrent}
            className="btn"
            style={{ height: 40, paddingInline: 16 }}
            title="立即结束当前句，触发出稿 (F)"
          >
            <Scissors size={15} />
            切句
            <span className="ml-1 kbd" style={{ border: 'none', background: 'transparent', padding: 0 }}>
              F
            </span>
          </button>

          {/* 分隔线 */}
          <span className="separator-v" aria-hidden />

          {/* 危险操作 */}
          <button
            type="button"
            onClick={() => void stop()}
            className="btn"
            style={{
              height: 40,
              paddingInline: 20,
              background: 'var(--danger)',
              borderColor: 'var(--danger)',
              color: '#fff',
            }}
            title="结束会议 (Esc)"
          >
            <Square size={14} fill="currentColor" />
            结束会议
            <span
              className="ml-1 kbd"
              style={{ border: 'none', background: 'transparent', padding: 0, color: 'rgba(255,255,255,0.6)' }}
            >
              Esc
            </span>
          </button>
        </>
      )}
    </div>
  )
}
