import { describe, expect, it } from 'vitest'

import { normalizeSpokenName, speechSupported } from './speech'

describe('normalizeSpokenName', () => {
  it('去掉空格与标点', () => {
    expect(normalizeSpokenName('张 三。')).toBe('张三')
    expect(normalizeSpokenName('  李 四，')).toBe('李四')
    expect(normalizeSpokenName('王五、')).toBe('王五')
  })

  it('保留姓名本身，限制长度', () => {
    expect(normalizeSpokenName('欧阳修文')).toBe('欧阳修文')
    expect(normalizeSpokenName('一'.repeat(40)).length).toBe(32)
  })

  it('空输入不崩', () => {
    expect(normalizeSpokenName('')).toBe('')
    expect(normalizeSpokenName(undefined as unknown as string)).toBe('')
  })
})

describe('speechSupported', () => {
  it('无浏览器环境时返回 false（不能崩，也不能假装支持）', () => {
    expect(speechSupported()).toBe(false)
  })
})
