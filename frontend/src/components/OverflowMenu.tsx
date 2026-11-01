import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import {
  BookOpen,
  AudioLines,
  History,
  LogIn,
  LogOut,
  MoreVertical,
  Settings,
  ShieldCheck,
} from 'lucide-react'
import type { User } from '../api/types'
import { useAuth } from '../store/auth'

interface Props {
  /** 打开设置面板（状态由 TopBar 持有） */
  onOpenSettings: () => void
  /** 当前登录用户；null 表示未登录（菜单里给「管理登录」入口） */
  user: User | null
}

/**
 * 紧凑顶栏的「更多」菜单（仅手机档使用）。
 *
 * 手机宽度里塞不下品牌名 + 标题 + 状态 + 5 个入口 —— 实测 390px 需要 415px，
 * 横向溢出 25px。这里把导航、设置与账号操作收进下拉菜单，顶栏只保留「标题 + 状态」。
 * 账号区也必须在这里出现：否则手机上登录后无法退出、也进不去后台配置。
 * 交互与账号菜单一致：点外部或 Esc 收起，避免给整页加遮罩拖慢点击。
 */
export function OverflowMenu({ onOpenSettings, user }: Props) {
  const [open, setOpen] = useState(false)
  const boxRef = useRef<HTMLDivElement | null>(null)
  const navigate = useNavigate()
  const { logout } = useAuth()

  useEffect(() => {
    if (!open) return
    const onDown = (event: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(event.target as Node)) setOpen(false)
    }
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  const items = [
    { to: '/history', label: '历史会议', icon: <History size={16} /> },
    { to: '/knowledge', label: '会议知识库', icon: <BookOpen size={16} /> },
    { to: '/voiceprints', label: '声纹管理', icon: <AudioLines size={16} /> },
  ]

  // 手机上的每一项都要够拇指点：min-h-11 = 44px，与 --tap-min 同档。
  const rowClass =
    'flex min-h-11 w-full items-center gap-2.5 rounded-md px-3 text-left text-[14px] no-underline'

  return (
    <div className="relative" ref={boxRef}>
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="btn btn-ghost"
        aria-label="更多入口"
        aria-expanded={open}
        title="更多"
      >
        <MoreVertical size={17} />
      </button>

      {open && (
        <div className="panel absolute right-0 top-[44px] z-50 w-[200px] p-1.5">
          {items.map((item) => (
            <Link
              key={item.to}
              to={item.to}
              onClick={() => setOpen(false)}
              className={rowClass}
              style={{ color: 'var(--text-secondary)' }}
            >
              {item.icon}
              {item.label}
            </Link>
          ))}
          <button
            type="button"
            onClick={() => {
              setOpen(false)
              onOpenSettings()
            }}
            className={rowClass}
            style={{ color: 'var(--text-secondary)' }}
          >
            <Settings size={16} />
            设置
          </button>

          <div className="my-1 h-px" style={{ background: 'var(--border-subtle)' }} aria-hidden />

          {user ? (
            <>
              <div className="px-3 py-1.5">
                <div className="truncate text-[13px] font-medium" style={{ color: 'var(--text-primary)' }}>
                  {user.display_name || user.username}
                </div>
                <div className="text-ui text-tertiary">
                  {user.role === 'admin' ? '管理员' : '普通用户'}
                </div>
              </div>
              {user.role === 'admin' && (
                <button
                  type="button"
                  onClick={() => {
                    setOpen(false)
                    navigate('/admin')
                  }}
                  className={rowClass}
                  style={{ color: 'var(--text-secondary)' }}
                >
                  <ShieldCheck size={16} />
                  后台配置
                </button>
              )}
              <button
                type="button"
                onClick={() => {
                  setOpen(false)
                  // 只清登录态、不主动跳转：落点交给路由（与账号菜单同一约定）
                  logout()
                }}
                className={rowClass}
                style={{ color: 'var(--text-secondary)' }}
              >
                <LogOut size={16} />
                退出登录
              </button>
            </>
          ) : (
            <Link
              to="/login"
              onClick={() => setOpen(false)}
              className={rowClass}
              style={{ color: 'var(--text-secondary)' }}
            >
              <LogIn size={16} />
              管理登录
            </Link>
          )}
        </div>
      )}
    </div>
  )
}
