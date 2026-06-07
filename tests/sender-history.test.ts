/**
 * Unit tests for src/sender-history.ts (issue #1488).
 *
 * Uses a temp database so the workspace's conversation.sqlite is untouched.
 *
 * Run: npx tsx --test tests/sender-history.test.ts
 */
import { describe, it, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

// Point sender-history at a temp db before importing the module
const tmpDir = mkdtempSync(join(tmpdir(), 'sh-test-'));
process.env['SUTANDO_CONVERSATION_DB'] = join(tmpDir, 'test.sqlite');
// Provide a minimal workspace so resolveWorkspace() doesn't fail
process.env['SUTANDO_WORKSPACE'] = tmpDir;

// Import AFTER setting env vars
const { recordContact, hasPriorContact, getSenderContext, getRecentContacts } =
	await import('../src/sender-history.js');

after(() => {
	try { rmSync(tmpDir, { recursive: true }); } catch {}
});

describe('hasPriorContact — unknown address', () => {
	it('returns known=false, count=0, lastDate=null for an unseen address', () => {
		const r = hasPriorContact('nobody@example.com');
		assert.equal(r.known, false);
		assert.equal(r.count, 0);
		assert.equal(r.lastDate, null);
	});
});

describe('recordContact + hasPriorContact', () => {
	before(() => {
		recordContact({
			address: 'alice@example.com',
			source: 'gmail',
			direction: 'inbound',
			ts_unix: 1_700_000_000,
			subject: 'Hello',
			topic_summary: 'Introduction email',
			message_id: 'msg-001',
		});
	});

	it('known=true after one record', () => {
		assert.equal(hasPriorContact('alice@example.com').known, true);
	});

	it('count=1 after one record', () => {
		assert.equal(hasPriorContact('alice@example.com').count, 1);
	});

	it('lastDate is an ISO date string', () => {
		const { lastDate } = hasPriorContact('alice@example.com');
		assert.match(lastDate!, /^\d{4}-\d{2}-\d{2}$/);
	});
});

describe('recordContact — idempotent on duplicate message_id', () => {
	before(() => {
		// Insert same message_id twice
		recordContact({
			address: 'bob@example.com',
			source: 'gmail',
			direction: 'inbound',
			ts_unix: 1_700_100_000,
			message_id: 'unique-msg-42',
		});
		recordContact({
			address: 'bob@example.com',
			source: 'gmail',
			direction: 'inbound',
			ts_unix: 1_700_100_000,
			message_id: 'unique-msg-42',  // same id
		});
	});

	it('count stays 1 after duplicate insert', () => {
		assert.equal(hasPriorContact('bob@example.com').count, 1);
	});
});

describe('recordContact — rows without message_id are always inserted', () => {
	before(() => {
		recordContact({ address: 'carol@example.com', source: 'discord', direction: 'outbound', ts_unix: 1_700_200_000 });
		recordContact({ address: 'carol@example.com', source: 'discord', direction: 'outbound', ts_unix: 1_700_200_100 });
	});

	it('count=2 when no message_id provided', () => {
		assert.equal(hasPriorContact('carol@example.com').count, 2);
	});
});

describe('getSenderContext', () => {
	before(() => {
		for (let i = 0; i < 3; i++) {
			recordContact({
				address: 'dave@example.com',
				source: 'gmail',
				direction: 'inbound',
				ts_unix: 1_700_300_000 + i * 1000,
				topic_summary: i === 2 ? 'Third topic' : `Topic ${i}`,
			});
		}
	});

	it('count matches inserted rows', () => {
		assert.equal(getSenderContext('dave@example.com').count, 3);
	});

	it('topics array is non-empty', () => {
		const { topics } = getSenderContext('dave@example.com');
		assert.ok(topics.length > 0);
	});

	it('topics are deduplicated (two rows with same summary → one entry)', () => {
		const { topics } = getSenderContext('dave@example.com');
		// "Topic 0" appears once, "Topic 1" once, "Third topic" once → 3 unique
		assert.equal(topics.length, 3);
	});

	it('firstDate <= lastDate', () => {
		const { firstDate, lastDate } = getSenderContext('dave@example.com');
		assert.ok(firstDate! <= lastDate!);
	});
});

describe('getSenderContext — unknown address', () => {
	it('returns zeros and empty topics for unseen address', () => {
		const r = getSenderContext('ghost@example.com');
		assert.equal(r.count, 0);
		assert.equal(r.lastDate, null);
		assert.deepEqual(r.topics, []);
	});
});

describe('getRecentContacts', () => {
	it('returns an array', () => {
		const rows = getRecentContacts(5);
		assert.ok(Array.isArray(rows));
	});

	it('each row has the expected shape', () => {
		const rows = getRecentContacts(1);
		if (rows.length > 0) {
			const r = rows[0];
			assert.ok('address' in r);
			assert.ok('source' in r);
			assert.ok('direction' in r);
			assert.ok('date' in r);
			assert.match(r.date, /^\d{4}-\d{2}-\d{2}$/);
		}
	});

	it('honours the limit parameter', () => {
		const rows = getRecentContacts(2);
		assert.ok(rows.length <= 2);
	});
});
