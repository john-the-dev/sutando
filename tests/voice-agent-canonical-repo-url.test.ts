/**
 * Structural contract tests for voice-agent.ts canonical repo URL fix.
 *
 * Before the fix, voice-agent's session-instructions block called:
 *   execFileSync('git', ['remote', 'get-url', 'origin'], ...)
 * This returns the user's fork URL (e.g. https://github.com/john-the-dev/sutando),
 * not the canonical public repo — so the voice agent would tell callers the wrong
 * GitHub link. On a machine with no git remote it would throw and silently omit
 * the hint.
 *
 * After the fix the URL is hardcoded to the canonical public repo with an env-override
 * (matching the pattern used in discord-voice-server.ts #1573 and conversation-server.ts #1575):
 *   process.env.SUTANDO_GH_REPO_URL || 'https://github.com/sonichi/sutando'
 *
 * These tests pin the fix so a refactor that reintroduces the dynamic git-remote
 * lookup fails before it ships.
 */

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const SRC = readFileSync(join(__dirname, '../src/voice-agent.ts'), 'utf8');

describe('voice-agent canonical repo URL contract', () => {
	it('voice-agent.ts source file is readable', () => {
		assert.ok(SRC.length > 0, 'voice-agent.ts must be non-empty');
	});

	it('does NOT use git remote get-url origin for the repo URL hint', () => {
		assert.ok(
			!SRC.includes("execFileSync('git', ['remote', 'get-url', 'origin']") &&
				!SRC.includes('execFileSync("git", ["remote", "get-url", "origin"]') &&
				!SRC.includes("execSync('git remote get-url origin'") &&
				!SRC.includes('execSync("git remote get-url origin"'),
			'voice-agent.ts must NOT call git remote get-url origin for the session-instructions ' +
				'repo URL — this resolves to the user\'s private fork, not the canonical public repo. ' +
				'Use process.env.SUTANDO_GH_REPO_URL || the hardcoded canonical URL instead.',
		);
	});

	it('uses SUTANDO_GH_REPO_URL env-override with canonical fallback', () => {
		assert.ok(
			SRC.includes("process.env.SUTANDO_GH_REPO_URL || 'https://github.com/sonichi/sutando'"),
			"voice-agent.ts must use process.env.SUTANDO_GH_REPO_URL || 'https://github.com/sonichi/sutando' " +
				'as the repo URL — env-override for custom deployments, canonical public URL as the fallback.',
		);
	});

	it('Sutando GitHub repo hint still present in session instructions', () => {
		assert.ok(
			SRC.includes('The Sutando GitHub repo is'),
			'The session-instructions block must still provide the "The Sutando GitHub repo is" hint to the voice agent.',
		);
	});
});
