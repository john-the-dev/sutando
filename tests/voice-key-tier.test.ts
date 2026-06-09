/**
 * Contract tests for voiceKeyWithTier() and tier-based voice config defaults.
 *
 * Issue #1008 sub-item B: tier-aware key resolution and voice config defaults.
 * GEMINI_KEY_PAID → paid tier (2.5-native-audio + search:true).
 * Everything else → free tier (3.1-flash-live + search:false, OSS-safe).
 *
 * Source-read pattern — no env mutation, no module side-effects, fast.
 */

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const KEY_SRC = readFileSync(join(__dirname, '../src/voice-key.ts'), 'utf8');
const CFG_SRC = readFileSync(join(__dirname, '../src/voice-config.ts'), 'utf8');

describe('voiceKeyWithTier — source contract', () => {
	it('voice-key.ts is readable', () => {
		assert.ok(KEY_SRC.length > 0, 'voice-key.ts must be non-empty');
	});

	it('VoiceKeyTier type is exported', () => {
		assert.ok(
			KEY_SRC.includes("export type VoiceKeyTier"),
			'VoiceKeyTier type must be exported from voice-key.ts',
		);
	});

	it('voiceKeyWithTier is exported', () => {
		assert.ok(
			KEY_SRC.includes('export function voiceKeyWithTier'),
			'voiceKeyWithTier must be exported from voice-key.ts',
		);
	});

	it('GEMINI_KEY_PAID resolves to paid tier', () => {
		assert.ok(
			KEY_SRC.includes('GEMINI_KEY_PAID') && KEY_SRC.includes("tier: 'paid'"),
			"GEMINI_KEY_PAID must map to tier: 'paid'",
		);
		const paidLine = KEY_SRC.split('\n').find(l => l.includes('GEMINI_KEY_PAID') && l.includes("'paid'"));
		assert.ok(paidLine, 'GEMINI_KEY_PAID must appear on the same line as paid tier assignment');
	});

	it('GEMINI_KEY_FREE resolves to free tier', () => {
		assert.ok(
			KEY_SRC.includes('GEMINI_KEY_FREE'),
			'GEMINI_KEY_FREE must be checked in voiceKeyWithTier',
		);
		const freeLine = KEY_SRC.split('\n').find(l => l.includes('GEMINI_KEY_FREE') && l.includes("'free'"));
		assert.ok(freeLine, 'GEMINI_KEY_FREE must appear on the same line as free tier assignment');
	});

	it('GEMINI_KEY_PAID takes priority over GEMINI_KEY_FREE', () => {
		const paidIdx = KEY_SRC.indexOf('GEMINI_KEY_PAID');
		const freeIdx = KEY_SRC.indexOf('GEMINI_KEY_FREE');
		assert.ok(paidIdx < freeIdx, 'GEMINI_KEY_PAID must be checked before GEMINI_KEY_FREE (higher priority)');
	});

	it('legacy GEMINI_VOICE_API_KEY resolves to free tier', () => {
		// Find the line that checks GEMINI_VOICE_API_KEY in the function body (not the comment)
		const voiceFreeLine = KEY_SRC.split('\n').find(
			l => l.includes('GEMINI_VOICE_API_KEY') && l.includes("'free'") && !l.trim().startsWith('*'),
		);
		assert.ok(
			voiceFreeLine,
			"GEMINI_VOICE_API_KEY must appear in function body resolving to tier: 'free'",
		);
	});

	it('legacy GEMINI_API_KEY resolves to free tier', () => {
		const paidIdx = KEY_SRC.indexOf('GEMINI_KEY_PAID');
		const mainIdx = KEY_SRC.lastIndexOf('GEMINI_API_KEY');
		assert.ok(mainIdx > paidIdx, 'GEMINI_API_KEY must come after GEMINI_KEY_PAID (legacy fallback)');
	});

	it('voiceApiKey() backward-compat accessor is present', () => {
		assert.ok(
			KEY_SRC.includes('export function voiceApiKey'),
			'voiceApiKey() must be kept for backward compatibility',
		);
	});

	it('voiceApiKey() delegates to voiceKeyWithTier()', () => {
		const voiceApiKeyFn = KEY_SRC.slice(KEY_SRC.indexOf('export function voiceApiKey'));
		assert.ok(
			voiceApiKeyFn.includes('voiceKeyWithTier'),
			'voiceApiKey() must call voiceKeyWithTier() for consistent resolution',
		);
	});
});

describe('voice-config tier defaults — source contract', () => {
	it('voice-config.ts is readable', () => {
		assert.ok(CFG_SRC.length > 0, 'voice-config.ts must be non-empty');
	});

	it('VOICE_CONFIG_DEFAULTS is still exported (backward compat)', () => {
		assert.ok(
			CFG_SRC.includes('export const VOICE_CONFIG_DEFAULTS'),
			'VOICE_CONFIG_DEFAULTS must remain exported for backward compatibility (voice-config-switch.ts depends on it)',
		);
	});

	it('VOICE_CONFIG_DEFAULTS_FREE is exported', () => {
		assert.ok(
			CFG_SRC.includes('export const VOICE_CONFIG_DEFAULTS_FREE'),
			'VOICE_CONFIG_DEFAULTS_FREE must be exported from voice-config.ts',
		);
	});

	it('free-tier defaults use gemini-3.1-flash-live-preview', () => {
		const freeIdx = CFG_SRC.indexOf('VOICE_CONFIG_DEFAULTS_FREE');
		const freeSection = CFG_SRC.slice(freeIdx, freeIdx + 300);
		assert.ok(
			freeSection.includes('gemini-3.1-flash-live-preview'),
			'VOICE_CONFIG_DEFAULTS_FREE must use gemini-3.1-flash-live-preview (works on free Gemini keys)',
		);
	});

	it('free-tier defaults have googleSearch: false', () => {
		const freeIdx = CFG_SRC.indexOf('VOICE_CONFIG_DEFAULTS_FREE');
		const freeSection = CFG_SRC.slice(freeIdx, freeIdx + 300);
		assert.ok(
			freeSection.includes('googleSearch: false'),
			'VOICE_CONFIG_DEFAULTS_FREE must have googleSearch: false (avoids 1011 entitlement errors)',
		);
	});

	it('getVoiceConfigDefaults is exported', () => {
		assert.ok(
			CFG_SRC.includes('export function getVoiceConfigDefaults'),
			'getVoiceConfigDefaults(tier) must be exported from voice-config.ts',
		);
	});

	it('getVoiceConfigDefaults routes paid to VOICE_CONFIG_DEFAULTS', () => {
		const fnIdx = CFG_SRC.indexOf('export function getVoiceConfigDefaults');
		const fnBody = CFG_SRC.slice(fnIdx, fnIdx + 200);
		assert.ok(
			fnBody.includes('VOICE_CONFIG_DEFAULTS'),
			"getVoiceConfigDefaults('paid') must return VOICE_CONFIG_DEFAULTS (paid-tier defaults)",
		);
	});

	it('loadVoiceConfig accepts tier parameter', () => {
		const fnIdx = CFG_SRC.indexOf('export function loadVoiceConfig');
		const fnSig = CFG_SRC.slice(fnIdx, fnIdx + 100);
		assert.ok(
			fnSig.includes("tier:") || fnSig.includes("tier?:"),
			'loadVoiceConfig must accept a tier parameter',
		);
	});

	it('loadVoiceConfig uses getVoiceConfigDefaults internally', () => {
		const fnIdx = CFG_SRC.indexOf('export function loadVoiceConfig');
		const fnBody = CFG_SRC.slice(fnIdx, fnIdx + 500);
		assert.ok(
			fnBody.includes('getVoiceConfigDefaults'),
			'loadVoiceConfig must delegate to getVoiceConfigDefaults for tier-appropriate defaults',
		);
	});
});
