import { describe, expect, it } from 'vitest'

import { parseHistoryTags } from './historyTags'

describe('parseHistoryTags', () => {
  it('trims comma-separated tags and drops empty values', () => {
    expect(parseHistoryTags(' 项目A, 项目B ,, ')).toEqual(['项目A', '项目B'])
  })

  it('treats an empty value as no tags', () => {
    expect(parseHistoryTags('')).toEqual([])
  })
})
