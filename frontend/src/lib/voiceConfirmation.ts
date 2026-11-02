import type { ConfirmSampleAssessment } from '../api/types'

export interface VoiceConfirmation {
  kind: 'voiceprint_confirmation'
  assessment: ConfirmSampleAssessment
  voiceprint_id: number
  name: string
  meeting_id: number
  segment_id: string | null
  start_ms: number | null
  requires_slot: boolean
  templates: { slot: number; quality: number; scenario?: Record<string, string> }[]
}

export interface VoiceChoice {
  /**
   * - `confirm_self` / `reassign` / `cancel`：按声纹协议裁决
   * - `label_only`：这次只改本场显示名，不写模板、不改声纹库名、不绑人员
   */
  decision: 'confirm_self' | 'reassign' | 'cancel' | 'label_only'
  target_voiceprint_id?: number
  slot?: number
}

export interface VoiceConfirmationEvent {
  confirmation: VoiceConfirmation
  resolve: (choice: VoiceChoice) => void
}

export const VOICE_CONFIRMATION_EVENT = 'imeeting-voice-confirmation'

export function askVoiceConfirmation(confirmation: VoiceConfirmation): Promise<VoiceChoice> {
  return new Promise((resolve, reject) => {
    const event = new CustomEvent<VoiceConfirmationEvent>(VOICE_CONFIRMATION_EVENT, {
      cancelable: true,
      detail: { confirmation, resolve },
    })
    if (window.dispatchEvent(event)) reject(new Error('请打开会议页面完成声纹核对'))
  })
}
