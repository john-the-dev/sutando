/**
 * Offline unit test for #1767's OAuth-refresh response handling.
 *
 * Validates the highest-risk logic — field-name handling + the access-token
 * guard in parseRefreshResponse — WITHOUT any network, keychain, or token
 * rotation. The live round-trip (real endpoint behavior) is validated
 * separately on a proxy-routed node; this catches the parse/guard bugs that
 * would otherwise only surface in production.
 */
import { test } from 'node:test';
import assert from 'node:assert';
import {
	firstUsableCandidate,
	keychainServiceCandidates,
	nextRefreshBackoffMs,
	parseRefreshResponse,
	redactForLog,
	scopedKeychainService,
	shouldAttemptRefresh,
	tokenHardExpired,
} from '../skills/quota-tracker/scripts/credential-proxy.ts';

const base = { accessToken: 'old-access', refreshToken: 'old-refresh', expiresAt: 1000 };
const A = 'a'.repeat(40); // a plausible (>=20 char) access token

test('snake_case response → fresh cred, expiresAt from expires_in', () => {
	const r = parseRefreshResponse(200, JSON.stringify({ access_token: A, refresh_token: 'new-refresh', expires_in: 3600 }), base, 0);
	assert.equal(r?.accessToken, A);
	assert.equal(r?.refreshToken, 'new-refresh');
	assert.equal(r?.expiresAt, 3600 * 1000);
});

test('camelCase response is tolerated', () => {
	const r = parseRefreshResponse(200, JSON.stringify({ accessToken: A, refreshToken: 'r2', expiresAt: 9999 }), base, 0);
	assert.equal(r?.accessToken, A);
	assert.equal(r?.refreshToken, 'r2');
	assert.equal(r?.expiresAt, 9999);
});

test('no rotation in response → keeps the existing refresh token', () => {
	const r = parseRefreshResponse(200, JSON.stringify({ access_token: A, expires_in: 60 }), base, 0);
	assert.equal(r?.refreshToken, 'old-refresh');
});

test('HTTP >=400 → null (no write)', () => {
	assert.equal(parseRefreshResponse(401, JSON.stringify({ access_token: A }), base), null);
	assert.equal(parseRefreshResponse(500, JSON.stringify({ access_token: A }), base), null);
});

test('garbage / non-JSON body → null', () => {
	assert.equal(parseRefreshResponse(200, 'not-json', base), null);
	assert.equal(parseRefreshResponse(200, '', base), null);
});

test('missing access token → null', () => {
	assert.equal(parseRefreshResponse(200, JSON.stringify({ refresh_token: 'x' }), base), null);
});

test('short access token (<20 chars) → null (the anti-garbage guard)', () => {
	assert.equal(parseRefreshResponse(200, JSON.stringify({ access_token: 'short' }), base), null);
});

test('non-string access token → null', () => {
	assert.equal(parseRefreshResponse(200, JSON.stringify({ access_token: 12345 }), base), null);
});

test('preserves unrelated existing fields (scopes etc.)', () => {
	const withScopes = { ...base, scopes: ['a', 'b'], subscriptionType: 'max' };
	const r = parseRefreshResponse(200, JSON.stringify({ access_token: A, expires_in: 10 }), withScopes, 0);
	assert.deepEqual(r?.scopes, ['a', 'b']);
	assert.equal(r?.subscriptionType, 'max');
});

test('CLAUDE_CONFIG_DIR maps to Claude Code scoped keychain service', () => {
	const ccd = '/Users/ruiwang/.sutando/repo/workspace/.claude-sutando';
	assert.equal(scopedKeychainService(ccd), 'Claude Code-credentials-c365fc51');
	assert.deepEqual(keychainServiceCandidates(ccd), [
		'Claude Code-credentials-c365fc51',
		'Claude Code-credentials',
	]);
});

test('keychain candidates fall back to the default service when no config dir is set', () => {
	assert.equal(scopedKeychainService(''), null);
	assert.deepEqual(keychainServiceCandidates(''), ['Claude Code-credentials']);
});

// --- Refresh-failure backoff (regression for the tight retry loop) ---------
// Before this guard, a refresh that returned HTTP 400 (revoked/rotated refresh
// token) was re-attempted on EVERY subsequent request because `needsRefresh`
// stays true while the token is near expiry — hammering the OAuth endpoint.
// nextRefreshBackoffMs + shouldAttemptRefresh cap that to one attempt per
// (growing) backoff window. Defaults: 30s base, 15min cap.

test('no failures → attempt immediately (zero backoff)', () => {
	assert.equal(nextRefreshBackoffMs(0), 0);
	assert.equal(nextRefreshBackoffMs(-1), 0);
});

test('backoff grows exponentially from the 30s base', () => {
	assert.equal(nextRefreshBackoffMs(1), 30_000);
	assert.equal(nextRefreshBackoffMs(2), 60_000);
	assert.equal(nextRefreshBackoffMs(3), 120_000);
	assert.equal(nextRefreshBackoffMs(4), 240_000);
});

test('backoff is capped at the 15min max and never exceeds it', () => {
	assert.equal(nextRefreshBackoffMs(6), 15 * 60 * 1000);
	assert.equal(nextRefreshBackoffMs(100), 15 * 60 * 1000);
	// monotonic non-decreasing, always positive after a failure, always <= cap
	let prev = 0;
	for (let n = 1; n <= 50; n++) {
		const b = nextRefreshBackoffMs(n);
		assert.ok(b > 0, `failure ${n} must impose a positive cooldown`);
		assert.ok(b >= prev, `backoff must not shrink at failure ${n}`);
		assert.ok(b <= 15 * 60 * 1000, `backoff must stay within cap at failure ${n}`);
		prev = b;
	}
});

test('shouldAttemptRefresh: skip entirely when the token does not need refresh', () => {
	assert.equal(shouldAttemptRefresh(false, 1_000_000, 0), false);
});

test('shouldAttemptRefresh: attempt when needed and no active cooldown', () => {
	assert.equal(shouldAttemptRefresh(true, 1_000_000, 0), true);
	assert.equal(shouldAttemptRefresh(true, 1_000_000, 1_000_000), true); // exactly at the boundary
});

test('shouldAttemptRefresh: suppress the retry storm while inside the cooldown window', () => {
	const now = 1_000_000;
	const nextAllowed = now + nextRefreshBackoffMs(1); // one failure → 30s cooldown
	// Every request that lands during the window is refused a fresh attempt...
	assert.equal(shouldAttemptRefresh(true, now, nextAllowed), false);
	assert.equal(shouldAttemptRefresh(true, now + 1, nextAllowed), false);
	assert.equal(shouldAttemptRefresh(true, now + 29_999, nextAllowed), false);
	// ...until the window elapses.
	assert.equal(shouldAttemptRefresh(true, nextAllowed, nextAllowed), true);
});

test('redactForLog: surfaces the real token-endpoint error (invalid_grant)', () => {
	const out = redactForLog(JSON.stringify({ error: 'invalid_grant', error_description: 'refresh token expired' }));
	assert.ok(out.includes('invalid_grant'), out);
	assert.ok(out.includes('refresh token expired'), out);
});

test('redactForLog: masks any long token-like string (never log a secret)', () => {
	const jwt = 'eyJhbGciOiJIUzI1NiJ9.' + 'a'.repeat(60) + '.' + 'b'.repeat(40);
	const out = redactForLog(`{"error":"x","access_token":"${jwt}"}`);
	assert.ok(!out.includes(jwt), out);
	assert.ok(out.includes('[redacted]'), out);
	assert.ok(out.includes('error'), out); // non-secret error field still visible
});

test('redactForLog: empty/whitespace body → explicit marker (not blank)', () => {
	assert.equal(redactForLog(''), '(empty body)');
	assert.equal(redactForLog('   '), '(empty body)');
});

test('redactForLog: truncates an over-long body', () => {
	// Many short words (each < 20 chars, so none get redacted away) → the body
	// stays long and must be truncated to the cap.
	const longBody = ('err bad grant no ').repeat(40); // ~680 chars, all short tokens
	const out = redactForLog(longBody, 300);
	assert.ok(out.length <= 300 + '…(truncated)'.length, String(out.length));
	assert.ok(out.endsWith('…(truncated)'));
});

// --- doomed-token fallback (candidate selection when a scoped token is
// expired-and-unrefreshable, e.g. the recurring 401 loop cured manually by
// deleting the scoped keychain entry) ---

test('tokenHardExpired: at/before now → true; future or missing expiresAt → false', () => {
	assert.equal(tokenHardExpired({ accessToken: A, expiresAt: 100 }, 100), true);  // == now
	assert.equal(tokenHardExpired({ accessToken: A, expiresAt: 99 }, 100), true);   // before now
	assert.equal(tokenHardExpired({ accessToken: A, expiresAt: 101 }, 100), false); // future
	assert.equal(tokenHardExpired({ accessToken: A }, 100), false);                 // unknown → usable
});

const scopedSvc = 'Claude Code-credentials-c365fc51';
const defaultSvc = 'Claude Code-credentials';
const cand = (service: string, expiresAt?: number) => ({ service, oauth: { accessToken: A, refreshToken: 'r', expiresAt } });

test('firstUsableCandidate: expired scoped + valid default → picks default (the auto delete-scoped workaround)', () => {
	const picked = firstUsableCandidate([cand(scopedSvc, 50), cand(defaultSvc, 999)], 100);
	assert.equal(picked?.service, defaultSvc);
});

test('firstUsableCandidate: healthy scoped is preferred (first non-expired wins)', () => {
	const picked = firstUsableCandidate([cand(scopedSvc, 999), cand(defaultSvc, 999)], 100);
	assert.equal(picked?.service, scopedSvc);
});

test('firstUsableCandidate: excludeService skips the just-failed scoped item', () => {
	const picked = firstUsableCandidate([cand(scopedSvc, 999), cand(defaultSvc, 999)], 100, scopedSvc);
	assert.equal(picked?.service, defaultSvc);
});

test('firstUsableCandidate: all hard-expired → null (caller keeps existing behavior)', () => {
	assert.equal(firstUsableCandidate([cand(scopedSvc, 10), cand(defaultSvc, 20)], 100), null);
	assert.equal(firstUsableCandidate([], 100), null);
});

// ── CR #2106: the doomed-token fallback must hold when refresh is SKIPPED ──
// (failure-backoff cooldown / no refreshToken), not only after a failed attempt.

import { serveWithoutRefresh } from '../skills/quota-tracker/scripts/credential-proxy.ts';

const NOW = 1_000_000;
const scopedDoomed = {
	service: 'Claude Code-credentials-scoped01',
	oauth: { accessToken: 'doomed-scoped-token-aaaaaaaaaa', refreshToken: 'dead', expiresAt: NOW - 60_000 },
};
const defaultValid = {
	service: 'Claude Code-credentials',
	oauth: { accessToken: 'valid-default-token-bbbbbbbbbb', expiresAt: NOW + 3_600_000 },
};

test('cooldown skips the refresh attempt (shouldAttemptRefresh false during backoff)', () => {
	assert.equal(shouldAttemptRefresh(true, NOW, NOW + 60_000), false);
});

test('cooldown path: hard-expired scoped token falls back to the valid sibling', () => {
	// This is the exact branch a cooldown-skipped call takes in getFreshOAuthToken.
	const served = serveWithoutRefresh(scopedDoomed, [scopedDoomed, defaultValid], NOW);
	assert.equal(served.fellBack, true);
	assert.equal(served.via, 'Claude Code-credentials');
	assert.equal(served.token, 'valid-default-token-bbbbbbbbbb');
});

test('cooldown path: still-valid token is served as-is (no spurious fallback)', () => {
	const scopedValid = { ...scopedDoomed, oauth: { ...scopedDoomed.oauth, expiresAt: NOW + 120_000 } };
	const served = serveWithoutRefresh(scopedValid, [scopedValid, defaultValid], NOW);
	assert.equal(served.fellBack, false);
	assert.equal(served.token, 'doomed-scoped-token-aaaaaaaaaa');
});

test('cooldown path: no usable sibling → original token served (fail-safe, never null)', () => {
	const served = serveWithoutRefresh(scopedDoomed, [scopedDoomed], NOW);
	assert.equal(served.fellBack, false);
	assert.equal(served.token, 'doomed-scoped-token-aaaaaaaaaa');
});
