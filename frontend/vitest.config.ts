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
