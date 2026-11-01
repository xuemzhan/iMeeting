import { QueryClientProvider } from '@tanstack/react-query'
import { Suspense, lazy } from 'react'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { Loader2 } from 'lucide-react'

import { VoiceConfirmationDialog } from '../components/VoiceConfirmationDialog'
import { ErrorBoundary } from '../components/ErrorBoundary'
// 首屏路由（会议主界面）保持同步加载：打开应用第一眼就要用它。
import Meeting from '../pages/Meeting'
import { AdminGuard, Gate } from './guards'
import { queryClient } from './queryClient'

// 其余页面按路由懒加载：历史/详情/知识库/声纹/后台都不是首屏必需，
// 打包成独立 chunk 后首屏 JS 显著变小，便携包在弱网/慢机上的首屏更快。
const Admin = lazy(() => import('../pages/admin/Admin'))
const ChangePassword = lazy(() => import('../pages/ChangePassword'))
const History = lazy(() => import('../pages/History'))
const Knowledge = lazy(() => import('../pages/Knowledge'))
const Login = lazy(() => import('../pages/Login'))
const MeetingDetail = lazy(() => import('../pages/MeetingDetail'))
const SharedMeeting = lazy(() => import('../pages/SharedMeeting'))
const Voiceprints = lazy(() => import('../pages/Voiceprints'))

function RouteFallback() {
  return (
    <div className="flex h-full min-h-[240px] flex-col items-center justify-center gap-2">
      <Loader2 size={16} className="animate-spin" style={{ color: 'var(--accent)' }} />
      <p className="text-ui text-tertiary">正在加载页面…</p>
    </div>
  )
}

export function App() {
  return (
    <ErrorBoundary>
      <QueryClientProvider client={queryClient}>
        <VoiceConfirmationDialog />
        <BrowserRouter>
          <Gate>
            <Suspense fallback={<RouteFallback />}>
              <Routes>
                <Route path="/" element={<Meeting />} />
                <Route path="/history" element={<History />} />
                <Route path="/history/:id" element={<MeetingDetail />} />
                <Route path="/knowledge" element={<Knowledge />} />
                <Route path="/voiceprints" element={<Voiceprints />} />
                <Route path="/login" element={<Login />} />
                <Route path="/change-password" element={<ChangePassword />} />
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
            </Suspense>
          </Gate>
        </BrowserRouter>
      </QueryClientProvider>
    </ErrorBoundary>
  )
}
