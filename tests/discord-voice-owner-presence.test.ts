// E2E-of-the-logic test for owner-presence (piece ④): the bot leaves the voice
// channel only when an OWNER left AND no owner remains. Pure decision extracted
// from the voiceStateUpdate handler so it's testable without a live Discord
// connection.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { shouldLeaveOnOwnerExit } from '../skills/discord-voice/scripts/access-tier.js';

const OWNER_A = 'owner-A';
const OWNER_B = 'owner-B';        // e.g. a second person, OR a peer-bot whose id is in allowFrom
const STRANGER = 'stranger-9';
const owners = new Set([OWNER_A, OWNER_B]);

test('owner leaves and no owner remains → leave', () => {
	assert.equal(shouldLeaveOnOwnerExit(OWNER_A, [STRANGER], owners), true);
});
test('owner leaves but channel now empty → leave (alone = no owner)', () => {
	assert.equal(shouldLeaveOnOwnerExit(OWNER_A, [], owners), true);
});
test('owner leaves but another owner remains → stay', () => {
	// 2-person meeting: A leaves, B (also owner) stays → A-less bot must NOT leave.
	assert.equal(shouldLeaveOnOwnerExit(OWNER_A, [OWNER_B, STRANGER], owners), false);
});
test('a non-owner leaving is ignored → stay', () => {
	assert.equal(shouldLeaveOnOwnerExit(STRANGER, [OWNER_A], owners), false);
});
test('undefined leaver → stay (no-op)', () => {
	assert.equal(shouldLeaveOnOwnerExit(undefined, [OWNER_A], owners), false);
});
test('owner leaves, only non-owners remain → leave', () => {
	assert.equal(shouldLeaveOnOwnerExit(OWNER_A, [STRANGER, 'stranger-2'], owners), true);
});
test('empty owner set → never leave (gate disarmed by caller anyway)', () => {
	assert.equal(shouldLeaveOnOwnerExit(OWNER_A, [], new Set()), false);
});
