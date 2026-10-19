import { useState } from 'react'
import { Link, Navigate, useNavigate } from 'react-router-dom'
import { KeyRound, Loader2 } from 'lucide-react'

import { useAuth } from '../store/auth'

export default function ChangePassword() {
  const navigate = useNavigate()
  const { user, changePassword, error } = useAuth()
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [localError, setLocalError] = useState<string | null>(null)

  if (!user) return <Navigate to="/login" replace />

  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    if (submitting) return
    if (newPassword !== confirmPassword) {
      setLocalError('两次输入的新密码不一致')
      return
    }
    setLocalError(null)
    setSubmitting(true)
    try {
      if (await changePassword(currentPassword, newPassword)) {
        navigate('/admin', { replace: true })
      }
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="flex h-full items-center justify-center px-6">
      <div className="panel w-full max-w-[380px] p-6">
        <div className="mb-4 flex items-center gap-2">
          <KeyRound size={16} style={{ color: 'var(--accent)' }} />
          <h1 className="text-[15px] font-medium">修改密码</h1>
        </div>
        <p className="mb-5 text-[11px] leading-relaxed text-tertiary">
          {user.must_change_password
            ? '当前账号仍在使用初始化密码，修改后才能进入业务页面。'
            : '修改后，当前登录令牌保持有效。'}
        </p>

        <form onSubmit={submit} className="space-y-3">
          <div>
            <label className="label" htmlFor="current-password">
              当前密码
            </label>
            <input
              id="current-password"
              type="password"
              className="input"
              value={currentPassword}
              onChange={(event) => setCurrentPassword(event.target.value)}
              autoComplete="current-password"
              required
            />
          </div>
          <div>
            <label className="label" htmlFor="new-password">
              新密码
            </label>
            <input
              id="new-password"
              type="password"
              className="input"
              value={newPassword}
              onChange={(event) => setNewPassword(event.target.value)}
              autoComplete="new-password"
              minLength={8}
              required
            />
          </div>
          <div>
            <label className="label" htmlFor="confirm-password">
              确认新密码
            </label>
            <input
              id="confirm-password"
              type="password"
              className="input"
              value={confirmPassword}
              onChange={(event) => setConfirmPassword(event.target.value)}
              autoComplete="new-password"
              minLength={8}
              required
            />
          </div>

          {(localError || error) && (
            <p
              className="rounded-md px-2.5 py-2 text-[12px]"
              style={{ background: 'var(--danger-soft)', color: 'var(--danger-text)' }}
            >
              {localError || error}
            </p>
          )}

          <button
            type="submit"
            className="btn btn-primary w-full"
            style={{ height: 36 }}
            disabled={submitting}
          >
            {submitting ? (
              <Loader2 size={14} className="animate-spin" />
            ) : (
              <KeyRound size={14} />
            )}
            {submitting ? '正在修改…' : '修改密码'}
          </button>
        </form>

        <Link
          to="/login"
          className="mt-4 block text-center text-[12px] text-tertiary no-underline hover:text-secondary"
        >
          返回登录页
        </Link>
      </div>
    </div>
  )
}
