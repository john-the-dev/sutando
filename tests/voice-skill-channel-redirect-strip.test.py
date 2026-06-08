"""Structural tests: delivery-routing markers are stripped (not narrated) in
the three voice-skill result-injection paths.

Voice surfaces (phone, discord-voice, task-bridge) cannot route to Discord /
Slack channels or attach files. The correct behavior mirrors Telegram: strip
[channel:] from the first line and strip [file:/send:/attach:] markers
anywhere in the body, then narrate the remaining content.

These tests grep the source for the required logic without running a live
server — same pattern used by other structural test files in tests/.
"""
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PHONE_SRV = REPO / "skills/phone-conversation/scripts/conversation-server.ts"
DVOICE_SRV = REPO / "skills/discord-voice/scripts/discord-voice-server.ts"
TASK_BRIDGE = REPO / "src/task-bridge.ts"

# Regex substrings that must appear in each TS source
CHANNEL_PATTERN = r"[channel:[^\]]+\]"
FILE_PATTERN = r"(?:file|send|attach):[^\]]+\]"

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
bridge_src = TASK_BRIDGE.read_text()

# --- conversation-server.ts ---

name = "phone: [channel:] strip regex present"
if CHANNEL_PATTERN in phone_src:
    ok(name)
else:
    fail(name, f"pattern {CHANNEL_PATTERN!r} not found in {PHONE_SRV.name}")

name = "phone: [file:/send:/attach:] strip regex present"
if FILE_PATTERN in phone_src:
    ok(name)
else:
    fail(name, f"pattern {FILE_PATTERN!r} not found in {PHONE_SRV.name}")

name = "phone: strip is before result injection (resultForNarration used in injectedText)"
for_narration_pos = phone_src.find("resultForNarration")
inject_pos = phone_src.find("injectedText")
if for_narration_pos != -1 and inject_pos != -1 and for_narration_pos < inject_pos:
    ok(name)
else:
    fail(name, f"resultForNarration_pos={for_narration_pos} inject_pos={inject_pos} — strip must precede injection")

name = "phone: fallback preserves result when strip produces empty string"
# Pattern: `|| result` used as fallback
if "|| result" in phone_src[phone_src.find(CHANNEL_PATTERN):phone_src.find(CHANNEL_PATTERN) + 300]:
    ok(name)
else:
    fail(name, "no || result fallback within 300 chars of strip regex")

# --- discord-voice-server.ts ---

name = "discord-voice: [channel:] strip regex present"
if CHANNEL_PATTERN in dvoice_src:
    ok(name)
else:
    fail(name, f"pattern {CHANNEL_PATTERN!r} not found in {DVOICE_SRV.name}")

name = "discord-voice: [file:/send:/attach:] strip regex present"
if FILE_PATTERN in dvoice_src:
    ok(name)
else:
    fail(name, f"pattern {FILE_PATTERN!r} not found in {DVOICE_SRV.name}")

name = "discord-voice: strip is before resultQueue.push (resultForNarration used)"
dv_for_narration_pos = dvoice_src.find("resultForNarration")
dv_queue_pos = dvoice_src.find("resultQueue.push(", dv_for_narration_pos) if dv_for_narration_pos != -1 else -1
if dv_for_narration_pos != -1 and dv_queue_pos != -1 and dv_for_narration_pos < dv_queue_pos:
    ok(name)
else:
    fail(name, f"resultForNarration_pos={dv_for_narration_pos} queue_pos={dv_queue_pos} — strip must precede push")

name = "discord-voice: fallback preserves result when strip produces empty string"
dv_pattern_pos = dvoice_src.find(CHANNEL_PATTERN)
if "|| result" in dvoice_src[dv_pattern_pos:dv_pattern_pos + 300]:
    ok(name)
else:
    fail(name, "no || result fallback within 300 chars of strip regex")

# --- task-bridge.ts ---

name = "task-bridge: [channel:] strip regex present"
if CHANNEL_PATTERN in bridge_src:
    ok(name)
else:
    fail(name, f"pattern {CHANNEL_PATTERN!r} not found in {TASK_BRIDGE.name}")

name = "task-bridge: [file:/send:/attach:] strip regex present"
if FILE_PATTERN in bridge_src:
    ok(name)
else:
    fail(name, f"pattern {FILE_PATTERN!r} not found in {TASK_BRIDGE.name}")

name = "task-bridge: strip is before onResult call for task-* fallthrough path"
narration_var_pos = bridge_src.find("_narration")
on_result_pos = bridge_src.find("onResult(_narration)")
if narration_var_pos != -1 and on_result_pos != -1 and narration_var_pos < on_result_pos:
    ok(name)
else:
    fail(name, f"_narration_pos={narration_var_pos} onResult_pos={on_result_pos} — strip must precede onResult")

name = "task-bridge: strip is before onResult call for voice-only path"
voice_narration_pos = bridge_src.find("_voiceNarration")
voice_on_result_pos = bridge_src.find("onResult(_voiceNarration)")
if voice_narration_pos != -1 and voice_on_result_pos != -1 and voice_narration_pos < voice_on_result_pos:
    ok(name)
else:
    fail(name, f"_voiceNarration_pos={voice_narration_pos} onResult_pos={voice_on_result_pos} — strip must precede onResult")

# Summary
print(f"\n{passed + failed} tests: {passed} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
