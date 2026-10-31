import { expect, test } from '@playwright/test'

for (const mode of ['cancel', 'confirm_self', 'reassign', 'confirm_self_full', 'reassign_full'] as const) {
  test(`transcript voice confirmation: ${mode}`, async ({ page }) => {
    const decision = mode === 'confirm_self_full' ? 'confirm_self' : mode === 'reassign_full' ? 'reassign' : mode
    const samples = Array.from({ length: 5 }, (_, slot) => ({ slot, quality: 0.8, active: true, superseded_by: null }))
    let speaker = '原说话人'
    const decisions: string[] = []
    await page.addInitScript(() => localStorage.setItem('imeeting-token', 'isolated-ui-fixture'))
    await page.route('**/api/v1/**', async (route) => {
      const request = route.request()
      const path = new URL(request.url()).pathname
      const reply = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
      if (path === '/api/v1/auth/me') return reply({ id: 1, username: 'fixture', display_name: '测试用户', role: 'admin', is_active: true, must_change_password: false })
      if (path === '/api/v1/voiceprints/relabel') {
        const body = request.postDataJSON()
        if (!body.decision) return reply({ detail: { kind: 'voiceprint_confirmation', voiceprint_id: 1, name: body.to_name, meeting_id: 1, segment_id: 's-1', start_ms: 1000, requires_slot: mode === 'confirm_self_full', templates: mode === 'confirm_self_full' ? samples : [], assessment: { conflict_id: 42, self_score: 0.2, other: { voiceprint_id: 2, name: '另一位参会人', score: 0.95 }, reason: 'both', level: 'high', tolerated: false, blocking: true, allowed_actions: ['confirm_self', 'reassign', 'cancel'] } } }, 409)
        decisions.push(body.decision)
        if (mode.endsWith('_full')) expect(body.slot).toBe(2)
        if (body.decision === 'confirm_self') speaker = '张三'
        if (body.decision === 'reassign') { expect(body.target_voiceprint_id).toBe(2); speaker = '另一位参会人' }
        return reply({ renamed_voiceprint: body.decision !== 'cancel', updated_segments: body.decision === 'cancel' ? 0 : 1, name: speaker })
      }
      if (path === '/api/v1/voiceprints') return reply([{ id: 1, name: '张三', provisional: false }, { id: 2, name: '另一位参会人', provisional: false }])
      if (path === '/api/v1/minutes-templates') return reply([])
      if (path.startsWith('/api/v1/voiceprints/') && path.endsWith('/templates')) return reply({ max_templates: 5, templates: mode === 'reassign_full' && path.includes('/2/') ? samples : [] })
      if (path === '/api/v1/meetings/1') return reply({ id: 1, title: '身份确认隔离验收', owner_id: 1, status: 'finished', duration_ms: 5000, segment_count: 1, created_at: '2026-10-31T00:00:00Z', has_audio: false, participants: speaker, tags: '', archived: false, description: '', agenda: '', meeting_type: '', speakers: [{ speaker, speaker_key: 'fixture-key', importance: 3, segments: 1, speech_ms: 3000 }], segments: [{ segment_id: 's-1', seq: 0, text: '这一段内容用于验证确认不会静默覆盖说话人。', is_final: true, start_ms: 1000, end_ms: 4000, speaker, speaker_key: 'fixture-key', speaker_score: 0.6, speaker_low_confidence: false }] })
      if (path.endsWith('/participants')) return reply([{ participant_id: 1, person_id: 1, speaker, speaker_key: 'fixture-key', role: '', importance: 3, state: 'confirmed', confidence: 1, ambiguous: false, evidence: ['s-1'], attributes: [] }])
      if (path.endsWith('/speakers')) return reply([{ speaker, speaker_key: 'fixture-key', importance: 3, segments: 1, speech_ms: 3000 }])
      if (path.includes('minutes') && !path.endsWith('/templates')) return reply(null)
      if (path.endsWith('/health')) return reply({ status: 'ok', require_auth: true, asr_mode: 'off', models: {} })
      if (path.endsWith('/config')) return reply({})
      return reply([])
    })
    await page.goto('/history/1')
    await page.waitForLoadState('networkidle')
    await page.getByTitle('点击可改为真实姓名').click()
    await page.getByRole('textbox', { name: '说话人姓名', exact: true }).fill('张三')
    await page.getByRole('textbox', { name: '说话人姓名', exact: true }).press('Enter')
    const dialog = page.getByRole('dialog', { name: '请核对说话人：张三' })
    await expect(dialog).toBeVisible()
    expect(decisions).toEqual([])
    if (mode === 'confirm_self_full') {
      await expect(dialog.getByRole('button', { name: '确认为本人', exact: true })).toBeDisabled()
      await dialog.getByRole('combobox', { name: '替换模板', exact: true }).selectOption('2')
    }
    if (mode === 'reassign_full') {
      await expect(dialog.getByRole('button', { name: '改为选中的人', exact: true })).toBeDisabled()
      await dialog.getByRole('combobox', { name: '替换目标样本', exact: true }).selectOption('2')
    }
    if (decision === 'cancel') await dialog.press('Escape')
    else if (decision === 'confirm_self') await dialog.getByRole('button', { name: '确认为本人', exact: true }).click()
    else await dialog.getByRole('button', { name: '改为选中的人', exact: true }).click()
    await expect(dialog).not.toBeVisible()
    expect(decisions).toEqual([decision])
    const expected = decision === 'cancel' ? '原说话人' : decision === 'reassign' ? '另一位参会人' : '张三'
    await expect(page.getByTitle('点击可改为真实姓名')).toHaveText(expected)
    await page.getByRole('button', { name: '展开明细', exact: true }).click()
    await expect(page.getByRole('link', { name: '证据1' })).toHaveAttribute('href', '/history/1#s-1')
    await page.getByTitle('已绑定声音，点击修改').click()
    await expect(page.getByRole('combobox', { name: '选择说话人' })).toBeVisible()
    await expect(page.getByRole('combobox', { name: '选择说话人' }).getByRole('option', { name: `${expected}（1段发言）` })).toHaveCount(1)
    await expect(page.getByPlaceholder('spk_xxx')).toHaveCount(0)
  })
}
