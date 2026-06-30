import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { CHAT_HTML } from '../src/chat-ui.js';

const repoRoot = dirname(dirname(fileURLToPath(import.meta.url)));
const webClient = readFileSync(join(repoRoot, 'src', 'web-client.ts'), 'utf8');

test('dashboard text sends persist and resume pending task results', () => {
	assert.match(webClient, /PERSIST_KEY_CHAT_PENDING = 'sutando-dashboard-chat-pending-v1'/);
	assert.match(webClient, /function addPendingChatSend\(taskId, text\)/);
	assert.match(webClient, /addPendingChatSend\(d\.task_id, text\)/);
	assert.match(webClient, /function resumePendingChatSends\(\)/);
	assert.match(webClient, /pollChatReply\(d\.task_id, placeholder\)/);

	const keyIndex = webClient.indexOf("PERSIST_KEY_CHAT_PENDING = 'sutando-dashboard-chat-pending-v1'");
	const resumeIndex = webClient.lastIndexOf('resumePendingChatSends();');
	assert.ok(keyIndex > 0, 'pending localStorage key should exist');
	assert.ok(resumeIndex > keyIndex, 'resume must run after the pending key is initialized');
});

test('/chat sends persist and resume pending task results', () => {
	assert.match(CHAT_HTML, /PENDING_KEY = 'sutando-chat-page-pending-v1'/);
	assert.match(CHAT_HTML, /function rememberPendingTask\(taskId, text\)/);
	assert.match(CHAT_HTML, /rememberPendingTask\(taskId, text\)/);
	assert.match(CHAT_HTML, /function resumePendingTasks\(\)/);
	assert.match(CHAT_HTML, /resumePendingTasks\(\);/);
	assert.match(CHAT_HTML, /pollPendingTask\(taskId, pendingMsg\)/);
});
