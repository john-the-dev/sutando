/**
 * Structural contract tests for get_voice_search_state inline tool.
 *
 * Issue #1008 sub-item A: voice agent should know its own googleSearch on/off
 * state and surface it. After context compaction, the system-prompt reminder
 * about Google Search may roll off; the tool provides a durable config-file
 * read that answers truthfully regardless of what the agent "remembers".
 *
 * These tests pin the contract so a refactor that removes the tool, renames
 * it, or breaks the return schema fails before it ships.
 */

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const SRC = readFileSync(join(__dirname, '../src/inline-tools.ts'), 'utf8');

describe('get_voice_search_state inline tool — source contract', () => {
	it('inline-tools.ts source is readable', () => {
		assert.ok(SRC.length > 0, 'inline-tools.ts must be non-empty');
	});

	it('getVoiceSearchStateTool is exported from inline-tools.ts', () => {
		assert.ok(
			SRC.includes('export const getVoiceSearchStateTool'),
			'getVoiceSearchStateTool must be exported from inline-tools.ts — tool was removed or renamed',
		);
	});

	it('tool is named get_voice_search_state', () => {
		assert.ok(
			SRC.includes("name: 'get_voice_search_state'"),
			"tool must be registered with name 'get_voice_search_state'",
		);
	});

	it('tool reads from voice-agent.json in the workspace config dir', () => {
		assert.ok(
			SRC.includes("'config', 'voice-agent.json'"),
			"tool must read from 'config/voice-agent.json' under resolveWorkspace() — wrong config path",
		);
	});

	it('tool returns google_search field', () => {
		assert.ok(
			SRC.includes('google_search: true') && SRC.includes('google_search: false'),
			'tool must return {google_search: true/false} — field missing or renamed',
		);
	});

	it('tool falls back to VOICE_CONFIG_DEFAULTS on missing config', () => {
		assert.ok(
			SRC.includes('VOICE_CONFIG_DEFAULTS'),
			'tool must fall back to VOICE_CONFIG_DEFAULTS when config file is missing',
		);
	});

	it('getVoiceSearchStateTool is included in inlineTools array', () => {
		assert.ok(
			SRC.includes('getVoiceSearchStateTool,') || SRC.includes('getVoiceSearchStateTool\n') || SRC.includes('getVoiceSearchStateTool '),
			'getVoiceSearchStateTool must appear in the inlineTools array',
		);
	});

	it('getVoiceSearchStateTool is included in anyCallerTools array', () => {
		const anyCaller = SRC.slice(SRC.indexOf('anyCallerTools'), SRC.indexOf('anyCallerTools') + 300);
		assert.ok(
			anyCaller.includes('getVoiceSearchStateTool'),
			'getVoiceSearchStateTool must be in anyCallerTools — non-owner callers need this for session-state queries',
		);
	});

	it('off-state description hints at switch_voice_config to enable search', () => {
		assert.ok(
			SRC.includes('switch to search mode') || SRC.includes('switch_voice_config'),
			'off-state result must hint how to enable Google Search (via switch_voice_config or "switch to search mode")',
		);
	});
});
