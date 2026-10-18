import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

import './styles/index.css'
import { App } from './app/App'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)

// 生产构建注册离线壳缓存；开发环境不注册，避免 SW 缓存掩盖 HMR 改动。
if (import.meta.env.PROD && 'serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    void navigator.serviceWorker.register('/service-worker.js')
  })
}
