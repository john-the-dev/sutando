/**
 * Tests for skills/screen-companion/scripts/load-config.ts
 *
 * Covers:
 *   validateConfig(raw, path) — pure validator; no filesystem reads.
 *   renderGoal(config, goal)  — pure template renderer.
 *
 * Run: tsx --test tests/screen-companion-load-config.test.ts
 */

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { validateConfig, renderGoal } from '../skills/screen-companion/scripts/load-config.js';
import type { ScreenCompanionConfig } from '../skills/screen-companion/scripts/load-config.js';

// Minimal valid config fixture
function validRaw(): Record<string, unknown> {
	return {
		name: 'pair-review',
		activation: {
			voice_phrases: ['hey lucy', 'activate'],
			button_label: 'Code Review',
			cli_alias: 'pair-review',
		},
		vision_mode: 'pull',
		system_prompt_overlay: 'Review the code on screen.',
		tools_allow: ['get_screen', 'work'],
	};
}

function validPushRaw(): Record<string, unknown> {
	return {
		...validRaw(),
		vision_mode: 'push',
		vision_cadence_ms: 500,
	};
}

// ---------------------------------------------------------------------------
// validateConfig — happy paths
// ---------------------------------------------------------------------------

describe('validateConfig — happy paths', () => {
	it('accepts a valid pull config', () => {
		const config = validateConfig(validRaw(), 'test.yaml');
		assert.equal(config.name, 'pair-review');
		assert.equal(config.vision_mode, 'pull');
		assert.deepEqual(config.tools_allow, ['get_screen', 'work']);
	});

	it('accepts a valid push config with vision_cadence_ms', () => {
		const config = validateConfig(validPushRaw(), 'test.yaml');
		assert.equal(config.vision_mode, 'push');
		assert.equal(config.vision_cadence_ms, 500);
	});

	it('accepts push config with cadence at lower boundary (100ms)', () => {
		const raw = { ...validPushRaw(), vision_cadence_ms: 100 };
		const config = validateConfig(raw, 'test.yaml');
		assert.equal(config.vision_cadence_ms, 100);
	});

	it('accepts push config with cadence at upper boundary (5000ms)', () => {
		const raw = { ...validPushRaw(), vision_cadence_ms: 5000 };
		const config = validateConfig(raw, 'test.yaml');
		assert.equal(config.vision_cadence_ms, 5000);
	});

	it('accepts optional goal_template field', () => {
		const raw = { ...validRaw(), goal_template: 'Review: {goal}' };
		const config = validateConfig(raw, 'test.yaml');
		assert.equal(config.goal_template, 'Review: {goal}');
	});

	it('accepts empty tools_allow array', () => {
		const raw = { ...validRaw(), tools_allow: [] };
		const config = validateConfig(raw, 'test.yaml');
		assert.deepEqual(config.tools_allow, []);
	});
});

// ---------------------------------------------------------------------------
// validateConfig — required field errors
// ---------------------------------------------------------------------------

describe('validateConfig — missing required fields', () => {
	it('throws when top-level config is null', () => {
		assert.throws(() => validateConfig(null, 'f.yaml'), /must be an object/);
	});

	it('throws when top-level config is a string', () => {
		assert.throws(() => validateConfig('bad', 'f.yaml'), /must be an object/);
	});

	for (const field of ['name', 'activation', 'vision_mode', 'system_prompt_overlay', 'tools_allow']) {
		it(`throws when "${field}" is missing`, () => {
			const raw = validRaw();
			delete (raw as any)[field];
			assert.throws(() => validateConfig(raw, 'f.yaml'), /missing required fields/);
		});
	}

	for (const field of ['voice_phrases', 'button_label', 'cli_alias']) {
		it(`throws when activation.${field} is missing`, () => {
			const raw = validRaw();
			(raw.activation as any) = { ...validRaw().activation as object };
			delete ((raw.activation as any) as Record<string, unknown>)[field];
			assert.throws(() => validateConfig(raw, 'f.yaml'), /activation missing/);
		});
	}
});

// ---------------------------------------------------------------------------
// validateConfig — vision_mode validation
// ---------------------------------------------------------------------------

describe('validateConfig — vision_mode validation', () => {
	it('throws on unknown vision_mode value', () => {
		const raw = { ...validRaw(), vision_mode: 'stream' };
		assert.throws(() => validateConfig(raw, 'f.yaml'), /vision_mode must be/);
	});

	it('throws when push config omits vision_cadence_ms', () => {
		const raw = { ...validRaw(), vision_mode: 'push' };
		// No vision_cadence_ms
		assert.throws(() => validateConfig(raw, 'f.yaml'), /vision_cadence_ms/);
	});

	it('throws when push cadence is too low (below 100ms)', () => {
		const raw = { ...validPushRaw(), vision_cadence_ms: 50 };
		assert.throws(() => validateConfig(raw, 'f.yaml'), /100–5000ms/);
	});

	it('throws when push cadence is too high (above 5000ms)', () => {
		const raw = { ...validPushRaw(), vision_cadence_ms: 70000 };
		assert.throws(() => validateConfig(raw, 'f.yaml'), /100–5000ms/);
	});

	it('throws when tools_allow is not an array', () => {
		const raw = { ...validRaw(), tools_allow: 'get_screen' };
		assert.throws(() => validateConfig(raw, 'f.yaml'), /tools_allow must be a string\[\]/);
	});

	it('throws when tools_allow contains a non-string', () => {
		const raw = { ...validRaw(), tools_allow: [42, 'get_screen'] };
		assert.throws(() => validateConfig(raw, 'f.yaml'), /tools_allow must be a string\[\]/);
	});
});

// ---------------------------------------------------------------------------
// renderGoal
// ---------------------------------------------------------------------------

describe('renderGoal', () => {
	function cfg(goal_template?: string): ScreenCompanionConfig {
		return validateConfig({ ...validRaw(), ...(goal_template !== undefined ? { goal_template } : {}) }, 'x.yaml');
	}

	it('returns undefined when no goal_template', () => {
		assert.equal(renderGoal(cfg(), 'my goal'), undefined);
	});

	it('returns template as-is when no goal provided', () => {
		assert.equal(renderGoal(cfg('Do {goal} now'), undefined), 'Do {goal} now');
	});

	it('replaces {goal} with the user goal', () => {
		assert.equal(renderGoal(cfg('Review: {goal}'), 'PR #123'), 'Review: PR #123');
	});

	it('leaves template unchanged when {goal} not present', () => {
		assert.equal(renderGoal(cfg('Generic task'), 'something'), 'Generic task');
	});

	it('replaces only first occurrence of {goal}', () => {
		// JS String.replace replaces only the first match
		assert.equal(renderGoal(cfg('{goal} and {goal}'), 'X'), 'X and {goal}');
	});
});
