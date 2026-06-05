// E2E-of-the-logic tests for the discord-voice multi-bot name-gate (the
// speak-gate that made meeting-mode safe). Pure functions, no Discord/Gemini —
// these lock the wake/silence behaviour that the live test surfaced bugs in.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
	isAddressedBy,
	createGate,
	decideForTurn,
} from '../skills/discord-voice/scripts/name-gate.js';

// --- isAddressedBy: address vs mere-mention -------------------------------
test('isAddressedBy: greeting + name addresses', () => {
	assert.equal(isAddressedBy('hey BotA', ['BotA']), true);
	assert.equal(isAddressedBy('hi, BotA', ['BotA']), true);
	assert.equal(isAddressedBy('ok BotA', ['BotA']), true);
});
test('isAddressedBy: comma/question tag addresses', () => {
	assert.equal(isAddressedBy('BotA, what time is it', ['BotA']), true);
	assert.equal(isAddressedBy('BotA?', ['BotA']), true);
});
test('isAddressedBy: imperative verb at clause start addresses', () => {
	assert.equal(isAddressedBy('BotA check the PR', ['BotA']), true);
});
test('isAddressedBy: plain mention does NOT address', () => {
	assert.equal(isAddressedBy('thanks BotA', ['BotA']), false);
	assert.equal(isAddressedBy("BotA's answer was good", ['BotA']), false);
});
test('isAddressedBy: empty names / empty text never matches', () => {
	assert.equal(isAddressedBy('BotA, hi', []), false);
	assert.equal(isAddressedBy('', ['BotA']), false);
});

// --- decideForTurn: the wake/silence state machine ------------------------
const gate = () => createGate({ instanceName: 'BotA', otherInstances: ['BotB'] });

test('decideForTurn: addressed to me → allow (and sticks)', () => {
	const g = gate();
	assert.equal(decideForTurn(g, 'BotA, what time is it'), 'allow');
	// follow-up with no name carries the sticky allow
	assert.equal(decideForTurn(g, 'and the weather?'), 'allow');
});
test('decideForTurn: addressed to a peer → drop (and sticks)', () => {
	const g = gate();
	assert.equal(decideForTurn(g, 'BotB, hello'), 'drop');
	assert.equal(decideForTurn(g, 'how are you?'), 'drop'); // sticky drop
});
test('decideForTurn: my-name flips a sticky drop back to allow', () => {
	const g = gate();
	decideForTurn(g, 'BotB, you handle it');      // drop
	assert.equal(decideForTurn(g, 'actually BotA, you do it'), 'allow');
});
test('decideForTurn: no peer configured → always allow (single-bot)', () => {
	const g = createGate({ instanceName: 'BotA', otherInstances: [] });
	assert.equal(decideForTurn(g, 'anything at all'), 'allow');
	assert.equal(decideForTurn(g, 'BotB, hi'), 'allow'); // no peer set → gate off
});
test('decideForTurn: primary defaults to allow on a cold opener', () => {
	const g = createGate({ instanceName: 'BotA', otherInstances: ['BotB'], primary: true });
	assert.equal(decideForTurn(g, 'hello everyone'), 'allow'); // primary cold-open
});
test('decideForTurn: non-primary stays silent until named', () => {
	const g = createGate({ instanceName: 'BotA', otherInstances: ['BotB'], primary: false });
	assert.equal(decideForTurn(g, 'hello everyone'), 'drop'); // not addressed, not primary
});
