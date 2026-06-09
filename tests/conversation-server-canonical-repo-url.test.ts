/**
 * Structural contract tests for conversation-server.ts canonical repo URL fix (#1575).
 *
 * Before the fix, the phone-conversation session-instructions block ran:
 *   execSync('git remote get-url origin', { timeout: 2_000 })
 * This returns the caller's fork URL (e.g. https://github.com/john-the-dev/sutando),
 * not the public repo — so the phone agent would open the wrong GitHub link.
 * On a machine with no git remote it would throw and silently drop the hint entirely.
 *
 * After the fix, the URL is hardcoded to the canonical public repo with an env-override:
 *   process.env.SUTANDO_GH_REPO_URL || 'https://github.com/sonichi/sutando'
 *
 * These tests pin the fix structurally so a future refactor that reintroduces the
 * dynamic git-remote lookup fails here before it ships.
 */

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

const SRC = readFileSync(
	join(import.meta.dirname ?? '.', '..', 'skills/phone-conversation/scripts/conversation-server.ts'),
	'utf-8',
);

describe('conversation-server canonical repo URL contract', () => {
	it('conversation-server.ts source file is readable', () => {
		assert.ok(SRC.length > 0, 'conversation-server.ts must be non-empty');
	});

	it('does NOT use git remote get-url origin for the repo URL', () => {
		assert.ok(
			!SRC.includes("git remote get-url origin"),
			'conversation-server.ts must NOT call `git remote get-url origin` — ' +
				'this resolves to the user\'s private fork URL, not the canonical public repo. ' +
				'Use process.env.SUTANDO_GH_REPO_URL || the hardcoded canonical URL instead.',
		);
	});

	it('uses SUTANDO_GH_REPO_URL env-override with canonical fallback', () => {
		assert.ok(
			SRC.includes("process.env.SUTANDO_GH_REPO_URL || 'https://github.com/sonichi/sutando'"),
			"conversation-server.ts must use process.env.SUTANDO_GH_REPO_URL || 'https://github.com/sonichi/sutando' " +
				'as the repo URL — env-override for custom deployments, canonical public URL as the fallback.',
		);
	});

	it('Sutando GitHub repo hint still present in session instructions', () => {
		assert.ok(
			SRC.includes('Sutando GitHub repo:'),
			'The session-instructions block must still provide the "Sutando GitHub repo:" hint to the phone agent.',
		);
	});
});
