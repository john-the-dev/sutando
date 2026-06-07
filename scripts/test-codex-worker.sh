#!/bin/bash
# Quick test script for Codex worker
# Usage: bash scripts/test-codex-worker.sh

set -euo pipefail

echo "=== Testing Codex Worker ==="
echo

# Check if Codex is installed
if ! command -v codex >/dev/null 2>&1; then
  echo "✗ Codex CLI not found in PATH"
  echo "  Install from: https://codex.com/docs/installation"
  exit 1
fi

echo "✓ Codex CLI found: $(command -v codex)"

# Check Codex auth status
if ! codex login status >/dev/null 2>&1; then
  echo "✗ Codex not authenticated"
  echo "  Run: codex login"
  exit 1
fi

echo "✓ Codex authenticated"
echo

# Create test task
TASK_FILE="/tmp/sutando-codex-test-$(date +%s).txt"
RESULT_FILE="/tmp/sutando-codex-result-$(date +%s).txt"

cat > "$TASK_FILE" <<EOF
id: test-$(date +%s)
timestamp: $(date -u +%Y-%m-%dT%H:%M:%SZ)
source: test
channel_id: local-test
user_id: test-user
access_tier: owner
priority: normal
task: What is the capital of France? Respond with just the city name, nothing else.
EOF

echo "Created test task: $TASK_FILE"
echo

# Test with worker-agent
echo "Testing worker execution..."
npx tsx -e "
import { CodexWorker } from './src/worker-agent.ts';

(async () => {
  const worker = new CodexWorker();
  const available = await worker.isAvailable();

  if (!available) {
    console.error('✗ Codex worker not available');
    process.exit(1);
  }

  console.log('✓ Codex worker is available');
  console.log('');
  console.log('Executing task (timeout: 30s)...');

  const startTime = Date.now();
  const success = await worker.execute(
    '$TASK_FILE',
    '$RESULT_FILE',
    { timeoutMs: 30000 }
  );
  const duration = ((Date.now() - startTime) / 1000).toFixed(1);

  if (!success) {
    console.error(\`✗ Execution failed after \${duration}s\`);
    process.exit(1);
  }

  console.log(\`✓ Execution succeeded in \${duration}s\`);
  console.log('');
})();
"

# Check result
if [ -f "$RESULT_FILE" ]; then
  echo "Result:"
  echo "---"
  cat "$RESULT_FILE"
  echo "---"
  echo
  echo "✓ Test passed!"

  # Cleanup
  rm -f "$TASK_FILE" "$RESULT_FILE"
  exit 0
else
  echo "✗ Result file not created"
  exit 1
fi
