// Regression guard for #1585: model-fabricated user turns must not be able to
// fire tool calls. The fix is a provenance lease minted only from real inbound
// Discord audio (resampler.on('end')), checked in the tool execute wrapper, and
// cleared at turn.end so a fabricated second turn finds no lease.
//
// These are source-assertion tests — verifying the structural contract rather
// than exercising the live Discord session (which requires real API credentials).

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const SRC = readFileSync('skills/discord-voice/scripts/discord-voice-server.ts', 'utf-8');

describe('discord-voice action lease — provenance gate (#1585)', () => {
	it('DiscordVoiceSession interface declares actionLease field', () => {
		assert.ok(
			SRC.includes('actionLease: {'),
			'Expected actionLease field in DiscordVoiceSession interface',
		);
		assert.ok(
			SRC.includes('mintedAt: number'),
			'Expected mintedAt: number in lease type',
		);
		assert.ok(
			SRC.includes('ttlMs: number'),
			'Expected ttlMs: number in lease type',
		);
	});

	it('actionLease is initialized to null in session creation', () => {
		assert.ok(
			SRC.includes('actionLease: null'),
			'Expected actionLease: null in session initializer',
		);
	});

	it('lease is minted in resampler.on("end") — the real STT signal path', () => {
		// The mint must follow the utterancesSinceTurn increment (which is inside
		// resampler.on('end')) to confirm it is in the right handler.
		const utteranceIdx = SRC.indexOf('utterancesSinceTurn || 0) + 1');
		const mintIdx = SRC.indexOf('s.actionLease = {', utteranceIdx);
		assert.ok(
			utteranceIdx >= 0,
			'utterancesSinceTurn increment not found (resampler.on("end") landmark)',
		);
		assert.ok(
			mintIdx > utteranceIdx,
			'actionLease mint must appear after utterancesSinceTurn increment in resampler.on("end")',
		);
		// Confirm the mint structure: id, mintedAt, ttlMs.
		const mintBlock = SRC.slice(mintIdx, mintIdx + 200);
		assert.ok(mintBlock.includes('id:'), 'mint block must include id');
		assert.ok(mintBlock.includes('mintedAt: Date.now()'), 'mint block must set mintedAt to Date.now()');
		assert.ok(mintBlock.includes('ttlMs:'), 'mint block must include ttlMs');
	});

	it('lease is cleared in turn.end handler — fabricated subsequent turns find no lease', () => {
		// The clear must appear after turnSpeakers.clear() (the turn.end landmark).
		const speakersIdx = SRC.indexOf('s.turnSpeakers.clear()');
		const clearIdx = SRC.indexOf('s.actionLease = null', speakersIdx);
		assert.ok(
			speakersIdx >= 0,
			'turnSpeakers.clear() not found (turn.end landmark)',
		);
		assert.ok(
			clearIdx > speakersIdx,
			'actionLease = null must appear after turnSpeakers.clear() in turn.end handler',
		);
	});

	it('execute wrapper checks lease before calling inner — tool is blocked when no lease', () => {
		// The provenance check must appear after the tier check (toolAllowed) and
		// before the inner(args) call in the same wrapper.
		const tierCheckIdx = SRC.indexOf('const ok = toolAllowed(need, tier)');
		const leaseCheckIdx = SRC.indexOf('s.actionLease', tierCheckIdx);
		const innerCallIdx = SRC.indexOf('return inner(args)', leaseCheckIdx);
		assert.ok(tierCheckIdx >= 0, 'toolAllowed tier check not found in execute wrapper');
		assert.ok(
			leaseCheckIdx > tierCheckIdx,
			'lease check must appear after tier check in execute wrapper',
		);
		assert.ok(
			innerCallIdx > leaseCheckIdx,
			'inner(args) must appear after the lease check',
		);
	});

	it('lease check validates TTL — stale leases are rejected', () => {
		// The gate must compare Date.now() - lease.mintedAt against lease.ttlMs.
		assert.ok(
			SRC.includes('Date.now() - lease.mintedAt > lease.ttlMs'),
			'Expected TTL validation: Date.now() - lease.mintedAt > lease.ttlMs',
		);
	});

	it('denied tool call returns status:denied with a descriptive message', () => {
		assert.ok(
			SRC.includes("status: 'denied', message: 'Tool call blocked"),
			"Expected denied tool call to return status:'denied' with 'Tool call blocked' message",
		);
	});
});
