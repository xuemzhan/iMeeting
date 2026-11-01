import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'

/**
 * 前端静态检查配置（flat config）。
 *
 * 项目此前完全没有前端 lint（`package.json` 里的 `lint` 脚本指向未安装的
 * eslint，等于死脚本）。一次引入全部 recommended 会产生大量与业务无关的噪声，
 * 因此这里按「能变成真 bug」的优先级裁剪：
 *
 * - `rules-of-hooks` 保持 error：hooks 调用顺序错乱是会直接崩溃的。
 * - `exhaustive-deps` / `set-state-in-effect` / `purity` / `refs` 设为 warn：
 *   这些是 React 19 + 编译器时代的新建议，现有代码里有 DND、音频波形和
 *   「store 同步到本地状态」等合理例外，先当提示，逐处收敛。
 * - 未定义变量、未使用变量、无效代码一律 error。
 */
export default tseslint.config(
  {
    ignores: [
      'dist',
      'dist-next',
      'node_modules',
      'playwright-report',
      'test-results',
      'coverage',
    ],
  },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ['**/*.{ts,tsx}'],
    languageOptions: {
      ecmaVersion: 2022,
      globals: { ...globals.browser, ...globals.node },
    },
    plugins: {
      'react-hooks': reactHooks,
      'react-refresh': reactRefresh,
    },
    rules: {
      'react-hooks/rules-of-hooks': 'error',
      'react-hooks/exhaustive-deps': 'warn',
      'react-hooks/set-state-in-effect': 'warn',
      'react-hooks/purity': 'warn',
      'react-hooks/refs': 'warn',
      'react-refresh/only-export-components': ['warn', { allowConstantExport: true }],
      // 项目里 `_` 前缀参数是「刻意不用」的约定，不报未使用
      '@typescript-eslint/no-unused-vars': [
        'error',
        { argsIgnorePattern: '^_', varsIgnorePattern: '^_', caughtErrorsIgnorePattern: '^_' },
      ],
    },
  },
  {
    // AudioWorklet / Service Worker 脚本：运行在 worker 全局作用域，不在 window 上
    files: ['src/audio/*.js', 'public/*.js', 'scripts/*.mjs'],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: 'script',
      globals: {
        ...globals.browser,
        ...globals.serviceworker,
        AudioWorkletProcessor: 'readonly',
        registerProcessor: 'readonly',
        sampleRate: 'readonly',
        currentTime: 'readonly',
        currentFrame: 'readonly',
        importScripts: 'readonly',
        module: 'writable',
      },
    },
    rules: {
      'no-undef': 'error',
      'no-unused-vars': ['error', { argsIgnorePattern: '^_' }],
    },
  },
  {
    files: ['scripts/*.mjs'],
    languageOptions: { sourceType: 'module', globals: { ...globals.node } },
  },
  {
    files: ['**/*.test.{ts,tsx}', 'e2e/**/*.ts', 'vitest.config.ts', 'playwright.config.ts'],
    languageOptions: { globals: { ...globals.node } },
  },
)
