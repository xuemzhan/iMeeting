/**
 * Vitest 专用配置。
 *
 * 两层测试共用一份配置：
 * - 逻辑/协议层（lib、store、api）跑 Node 环境，启动快；
 * - 组件层（`src/components/*.test.tsx`）在文件头用
 *   `// @vitest-environment jsdom` 单独切到 DOM 环境，避免给所有用例加载 jsdom。
 *
 * 覆盖率统计排除测试文件、入口与纯样式文件（对应审查报告 P3-5）。
 */

import { defineConfig } from 'vitest/config'

export default defineConfig({
  test: {
    environment: 'node',
    // 并行 worker 在部分 Windows + Node 24 组合下会偶发崩掉：没有断言失败，
    // 整个 run 却以 1（worker exited）甚至 0xC0000409（进程级）退出。
    // 这里退回 fork 池并关掉文件级并行——22 个文件串行仍在半分钟内，
    // 换来的是稳定可复现的结果。
    pool: 'forks',
    fileParallelism: false,
    include: ['src/**/*.{test,spec}.{ts,tsx}'],
    setupFiles: ['./src/test-setup.ts'],
    coverage: {
      provider: 'v8',
      reporter: ['text', 'lcov', 'json-summary'],
      reportsDirectory: './coverage',
      include: ['src/**/*.{ts,tsx}'],
      exclude: [
        'src/**/*.{test,spec}.{ts,tsx}',
        'src/main.tsx',
        'src/test-setup.ts',
        'src/api/types.ts',
        'src/vite-env.d.ts',
      ],
    },
  },
})
