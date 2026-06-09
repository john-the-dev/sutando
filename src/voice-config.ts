/**
 * Per-surface voice configuration loader.
 *
 * `loadVoiceConfig(path, tier?)` is path-agnostic — each caller decides where
 * its config lives and passes the absolute path in. The config is per-user
 * DATA (model + grounding prefs the operator tunes), not code, so it does NOT
 * live in the git repo — it lives in the workspace:
 *
 *   - voice-agent        → `$SUTANDO_WORKSPACE/config/voice-agent.json`
 *   - phone-conversation → `$SUTANDO_WORKSPACE/config/phone-conversation.json`
 *   - discord-voice      → `$SUTANDO_WORKSPACE/config/discord-voice.json`
 *
 * Each surface ships a committed `*.example` template (`src/voice-agent.config
 * .json.example`, `skills/<surface>/config.json.example`); on first run the
 * surface copies the template into the workspace if the live config is
 * missing. Schema:
 *
 *   {
 *     "model": "gemini-2.5-flash-native-audio-preview-12-2025",
 *     "googleSearch": true,
 *     "owner_mode": false,
 *     "channels": { "<voice_channel_id>": { "owner_mode": true } }
 *   }
 *
 * Missing file → tier-based defaults. Partial file → fill in missing keys
 * from tier-based defaults. Owner can always override any field explicitly.
 *
 * Tier-based defaults (issue #1008 sub-item B):
 *   paid  → 2.5-native-audio + search:true  (GEMINI_KEY_PAID set)
 *   free  → 3.1-flash-live-preview + search:false  (OSS default)
 *
 * VOICE_CONFIG_DEFAULTS (paid-tier defaults) is preserved for backward
 * compatibility. New code should use getVoiceConfigDefaults(tier) instead.
 *
 * `owner_mode` / `channels` are the discord-voice trust-boundary knobs
 * (issue #1016) — `owner_mode` is the skill-wide default and `channels[id]`
 * is a per-voice-channel override. They replaced the coarse global env flag
 * the skill previously used. Both default to a safe read-only posture.
 */

import { readFileSync, existsSync } from 'fs';

/** Per-channel override entry. Object-shaped so it stays extensible. */
export interface VoiceChannelConfig {
	owner_mode?: boolean;
}

export interface VoiceConfig {
	model: string;
	googleSearch: boolean;
	/** Skill-wide default for owner-mode. Safe default: false (read-only). */
	owner_mode: boolean;
	/** Per-channel overrides, keyed by voice channel id. */
	channels: Record<string, VoiceChannelConfig>;
}

/** Paid-tier defaults: best model + search on. Backward-compat alias kept for voice-config-switch.ts. */
export const VOICE_CONFIG_DEFAULTS: VoiceConfig = {
	model: 'gemini-2.5-flash-native-audio-preview-12-2025',
	googleSearch: true,
	owner_mode: false,
	channels: {},
};

/** Free-tier defaults: conservative model + search off (avoids 1011 entitlement errors on free keys). */
export const VOICE_CONFIG_DEFAULTS_FREE: VoiceConfig = {
	model: 'gemini-3.1-flash-live-preview',
	googleSearch: false,
	owner_mode: false,
	channels: {},
};

/** Return tier-appropriate defaults. Paid → 2.5+search:true. Free → 3.1+search:false. */
export function getVoiceConfigDefaults(tier: 'paid' | 'free'): VoiceConfig {
	return tier === 'paid' ? VOICE_CONFIG_DEFAULTS : VOICE_CONFIG_DEFAULTS_FREE;
}

/**
 * Resolve the effective owner-mode for a discord-voice channel — fail-closed.
 *
 * The config is raw JSON spread into `VoiceConfig`, so a hand-edited file can
 * carry a non-boolean value (string `"false"`, `null`, a number, a typo). A
 * loose `?? false` / truthy check would treat the *string* `"false"` as
 * truthy and grant owner tier to every speaker — a trust-boundary bug. Owner
 * mode is therefore granted ONLY when the value is the boolean literal `true`;
 * every other shape fails closed to `false`.
 *
 * Precedence (must NOT collapse to an OR of the two levels — that would break
 * a channel's explicit opt-out of a skill-wide default):
 *   1. If the channel entry exists AND carries an `owner_mode` key, that key
 *      decides — `=== true` grants, present-but-not-`true` (incl. `false`)
 *      denies. A channel-explicit `false` correctly overrides a skill default
 *      of `true`.
 *   2. Otherwise the skill-wide `config.owner_mode` decides (`=== true`).
 *   3. Otherwise `false`.
 */
export function resolveOwnerMode(
	config: VoiceConfig,
	channelId?: string,
): boolean {
	const channelEntry =
		channelId !== undefined ? config.channels?.[channelId] : undefined;
	if (
		channelEntry &&
		Object.prototype.hasOwnProperty.call(channelEntry, 'owner_mode')
	) {
		return channelEntry.owner_mode === true;
	}
	return config.owner_mode === true;
}

export function loadVoiceConfig(configPath: string, tier: 'paid' | 'free' = 'free'): VoiceConfig {
	const defaults = getVoiceConfigDefaults(tier);
	if (!existsSync(configPath)) return { ...defaults, channels: {} };
	try {
		const raw = JSON.parse(readFileSync(configPath, 'utf-8'));
		return {
			...defaults,
			...raw,
			// channels is a nested object — spread can't deep-merge, so take the
			// file's map verbatim when present, else fall back to the empty default.
			channels: raw.channels ?? {},
		};
	} catch (e) {
		console.warn(`[voice-config] failed to parse ${configPath}, using defaults: ${(e as Error).message}`);
		return { ...defaults, channels: {} };
	}
}
