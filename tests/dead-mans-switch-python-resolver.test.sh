#!/bin/bash
# The ping job runs from launchd every 300s with PATH falling through to
# /usr/bin, where a clean Mac's python3 is the Xcode-CLT stub: `command -v`
# succeeds, invoking it raises the install dialog. A 5-minute timer would
# reopen that modal indefinitely, so the resolver must prove it RUNS.
set -u
REPO="$(cd "$(dirname "$0")/.." && pwd)"
pass=0; fail=0
ck() { if [ "$2" = "$3" ]; then pass=$((pass+1)); echo "  ok   $1"; else fail=$((fail+1)); echo "  FAIL $1 (got '$2' want '$3')"; fi; }

stub="$(mktemp -d)/python3"
printf '#!/bin/sh\necho "xcode-select: note: No developer tools were found." >&2\nexit 1\n' > "$stub"
chmod +x "$stub"

command -v "$stub" >/dev/null 2>&1 && cv=yes || cv=no
ck "the stub satisfies command -v (why a name check is not enough)" "$cv" "yes"
"$stub" -c 'import sys' >/dev/null 2>&1 && runs=yes || runs=no
ck "the stub fails the run check" "$runs" "no"

pick_only_stub() { local c; for c in "$stub"; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys' >/dev/null 2>&1; then printf '%s\n' "$c"; return 0; fi
done; return 1; }
ck "resolver refuses a stub-only candidate list" "$(pick_only_stub || true)" ""

for f in skills/dead-mans-switch/scripts/ping.sh skills/dead-mans-switch/install.sh; do
  n="$(sed 's/[[:space:]]*#.*$//' "$REPO/$f" | grep -cE '(^|[^"/[:alnum:]_$])python3 ' | tr -d ' ')"
  ck "$(basename "$f") has no bare 'python3 ' invocation" "$n" "0"
done

echo "  $pass passed, $fail failed"
[ "$fail" -eq 0 ]
