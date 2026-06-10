/**
 * Unified parsing for the result-body protocol markers used by every bridge
 * (discord, slack, telegram, voice/task-bridge). TypeScript parity with
 * src/result_markers.py (#873 / #1381).
 *
 * Marker spec (matches CLAUDE.md → "Result-body protocol markers"):
 *
 *   SKIP markers — at body start:
 *     [no-send]
 *     [REPLIED]
 *     [deduped: <task-id>]
 *   Bridge archives the task silently, delivers nothing to the user.
 *
 *   REDIRECT marker — first non-empty line:
 *     [channel: <channel-id>]
 *   Bridge delivers body to <channel-id> instead of the originating channel.
 *
 *   ATTACH markers — anywhere in the body:
 *     [file: /path]  [send: /path]  [attach: /path]
 *   Bridge extracts path, runs its own allowlist check, uploads the file.
 *   Marker is stripped from the delivered text body.
 *
 * Parse contract: skip is terminal. If text starts with a skip marker, only
 * the skip action is returned — no redirect or attach extraction.
 *
 * This module does NOT enforce path allowlists. File-marker extraction returns
 * paths; the caller's allowlist check must run at the upload sink.
 */

export type ActionKind = 'skip' | 'redirect' | 'attach';

export interface Action {
	kind: ActionKind;
	/** skip → "no-send"|"REPLIED"|"deduped"; redirect → channel-id; attach → file path */
	value: string;
	/** skip/deduped only: the referenced task-id */
	extra?: string;
}

export interface ParseResult {
	body: string;
	actions: Action[];
}

// Skip patterns anchored at body start (whitespace before is OK).
const _SKIP_PATTERNS: Array<[RegExp, string]> = [
	[/^\s*\[no-send\]\s*/i, 'no-send'],
	[/^\s*\[REPLIED\]\s*/, 'REPLIED'],
	[/^\s*\[deduped:\s*([^\]]+)\]\s*/i, 'deduped'],
];

// Redirect marker — anchored at body start after optional D7 header.
const _REDIRECT_RE = /^\s*\[channel:\s*([^\]]+)\]\s*\n?/;

// D7 reply-header — pool cores prepend `**[core: N]**` (+optional italic sub-line).
// Peeled off before marker scan so it doesn't shadow [channel:]; stitched back
// onto the returned body so the human reader still sees it.
const _D7_HEADER_RE = /^\*\*\[core:\s*[^\]]+\]\*\*\s*\n(?:_[^\n]*_\s*\n)?\s*/;

// Attach markers — file/send/attach are aliases.
// Only ever called via String.replace() which resets lastIndex automatically — safe as a module-level constant.
const _ATTACH_RE = /\[(?:file|send|attach):\s*([^\]]+)\]/g;

/**
 * Parse a result-body string and return body + action list.
 *
 * Evaluation order:
 *   1. SKIP — if any skip marker matches at body start, return immediately.
 *   2. REDIRECT — if body starts with [channel: <id>], strip and add action.
 *   3. ATTACH — scan remaining body for file markers, collect paths, strip.
 */
export function parseMarkers(text: string): ParseResult {
	if (!text) return { body: '', actions: [] };

	const actions: Action[] = [];

	// Peel off optional D7 header before marker scan.
	let d7Prefix = '';
	const d7Match = _D7_HEADER_RE.exec(text);
	let body = text;
	if (d7Match) {
		d7Prefix = d7Match[0];
		body = text.slice(d7Match[0].length);
	}

	// 1. SKIP — terminal if found.
	for (const [pat, reason] of _SKIP_PATTERNS) {
		const m = pat.exec(body);
		if (m) {
			const extra = reason === 'deduped' ? m[1].trim() : undefined;
			actions.push({ kind: 'skip', value: reason, ...(extra !== undefined && { extra }) });
			return { body: '', actions };
		}
	}

	// 2. REDIRECT — anchored at body start.
	const redirectMatch = _REDIRECT_RE.exec(body);
	if (redirectMatch) {
		const channel = redirectMatch[1].trim();
		actions.push({ kind: 'redirect', value: channel });
		body = body.slice(redirectMatch[0].length);
	}

	// Restore D7 header onto the body so it appears to the user.
	if (d7Prefix) body = d7Prefix + body;

	// 3. ATTACH — scan everywhere; collect in document order.
	const strippedPaths: string[] = [];
	body = body.replace(_ATTACH_RE, (_, path: string) => {
		strippedPaths.push(path.trim());
		return '';
	});
	for (const path of strippedPaths) {
		actions.push({ kind: 'attach', value: path });
	}

	return { body: body.trim(), actions };
}

/**
 * Return the first action of the given kind, or undefined.
 * Useful for "do I have a skip / redirect?" checks.
 * For attach actions, iterate actions directly to upload every file.
 */
export function firstAction(result: ParseResult, kind: ActionKind): Action | undefined {
	return result.actions.find(a => a.kind === kind);
}
