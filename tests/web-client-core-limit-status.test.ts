import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const repoRoot = dirname(dirname(fileURLToPath(import.meta.url)));
const webClient = readFileSync(join(repoRoot, 'src', 'web-client.ts'), 'utf8');

test('web UI surfaces core session-limit state from agent-api', () => {
	assert.match(webClient, /data\.core_limit && data\.core_limit\.limited/);
	assert.match(webClient, /loopData\.core_limit && loopData\.core_limit\.limited/);
	assert.match(webClient, /Claude session limit reached/);
});
