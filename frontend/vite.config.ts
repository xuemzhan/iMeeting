import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { existsSync, readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { resolve } from 'node:path'

const here = fileURLToPath(new URL('.', import.meta.url))

/**
 * 解析后端地址，作为 /api 与 /ws 的代理目标。
 *
 * 端口只在 `backend/.env` 里定义一处，这里读出来复用——避免「后端改了端口、
 * 前端代理没跟着改」导致所有 /api 请求都变成 500（代理连不上目标时 Vite
 * 返回的就是 500，且响应体不是 JSON，前端只能显示兜底的「请求失败 (500)」，
 * 排查起来非常费劲）。
 *
 * 优先级：VITE_API_TARGET 环境变量 > backend/.env 的 PORT > 8000。
 * 需要连别的机器时用 VITE_API_TARGET 显式覆盖即可。
 */
function resolveBackendTarget(): string {
  const override = process.env.VITE_API_TARGET?.trim()
  if (override) return override

  const envPath = resolve(here, '../backend/.env')
  let port = '8000'
  if (existsSync(envPath)) {
    for (const raw of readFileSync(envPath, 'utf8').split(/\r?\n/)) {
      // 只认行首的 PORT=，忽略注释行与其它含 PORT 字样的配置
      const m = /^\s*PORT\s*=\s*(\d+)\s*$/.exec(raw)
      if (m) {
        port = m[1]
        break
      }
    }
  }
  return `http://127.0.0.1:${port}`
}

export default defineConfig(() => {
  const target = resolveBackendTarget()
  return {
    plugins: [react(), tailwindcss()],
    server: {
      port: 5173,
      proxy: {
        '/api': { target, changeOrigin: true },
        '/ws': { target: target.replace(/^http/, 'ws'), ws: true },
      },
    },
    build: {
      target: 'es2022',
      sourcemap: false,
      chunkSizeWarningLimit: 800,
    },
  }
})
