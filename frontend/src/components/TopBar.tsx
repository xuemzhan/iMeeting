import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import {
  Settings,
  History,
  Loader2,
  AudioLines,
  LogIn,
  LogOut,
  ShieldCheck,
  BookOpen,
} from 'lucide-react'
import type { User } from '../api/types'
import { useAuth } from '../store/auth'
import { useSession, formatDuration } from '../store/session'
import { Waveform } from './Waveform'
import { BrandMark } from './BrandMark'
import { SettingsPanel } from './SettingsPanel'

export function TopBar() {
  const [settingsOpen, setSettingsOpen] = useState(false)

  const { user } = useAuth()
  const status = useSession((s) => s.status)
  const elapsedMs = useSession((s) => s.elapsedMs)
  const level = useSession((s) => s.level)
  const title = useSession((s) => s.title)
  const setTitle = useSession((s) => s.setTitle)
  const persistTitle = useSession((s) => s.persistTitle)
  const reset = useSession((s) => s.reset)
  const micAlive = useSession((s) => s.micAlive)

  const isRecording = status === 'recording'
  const isPaused = status === 'paused'
  const isBusy = status === 'connecting' || status === 'stopping'

  return (
    <header
      className="flex h-14 shrink-0 items-center gap-3 px-4"
      style={{ borderBottom: '1px solid var(--border-subtle)', background: 'var(--bg-surface)' }}
    >
      {/* 品牌 */}
      <Link
        to="/"
        className="flex items-center gap-2 text-[13px] font-medium no-underline"
        title="返回会议首页"
        onClick={() => {
          // 会议已结束时清掉上一次会话的片段与 meetingId，确保 Logo
          // 真正回到可开始新会议的首页，而不是显示上一场的残留内容。
          if (status === 'idle' || status === 'error') reset()
        }}
      >
        <BrandMark size={26} />
        <span
          className="text-[15px] font-semibold tracking-tight"
          style={{ color: 'var(--text-primary)' }}
        >
          iMeeting
        </span>
      </Link>

      <span className="h-4 w-px" style={{ background: 'var(--border-default)' }} aria-hidden />

      {/* 会议标题（开始后可编辑） */}
      {status === 'idle' ? (
        <span className="text-[13px] text-tertiary">未开始</span>
      ) : (
        <input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          onBlur={() => void persistTitle()}
          onKeyDown={(e) => {
            if (e.key === 'Enter') e.currentTarget.blur()
          }}
          className="max-w-[280px] rounded-md border border-transparent bg-transparent px-1.5 py-0.5 text-[13px] outline-none transition-colors hover:border-[var(--border-subtle)] focus:border-[var(--border-default)]"
          style={{ color: 'var(--text-primary)' }}
          aria-label="会议标题"
        />
      )}

      {/* 计时 */}
      {(isRecording || isPaused || isBusy) && (
        <span
          className="font-mono text-[12px] tabular-nums"
          style={{ color: 'var(--text-secondary)' }}
        >
          {formatDuration(elapsedMs)}
        </span>
      )}

      <div className="flex-1" />

      {/* 波形 */}
      {(isRecording || isPaused) && <Waveform level={level} active={isRecording} />}

      {/* 麦克风信号指示：把「没声音」这件最容易误判的事显式说出来 */}
      {(isRecording || isPaused) && micAlive === false && (
        <span
          className="flex items-center gap-1.5 rounded-full px-2 py-1 text-[11px] font-medium"
          style={{ background: 'var(--danger-soft)', color: 'var(--danger-text)' }}
          title="已连续数秒未采集到声音，请检查麦克风设备与静音开关"
        >
          <span className="size-1.5 rounded-full" style={{ background: 'var(--danger)' }} aria-hidden />
          无声音输入
        </span>
      )}

      {/* 状态胶囊 */}
      <StatusPill status={status} />

      <Link to="/history" className="btn btn-ghost no-underline" title="历史会议">
        <History size={14} />
      </Link>

      <Link to="/knowledge" className="btn btn-ghost no-underline" title="会议知识库">
        <BookOpen size={14} />
      </Link>

      <Link to="/voiceprints" className="btn btn-ghost no-underline" title="声纹管理">
        <AudioLines size={14} />
      </Link>

      {/* 设置：对所有用户开放（外观、麦克风等本地偏好）。
          管理员的「后台配置」入口收进账号菜单里，避免顶栏出现两个齿轮。 */}
      <button
        type="button"
        onClick={() => setSettingsOpen(true)}
        className="btn btn-ghost"
        title="设置"
        aria-label="设置"
      >
        <Settings size={14} />
      </button>

      <AccountEntry user={user} />

      <SettingsPanel open={settingsOpen} onClose={() => setSettingsOpen(false)} />
    </header>
  )
}

/** 顶栏右侧的账号区：未登录显示「管理登录」入口，已登录显示头像菜单。 */
function AccountEntry({ user }: { user: User | null }) {
  const navigate = useNavigate()
  const { logout } = useAuth()
  const [open, setOpen] = useState(false)
  const boxRef = useRef<HTMLDivElement | null>(null)

  // 点击外部 / 按 Esc 收起菜单。挂 document 监听比给整页加遮罩更轻，
  // 也不会挡住顶栏其它按钮。
  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  if (!user) {
    return (
      <Link
        to="/login"
        className="btn btn-ghost no-underline"
        title="管理登录"
        aria-label="管理登录"
      >
        <LogIn size={14} />
      </Link>
    )
  }

  const name = user.display_name || user.username
  const isAdmin = user.role === 'admin'

  return (
    <div className="relative" ref={boxRef}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex size-6 items-center justify-center rounded-full text-[11px] font-medium"
        style={{ background: 'var(--accent-soft)', color: 'var(--accent-text)' }}
        title={`${name}${isAdmin ? '（管理员）' : ''}`}
        aria-label="账号菜单"
        aria-expanded={open}
      >
        {name.slice(0, 1).toUpperCase()}
      </button>

      {open && (
        <div className="panel absolute right-0 top-[34px] z-50 w-[184px] p-1.5">
          <div className="px-2 py-1.5">
            <div className="truncate text-[12px] font-medium" style={{ color: 'var(--text-primary)' }}>
              {name}
            </div>
            <div className="text-[11px] text-tertiary">
              {isAdmin ? '管理员' : '普通用户'}
            </div>
          </div>

          <div className="my-1 h-px" style={{ background: 'var(--border-subtle)' }} aria-hidden />

          {isAdmin && (
            <button
              type="button"
              onClick={() => {
                setOpen(false)
                navigate('/admin')
              }}
              className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-[12px] hover:bg-[var(--bg-subtle)]"
              style={{ color: 'var(--text-secondary)' }}
            >
              <ShieldCheck size={13} />
              后台配置
            </button>
          )}

          <button
            type="button"
            onClick={() => {
              setOpen(false)
              // 只清登录态，不自己跳转：落点交给路由决定。
              //   在会议页退出 → 留在原地，顶栏变回「管理登录」入口；
              //   在 /admin 退出 → AdminGuard 会把人送到 /login（登录页此时已
              //   预填好出厂账号，可直接登回）。这正是本应用既有的约定
              //   ——「admin 区未登录就回登录页」，不要在这里另立一套。
              logout()
            }}
            className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-[12px] hover:bg-[var(--bg-subtle)]"
            style={{ color: 'var(--text-secondary)' }}
          >
            <LogOut size={13} />
            退出登录
          </button>
        </div>
      )}
    </div>
  )
}

function StatusPill({ status }: { status: string }) {
  if (status === 'recording') {
    return (
      <span
        className="chip"
        style={{ background: 'var(--live-soft)', color: 'var(--live-text)' }}
      >
        <span className="animate-breathe size-1.5 rounded-full" style={{ background: 'var(--live)' }} />
        识别中
      </span>
    )
  }

  if (status === 'paused') {
    return (
      <span className="chip" style={{ background: 'var(--warn-soft)', color: 'var(--warn-text)' }}>
        <span className="size-1.5 rounded-full" style={{ background: 'var(--warn)' }} />
        已暂停
      </span>
    )
  }

  if (status === 'connecting' || status === 'stopping') {
    return (
      <span className="chip" style={{ background: 'var(--bg-subtle)', color: 'var(--text-secondary)' }}>
        <Loader2 size={11} className="animate-spin" />
        {status === 'connecting' ? '连接中' : '收尾中'}
      </span>
    )
  }

  if (status === 'error') {
    return (
      <span className="chip" style={{ background: 'var(--danger-soft)', color: 'var(--danger-text)' }}>
        <span className="size-1.5 rounded-full" style={{ background: 'var(--danger)' }} />
        异常
      </span>
    )
  }

  return (
    <span className="chip" style={{ background: 'var(--bg-subtle)', color: 'var(--text-tertiary)' }}>
      待开始
    </span>
  )
}
