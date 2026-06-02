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
	assert.equal(isAddressedBy('hey Lucy', ['Lucy']), true);
	assert.equal(isAddressedBy('hi, Lucy', ['Lucy']), true);
	assert.equal(isAddressedBy('ok Lucy', ['Lucy']), true);
});
test('isAddressedBy: comma/question tag addresses', () => {
	assert.equal(isAddressedBy('Lucy, what time is it', ['Lucy']), true);
	assert.equal(isAddressedBy('Lucy?', ['Lucy']), true);
});
test('isAddressedBy: imperative verb at clause start addresses', () => {
	assert.equal(isAddressedBy('Lucy check the PR', ['Lucy']), true);
});
test('isAddressedBy: plain mention does NOT address', () => {
	assert.equal(isAddressedBy('thanks Lucy', ['Lucy']), false);
	assert.equal(isAddressedBy("Lucy's answer was good", ['Lucy']), false);
});
test('isAddressedBy: empty names / empty text never matches', () => {
	assert.equal(isAddressedBy('Lucy, hi', []), false);
	assert.equal(isAddressedBy('', ['Lucy']), false);
});

// --- decideForTurn: the wake/silence state machine ------------------------
const gate = () => createGate({ instanceName: 'Lucy', otherInstances: ['Maddy'] });

test('decideForTurn: addressed to me → allow (and sticks)', () => {
	const g = gate();
	assert.equal(decideForTurn(g, 'Lucy, what time is it'), 'allow');
	// follow-up with no name carries the sticky allow
	assert.equal(decideForTurn(g, 'and the weather?'), 'allow');
});
test('decideForTurn: addressed to a peer → drop (and sticks)', () => {
	const g = gate();
	assert.equal(decideForTurn(g, 'Maddy, hello'), 'drop');
	assert.equal(decideForTurn(g, 'how are you?'), 'drop'); // sticky drop
});
test('decideForTurn: my-name flips a sticky drop back to allow', () => {
	const g = gate();
	decideForTurn(g, 'Maddy, you handle it');      // drop
	assert.equal(decideForTurn(g, 'actually Lucy, you do it'), 'allow');
});
test('decideForTurn: no peer configured → always allow (single-bot)', () => {
	const g = createGate({ instanceName: 'Lucy', otherInstances: [] });
	assert.equal(decideForTurn(g, 'anything at all'), 'allow');
	assert.equal(decideForTurn(g, 'Maddy, hi'), 'allow'); // no peer set → gate off
});
test('decideForTurn: primary defaults to allow on a cold opener', () => {
	const g = createGate({ instanceName: 'Lucy', otherInstances: ['Maddy'], primary: true });
	assert.equal(decideForTurn(g, 'hello everyone'), 'allow'); // primary cold-open
});
test('decideForTurn: non-primary stays silent until named', () => {
	const g = createGate({ instanceName: 'Lucy', otherInstances: ['Maddy'], primary: false });
	assert.equal(decideForTurn(g, 'hello everyone'), 'drop'); // not addressed, not primary
});
