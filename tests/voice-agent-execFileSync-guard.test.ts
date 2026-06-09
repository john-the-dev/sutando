import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

// Security regression guard — execSync → execFileSync for voice-agent.ts
// and voice-mode-resolver.ts (shipped in PRs #1557 + #1558, closes #1451 series).
//
// execSync spawns a shell (/bin/sh -c) and passes the argument string verbatim.
// Any dynamic value interpolated into that string is a potential shell-injection
// surface. execFileSync bypasses the shell entirely — arguments are passed as an
// array, never interpreted by /bin/sh. This removes the injection class by
// construction.
//
// These guards pin the fix: if a future refactor accidentally re-imports
// execSync or reverts a call site, CI fails here before the change ships.

const ROOT = join(import.meta.dirname ?? '.', '..');

const VOICE_AGENT = readFileSync(join(ROOT, 'src/voice-agent.ts'), 'utf-8');
const VOICE_MODE = readFileSync(join(ROOT, 'src/voice-mode-resolver.ts'), 'utf-8');

describe('voice-agent.ts — execSync → execFileSync guard (#1451, PR #1558)', () => {
	it('does not import execSync from node:child_process', () => {
		assert.doesNotMatch(
			VOICE_AGENT,
			/import\s*\{[^}]*\bexecSync\b[^}]*\}\s*from\s*['"]node:child_process['"]/,
			'voice-agent.ts imports execSync — should only import execFileSync. ' +
				'execSync passes strings through /bin/sh; execFileSync skips the shell.',
		);
	});

	it('has no execSync( call sites', () => {
		// Allow `execFileSync(` — only reject bare `execSync(`
		const stripped = VOICE_AGENT.replace(/execFileSync/g, '');
		assert.doesNotMatch(
			stripped,
			/execSync\s*\(/,
			'voice-agent.ts calls execSync() — replace with execFileSync(cmd, args[]) ' +
				'to eliminate the shell-injection surface.',
		);
	});

	it('uses execFileSync for child_process calls', () => {
		assert.match(
			VOICE_AGENT,
			/execFileSync/,
			'voice-agent.ts has no execFileSync calls — expected at least one for osascript/pgrep.',
		);
	});
});

describe('voice-mode-resolver.ts — execSync → execFileSync guard (#1451, PR #1557)', () => {
	it('does not import execSync from node:child_process', () => {
		assert.doesNotMatch(
			VOICE_MODE,
			/import\s*\{[^}]*\bexecSync\b[^}]*\}\s*from\s*['"]node:child_process['"]/,
			'voice-mode-resolver.ts imports execSync — should only import execFileSync.',
		);
	});

	it('has no execSync( call sites', () => {
		const stripped = VOICE_MODE.replace(/execFileSync/g, '');
		assert.doesNotMatch(
			stripped,
			/execSync\s*\(/,
			'voice-mode-resolver.ts calls execSync() — replace with execFileSync(cmd, args[]).',
		);
	});

	it('uses execFileSync for child_process calls', () => {
		assert.match(
			VOICE_MODE,
			/execFileSync/,
			'voice-mode-resolver.ts has no execFileSync calls — expected at least one for curl.',
		);
	});
});
