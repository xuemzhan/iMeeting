import { defineConfig, devices } from '@playwright/test'
import { rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const backendRoot = resolve(here, '../backend')
const databasePath = join(tmpdir(), `imeeting-playwright-${process.pid}.db`)
for (const suffix of ('', '-wal', '-shm')) {
  rmSync(`${databasePath}${suffix}`, { force: true })
}

export default defineConfig({
  testDir: './e2e',
  timeout: 45_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [['list'], ['html', { open: 'never' }]],
  use: {
    baseURL: 'http://127.0.0.1:5174',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    ...devices['Desktop Chrome'],
    // 本地复用已安装的 Chrome；CI 使用 Playwright 下载的 Chromium。
    ...(process.env.CI ? {} : { channel: 'chrome' }),
  },
  webServer: [
    {
      command: 'python -m uvicorn imeeting.main:app --host 127.0.0.1 --port 8020',
      cwd: backendRoot,
      url: 'http://127.0.0.1:8020/api/v1/live',
      timeout: 120_000,
      reuseExistingServer: false,
      env: {
        APP_ENV: 'development',
        ASR_MODE: 'off',
        ADMIN_PASSWORD: 'admin123',
        DATABASE_URL: `sqlite:///${databasePath.replaceAll('\\', '/')}`,
        LOG_FILE: join(tmpdir(), 'imeeting-playwright.log'),
        MEETING_SKIP_ALEMBIC: '1',
        PYTHONPATH: resolve(backendRoot, 'src'),
        REQUIRE_AUTH: '1',
        SECRET_KEY: 'playwright-secret-key-0123456789abcdef',
        SKIP_MODEL_WARMUP: '1',
      },
    },
    {
      command: 'npm run dev -- --host 127.0.0.1 --port 5174',
      cwd: here,
      url: 'http://127.0.0.1:5174',
      timeout: 60_000,
      reuseExistingServer: false,
      env: {
        VITE_API_TARGET: 'http://127.0.0.1:8020',
      },
    },
  ],
})
