import { expect, test } from '@playwright/test'

/**
 * 两个现场问题的回归：
 *
 * 1. 「参会人」一行里识别错的人要能直接删，不能只给「确认全部」；
 * 2. 「参会人重要程度」里同名不同声音的两行，删除必须落到点的那一行
 *    （按 speaker_key 写，不能只发显示名）。
 */

const speakers = [
  { speaker: '周YJ', speaker_key: 'spk_a', importance: 1, state: 0, segments: 2, speech_ms: 8000 },
  { speaker: '周YJ', speaker_key: 'spk_b', importance: 3, state: 0, segments: 1, speech_ms: 3000 },
  { speaker: '郭睿', speaker_key: 'spk_c', importance: 3, state: 0, segments: 1, speech_ms: 2000 },
]

const segments = [
  { segment_id: 's-1', seq: 0, text: '第一段', is_final: true, start_ms: 0, end_ms: 4000, speaker: '周YJ', speaker_key: 'spk_a' },
  { segment_id: 's-2', seq: 1, text: '第二段', is_final: true, start_ms: 4000, end_ms: 8000, speaker: '周YJ', speaker_key: 'spk_a' },
  { segment_id: 's-3', seq: 2, text: '第三段', is_final: true, start_ms: 8000, end_ms: 11000, speaker: '周YJ', speaker_key: 'spk_b' },
  { segment_id: 's-4', seq: 3, text: '第四段', is_final: true, start_ms: 11000, end_ms: 13000, speaker: '郭睿', speaker_key: 'spk_c' },
]

test('参会人一行可直接删除识别错的人', async ({ page }) => {
  let participants = [
    { participant_id: 1, person_id: 1, speaker: '周YJ', speaker_key: 'spk_a', role: '', importance: 3, state: 'pending', confidence: 0.3, ambiguous: false, evidence: [], attributes: [] },
    { participant_id: 2, person_id: 2, speaker: '郭睿', speaker_key: 'spk_c', role: '', importance: 3, state: 'pending', confidence: 0.3, ambiguous: false, evidence: [], attributes: [] },
  ]
  const patches: { personId: number; body: Record<string, unknown> }[] = []

  await page.addInitScript(() => localStorage.setItem('imeeting-token', 'isolated-ui-fixture'))
  await page.route('**/api/v1/**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    const reply = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
    if (path === '/api/v1/auth/me') return reply({ id: 1, username: 'fixture', display_name: '测试用户', role: 'admin', is_active: true, must_change_password: false })
    if (path === '/api/v1/meetings/1') return reply({
      id: 1, title: '参会人控件回归', owner_id: 1, status: 'finished', duration_ms: 20000,
      segment_count: segments.length, created_at: '2026-11-01T00:00:00Z', has_audio: false,
      participants: '', tags: '', archived: false, description: '', agenda: '', meeting_type: '',
      speakers, segments,
    })
    if (path === '/api/v1/meetings/1/participants') return reply(participants)
    if (path === '/api/v1/meetings/1/speakers') return reply(speakers)
    const patch = path.match(/^\/api\/v1\/meetings\/1\/participants\/(\d+)$/)
    if (patch && request.method() === 'PATCH') {
      const personId = Number(patch[1])
      const body = request.postDataJSON()
      patches.push({ personId, body })
      participants = participants.map((item) => (item.person_id === personId ? { ...item, state: body.state ?? item.state } : item))
      return reply(participants.find((item) => item.person_id === personId))
    }
    if (path.endsWith('/minutes') && !path.endsWith('/templates')) return reply(null)
    if (path === '/api/v1/minutes-templates') return reply([])
    if (path.endsWith('/health')) return reply({ status: 'ok', require_auth: true, asr_mode: 'off', models: {} })
    if (path.endsWith('/config')) return reply({})
    return reply([])
  })

  await page.goto('/history/1')
  await page.waitForLoadState('networkidle')

  const strip = page.getByRole('region', { name: '会议参会人' })
  // 不展开「明细」也能看到逐人删除
  await expect(strip.getByRole('button', { name: '删除参会人 郭睿', exact: true })).toBeVisible()
  await strip.getByRole('button', { name: '删除参会人 郭睿', exact: true }).click()

  expect(patches).toEqual([{ personId: 2, body: { state: 'rejected' } }])
  await expect(strip.getByRole('button', { name: '删除参会人 郭睿', exact: true })).toHaveCount(0)
  await expect(strip.getByRole('button', { name: '删除参会人 周YJ', exact: true })).toHaveCount(1)
})

test('同名两个声音：删除落到点的那一行（speaker_key）', async ({ page }) => {
  let current = speakers.map((item) => ({ ...item }))
  const writes: Record<string, unknown>[] = []

  await page.addInitScript(() => localStorage.setItem('imeeting-token', 'isolated-ui-fixture'))
  await page.route('**/api/v1/**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    const reply = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
    if (path === '/api/v1/auth/me') return reply({ id: 1, username: 'fixture', display_name: '测试用户', role: 'admin', is_active: true, must_change_password: false })
    if (path === '/api/v1/meetings/1') return reply({
      id: 1, title: '同名说话人回归', owner_id: 1, status: 'finished', duration_ms: 13000,
      segment_count: segments.length, created_at: '2026-11-01T00:00:00Z', has_audio: false,
      participants: '', tags: '', archived: false, description: '', agenda: '', meeting_type: '',
      speakers: current, segments,
    })
    if (path === '/api/v1/meetings/1/participants') return reply([])
    if (path === '/api/v1/meetings/1/speakers') return reply(current)
    if (path === '/api/v1/meetings/1/speakers/state' && request.method() === 'PUT') {
      const body = request.postDataJSON()
      writes.push(body)
      const key = body.speaker_key ?? null
      if (!key || !current.some((item) => item.speaker_key === key)) return reply({ detail: '没有这个说话人' }, 404)
      current = current.map((item) => (item.speaker_key === key ? { ...item, state: body.state } : item))
      return reply(current)
    }
    if (path.endsWith('/minutes') && !path.endsWith('/templates')) return reply(null)
    if (path === '/api/v1/minutes-templates') return reply([])
    if (path.endsWith('/health')) return reply({ status: 'ok', require_auth: true, asr_mode: 'off', models: {} })
    if (path.endsWith('/config')) return reply({})
    return reply([])
  })

  await page.goto('/history/1')
  await page.waitForLoadState('networkidle')

  const panel = page.locator('aside').getByText('参会人重要程度').locator('..').locator('..')
  // 同名两行要能区分
  await expect(panel.getByText('周YJ#1')).toBeVisible()
  await expect(panel.getByText('周YJ#2')).toBeVisible()

  await panel.getByRole('button', { name: '删除参会人 周YJ #2', exact: true }).click()

  expect(writes).toEqual([{ speaker: '周YJ', state: 2, speaker_key: 'spk_b' }])
  // spk_b 那一行消失，spk_a 还在
  await expect(panel.getByRole('button', { name: '删除参会人 周YJ #2', exact: true })).toHaveCount(0)
  await expect(panel.getByRole('button', { name: '删除参会人 周YJ', exact: true })).toHaveCount(1)
})
