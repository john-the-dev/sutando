#!/usr/bin/env python3
"""Behavioral test for hooks/skill-usage-telemetry.py.

Runs the hook as a REAL subprocess (the way Claude Code invokes it) and verifies
the actual `feature_used` wiring by pointing it at a stub `telemetry` module that
records calls to a file — no mocks of the hook's own code, no network. Proves:
  - a Skill PostToolUse payload → feature_used("skill:<name>", flush=True)
  - a non-Skill tool → no emission
  - missing / blank skill name → no emission
  - malformed stdin → exit 0, no crash (fail-open)
  - the name is trimmed, slash-stripped, and length-bounded
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "hooks" / "skill-usage-telemetry.py"


def _run(payload_obj, *, stub_root: Path, stdin: str | None = None) -> tuple[int, list]:
    """Invoke the hook subprocess with a stub telemetry on its src/ path.
    Returns (exit_code, recorded_feature_used_calls)."""
    rec = stub_root / "rec.jsonl"
    src = stub_root / "src"
    src.mkdir(parents=True, exist_ok=True)
    # Stub telemetry.feature_used → append the call to rec.jsonl (behavioral capture).
    (src / "telemetry.py").write_text(
        "import json, os\n"
        "def feature_used(feature, *, flush=False):\n"
        f"    open(r'{rec}', 'a').write(json.dumps({{'feature': feature, 'flush': flush}}) + '\\n')\n"
    )
    data = stdin if stdin is not None else json.dumps(payload_obj)
    env = {**os.environ, "SUTANDO_REPO_ROOT": str(stub_root)}
    p = subprocess.run(
        [sys.executable, str(HOOK)], input=data, text=True,
        capture_output=True, env=env, timeout=10,
    )
    calls = []
    if rec.exists():
        calls = [json.loads(x) for x in rec.read_text().splitlines() if x.strip()]
    return p.returncode, calls


def main() -> int:
    passed = 0

    # 1) Skill invocation → exactly one feature_used("skill:<name>", flush=True).
    with tempfile.TemporaryDirectory() as td:
        rc, calls = _run(
            {"tool_name": "Skill", "tool_input": {"skill": "context-reconstruct"}},
            stub_root=Path(td),
        )
        assert rc == 0, rc
        assert calls == [{"feature": "skill:context-reconstruct", "flush": True}], calls
        passed += 1
        print("ok   Skill use → feature_used('skill:<name>', flush=True)")

    # 2) A non-Skill tool → no emission.
    with tempfile.TemporaryDirectory() as td:
        rc, calls = _run(
            {"tool_name": "Bash", "tool_input": {"command": "ls"}},
            stub_root=Path(td),
        )
        assert rc == 0 and calls == [], calls
        passed += 1
        print("ok   non-Skill tool → zero emissions")

    # 3) Missing / blank skill name → no emission (nothing to attribute).
    for ti in ({"tool_input": {}}, {"tool_input": {"skill": ""}}, {"tool_input": {"skill": "   "}}):
        with tempfile.TemporaryDirectory() as td:
            rc, calls = _run({"tool_name": "Skill", **ti}, stub_root=Path(td))
            assert rc == 0 and calls == [], (ti, calls)
    passed += 1
    print("ok   missing/blank skill name → zero emissions")

    # 4) Malformed stdin → exit 0, no crash (fail-open, telemetry never breaks a tool).
    for bad in ("", "   ", "not json", "{"):
        with tempfile.TemporaryDirectory() as td:
            rc, calls = _run(None, stub_root=Path(td), stdin=bad)
            assert rc == 0 and calls == [], (repr(bad), rc, calls)
    passed += 1
    print("ok   malformed/empty stdin → exit 0, no emission (fail-open)")

    # 5) Name is trimmed, slash-stripped, and length-bounded (property hygiene).
    with tempfile.TemporaryDirectory() as td:
        rc, calls = _run(
            {"tool_name": "Skill", "tool_input": {"skill": "  /morning-briefing  "}},
            stub_root=Path(td),
        )
        assert calls == [{"feature": "skill:morning-briefing", "flush": True}], calls
        passed += 1
        print("ok   name trimmed + slash-stripped")
    with tempfile.TemporaryDirectory() as td:
        long = "x" * 200
        rc, calls = _run(
            {"tool_name": "Skill", "tool_input": {"skill": long}},
            stub_root=Path(td),
        )
        assert calls[0]["feature"] == "skill:" + "x" * 64, calls
        passed += 1
        print("ok   name length-bounded to 64 chars")

    print(f"\nALL PASS ({passed} checks)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
