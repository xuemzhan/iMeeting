/** Vitest 专用配置。
 *
 * 测试与生产构建分开配置：测试只覆盖纯逻辑和协议层，
 * 不需要加载 React/Tailwind 插件，保持 Node 环境和最小启动成本。
 */

import { defineConfig } from 'vitest/config'

export default defineConfig({
  test: {
    // 被测逻辑（重连、片段合并）都不依赖 DOM
    environment: 'node',
    include: ['src/**/*.{test,spec}.{ts,tsx}'],
  },
})
