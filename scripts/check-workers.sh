#!/bin/bash
# Check status of all available worker agents
# Usage: bash scripts/check-workers.sh

echo "=== Worker Agent Status ==="
echo

# Check current configuration
if [ -n "$SUTANDO_WORKER_AGENT" ]; then
  echo "Current worker: $SUTANDO_WORKER_AGENT"
else
  echo "Current worker: claude-code (default)"
fi
echo

# Check all workers
npx tsx -e "
import { checkWorkerStatus } from './src/worker-agent.ts';

(async () => {
  const status = await checkWorkerStatus();

  console.log('Worker Availability:');
  for (const [name, available] of Object.entries(status)) {
    const icon = available ? '✓' : '✗';
    const text = available ? 'available' : 'unavailable';
    console.log(\`  \${icon} \${name}: \${text}\`);
  }
})();
"

echo
echo "To switch workers:"
echo "  export SUTANDO_WORKER_AGENT=codex"
echo "  export SUTANDO_WORKER_AGENT=claude-code"
echo
echo "To test Codex worker:"
echo "  bash scripts/test-codex-worker.sh"
