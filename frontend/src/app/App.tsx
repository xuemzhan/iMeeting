import { QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'

import { ErrorBoundary } from '../components/ErrorBoundary'
import Admin from '../pages/admin/Admin'
import History from '../pages/History'
import Knowledge from '../pages/Knowledge'
import Login from '../pages/Login'
import Meeting from '../pages/Meeting'
import MeetingDetail from '../pages/MeetingDetail'
import SharedMeeting from '../pages/SharedMeeting'
import Voiceprints from '../pages/Voiceprints'
import { AdminGuard, Gate } from './guards'
import { queryClient } from './queryClient'

export function App() {
  return (
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
  )
}

