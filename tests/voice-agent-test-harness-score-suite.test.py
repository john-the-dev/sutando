#!/usr/bin/env python3
"""Regression guard: pure helpers in score.py and run_suite.py of the
voice-agent test harness.

score.py:
  _coerce(raw_dict) -> Judgement
    - accuracy coerced to lowercase; invalid values fall back to "fail"
    - clarity clamped to [1, 5]; None / missing → 1
    - rubric_version always JUDGE_RUBRIC_VERSION (1)

  to_dict(Judgement) -> dict
    - roundtrip via dataclasses.asdict

run_suite.py:
  _row(case, lat, accuracy, clarity, rationale, no_response, transcript) -> dict
    - canonical result row schema

  precondition_gate(dry=True) -> (True, "dry-run: gate skipped")
    - skips audio.calibrate() in dry mode

  _run_one_dry(case) -> dict
    - returns canned data for known ids; fallback for unknown ids

All tests are IO-free (no network, no audio, no files).

Run: python3 tests/voice-agent-test-harness-score-suite.test.py
Exit: 0 on pass, 1 on fail.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SKILL_SCRIPTS = REPO / "skills" / "voice-agent-test-harness" / "scripts"

# Add skill scripts to path so cross-module imports resolve
sys.path.insert(0, str(SKILL_SCRIPTS))

# Load score.py
_score_spec = importlib.util.spec_from_file_location("score", SKILL_SCRIPTS / "score.py")
_score = importlib.util.module_from_spec(_score_spec)
sys.modules["score"] = _score
_score_spec.loader.exec_module(_score)

# Load baseline + report first (run_suite imports them at module level)
for _name in ("baseline", "report"):
    _sp = importlib.util.spec_from_file_location(_name, SKILL_SCRIPTS / f"{_name}.py")
    _sm = importlib.util.module_from_spec(_sp)
    sys.modules[_name] = _sm
    _sp.loader.exec_module(_sm)

# Load run_suite.py
_suite_spec = importlib.util.spec_from_file_location("run_suite", SKILL_SCRIPTS / "run_suite.py")
_suite = importlib.util.module_from_spec(_suite_spec)
sys.modules["run_suite"] = _suite
_suite_spec.loader.exec_module(_suite)

_passed = 0
_failed = 0


def _check(label: str, condition: bool, detail: str = "") -> None:
    global _passed, _failed
    if condition:
        _passed += 1
    else:
        _failed += 1
        print(f"FAIL [{label}]{': ' + detail if detail else ''}", file=sys.stderr)


# ---------------------------------------------------------------------------
# score._coerce
# ---------------------------------------------------------------------------

def _test_coerce():
    c = _score._coerce

    # Valid accuracies pass through
    for acc in ("pass", "partial", "fail"):
        j = c({"accuracy": acc, "clarity": 3, "rationale": "ok"})
        _check(f"coerce-acc-{acc}", j.accuracy == acc)

    # Uppercase normalised to lowercase
    j = c({"accuracy": "PASS", "clarity": 3, "rationale": "ok"})
    _check("coerce-acc-upper", j.accuracy == "pass")

    # Unknown accuracy → "fail"
    j = c({"accuracy": "unknown", "clarity": 3, "rationale": "ok"})
    _check("coerce-acc-invalid", j.accuracy == "fail")

    # Missing accuracy → "fail"
    j = c({"clarity": 3, "rationale": "ok"})
    _check("coerce-acc-missing", j.accuracy == "fail")

    # Clarity clamped to [1,5] — over max
    j = c({"accuracy": "pass", "clarity": 10, "rationale": "ok"})
    _check("coerce-clarity-max", j.clarity == 5)

    # Clarity clamped to [1,5] — under min
    j = c({"accuracy": "pass", "clarity": 0, "rationale": "ok"})
    _check("coerce-clarity-min", j.clarity == 1)

    # Clarity None → 1
    j = c({"accuracy": "pass", "clarity": None, "rationale": "ok"})
    _check("coerce-clarity-none", j.clarity == 1)

    # Clarity missing → 1
    j = c({"accuracy": "pass", "rationale": "ok"})
    _check("coerce-clarity-missing", j.clarity == 1)

    # Clarity exactly 1 and 5 are in range
    j = c({"accuracy": "pass", "clarity": 1, "rationale": "ok"})
    _check("coerce-clarity-1", j.clarity == 1)
    j = c({"accuracy": "pass", "clarity": 5, "rationale": "ok"})
    _check("coerce-clarity-5", j.clarity == 5)

    # Rationale stripped
    j = c({"accuracy": "pass", "clarity": 3, "rationale": "  lots of whitespace  "})
    _check("coerce-rationale-strip", j.rationale == "lots of whitespace")

    # Rationale missing → empty string
    j = c({"accuracy": "pass", "clarity": 3})
    _check("coerce-rationale-missing", j.rationale == "")

    # rubric_version always JUDGE_RUBRIC_VERSION (1)
    j = c({"accuracy": "pass", "clarity": 3, "rationale": "ok"})
    _check("coerce-rubric-version", j.rubric_version == _score.JUDGE_RUBRIC_VERSION)

    # is_fail() correct
    j_fail = c({"accuracy": "fail", "clarity": 3, "rationale": "bad"})
    _check("coerce-is-fail-true", j_fail.is_fail() is True)
    j_pass = c({"accuracy": "pass", "clarity": 3, "rationale": "good"})
    _check("coerce-is-fail-false", j_pass.is_fail() is False)
    j_partial = c({"accuracy": "partial", "clarity": 3, "rationale": "meh"})
    _check("coerce-is-fail-partial", j_partial.is_fail() is False)


_test_coerce()


# ---------------------------------------------------------------------------
# score.to_dict
# ---------------------------------------------------------------------------

def _test_to_dict():
    j = _score.Judgement(accuracy="pass", clarity=4, rationale="nice", rubric_version=1)
    d = _score.to_dict(j)
    _check("to_dict-accuracy",       d["accuracy"] == "pass")
    _check("to_dict-clarity",        d["clarity"] == 4)
    _check("to_dict-rationale",      d["rationale"] == "nice")
    _check("to_dict-rubric_version", d["rubric_version"] == 1)
    _check("to_dict-keys",           set(d.keys()) == {"accuracy", "clarity", "rationale", "rubric_version"})


_test_to_dict()


# ---------------------------------------------------------------------------
# run_suite._row
# ---------------------------------------------------------------------------

def _test_row():
    r = _suite._row

    # Default no_response + transcript
    row = r({"id": "T01", "category": "basic", "soft": False}, 300.0, "pass", 4, "good")
    _check("row-id",          row["id"] == "T01")
    _check("row-category",    row["category"] == "basic")
    _check("row-soft",        row["soft"] is False)
    _check("row-latency",     row["latency_ms"] == 300.0)
    _check("row-accuracy",    row["accuracy"] == "pass")
    _check("row-clarity",     row["clarity"] == 4)
    _check("row-rationale",   row["rationale"] == "good")
    _check("row-no-response", row["no_response"] is False)
    _check("row-transcript",  row["transcript"] == "")

    # With no_response=True and transcript
    row2 = r({"id": "T02", "category": "timer", "soft": True},
             1500.0, "fail", 1, "silent", no_response=True, transcript="...")
    _check("row-no-resp-true",  row2["no_response"] is True)
    _check("row-transcript-set", row2["transcript"] == "...")
    _check("row-soft-true",     row2["soft"] is True)

    # Missing category → None
    row3 = r({"id": "T03"}, 100.0, "partial", 3, "ok")
    _check("row-category-none", row3["category"] is None)


_test_row()


# ---------------------------------------------------------------------------
# run_suite.precondition_gate
# ---------------------------------------------------------------------------

def _test_precondition_gate():
    ok, msg = _suite.precondition_gate(dry=True)
    _check("pgate-dry-ok",  ok is True)
    _check("pgate-dry-msg", msg == "dry-run: gate skipped")


_test_precondition_gate()


# ---------------------------------------------------------------------------
# run_suite._run_one_dry
# ---------------------------------------------------------------------------

def _test_run_one_dry():
    d = _suite._run_one_dry

    # Known id — "liveness"
    row = d({"id": "liveness", "category": "basic", "soft": False})
    _check("dry-liveness-acc",     row["accuracy"] == "pass")
    _check("dry-liveness-lat",     row["latency_ms"] == 540.0)
    _check("dry-liveness-clarity", row["clarity"] >= 1)

    # Known id — "arithmetic"
    row2 = d({"id": "arithmetic", "category": "basic", "soft": False})
    _check("dry-arith-acc", row2["accuracy"] == "pass")
    _check("dry-arith-lat", row2["latency_ms"] == 690.0)

    # Unknown id → fallback canned reply
    row3 = d({"id": "unknown-xyz", "category": "custom"})
    _check("dry-fallback-acc",      row3["accuracy"] == "pass")
    _check("dry-fallback-lat",      row3["latency_ms"] == 1500.0)
    _check("dry-fallback-rationale", row3["rationale"] == "dry-run canned")

    # Clarity derived from confidence: conf * 5, clamped [1,5]
    # liveness has conf=0.97 → round(0.97*5)=5
    row4 = d({"id": "liveness"})
    _check("dry-clarity-high", row4["clarity"] == 5)

    # weather has conf=0.93 → round(0.93*5)=round(4.65)=5
    row5 = d({"id": "weather"})
    _check("dry-clarity-weather", row5["clarity"] == 5)

    # nonsense has conf=0.88 → round(0.88*5)=round(4.4)=4
    row6 = d({"id": "nonsense"})
    _check("dry-clarity-nonsense", row6["clarity"] == 4,
           f"got {row6['clarity']}")

    # Result always has required row keys
    for key in ("id", "category", "soft", "latency_ms", "accuracy",
                "clarity", "rationale", "transcript", "no_response"):
        _check(f"dry-key-{key}", key in row3, f"missing key {key!r}")


_test_run_one_dry()


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

total = _passed + _failed
print(
    f"voice-agent-test-harness-score-suite: {_passed}/{total} passed"
    f"{'' if _failed == 0 else f' — {_failed} FAILED'}"
)
sys.exit(0 if _failed == 0 else 1)
