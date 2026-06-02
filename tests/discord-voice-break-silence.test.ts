// Owner-only addressing (meeting-companion v1 boundary, Mini's design): the bot
// breaks its silence only when a turn is addressed to it by name AND the speaker
// is the owner — unless open-floor (v2) is enabled. Pure decision extracted from
// the discord-voice name-gate so it's testable without a live Gemini session.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { breakSilenceAllowed } from '../skills/discord-voice/scripts/access-tier.js';

// --- v1 default (open-floor OFF): owner-only addressing -----------------------
test('addressed by owner → break silence (speak)', () => {
	assert.equal(breakSilenceAllowed(true, 'owner', false), true);
});
test('addressed by team-tier (non-owner) → stay silent', () => {
	assert.equal(breakSilenceAllowed(true, 'team', false), false);
});
test('addressed by other-tier (non-owner) → stay silent', () => {
	assert.equal(breakSilenceAllowed(true, 'other', false), false);
});
test('NOT addressed, even by owner → stay silent', () => {
	assert.equal(breakSilenceAllowed(false, 'owner', false), false);
});
test('not addressed + non-owner → stay silent', () => {
	assert.equal(breakSilenceAllowed(false, 'other', false), false);
});

// --- v2 open-floor ON: any addressed turn breaks silence ---------------------
test('open-floor: addressed by non-owner → break silence', () => {
	assert.equal(breakSilenceAllowed(true, 'other', true), true);
});
test('open-floor: addressed by team → break silence', () => {
	assert.equal(breakSilenceAllowed(true, 'team', true), true);
});
test('open-floor: addressed by owner → break silence', () => {
	assert.equal(breakSilenceAllowed(true, 'owner', true), true);
});
test('open-floor does NOT override the addressing requirement — not addressed → silent', () => {
	assert.equal(breakSilenceAllowed(false, 'other', true), false);
	assert.equal(breakSilenceAllowed(false, 'owner', true), false);
});
