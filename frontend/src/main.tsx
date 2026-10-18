import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

import './styles/index.css'
import Meeting from './pages/Meeting'
import MeetingDetail from './pages/MeetingDetail'
import Voiceprints from './pages/Voiceprints'
import History from './pages/History'
import Login from './pages/Login'
import Admin from './pages/admin/Admin'
import Knowledge from './pages/Knowledge'
import SharedMeeting from './pages/SharedMeeting'
import { useAuth } from './store/auth'
import { useEffect } from 'react'
import { ErrorBoundary } from './components/ErrorBoundary'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: false,
      staleTime: 30_000,
    },
  },
})

function Gate({ children }: { children: React.ReactNode }) {
  const { loading, bootstrap } = useAuth()

  useEffect(() => {
    void bootstrap()
  }, [bootstrap])

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center">
        <span className="size-4 animate-spin rounded-full border-2 border-[var(--accent)] border-t-transparent" />
      </div>
    )
  }

  // 未登录也可以使用主界面（本地单机场景），仅后台需要登录
  return <>{children}</>
}

function AdminGuard({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth()

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center">
        <span className="size-4 animate-spin rounded-full border-2 border-[var(--accent)] border-t-transparent" />
      </div>
    )
  }

  if (!user) return <Navigate to="/login" replace />
  if (user.role !== 'admin') return <Navigate to="/" replace />
  return <>{children}</>
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ErrorBoundary>
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <Gate>
            <Routes>
              <Route path="/" element={<Meeting />} />
              <Route path="/history" element={<History />} />
              <Route path="/history/:id" element={<MeetingDetail />} />
              <Route path="/knowledge" element={<Knowledge />} />
              <Route path="/voiceprints" element={<Voiceprints />} />
              <Route path="/login" element={<Login />} />
              <Route path="/share/:token" element={<SharedMeeting />} />
              <Route
                path="/admin"
                element={
                  <AdminGuard>
                    <Admin />
                  </AdminGuard>
                }
              />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </Gate>
        </BrowserRouter>
      </QueryClientProvider>
    </ErrorBoundary>
  </StrictMode>,
)

// 生产构建注册离线壳缓存；开发环境不注册，避免 SW 缓存掩盖 HMR 改动。
if (import.meta.env.PROD && 'serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    void navigator.serviceWorker.register('/service-worker.js')
  })
}
