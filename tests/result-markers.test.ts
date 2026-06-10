/**
 * Parity tests for src/result_markers.ts — TypeScript equivalent of
 * src/result_markers.py. Mirrors the Python test suite case-for-case so
 * cross-language drift is caught by running both.
 *
 * Run: tsx --test tests/result-markers.test.ts
 */
import { test, describe } from 'node:test';
import assert from 'node:assert/strict';
import { parseMarkers, firstAction } from '../src/result_markers.js';

describe('skip markers', () => {
	test('[no-send] at start', () => {
		const r = parseMarkers('[no-send]\nthis is ignored');
		assert.equal(r.body, '');
		assert.equal(r.actions.length, 1);
		assert.equal(r.actions[0].kind, 'skip');
		assert.equal(r.actions[0].value, 'no-send');
	});

	test('[REPLIED] at start', () => {
		const r = parseMarkers('[REPLIED]\nalready handled');
		assert.equal(r.body, '');
		assert.equal(r.actions[0].value, 'REPLIED');
	});

	test('[deduped] captures task-id in extra', () => {
		const r = parseMarkers('[deduped: task-1779164273868]\nfull reply elsewhere');
		assert.equal(r.body, '');
		assert.equal(r.actions[0].value, 'deduped');
		assert.equal(r.actions[0].extra, 'task-1779164273868');
	});

	test('skip strips leading whitespace', () => {
		const r = parseMarkers('  [no-send]\nbody');
		assert.equal(r.actions[0].kind, 'skip');
	});

	test('skip is case-insensitive for no-send and deduped', () => {
		const r1 = parseMarkers('[NO-SEND]\nx');
		const r2 = parseMarkers('[DEDUPED: task-1]\nx');
		assert.equal(r1.actions[0].value, 'no-send');
		assert.equal(r2.actions[0].value, 'deduped');
	});

	test('[REPLIED] is case-sensitive — lowercase does not match', () => {
		const r = parseMarkers('[replied]\nbody');
		assert.equal(r.actions.length, 0);
		assert.ok(r.body.includes('[replied]'));
	});
});

describe('redirect marker', () => {
	test('[channel:] at start strips marker', () => {
		const r = parseMarkers('[channel: C09TEUW5DE1]\nhello team');
		assert.equal(r.body, 'hello team');
		assert.equal(r.actions[0].kind, 'redirect');
		assert.equal(r.actions[0].value, 'C09TEUW5DE1');
	});

	test('[channel:] with Discord numeric id', () => {
		const r = parseMarkers('[channel: 1499520683267592432]\nRFC announcement');
		assert.equal(r.actions[0].value, '1499520683267592432');
		assert.equal(r.body, 'RFC announcement');
	});

	test('[channel:] only matches at start — inline does not redirect', () => {
		const r = parseMarkers('body talking about [channel: 12345] inline');
		assert.equal(firstAction(r, 'redirect'), undefined);
		assert.ok(r.body.includes('[channel: 12345]'));
	});
});

describe('attach markers', () => {
	test('[file:] extracts path and strips marker', () => {
		const r = parseMarkers('here it is [file: /tmp/sutando-a.png]');
		assert.equal(r.actions[0].kind, 'attach');
		assert.equal(r.actions[0].value, '/tmp/sutando-a.png');
		assert.equal(r.body, 'here it is');
	});

	test('[send:] is an alias for file', () => {
		const r = parseMarkers('[send: /docs/x.pdf] check this');
		assert.equal(r.actions[0].value, '/docs/x.pdf');
		assert.equal(r.body, 'check this');
	});

	test('[attach:] is an alias for file', () => {
		const r = parseMarkers('done [attach: /notes/y.md]');
		assert.equal(r.actions[0].value, '/notes/y.md');
	});

	test('multiple attaches collected in document order', () => {
		const r = parseMarkers('a [file: /a] b [send: /b] c [attach: /c] d');
		const paths = r.actions.filter(a => a.kind === 'attach').map(a => a.value);
		assert.deepEqual(paths, ['/a', '/b', '/c']);
	});

	test('attach markers stripped from body', () => {
		const r = parseMarkers('here is [file: /a]');
		assert.ok(!r.body.includes('[file:'));
		assert.ok(!r.body.includes('/a]'));
	});
});

describe('precedence', () => {
	test('skip beats redirect — no redirect parsed', () => {
		const r = parseMarkers('[no-send]\n[channel: C123]\nbody');
		assert.equal(r.actions.length, 1);
		assert.equal(r.actions[0].kind, 'skip');
	});

	test('skip beats attach', () => {
		const r = parseMarkers('[deduped: task-1]\n[file: /x]');
		assert.equal(r.actions.length, 1);
		assert.equal(r.actions[0].kind, 'skip');
	});

	test('redirect and attach coexist', () => {
		const r = parseMarkers('[channel: C123]\nbody [file: /tmp/sutando-x.png]');
		const kinds = r.actions.map(a => a.kind);
		assert.deepEqual(kinds, ['redirect', 'attach']);
		assert.equal(r.body, 'body');
	});
});

describe('edge cases', () => {
	test('empty body', () => {
		const r = parseMarkers('');
		assert.equal(r.body, '');
		assert.deepEqual(r.actions, []);
	});

	test('plain text with no markers passes through unchanged', () => {
		const r = parseMarkers('just a normal reply');
		assert.equal(r.body, 'just a normal reply');
		assert.deepEqual(r.actions, []);
	});

	test('malformed skip (missing closing bracket) is not parsed', () => {
		const r = parseMarkers('[no-send\nbody');
		assert.deepEqual(r.actions, []);
		assert.ok(r.body.includes('[no-send'));
	});

	test('firstAction helper returns first match', () => {
		const r = parseMarkers('[channel: C1]\n[file: /a] [file: /b]');
		assert.equal(firstAction(r, 'redirect')?.value, 'C1');
		assert.equal(firstAction(r, 'attach')?.value, '/a');
		assert.equal(firstAction(r, 'skip'), undefined);
	});
});

describe('no-leak invariant', () => {
	test('no attach marker leaks into body', () => {
		const r = parseMarkers('body [file: /a] [send: /b] [attach: /c] end');
		for (const marker of ['[file:', '[send:', '[attach:']) {
			assert.ok(!r.body.includes(marker), `marker "${marker}" leaked into body`);
		}
	});

	test('[channel:] at start does not leak into body', () => {
		const r = parseMarkers('[channel: C1]\nhello');
		assert.ok(!r.body.includes('[channel:'));
	});

	test('skip strips entire body', () => {
		for (const prefix of ['[no-send]', '[REPLIED]', '[deduped: task-x]']) {
			const r = parseMarkers(`${prefix}\nthis is internal`);
			assert.equal(r.body, '', `body not empty for prefix "${prefix}"`);
		}
	});
});

describe('D7 header tolerance', () => {
	test('D7 header does not shadow [channel:] redirect', () => {
		const text = '**[core: 2]**\n\n[channel: C09XYZ]\nHello redirected.';
		const r = parseMarkers(text);
		assert.equal(firstAction(r, 'redirect')?.value, 'C09XYZ');
		assert.ok(r.body.startsWith('**[core: 2]**'));
		assert.ok(!r.body.includes('[channel:'));
	});

	test('D7 header with italic sub-line', () => {
		const text = [
			'**[core: 2]**',
			'_(channel→core handler switch from core-1)_',
			'',
			'[channel: C09XYZ]',
			'Body.',
		].join('\n');
		const r = parseMarkers(text);
		assert.equal(firstAction(r, 'redirect')?.value, 'C09XYZ');
		assert.ok(r.body.includes('**[core: 2]**'));
		assert.ok(r.body.includes('_(channel→core handler switch from core-1)_'));
	});

	test('D7 header without markers passes through unchanged', () => {
		const text = '**[core: 2]**\n\nJust a normal reply, no markers.';
		const r = parseMarkers(text);
		assert.deepEqual(r.actions, []);
		assert.equal(r.body, text);
	});

	test('D7 + skip: skip is terminal, body is empty', () => {
		const text = '**[core: 2]**\n\n[no-send]\nthis-is-internal';
		const r = parseMarkers(text);
		assert.equal(firstAction(r, 'skip')?.value, 'no-send');
		assert.equal(r.body, '');
	});

	test('D7 + deduped: skip is terminal with extra task-id', () => {
		const text = '**[core: 2]**\n[deduped: task-1779164273868]\nfull elsewhere';
		const r = parseMarkers(text);
		const skip = firstAction(r, 'skip');
		assert.equal(skip?.value, 'deduped');
		assert.equal(skip?.extra, 'task-1779164273868');
		assert.equal(r.body, '');
	});

	test('D7 + redirect + attach: all markers handled, header preserved', () => {
		const text = [
			'**[core: 2]**',
			'',
			'[channel: C09XYZ]',
			'[file: /tmp/x.txt]',
			'Body with attachment.',
		].join('\n');
		const r = parseMarkers(text);
		const kinds = r.actions.map(a => a.kind);
		assert.deepEqual(kinds, ['redirect', 'attach']);
		assert.equal(firstAction(r, 'attach')?.value, '/tmp/x.txt');
		assert.ok(!r.body.includes('[channel:'));
		assert.ok(!r.body.includes('[file:'));
		assert.ok(r.body.includes('**[core: 2]**'));
	});
});
