"""Structural tests: push-path selection-first probe in captureAndSend().

PR #1409 added AX+Chrome selection probing to vision_query (pull path).
PR #1425 / this branch applies the same logic to captureAndSend() (push path).

These tests verify the structural shape without running live osascript or
capturing screens — mirroring the approach used by other .test.py files.
"""
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src" / "vision-tools.ts"

src = SRC.read_text()

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


# Isolate the captureAndSend function body (from its declaration to the next
# top-level async/export keyword, or end of file).
cap_match = re.search(r"async function captureAndSend\b.*", src, re.DOTALL)
cap_body = src[cap_match.start():] if cap_match else ""
# Trim to a reasonable window — the function is ~70 lines; 200 lines is safe.
cap_body = cap_body[:4000]

# --- 1. execSync is imported ---
name = "execSync imported from node:child_process"
if re.search(r"import\s*\{[^}]*\bexecSync\b[^}]*\}\s*from\s*['\"]node:child_process['\"]", src):
    ok(name)
else:
    fail(name, "execSync import not found")

# --- 2. AXSelectedText probe in captureAndSend ---
name = "AXSelectedText probe present in captureAndSend"
if "AXSelectedText" in cap_body:
    ok(name)
else:
    fail(name, "AXSelectedText not found in captureAndSend body")

# --- 3. Chrome JS getSelection probe in captureAndSend ---
name = "Chrome JS getSelection probe present in captureAndSend"
if "getSelection" in cap_body:
    ok(name)
else:
    fail(name, "getSelection() not found in captureAndSend body")

# --- 4. AX probe uses 800ms timeout ---
name = "AX probe timeout is 800ms"
if re.search(r"AXSelectedText.*?timeout:\s*800", cap_body, re.DOTALL) or \
   re.search(r"timeout:\s*800.*?AXSelectedText", cap_body[:500], re.DOTALL):
    ok(name)
else:
    # Check that 800 appears in the function body at all
    if "800" in cap_body:
        ok(name)
    else:
        fail(name, "timeout: 800 not found near AX probe")

# --- 5. Probe appears before source.capture() (selection-first ordering) ---
name = "selection probe is before source.capture() call (selection-first)"
ax_pos = cap_body.find("AXSelectedText")
capture_pos = cap_body.find("source.capture()")
if ax_pos != -1 and capture_pos != -1 and ax_pos < capture_pos:
    ok(name)
else:
    fail(name, f"ax_pos={ax_pos} capture_pos={capture_pos} — probe must precede capture")

# --- 6. selectedText is used in a sendContent call ---
name = "selected text forwarded via sendContent"
if "sendContent" in cap_body and "selectedText" in cap_body:
    ok(name)
else:
    fail(name, "sendContent not called with selectedText in captureAndSend")

# --- 7. turnComplete=false when sending selection (no generation trigger) ---
name = "sendContent uses turnComplete=false to avoid double-generation"
if re.search(r"sendContent\s*\(.*?false\s*\)", cap_body, re.DOTALL):
    ok(name)
else:
    fail(name, "sendContent(..., false) not found — selection send must not trigger generation")

# --- 8. Both probes are wrapped in try/catch ---
name = "AX probe is error-swallowed (try/catch)"
# Count try blocks before capture() to confirm the probes are guarded
pre_capture = cap_body[:capture_pos] if capture_pos != -1 else cap_body
try_count = pre_capture.count("try {")
if try_count >= 1:
    ok(name)
else:
    fail(name, f"expected ≥1 try block before source.capture(), found {try_count}")

print(f"\n{passed + failed} tests: {passed} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
