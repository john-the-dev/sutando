#!/usr/bin/env tsx
/**
 * Test script for pluggable worker-agent system.
 * Tests both Claude Code and Codex backends.
 */

import { writeFileSync, unlinkSync, existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { getWorkerAgent, checkWorkerStatus, ClaudeCodeWorker, CodexWorker } from './src/worker-agent.js';

function log(msg: string) {
	console.log(`[test-worker-agent] ${msg}`);
}

async function testWorkerStatus() {
	log('Testing worker availability...');
	const status = await checkWorkerStatus();
	for (const [name, available] of Object.entries(status)) {
		log(`  ${name}: ${available ? '✓ available' : '✗ unavailable'}`);
	}
	return status;
}

async function testCodexWorker() {
	log('\nTesting Codex worker...');

	const worker = new CodexWorker();
	const available = await worker.isAvailable();

	if (!available) {
		log('  ✗ Codex not available - skipping execution test');
		return false;
	}

	log('  ✓ Codex is available');

	// Create a test task file
	const taskFile = join(tmpdir(), `test-task-${Date.now()}.txt`);
	const resultFile = join(tmpdir(), `test-result-${Date.now()}.txt`);

	const taskContent = `id: test-task-${Date.now()}
timestamp: ${new Date().toISOString()}
source: test
channel_id: local-test
user_id: test-user
access_tier: owner
priority: normal
task: What is 2 + 2? Respond with just the number.
`;

	writeFileSync(taskFile, taskContent);
	log(`  Created test task: ${taskFile}`);

	// Execute with 30s timeout
	const startTime = Date.now();
	const success = await worker.execute(taskFile, resultFile, { timeoutMs: 30000 });
	const duration = ((Date.now() - startTime) / 1000).toFixed(1);

	if (!success) {
		log(`  ✗ Execution failed after ${duration}s`);
		return false;
	}

	log(`  ✓ Execution succeeded in ${duration}s`);

	// Check result
	if (!existsSync(resultFile)) {
		log('  ✗ Result file not created');
		return false;
	}

	const result = readFileSync(resultFile, 'utf-8');
	log(`  ✓ Result written: ${result.slice(0, 100).trim()}...`);

	// Cleanup
	try {
		unlinkSync(taskFile);
		unlinkSync(resultFile);
	} catch {}

	return true;
}

async function testClaudeCodeWorker() {
	log('\nTesting Claude Code worker...');

	const worker = new ClaudeCodeWorker();
	const available = await worker.isAvailable();

	if (!available) {
		log('  ✗ Claude Code not available (should never happen)');
		return false;
	}

	log('  ✓ Claude Code is available');

	// Create a test task file
	const taskFile = join(tmpdir(), `test-task-cc-${Date.now()}.txt`);
	const taskContent = `id: test-task-cc-${Date.now()}
timestamp: ${new Date().toISOString()}
source: test
task: test task for Claude Code
`;

	writeFileSync(taskFile, taskContent);

	// Execute (Claude Code worker just verifies file exists)
	const success = await worker.execute(taskFile, '');

	// Cleanup
	try {
		unlinkSync(taskFile);
	} catch {}

	if (!success) {
		log('  ✗ Execution failed');
		return false;
	}

	log('  ✓ Execution succeeded');
	return true;
}

async function testEnvironmentSwitch() {
	log('\nTesting environment variable switching...');

	// Test default (should be claude-code)
	const defaultWorker = getWorkerAgent();
	log(`  Default worker: ${defaultWorker.name}`);
	if (defaultWorker.name !== 'claude-code') {
		log('  ✗ Expected default to be claude-code');
		return false;
	}

	// Test explicit codex
	process.env.SUTANDO_WORKER_AGENT = 'codex';
	const codexWorker = getWorkerAgent();
	log(`  SUTANDO_WORKER_AGENT=codex: ${codexWorker.name}`);
	if (codexWorker.name !== 'codex') {
		log('  ✗ Expected worker to be codex');
		return false;
	}

	// Test explicit claude-code
	process.env.SUTANDO_WORKER_AGENT = 'claude-code';
	const ccWorker = getWorkerAgent();
	log(`  SUTANDO_WORKER_AGENT=claude-code: ${ccWorker.name}`);
	if (ccWorker.name !== 'claude-code') {
		log('  ✗ Expected worker to be claude-code');
		return false;
	}

	// Reset
	delete process.env.SUTANDO_WORKER_AGENT;

	log('  ✓ Environment switching works correctly');
	return true;
}

async function main() {
	log('=== Pluggable Worker Agent Test Suite ===\n');

	const results = {
		status: await testWorkerStatus(),
		claudeCode: await testClaudeCodeWorker(),
		envSwitch: await testEnvironmentSwitch(),
		codex: await testCodexWorker(),
	};

	log('\n=== Test Summary ===');
	log(`Worker status check: ${results.status ? '✓' : '✗'}`);
	log(`Claude Code worker: ${results.claudeCode ? '✓' : '✗'}`);
	log(`Environment switch: ${results.envSwitch ? '✓' : '✗'}`);
	log(`Codex worker: ${results.codex ? '✓' : '✗'}`);

	const allPassed = results.claudeCode && results.envSwitch && results.codex;

	if (allPassed) {
		log('\n✓ All tests passed!');
		process.exit(0);
	} else {
		log('\n✗ Some tests failed');
		process.exit(1);
	}
}

main().catch((err) => {
	console.error('Test runner error:', err);
	process.exit(1);
});
