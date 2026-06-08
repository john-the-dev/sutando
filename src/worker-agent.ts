/**
 * Pluggable worker-agent interface for Sutando task execution.
 *
 * Allows switching between Claude Code (default) and alternative backends
 * like Codex CLI via SUTANDO_WORKER_AGENT environment variable.
 *
 * Worker contract:
 * - Input: task file path (contains structured task with headers + body)
 * - Output: result file path (written by worker)
 * - Exit code: 0 = success, non-zero = failure
 *
 * Each backend must implement the WorkerAgent interface.
 */

import { existsSync } from 'node:fs';
import { spawn } from 'node:child_process';
import { join } from 'node:path';
import { resolveWorkspace } from './workspace_default.js';

export interface WorkerAgent {
	/** Unique identifier for this worker backend */
	readonly name: string;

	/**
	 * Execute a task file and write result to result file.
	 *
	 * @param taskFile - Absolute path to task file
	 * @param resultFile - Absolute path where result should be written
	 * @param options - Optional execution options
	 * @returns Promise<boolean> - true if task executed successfully, false otherwise
	 */
	execute(taskFile: string, resultFile: string, options?: ExecutionOptions): Promise<boolean>;

	/**
	 * Check if this worker is available and properly configured.
	 * @returns Promise<boolean> - true if worker is ready to execute tasks
	 */
	isAvailable(): Promise<boolean>;
}

export interface ExecutionOptions {
	/** Timeout in milliseconds (0 = no timeout) */
	timeoutMs?: number;
	/** Working directory for execution */
	cwd?: string;
	/** Additional environment variables */
	env?: Record<string, string>;
	/**
	 * Codex sandbox level. Pass 'read-only' for non-owner access tiers
	 * (team / other) per CLAUDE.md access-control model; default is
	 * 'workspace-write' for owner tasks.
	 */
	sandboxLevel?: 'read-only' | 'workspace-write';
}

/**
 * Claude Code worker - delegates to the current Claude Code session.
 * This is the default worker that handles tasks via the file bridge.
 */
export class ClaudeCodeWorker implements WorkerAgent {
	readonly name = 'claude-code';

	async execute(taskFile: string, resultFile: string, options?: ExecutionOptions): Promise<boolean> {
		// Claude Code execution happens via the existing file bridge:
		// 1. Task file is written to tasks/
		// 2. fswatch picks it up
		// 3. Claude Code session processes it
		// 4. Result is written to results/
		//
		// This worker represents the "do nothing" path - the task is already
		// in the bridge, and the result watcher will handle completion.
		// We just verify the task file exists and return success.
		if (!existsSync(taskFile)) {
			return false;
		}
		// Task is in the bridge pipeline - return success
		return true;
	}

	async isAvailable(): Promise<boolean> {
		// Claude Code is always available (we're running in it)
		return true;
	}
}

/**
 * Codex CLI worker - delegates to local codex installation.
 * Reads task from file, invokes codex exec, writes result.
 */
export class CodexWorker implements WorkerAgent {
	readonly name = 'codex';
	private readonly workspace: string;

	constructor() {
		this.workspace = resolveWorkspace();
	}

	async execute(taskFile: string, resultFile: string, options?: ExecutionOptions): Promise<boolean> {
		if (!existsSync(taskFile)) {
			return false;
		}

		// Check if Codex is available
		const available = await this.isAvailable();
		if (!available) {
			return false;
		}

		// Read task content
		const fs = await import('node:fs/promises');
		const taskContent = await fs.readFile(taskFile, 'utf-8');

		// Parse task body (everything after "task:" line)
		const taskMatch = taskContent.match(/^task:\s*(.+)/ms);
		if (!taskMatch) {
			return false;
		}
		const taskBody = taskMatch[1].trim();

		// Prepare codex command
		const cwd = options?.cwd || this.workspace;
		const timeoutMs = options?.timeoutMs || 0;

		// Sandbox level: 'read-only' for non-owner tiers, 'workspace-write' for owner.
		const sandbox = options?.sandboxLevel ?? 'workspace-write';
		const args = [
			'exec',
			'-C', cwd,
			'-s', sandbox,
			'--skip-git-repo-check',
			'-o', resultFile,
			'--',
			taskBody
		];

		return new Promise<boolean>((resolve) => {
			const child = spawn('codex', args, {
				cwd,
				env: { ...process.env, ...options?.env },
				stdio: ['ignore', 'pipe', 'pipe']
			});

			let killed = false;
			const timeout = timeoutMs > 0 ? setTimeout(() => {
				killed = true;
				child.kill('SIGTERM');
				resolve(false);
			}, timeoutMs) : null;

			child.on('close', (code) => {
				if (timeout) clearTimeout(timeout);
				if (killed) {
					resolve(false);
				} else {
					resolve(code === 0 && existsSync(resultFile));
				}
			});

			child.on('error', () => {
				if (timeout) clearTimeout(timeout);
				resolve(false);
			});
		});
	}

	async isAvailable(): Promise<boolean> {
		return new Promise<boolean>((resolve) => {
			// Check if codex is in PATH using which command
			spawn('which', ['codex'], {
				stdio: 'ignore'
			}).on('close', (code) => {
				resolve(code === 0);
			}).on('error', () => {
				resolve(false);
			});
		});
	}
}

/**
 * Get the configured worker agent based on SUTANDO_WORKER_AGENT env var.
 *
 * Supported values:
 * - "claude-code" (default): Use Claude Code session
 * - "codex": Use local Codex CLI
 *
 * @returns WorkerAgent instance for the configured backend
 */
export function getWorkerAgent(): WorkerAgent {
	const workerType = process.env.SUTANDO_WORKER_AGENT || 'claude-code';

	switch (workerType.toLowerCase()) {
		case 'codex':
			return new CodexWorker();
		case 'claude-code':
		default:
			return new ClaudeCodeWorker();
	}
}

/**
 * Check all available workers and their status.
 * Useful for diagnostics and health checks.
 */
export async function checkWorkerStatus(): Promise<Record<string, boolean>> {
	const workers: WorkerAgent[] = [
		new ClaudeCodeWorker(),
		new CodexWorker(),
	];

	const status: Record<string, boolean> = {};

	for (const worker of workers) {
		status[worker.name] = await worker.isAvailable();
	}

	return status;
}
