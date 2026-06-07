# Pluggable Worker Agent System

Sutando's task execution engine now supports pluggable worker agents, allowing you to switch between different AI backends for task processing.

## Overview

The worker-agent system provides a standardized interface for delegating tasks to different execution backends. This enables:

- **Fallback capability**: Use Codex CLI when Claude Code is unavailable
- **Backend diversity**: Choose the right tool for specific task types
- **Easy extensibility**: Add new worker backends by implementing the WorkerAgent interface

## Supported Workers

### Claude Code (default)

The original Sutando execution path using the current Claude Code session via the file bridge.

**Configuration**: No configuration needed (default)

**Status**: Always available when Sutando is running

### Codex CLI

Delegates tasks to the local Codex CLI installation. Useful when:
- Claude Code is not available
- You want a second opinion from a different model
- You have an active Codex subscription

**Configuration**:
```bash
export SUTANDO_WORKER_AGENT=codex
```

**Requirements**:
- Codex CLI must be installed (`codex --version`)
- Must be authenticated (`codex login status`)

## Worker Agent Interface

Each worker must implement:

```typescript
interface WorkerAgent {
  readonly name: string;
  execute(taskFile: string, resultFile: string, options?: ExecutionOptions): Promise<boolean>;
  isAvailable(): Promise<boolean>;
}
```

**Contract**:
- **Input**: Path to task file (structured format with headers + body)
- **Output**: Path to result file (worker writes the result here)
- **Return**: `true` if successful, `false` otherwise
- **Exit code**: Workers should use standard exit codes (0 = success)

## Configuration

### Environment Variables

**SUTANDO_WORKER_AGENT**

Controls which worker backend to use for task execution.

Supported values:
- `claude-code` (default) - Use Claude Code session
- `codex` - Use local Codex CLI

Example:
```bash
# Use Codex for all tasks
export SUTANDO_WORKER_AGENT=codex
bash src/startup.sh

# Use Claude Code (default)
unset SUTANDO_WORKER_AGENT
bash src/startup.sh
```

### Per-Task Configuration

Currently, the worker is selected globally via environment variable. Future enhancement could add per-task worker selection via task file headers:

```
id: task-123
worker: codex
task: ...
```

## Testing

### Check Worker Status

```bash
npx tsx test-worker-agent.ts
```

This will:
1. Check which workers are available
2. Test Claude Code worker execution
3. Test environment variable switching
4. Test Codex worker execution (if installed)

### Manual Testing

#### Test Codex Worker

```bash
# Set environment
export SUTANDO_WORKER_AGENT=codex

# Create a test task
cat > /tmp/test-task.txt <<EOF
id: test-task-$(date +%s)
timestamp: $(date -u +%Y-%m-%dT%H:%M:%SZ)
source: test
channel_id: local-test
user_id: test-user
access_tier: owner
priority: normal
task: What is the capital of France? Respond with just the city name.
EOF

# Test with TypeScript (using worker-agent.ts)
npx tsx -e "
import { CodexWorker } from './src/worker-agent.js';
const worker = new CodexWorker();
const result = '/tmp/test-result.txt';
worker.execute('/tmp/test-task.txt', result, { timeoutMs: 30000 })
  .then(ok => console.log(ok ? 'Success' : 'Failed'));
"

# Check result
cat /tmp/test-result.txt
```

#### Test with Existing Codex Skill

The existing `claude-codex` skill (`skills/claude-codex/`) can also be used as a reference:

```bash
bash skills/claude-codex/scripts/codex-run.sh --check
bash skills/claude-codex/scripts/codex-run.sh -- "What is 2 + 2?"
```

## Implementation Details

### File Locations

- **Interface**: `src/worker-agent.ts`
- **Test Suite**: `test-worker-agent.ts`
- **Documentation**: `docs/worker-agent.md`

### Task File Format

Workers expect task files with this structure:

```
id: task-{timestamp}
timestamp: 2026-06-07T...
source: voice|discord|telegram|chat|test
channel_id: ...
user_id: ...
access_tier: owner|team|other
priority: urgent|normal|low
task: {actual task content starts here}
{multi-line task body allowed}
```

The worker extracts everything after `task:` as the prompt to execute.

### Codex Worker Behavior

- **Sandbox**: Uses `workspace-write` by default (safer than full access)
- **Output**: Uses `-o <file>` flag to write result directly
- **Working Directory**: Defaults to `$SUTANDO_WORKSPACE`, configurable via options
- **Timeout**: Respects `timeoutMs` option (0 = no timeout)
- **Error Handling**: Returns `false` on any failure (timeout, non-zero exit, missing result)

## Integration Points

### Task Bridge

The task bridge (`src/task-bridge.ts`) currently uses the file-based approach where Claude Code watches for task files. To fully integrate the worker-agent system, the task bridge would need to:

1. Import `getWorkerAgent()` from `worker-agent.ts`
2. When a task file is detected, call `worker.execute(taskFile, resultFile)`
3. Handle the result based on the worker's return value

**Current state**: Worker-agent system is implemented and tested independently. Task bridge integration is the next step.

### Voice Agent

The voice agent's `work` tool currently writes tasks directly to the file bridge. No changes needed - the worker selection happens transparently at the execution layer.

### Discord/Telegram/Slack Bridges

These bridges write task files and poll for results. No changes needed - they're agnostic to which worker executes the task.

## Limitations & Future Work

### Current Limitations

1. **Global configuration only**: Worker is selected via environment variable, not per-task
2. **No runtime switching**: Changing workers requires restart
3. **Limited error reporting**: Workers return boolean success/failure
4. **No load balancing**: Single worker per session

### Future Enhancements

1. **Per-task worker selection**: Allow task files to specify `worker: codex`
2. **Automatic fallback**: Try Codex if Claude Code is unresponsive
3. **Worker pool**: Run multiple workers in parallel for different tasks
4. **Cost tracking**: Log API usage per worker
5. **Model selection**: Let workers specify which model to use
6. **Streaming results**: Support incremental result delivery

## Related Issues

- **#1501**: Pluggable worker-agent support (this implementation)
- **#1534**: Workspace migration and channel bridge architecture

## Examples

### Example 1: Normal Operation (Claude Code)

```bash
# Default behavior - Claude Code handles all tasks
bash src/startup.sh

# Voice command gets processed by Claude Code
echo "task: Write a haiku about clouds" > ~/.sutando/workspace/tasks/task-123.txt
# Claude Code watches, executes, writes to results/task-123.txt
```

### Example 2: Using Codex

```bash
# Switch to Codex worker
export SUTANDO_WORKER_AGENT=codex
bash src/startup.sh

# Voice command gets processed by Codex CLI
echo "task: Write a haiku about clouds" > ~/.sutando/workspace/tasks/task-123.txt
# Codex CLI executes via spawn(), writes to results/task-123.txt
```

### Example 3: Health Check

```bash
# Check which workers are available
npx tsx -e "
import { checkWorkerStatus } from './src/worker-agent.js';
checkWorkerStatus().then(console.log);
"

# Output:
# { 'claude-code': true, 'codex': false }
```

## Troubleshooting

### Codex Worker Not Available

**Symptom**: `checkWorkerStatus()` shows `codex: false`

**Fixes**:
1. Install Codex CLI: See https://codex.com/docs/installation
2. Authenticate: `codex login`
3. Verify: `which codex && codex --version`

### Task Execution Fails

**Symptom**: Worker returns `false`, no result file created

**Debug steps**:
1. Check worker availability: `npx tsx -e "import { getWorkerAgent } from './src/worker-agent.js'; getWorkerAgent().isAvailable().then(console.log);"`
2. Check task file format: Ensure `task:` delimiter exists
3. Check permissions: Worker needs write access to result directory
4. Check timeout: Task may need longer `timeoutMs`
5. Check Codex auth: `codex login status`

### Wrong Worker Selected

**Symptom**: Expected Codex but Claude Code is used (or vice versa)

**Fix**:
```bash
# Check current configuration
echo $SUTANDO_WORKER_AGENT

# Set explicitly
export SUTANDO_WORKER_AGENT=codex  # or claude-code

# Verify
npx tsx -e "import { getWorkerAgent } from './src/worker-agent.js'; console.log(getWorkerAgent().name);"
```

## API Reference

### getWorkerAgent()

```typescript
function getWorkerAgent(): WorkerAgent
```

Returns the configured worker agent based on `SUTANDO_WORKER_AGENT` environment variable.

**Example**:
```typescript
import { getWorkerAgent } from './src/worker-agent.js';
const worker = getWorkerAgent();
console.log(worker.name); // 'claude-code' or 'codex'
```

### checkWorkerStatus()

```typescript
async function checkWorkerStatus(): Promise<Record<string, boolean>>
```

Checks availability of all known workers.

**Returns**: Object mapping worker names to availability status

**Example**:
```typescript
import { checkWorkerStatus } from './src/worker-agent.js';
const status = await checkWorkerStatus();
// { 'claude-code': true, 'codex': false }
```

### WorkerAgent.execute()

```typescript
async execute(
  taskFile: string,
  resultFile: string,
  options?: ExecutionOptions
): Promise<boolean>
```

Execute a task file and write result.

**Parameters**:
- `taskFile`: Absolute path to task file
- `resultFile`: Absolute path where result should be written
- `options`: Optional execution options (timeout, cwd, env)

**Returns**: `true` if successful, `false` otherwise

**Example**:
```typescript
import { CodexWorker } from './src/worker-agent.js';

const worker = new CodexWorker();
const success = await worker.execute(
  '/tmp/task-123.txt',
  '/tmp/result-123.txt',
  { timeoutMs: 30000, cwd: '/path/to/workspace' }
);
```

### WorkerAgent.isAvailable()

```typescript
async isAvailable(): Promise<boolean>
```

Check if this worker is available and properly configured.

**Returns**: `true` if worker is ready, `false` otherwise

**Example**:
```typescript
import { CodexWorker } from './src/worker-agent.js';

const worker = new CodexWorker();
if (await worker.isAvailable()) {
  console.log('Codex is ready');
} else {
  console.log('Codex not installed or not authenticated');
}
```
