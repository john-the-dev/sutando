/**
 * Structural tests for chat-ui.ts scroll behaviour (#1439).
 *
 * The scroll logic lives inside a string literal (CHAT_HTML) rather than
 * exported functions, so these tests verify the rendered HTML/JS source
 * contains the structural patterns that fix the reported issue:
 *
 *   1. scrollToBottom uses double requestAnimationFrame (layout recalc).
 *   2. appendMessage only calls scrollToBottom when save === true (new msgs).
 *   3. renderHistory calls scrollToBottom/requestAnimationFrame after forEach.
 *   4. The pending indicator scroll uses the smooth argument.
 */

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { CHAT_HTML } from '../src/chat-ui.js';

/** Extract the body of a named JS function from a source string. */
function extractFnBody(src: string, fnName: string): string {
	const idx = src.indexOf(`function ${fnName}`);
	if (idx < 0) return '';
	let depth = 0, i = idx, started = false;
	for (; i < src.length; i++) {
		if (src[i] === '{') { depth++; started = true; }
		else if (src[i] === '}') { depth--; if (started && depth === 0) return src.slice(idx, i + 1); }
	}
	return '';
}

/** Count non-overlapping occurrences of substr in str. */
function countOccurrences(str: string, substr: string): number {
	let n = 0, pos = 0;
	while ((pos = str.indexOf(substr, pos)) >= 0) { n++; pos += substr.length; }
	return n;
}

describe('chat-ui scroll behaviour (structural)', () => {
	it('scrollToBottom function exists', () => {
		assert.ok(
			CHAT_HTML.includes('function scrollToBottom'),
			'scrollToBottom function must exist in CHAT_HTML',
		);
	});

	it('scrollToBottom uses double requestAnimationFrame (nested)', () => {
		const body = extractFnBody(CHAT_HTML, 'scrollToBottom');
		assert.ok(body.length > 0, 'could not extract scrollToBottom body');
		const rafCount = countOccurrences(body, 'requestAnimationFrame');
		assert.ok(
			rafCount >= 2,
			`scrollToBottom must use at least 2 requestAnimationFrame calls (got ${rafCount})`,
		);
	});

	it('scrollToBottom uses scrollTo with behavior parameter', () => {
		const body = extractFnBody(CHAT_HTML, 'scrollToBottom');
		assert.ok(body.length > 0, 'could not extract scrollToBottom body');
		assert.ok(
			body.includes('scrollTo(') || body.includes('.scrollTo('),
			'scrollToBottom must use scrollTo({ behavior: ... }) not bare scrollTop',
		);
		assert.ok(
			body.includes('behavior'),
			'scrollToBottom must pass behavior option to scrollTo',
		);
	});

	it('appendMessage only triggers scroll when save is true', () => {
		const body = extractFnBody(CHAT_HTML, 'appendMessage');
		assert.ok(body.length > 0, 'could not extract appendMessage body');

		const saveBlockIdx = body.indexOf('if (save)');
		assert.ok(saveBlockIdx >= 0, 'appendMessage must have an `if (save)` guard');

		const beforeSave = body.slice(0, saveBlockIdx);
		assert.ok(
			!beforeSave.includes('scrollToBottom'),
			'scrollToBottom must NOT appear before the `if (save)` block in appendMessage',
		);

		const afterSave = body.slice(saveBlockIdx);
		assert.ok(
			afterSave.includes('scrollToBottom'),
			'scrollToBottom must appear inside the `if (save)` block in appendMessage',
		);
	});

	it('renderHistory calls a scroll after the forEach loop', () => {
		const body = extractFnBody(CHAT_HTML, 'renderHistory');
		assert.ok(body.length > 0, 'could not extract renderHistory body');

		const forEachIdx = body.indexOf('forEach');
		assert.ok(forEachIdx >= 0, 'renderHistory must iterate with forEach');

		const afterForEach = body.slice(forEachIdx);
		const hasExplicitScroll =
			afterForEach.includes('requestAnimationFrame') ||
			afterForEach.includes('scrollTop') ||
			afterForEach.includes('scrollTo(');
		assert.ok(
			hasExplicitScroll,
			'renderHistory must perform an explicit scroll after the forEach loop',
		);
	});

	it('pending indicator scroll uses smooth=true argument', () => {
		const pendingIdx = CHAT_HTML.indexOf('pendingMsg');
		assert.ok(pendingIdx >= 0, 'pending indicator code must exist');
		// Check the region after pendingMsg creation for scrollToBottom(true)
		const region = CHAT_HTML.slice(pendingIdx, pendingIdx + 600);
		assert.ok(
			region.includes('scrollToBottom(true)'),
			'pending indicator must call scrollToBottom(true) for smooth scroll',
		);
	});
});
