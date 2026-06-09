/**
 * Shared Gemini API-key resolution for voice surfaces (voice-agent,
 * phone-conversation, discord-voice).
 *
 * Tier detection (checked in priority order):
 *   GEMINI_KEY_PAID      → paid  (explicit opt-in; paid model + search:true defaults)
 *   GEMINI_KEY_FREE      → free  (explicit free; safe fallback defaults)
 *   GEMINI_VOICE_API_KEY → free  (legacy alias — no tier signal, treated as free per #1008)
 *   GEMINI_API_KEY       → free  (legacy fallback — same rationale)
 *   (none)               → free, empty key
 *
 * OSS default = free. Paid is an explicit opt-in via GEMINI_KEY_PAID.
 * Legacy vars are preserved for backward compatibility but map to the free
 * tier because we have no tier signal from the env name alone. Owner adds
 * GEMINI_KEY_PAID to opt into paid-tier model + googleSearch defaults without
 * touching any other env var.
 *
 * Why a util: all three voice surfaces should pick the same key the same way,
 * so a tier upgrade benefits all three at once. voiceApiKey() is kept for
 * backward compatibility.
 */

export type VoiceKeyTier = 'paid' | 'free';

export function voiceKeyWithTier(): { key: string; tier: VoiceKeyTier } {
	if (process.env.GEMINI_KEY_PAID) return { key: process.env.GEMINI_KEY_PAID, tier: 'paid' };
	if (process.env.GEMINI_KEY_FREE) return { key: process.env.GEMINI_KEY_FREE, tier: 'free' };
	if (process.env.GEMINI_VOICE_API_KEY) return { key: process.env.GEMINI_VOICE_API_KEY, tier: 'free' };
	if (process.env.GEMINI_API_KEY) return { key: process.env.GEMINI_API_KEY, tier: 'free' };
	return { key: '', tier: 'free' };
}

/** Backward-compatible key-only accessor. */
export function voiceApiKey(): string {
	return voiceKeyWithTier().key;
}
