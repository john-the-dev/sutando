/**
 * Sender-history storage — lightweight SQLite table tracking correspondent
 * history for inbox verification (issue #1488 / spec #1).
 *
 * Schema: sender_history table in conversation.sqlite (same db as
 * conversation-store.ts — one file, one WAL log, no extra path to configure).
 *
 * Answers two queries for the verification layer:
 *   hasPriorContact(address)   → { known: bool, count: number, lastDate: string|null }
 *   getSenderContext(address)  → { count, lastDate, topics: string[] }
 *
 * Ingestion is additive — call recordContact() whenever a message is sent
 * or received. Idempotent on repeated calls for the same exchange (keyed on
 * (source, message_id); if no message_id is available, the caller omits it
 * and a new row is always inserted).
 *
 * Best-effort throughout: sqlite errors never propagate, never block the caller.
 */
import { DatabaseSync } from 'node:sqlite';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { resolveWorkspace } from './workspace_default.js';

const DB_PATH = process.env.SUTANDO_CONVERSATION_DB
	|| join(resolveWorkspace(), 'data', 'conversation.sqlite');

export type ContactSource = 'gmail' | 'discord' | 'imessage' | 'sms' | 'slack' | 'telegram' | 'other';

export interface ContactRecord {
	address: string;          // normalized email / discord-id / phone / etc.
	source: ContactSource;
	direction: 'inbound' | 'outbound';
	ts_unix: number;          // epoch seconds (float)
	subject?: string;         // email subject or thread title (optional)
	topic_summary?: string;   // short LLM-generated summary (optional)
	message_id?: string;      // dedup key — source-specific (email Message-ID, etc.)
}

export interface PriorContactResult {
	known: boolean;
	count: number;
	lastDate: string | null;  // ISO date string or null
}

export interface SenderContextResult {
	count: number;
	lastDate: string | null;
	firstDate: string | null;
	topics: string[];         // up to 10 most recent topic_summaries, deduplicated
}

let _db: InstanceType<typeof DatabaseSync> | null = null;

function getDb(): InstanceType<typeof DatabaseSync> | null {
	if (_db) return _db;
	try {
		mkdirSync(join(resolveWorkspace(), 'data'), { recursive: true });
		const db = new DatabaseSync(DB_PATH);
		db.exec(`
			CREATE TABLE IF NOT EXISTS sender_history (
				id          INTEGER PRIMARY KEY,
				address     TEXT    NOT NULL,
				source      TEXT    NOT NULL,
				direction   TEXT    NOT NULL CHECK(direction IN ('inbound','outbound')),
				ts_unix     REAL    NOT NULL,
				subject     TEXT,
				topic_summary TEXT,
				message_id  TEXT
			);
			CREATE INDEX IF NOT EXISTS idx_sh_address     ON sender_history(address);
			CREATE INDEX IF NOT EXISTS idx_sh_address_ts  ON sender_history(address, ts_unix DESC);
			CREATE INDEX IF NOT EXISTS idx_sh_ts          ON sender_history(ts_unix DESC);
			CREATE UNIQUE INDEX IF NOT EXISTS idx_sh_msgid ON sender_history(source, message_id)
				WHERE message_id IS NOT NULL;
		`);
		_db = db;
		return db;
	} catch (e) {
		console.error('[sender-history] init failed:', e);
		return null;
	}
}

/**
 * Record a sent or received message for a given address.
 * If message_id is provided and already present, the row is silently skipped
 * (idempotent ingestion).
 */
export function recordContact(rec: ContactRecord): void {
	const db = getDb();
	if (!db) return;
	try {
		db.prepare(`
			INSERT OR IGNORE INTO sender_history
				(address, source, direction, ts_unix, subject, topic_summary, message_id)
			VALUES (?, ?, ?, ?, ?, ?, ?)
		`).run(
			rec.address,
			rec.source,
			rec.direction,
			rec.ts_unix,
			rec.subject ?? null,
			rec.topic_summary ?? null,
			rec.message_id ?? null,
		);
	} catch (e) {
		console.error('[sender-history] recordContact failed:', e);
	}
}

/**
 * Has the owner ever corresponded with this address?
 * Returns { known: false, count: 0, lastDate: null } on any error.
 */
export function hasPriorContact(address: string): PriorContactResult {
	const db = getDb();
	if (!db) return { known: false, count: 0, lastDate: null };
	try {
		const row = db.prepare(`
			SELECT COUNT(*) AS cnt,
			       MAX(ts_unix) AS last_ts
			FROM sender_history
			WHERE address = ?
		`).get(address) as { cnt: number; last_ts: number | null } | undefined;
		if (!row || row.cnt === 0) return { known: false, count: 0, lastDate: null };
		const lastDate = row.last_ts
			? new Date(row.last_ts * 1000).toISOString().slice(0, 10)
			: null;
		return { known: true, count: row.cnt, lastDate };
	} catch (e) {
		console.error('[sender-history] hasPriorContact failed:', e);
		return { known: false, count: 0, lastDate: null };
	}
}

/**
 * Return summary context for a sender — count, date range, and recent topics.
 */
export function getSenderContext(address: string): SenderContextResult {
	const db = getDb();
	if (!db) return { count: 0, lastDate: null, firstDate: null, topics: [] };
	try {
		const stats = db.prepare(`
			SELECT COUNT(*) AS cnt,
			       MAX(ts_unix) AS last_ts,
			       MIN(ts_unix) AS first_ts
			FROM sender_history
			WHERE address = ?
		`).get(address) as { cnt: number; last_ts: number | null; first_ts: number | null } | undefined;
		if (!stats || stats.cnt === 0) {
			return { count: 0, lastDate: null, firstDate: null, topics: [] };
		}
		const rows = db.prepare(`
			SELECT DISTINCT topic_summary
			FROM sender_history
			WHERE address = ? AND topic_summary IS NOT NULL
			ORDER BY ts_unix DESC
			LIMIT 10
		`).all(address) as Array<{ topic_summary: string }>;
		const topics = rows.map(r => r.topic_summary);
		const toDate = (ts: number | null) =>
			ts ? new Date(ts * 1000).toISOString().slice(0, 10) : null;
		return {
			count: stats.cnt,
			lastDate: toDate(stats.last_ts),
			firstDate: toDate(stats.first_ts),
			topics,
		};
	} catch (e) {
		console.error('[sender-history] getSenderContext failed:', e);
		return { count: 0, lastDate: null, firstDate: null, topics: [] };
	}
}

/**
 * Return the N most recent contacts across all senders (for backfill audit).
 */
export function getRecentContacts(limit = 20): Array<{
	address: string; source: string; direction: string; date: string; subject: string | null;
}> {
	const db = getDb();
	if (!db) return [];
	try {
		const rows = db.prepare(`
			SELECT address, source, direction, ts_unix, subject
			FROM sender_history
			ORDER BY ts_unix DESC
			LIMIT ?
		`).all(limit) as Array<{ address: string; source: string; direction: string; ts_unix: number; subject: string | null }>;
		return rows.map(r => ({
			address: r.address,
			source: r.source,
			direction: r.direction,
			date: new Date(r.ts_unix * 1000).toISOString().slice(0, 10),
			subject: r.subject,
		}));
	} catch (e) {
		console.error('[sender-history] getRecentContacts failed:', e);
		return [];
	}
}
