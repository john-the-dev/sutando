"""Structural tests: [deduped:], [no-send], [REPLIED] skip-marker support
in the two voice-skill result-injection paths.

These tests grep the source for the required logic without running a live
server — same pattern used by other structural test files in tests/.
"""
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PHONE_SRV = REPO / "skills/phone-conversation/scripts/conversation-server.ts"
DVOICE_SRV = REPO / "skills/discord-voice/scripts/discord-voice-server.ts"

# Substring that must appear in the TS source (JS regex literal, not Python regex)
SKIP_PATTERN = r"[(?:deduped:\s*task-|no-send|REPLIED)]"

passed = 0
failed = 0


def ok(name: str) -> None:
    global passed
    passed += 1
    print(f"  PASS  {name}")


def fail(name: str, reason: str) -> None:
    global failed
    failed += 1
    print(f"  FAIL  {name}: {reason}")


phone_src = PHONE_SRV.read_text()
dvoice_src = DVOICE_SRV.read_text()

# --- conversation-server.ts ---

name = "phone: skip-marker regex present"
if SKIP_PATTERN in phone_src:
    ok(name)
else:
    fail(name, f"pattern {SKIP_PATTERN!r} not found in {PHONE_SRV.name}")

name = "phone: skip check is after archive calls"
archive_pos = phone_src.find("archivePhoneFile(resultPath")
skip_pos = phone_src.find(SKIP_PATTERN)  # finds the JS regex literal substring
if archive_pos != -1 and skip_pos != -1 and skip_pos > archive_pos:
    ok(name)
else:
    fail(name, f"archive_pos={archive_pos} skip_pos={skip_pos} — skip must come after archive")

name = "phone: skip check is before cache/inject"
cache_pos = phone_src.find("taskResultCache.set(")
if skip_pos != -1 and cache_pos != -1 and skip_pos < cache_pos:
    ok(name)
else:
    fail(name, f"skip_pos={skip_pos} cache_pos={cache_pos} — skip must come before cache")

name = "phone: early return inside skip block"
skip_block_start = phone_src.find(SKIP_PATTERN)  # JS regex literal substring
# Find the next `return;` after the skip marker check
if skip_block_start != -1:
    tail = phone_src[skip_block_start:skip_block_start + 300]
    if "return;" in tail:
        ok(name)
    else:
        fail(name, "no return; within 300 chars of skip marker regex")
else:
    fail(name, "skip marker regex not found")

name = "phone: markerMatch log present"
if "markerMatch" in phone_src and "archived silently" in phone_src:
    ok(name)
else:
    fail(name, "expected markerMatch log line not found")

# --- discord-voice-server.ts ---

name = "discord-voice: skip-marker regex present"
if SKIP_PATTERN in dvoice_src:
    ok(name)
else:
    fail(name, f"pattern {SKIP_PATTERN!r} not found in {DVOICE_SRV.name}")

name = "discord-voice: skip check is after unlinkSync(resultPath)"
unlink_pos = dvoice_src.find("unlinkSync(resultPath)")
dv_skip_pos = dvoice_src.find(SKIP_PATTERN)  # JS regex literal substring
if unlink_pos != -1 and dv_skip_pos != -1 and dv_skip_pos > unlink_pos:
    ok(name)
else:
    fail(name, f"unlink_pos={unlink_pos} skip_pos={dv_skip_pos} — skip must come after unlink")

name = "discord-voice: skip check is before resultQueue.push"
# Find the resultQueue.push that comes AFTER the skip check (not any earlier one)
queue_pos_after = dvoice_src.find("resultQueue.push(", dv_skip_pos) if dv_skip_pos != -1 else -1
if dv_skip_pos != -1 and queue_pos_after != -1 and dv_skip_pos < queue_pos_after:
    ok(name)
else:
    fail(name, f"skip_pos={dv_skip_pos} next_queue_pos={queue_pos_after} — skip must come before resultQueue.push")

name = "discord-voice: early return inside skip block"
if dv_skip_pos != -1:
    tail = dvoice_src[dv_skip_pos:dv_skip_pos + 400]
    if "return;" in tail:
        ok(name)
    else:
        fail(name, "no return; within 300 chars of skip marker regex")
else:
    fail(name, "skip marker regex not found")

name = "discord-voice: markerMatch log present"
if "markerMatch" in dvoice_src and "archived silently" in dvoice_src:
    ok(name)
else:
    fail(name, "expected markerMatch log line not found")

# Summary
print(f"\n{passed + failed} tests: {passed} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
