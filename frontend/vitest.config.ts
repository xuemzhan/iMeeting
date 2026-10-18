/** Vitest 专用配置。
 *
 * 单独成文件而不是写进 `vite.config.ts`：vitest 2.x 内部依赖 vite 5，
 * 而本项目用 vite 6，两者 `PluginOption` 类型不兼容，
 * 混在一起会导致 `tsc -b` 报一堆联合类型错误。
 * 测试本身不需要 react/tailwind 插件，所以这里保持最小配置。
 */

import { defineConfig } from 'vitest/config'

export default defineConfig({
  test: {
    // 被测逻辑（重连、片段合并）都不依赖 DOM
    environment: 'node',
    include: ['src/**/*.{test,spec}.{ts,tsx}'],
  },
})
