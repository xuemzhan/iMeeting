import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

/**
 * 组件测试的公共收尾。
 *
 * Vitest 没开 `globals`，Testing Library 的自动清理不会注册；不显式 cleanup
 * 会让上一个用例的 DOM 留在 document 里，后续 `getByLabelText` 直接报
 * 「Found multiple elements」——这类假失败比真失败更难查。
 */
afterEach(() => {
  cleanup()
})
