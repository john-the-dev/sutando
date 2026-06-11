#!/usr/bin/env python3
"""Regression guard: _update_burn_rate() in skills/quota-tracker/scripts/read-quota.py.

_update_burn_rate(current_util_5h):
  Loads burn history, computes EWMA of per-pass utilization delta, saves back.
  Returns a dict with burn metrics when >= 2 samples exist, else None.

  Invariants tested:
    1. No history → None (first call ever)
    2. First valid sample → None (need >= 2 samples before reporting)
    3. Second valid sample → returns dict with correct fields + values
    4. Gap < 120 s → sample skipped (double-read guard)
    5. Gap > 7200 s → sample skipped (stale gap)
    6. Negative delta (reset / utilization dropped) → sample skipped
    7. Zero delta with valid gap → accepted (zero-burn case)
    8. samples capped at 99 regardless of call count
    9. Per-pass normalisation: delta * (300 / gap)

Run: python3 tests/quota-burn-rate-update.test.py
Exit: 0 on pass, 1 on fail.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SKILL_SCRIPTS = REPO / "skills" / "quota-tracker" / "scripts"

# ── bootstrap: create a real quota-state.json so the module doesn't exit(1) ──
_tmp_ws = tempfile.mkdtemp(prefix="quota-test-")
_state_dir = Path(_tmp_ws) / "state"
_state_dir.mkdir(parents=True)
_quota_file = _state_dir / "quota-state.json"
_quota_file.write_text(json.dumps({
    "headers": {
        "anthropic-ratelimit-unified-status": "allowed",
        "anthropic-ratelimit-unified-5h-utilization": "0.5",
        "anthropic-ratelimit-unified-7d-utilization": "0.3",
    }
}))
os.environ["SUTANDO_WORKSPACE"] = _tmp_ws

spec = importlib.util.spec_from_file_location(
    "read_quota", SKILL_SCRIPTS / "read-quota.py"
)
_mod = importlib.util.module_from_spec(spec)
sys.modules["read_quota"] = _mod
spec.loader.exec_module(_mod)

del os.environ["SUTANDO_WORKSPACE"]

# ── in-memory history store — monkey-patch load/save so tests are filesystem-free ──
_history_store: dict = {}


def _fake_load() -> dict:
    return dict(_history_store)


def _fake_save(h: dict) -> None:
    _history_store.clear()
    _history_store.update(h)


_mod._load_burn_history = _fake_load
_mod._save_burn_history = _fake_save

# ── test harness ──────────────────────────────────────────────────────────────
_passed = 0
_failed = 0


def _check(label: str, condition: bool, detail: str = "") -> None:
    global _passed, _failed
    if condition:
        _passed += 1
    else:
        _failed += 1
        print(f"FAIL [{label}]{': ' + detail if detail else ''}", file=sys.stderr)


def _reset(history: dict | None = None) -> None:
    """Reset the in-memory store to a given state (default: empty)."""
    _history_store.clear()
    if history:
        _history_store.update(history)


# ---------------------------------------------------------------------------
# Helper: build a pre-seeded history with a last read N seconds ago
# ---------------------------------------------------------------------------

def _seed(last_util: float, gap_s: float, ewma: float | None = None, samples: int = 0) -> dict:
    h: dict = {
        "last_read_ts": time.time() - gap_s,
        "last_util_5h": last_util,
        "burn_samples": samples,
        "schema_version": 1,
    }
    if ewma is not None:
        h["burn_rate_5h_ewma"] = ewma
    return h


# ---------------------------------------------------------------------------
# Test 1: No history → None
# ---------------------------------------------------------------------------

_reset()
result = _mod._update_burn_rate(0.50)
_check("br-no-history-none", result is None)
# But history is saved with last_read_ts and last_util
_check("br-no-history-saves-ts",   "last_read_ts" in _history_store)
_check("br-no-history-saves-util", _history_store.get("last_util_5h") == 0.50)


# ---------------------------------------------------------------------------
# Test 2: First valid sample → None (need >= 2 samples)
# ---------------------------------------------------------------------------

_reset(_seed(last_util=0.40, gap_s=300))  # 5-min gap, delta = current - 0.40
result2 = _mod._update_burn_rate(0.42)    # delta = 0.02, per_pass = 0.02
_check("br-first-sample-none", result2 is None)
# ewma is set to first sample value
_check("br-first-sample-ewma",    _history_store.get("burn_rate_5h_ewma") is not None)
_check("br-first-sample-count",   _history_store.get("burn_samples") == 1)


# ---------------------------------------------------------------------------
# Test 3: Second valid sample → returns dict with correct structure
# ---------------------------------------------------------------------------

_reset(_seed(last_util=0.40, gap_s=300, ewma=0.02, samples=1))
result3 = _mod._update_burn_rate(0.42)
_check("br-second-returns-dict", isinstance(result3, dict))
_check("br-second-field-rate",   "burn_rate_pct_per_pass" in (result3 or {}))
_check("br-second-field-samples","burn_samples" in (result3 or {}))
_check("br-second-field-passes", "estimated_passes_left" in (result3 or {}))
_check("br-second-field-minutes","estimated_minutes_left" in (result3 or {}))
_check("br-second-samples-2",    (result3 or {}).get("burn_samples") == 2)
_check("br-second-rate-positive",(result3 or {}).get("burn_rate_pct_per_pass", 0) > 0)


# ---------------------------------------------------------------------------
# Test 4: Gap < 120 s → sample skipped (double-read guard)
# ---------------------------------------------------------------------------

pre_ewma = 0.05
_reset(_seed(last_util=0.40, gap_s=60, ewma=pre_ewma, samples=2))  # 60s gap < 120s
result4 = _mod._update_burn_rate(0.50)
# ewma should be unchanged in stored history
_check("br-gap-small-skip", _history_store.get("burn_rate_5h_ewma") == pre_ewma)
# Still returns None if ewma hasn't changed
_check("br-gap-small-result-type", result4 is None or isinstance(result4, dict))


# ---------------------------------------------------------------------------
# Test 5: Gap > 7200 s → sample skipped (stale gap guard)
# ---------------------------------------------------------------------------

_reset(_seed(last_util=0.40, gap_s=8000, ewma=pre_ewma, samples=2))  # 8000s > 7200s
result5 = _mod._update_burn_rate(0.50)
_check("br-gap-large-skip", _history_store.get("burn_rate_5h_ewma") == pre_ewma)


# ---------------------------------------------------------------------------
# Test 6: Negative delta (utilization dropped → reset) → sample skipped
# ---------------------------------------------------------------------------

_reset(_seed(last_util=0.80, gap_s=300, ewma=pre_ewma, samples=2))
result6 = _mod._update_burn_rate(0.20)  # delta = -0.60
_check("br-negative-delta-skip", _history_store.get("burn_rate_5h_ewma") == pre_ewma)
# last_util updated to new value even on skip
_check("br-negative-delta-util-updated", abs(_history_store.get("last_util_5h", 0) - 0.20) < 1e-9)


# ---------------------------------------------------------------------------
# Test 7: Zero delta with valid gap → accepted, ewma updated toward 0
# ---------------------------------------------------------------------------

_reset(_seed(last_util=0.50, gap_s=300, ewma=0.05, samples=2))
result7 = _mod._update_burn_rate(0.50)  # delta = 0.0, per_pass = 0.0
# EWMA should move toward 0: new = 0.3 * 0 + 0.7 * 0.05 = 0.035
new_ewma = _history_store.get("burn_rate_5h_ewma", -1)
_check("br-zero-delta-ewma-decreases", abs(new_ewma - 0.035) < 1e-9, f"ewma={new_ewma}")


# ---------------------------------------------------------------------------
# Test 8: Samples capped at 99
# ---------------------------------------------------------------------------

_reset(_seed(last_util=0.40, gap_s=300, ewma=0.02, samples=99))
_mod._update_burn_rate(0.42)
_check("br-samples-capped", _history_store.get("burn_samples") == 99)


# ---------------------------------------------------------------------------
# Test 9: Per-pass normalisation — delta * (300 / gap)
# ---------------------------------------------------------------------------

# gap = 600 s (2x normal), delta = 0.02 → per_pass = 0.02 * (300/600) = 0.01
# ewma starts at None → ewma = 0.01 (first sample)
_reset(_seed(last_util=0.40, gap_s=600))  # no ewma yet → first sample
_mod._update_burn_rate(0.42)
ewma_600 = _history_store.get("burn_rate_5h_ewma")
_check("br-normalise-per-pass", abs(ewma_600 - 0.01) < 1e-9, f"ewma={ewma_600}")

# Compare: same delta but gap = 300 s → per_pass = 0.02 → ewma = 0.02
_reset(_seed(last_util=0.40, gap_s=300))
_mod._update_burn_rate(0.42)
ewma_300 = _history_store.get("burn_rate_5h_ewma")
_check("br-normalise-gap-matters", abs(ewma_300 - 0.02) < 1e-9, f"ewma={ewma_300}")


# ---------------------------------------------------------------------------
# Test 10: Estimated passes left calculation
# ---------------------------------------------------------------------------

# With ewma=0.01 and current_util=0.40 → delta=0, per_pass=0
# → ewma_final = 0.3*0 + 0.7*0.01 = 0.007, remaining=(1-0.40)*100=60%
# → passes_left = 60 / (0.007*100) = 60/0.7 ≈ 85.7
_reset(_seed(last_util=0.40, gap_s=300, ewma=0.01, samples=2))
result10 = _mod._update_burn_rate(0.40)
if result10 is not None:
    expected_passes = round(60.0 / 0.7, 1)  # ≈ 85.7
    _check("br-passes-left",
           abs(result10["estimated_passes_left"] - expected_passes) < 1.0,
           f"got {result10['estimated_passes_left']}, expected ~{expected_passes}")
    # minutes_left is round(passes_left * 5) on the unrounded passes value
    _check("br-minutes-left",
           abs(result10["estimated_minutes_left"] - result10["estimated_passes_left"] * 5) < 3,
           f"minutes={result10['estimated_minutes_left']}, passes={result10['estimated_passes_left']}")
else:
    _check("br-passes-left", False, "result10 is None unexpectedly")


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

total = _passed + _failed
print(
    f"quota-burn-rate-update: {_passed}/{total} passed"
    f"{'' if _failed == 0 else f' — {_failed} FAILED'}"
)
sys.exit(0 if _failed == 0 else 1)
