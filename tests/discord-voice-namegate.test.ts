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
	assert.equal(isAddressedBy('hey Echo', ['Echo']), true);
	assert.equal(isAddressedBy('hi, Echo', ['Echo']), true);
	assert.equal(isAddressedBy('ok Echo', ['Echo']), true);
});
test('isAddressedBy: comma/question tag addresses', () => {
	assert.equal(isAddressedBy('Echo, what time is it', ['Echo']), true);
	assert.equal(isAddressedBy('Echo?', ['Echo']), true);
});
test('isAddressedBy: imperative verb at clause start addresses', () => {
	assert.equal(isAddressedBy('Echo check the PR', ['Echo']), true);
});
test('isAddressedBy: plain mention does NOT address', () => {
	assert.equal(isAddressedBy('thanks Echo', ['Echo']), false);
	assert.equal(isAddressedBy("Echo's answer was good", ['Echo']), false);
});
test('isAddressedBy: empty names / empty text never matches', () => {
	assert.equal(isAddressedBy('Echo, hi', []), false);
	assert.equal(isAddressedBy('', ['Echo']), false);
});

// --- decideForTurn: the wake/silence state machine ------------------------
const gate = () => createGate({ instanceName: 'Echo', otherInstances: ['Foxtrot'] });

test('decideForTurn: addressed to me → allow (and sticks)', () => {
	const g = gate();
	assert.equal(decideForTurn(g, 'Echo, what time is it'), 'allow');
	// follow-up with no name carries the sticky allow
	assert.equal(decideForTurn(g, 'and the weather?'), 'allow');
});
test('decideForTurn: addressed to a peer → drop (and sticks)', () => {
	const g = gate();
	assert.equal(decideForTurn(g, 'Foxtrot, hello'), 'drop');
	assert.equal(decideForTurn(g, 'how are you?'), 'drop'); // sticky drop
});
test('decideForTurn: my-name flips a sticky drop back to allow', () => {
	const g = gate();
	decideForTurn(g, 'Foxtrot, you handle it');      // drop
	assert.equal(decideForTurn(g, 'actually Echo, you do it'), 'allow');
});
test('decideForTurn: no peer configured → always allow (single-bot)', () => {
	const g = createGate({ instanceName: 'Echo', otherInstances: [] });
	assert.equal(decideForTurn(g, 'anything at all'), 'allow');
	assert.equal(decideForTurn(g, 'Foxtrot, hi'), 'allow'); // no peer set → gate off
});
test('decideForTurn: primary defaults to allow on a cold opener', () => {
	const g = createGate({ instanceName: 'Echo', otherInstances: ['Foxtrot'], primary: true });
	assert.equal(decideForTurn(g, 'hello everyone'), 'allow'); // primary cold-open
});
test('decideForTurn: non-primary stays silent until named', () => {
	const g = createGate({ instanceName: 'Echo', otherInstances: ['Foxtrot'], primary: false });
	assert.equal(decideForTurn(g, 'hello everyone'), 'drop'); // not addressed, not primary
});
