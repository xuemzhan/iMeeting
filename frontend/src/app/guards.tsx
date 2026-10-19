import { useEffect, type ReactNode } from 'react'
import { Navigate, useLocation } from 'react-router-dom'

import { useAuth } from '../store/auth'

function LoadingScreen() {
  return (
    <div className="flex h-full items-center justify-center">
      <span className="size-4 animate-spin rounded-full border-2 border-[var(--accent)] border-t-transparent" />
    </div>
  )
}

export function Gate({ children }: { children: ReactNode }) {
  const { loading, bootstrap, user } = useAuth()
  const location = useLocation()

  useEffect(() => {
    void bootstrap()
  }, [bootstrap])

  if (loading) return <LoadingScreen />
  if (user?.must_change_password && location.pathname !== '/change-password') {
    return <Navigate to="/change-password" replace />
  }

  // 未登录也可以使用主界面（本地单机场景），仅后台需要登录
  return <>{children}</>
}

export function AdminGuard({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth()

  if (loading) return <LoadingScreen />
  if (!user) return <Navigate to="/login" replace />
  if (user.role !== 'admin') return <Navigate to="/" replace />
  return <>{children}</>
}

