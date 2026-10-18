import { useEffect, useState } from 'react'
import { Link, Navigate, useNavigate } from 'react-router-dom'
import { LogIn, ArrowLeft, Info } from 'lucide-react'
import { authApi } from '../api/client'
import { useAuth } from '../store/auth'

export default function Login() {
  const navigate = useNavigate()
  const { login, register, error, user } = useAuth()

  const [mode, setMode] = useState<'login' | 'register'>('login')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [submitting, setSubmitting] = useState(false)
  /** 是否已自动填入出厂默认账号（用于显示提示条）。 */
  const [prefilled, setPrefilled] = useState(false)

  // 拉取「出厂默认管理员账号」并预填，让首次使用免去翻文档找账号这一步。
  // 只在字段仍为空时写入，避免把用户已经输入的内容覆盖掉。
  useEffect(() => {
    let alive = true
    void (async () => {
      try {
        const data = await authApi.bootstrap()
        if (!alive) return
        setUsername((prev) => prev || data.default_username)
        if (data.using_default_password && data.default_password) {
          const pwd = data.default_password
          setPassword((prev) => prev || pwd)
          setPrefilled(true)
        }
      } catch {
        // 后端未就绪 / 接口不可用时静默降级：手动输入照样能登录。
      }
    })()
    return () => {
      alive = false
    }
  }, [])

  // 已登录则直接进后台。用 <Navigate> 而不是渲染期 navigate()：
  // 后者会在 render 阶段触发路由跳转，React 会告警且可能与 StrictMode 打架。
  if (user) return <Navigate to="/admin" replace />

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (submitting) return
    setSubmitting(true)
    try {
      const ok =
        mode === 'login'
          ? await login(username, password)
          : await register(username, password, displayName)
      if (ok) navigate('/admin')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="flex h-full items-center justify-center px-6">
      <div className="panel w-full max-w-[360px] p-6">
        <Link
          to="/"
          className="mb-4 inline-flex items-center gap-1 text-[12px] no-underline text-tertiary hover:text-secondary"
        >
          <ArrowLeft size={13} />
          返回会议
        </Link>

        <h1 className="mb-1 text-[15px] font-medium">
          {mode === 'login' ? '登录管理后台' : '创建账号'}
        </h1>
        <p className="mb-5 text-[12px] text-tertiary">
          {mode === 'login' ? '仅管理员可修改系统配置' : '注册后为普通用户权限'}
        </p>

        {mode === 'login' && prefilled && (
          <p
            className="mb-4 flex items-start gap-1.5 rounded-md px-2.5 py-2 text-[11px] leading-relaxed"
            style={{ background: 'var(--accent-soft)', color: 'var(--accent-text)' }}
          >
            <Info size={13} className="mt-px shrink-0" />
            <span>
              已自动填入出厂默认管理员账号，直接点「登录」即可。
              首次登录后建议在后台把默认密码改掉。
            </span>
          </p>
        )}

        <form onSubmit={handleSubmit} className="space-y-3">
          <div>
            <label className="label" htmlFor="username">
              用户名
            </label>
            <input
              id="username"
              className="input"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
              required
            />
          </div>

          {mode === 'register' && (
            <div>
              <label className="label" htmlFor="displayName">
                显示名称
              </label>
              <input
                id="displayName"
                className="input"
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                placeholder="可选"
              />
            </div>
          )}

          <div>
            <label className="label" htmlFor="password">
              密码
            </label>
            <input
              id="password"
              type="password"
              className="input"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
              required
            />
          </div>

          {error && (
            <p
              className="rounded-md px-2.5 py-2 text-[12px]"
              style={{ background: 'var(--danger-soft)', color: 'var(--danger-text)' }}
            >
              {error}
            </p>
          )}

          <button
            type="submit"
            className="btn btn-primary w-full"
            style={{ height: 36 }}
            disabled={submitting}
          >
            <LogIn size={14} />
            {submitting ? '处理中…' : mode === 'login' ? '登录' : '注册并登录'}
          </button>
        </form>

        <button
          type="button"
          onClick={() => setMode(mode === 'login' ? 'register' : 'login')}
          className="mt-4 w-full text-center text-[12px] text-tertiary hover:text-secondary"
        >
          {mode === 'login' ? '没有账号？创建一个' : '已有账号？去登录'}
        </button>
      </div>
    </div>
  )
}
