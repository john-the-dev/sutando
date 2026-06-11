#!/usr/bin/env python3
"""Regression guard: _onset() and latency_ms() in
skills/voice-agent-test-harness/scripts/audio.py.

_onset(samples, onset_rms, win_ms=30, guard_ms=200, min_run=4, skip_samples=0):
  Returns (onset_idx, end_idx, peak_rms).
  onset_idx == -1 when no SUSTAINED energy crosses the threshold after guard.

  Key rules:
  - Computes per-window RMS over non-overlapping 30ms windows.
  - Blanks the first max(guard_ms/win_ms, skip_samples/win) windows.
  - Requires `min_run` consecutive loud windows to declare onset.
  - Returns peak_rms from the whole window array regardless of onset.

latency_ms(prompt, reply) -> float | None:
  None when reply.onset_at is None.
  Otherwise (reply.onset_at - prompt.ended_at) * 1000, rounded to 1 dp.

These functions are IO-free (no audio hardware, no files, numpy only).

Run: python3 tests/voice-agent-test-harness-audio.test.py
Exit: 0 on pass, 1 on fail.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

try:
    import numpy as np
    _HAS_NUMPY = True
except ImportError:
    _HAS_NUMPY = False

if not _HAS_NUMPY:
    print("voice-agent-test-harness-audio: SKIP (numpy not installed)")
    sys.exit(0)

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "skills" / "voice-agent-test-harness" / "scripts"

sys.path.insert(0, str(SCRIPTS))

spec = importlib.util.spec_from_file_location("audio", SCRIPTS / "audio.py")
_mod = importlib.util.module_from_spec(spec)
sys.modules["audio"] = _mod
spec.loader.exec_module(_mod)

SR: int = _mod.SR         # 16000 Hz
WIN: int = int(SR * 30 / 1000)  # 480 samples per 30ms window

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
# _onset — basic cases
# ---------------------------------------------------------------------------

def _test_onset_basic():
    f = _mod._onset

    # 1. All silence → no onset; peak is 0
    samples = np.zeros(SR, dtype=np.float32)
    o, e, peak = f(samples, onset_rms=0.02)
    _check("onset-silence-idx",  o == -1)
    _check("onset-silence-end",  e == -1)
    _check("onset-silence-peak", peak == 0.0)

    # 2. Empty array → no onset
    empty = np.zeros(0, dtype=np.float32)
    o, e, peak = f(empty, onset_rms=0.02)
    _check("onset-empty", o == -1)

    # 3. All loud from the start; guard_ms=0 → finds onset at window 0
    samples3 = np.full(SR * 2, 0.5, dtype=np.float32)
    o, e, peak = f(samples3, onset_rms=0.02, guard_ms=0, min_run=4)
    _check("onset-all-loud-found", o == 0)
    _check("onset-all-loud-peak",  abs(peak - 0.5) < 0.01)

    # 4. Burst too short (fewer than min_run windows) → no onset, peak returned
    samples4 = np.zeros(SR, dtype=np.float32)
    samples4[: 2 * WIN] = 0.5   # only 2 loud windows (< min_run=4)
    o, e, peak = f(samples4, onset_rms=0.02, guard_ms=0, min_run=4)
    _check("onset-short-burst-idx",  o == -1)
    _check("onset-short-burst-peak", abs(peak - 0.5) < 0.01)

    # 5. Burst of exactly min_run windows → onset found
    samples5 = np.zeros(SR * 2, dtype=np.float32)
    samples5[: 4 * WIN] = 0.5
    o, e, peak = f(samples5, onset_rms=0.02, guard_ms=0, min_run=4)
    _check("onset-exact-min-run", o == 0)

    # 6. Burst below threshold → no onset
    samples6 = np.full(SR * 2, 0.005, dtype=np.float32)
    o, e, peak = f(samples6, onset_rms=0.02, guard_ms=0, min_run=1)
    _check("onset-below-thresh", o == -1)


_test_onset_basic()


# ---------------------------------------------------------------------------
# _onset — guard window
# ---------------------------------------------------------------------------

def _test_onset_guard():
    f = _mod._onset

    # guard_ms=200 → blank first int(200/30)=6 windows (2880 samples)
    # Burst starting at window 0 (0..5*WIN) is partially in guard
    # but windows that extend past guard are still detected if min_run satisfied

    # 1. Burst is 5 windows starting from 0; guard covers first 6 → only window 5+
    # is available, and window 5 is the only loud one past guard → not enough for min_run=4
    samples = np.zeros(SR * 2, dtype=np.float32)
    samples[: 5 * WIN] = 0.5   # windows 0-4 are loud; guard blanks windows 0-5
    o, e, peak = f(samples, onset_rms=0.02, guard_ms=200, min_run=4)
    _check("onset-guard-partial-miss", o == -1,
           f"expected no onset (burst before guard end), got o={o}")

    # 2. Burst starts well after guard: at 500ms (window 16)
    samples2 = np.zeros(SR * 2, dtype=np.float32)
    burst_w = 16
    samples2[burst_w * WIN : (burst_w + 8) * WIN] = 0.5
    o, e, peak = f(samples2, onset_rms=0.02, guard_ms=200, min_run=4)
    _check("onset-after-guard", o != -1, f"expected onset after guard, got o={o}")
    _check("onset-after-guard-pos", o == burst_w * WIN,
           f"expected onset at {burst_w*WIN}, got {o}")


_test_onset_guard()


# ---------------------------------------------------------------------------
# _onset — skip_samples
# ---------------------------------------------------------------------------

def _test_onset_skip():
    f = _mod._onset

    # skip_samples = 4*WIN → masks first 4 windows
    # Burst at windows 0-7; skip masks windows 0-3; windows 4-7 still loud → onset at window 4
    samples = np.zeros(SR * 2, dtype=np.float32)
    samples[: 8 * WIN] = 0.5
    o, e, peak = f(samples, onset_rms=0.02, guard_ms=0, skip_samples=4 * WIN, min_run=4)
    _check("onset-skip-found",    o != -1, f"expected onset past skip, got o={o}")
    _check("onset-skip-position", o == 4 * WIN, f"got o={o}")

    # skip_samples covers the entire burst → no onset
    samples2 = np.zeros(SR * 2, dtype=np.float32)
    samples2[: 4 * WIN] = 0.5
    o, e, peak = f(samples2, onset_rms=0.02, guard_ms=0, skip_samples=8 * WIN, min_run=4)
    _check("onset-skip-full", o == -1, f"expected no onset, got o={o}")


_test_onset_skip()


# ---------------------------------------------------------------------------
# _onset — end_idx reflects actual end of loud run
# ---------------------------------------------------------------------------

def _test_onset_end():
    f = _mod._onset

    # Loud burst from window 0 to window 7 (8 windows); end should be at window 8
    samples = np.zeros(SR * 2, dtype=np.float32)
    samples[: 8 * WIN] = 0.5
    o, e, peak = f(samples, onset_rms=0.02, guard_ms=0, min_run=4)
    _check("onset-end-idx", e == 8 * WIN,
           f"expected end {8*WIN}, got {e}")


_test_onset_end()


# ---------------------------------------------------------------------------
# _onset — peak_rms always reflects max RMS window
# ---------------------------------------------------------------------------

def _test_onset_peak():
    f = _mod._onset

    # Two sections: loud=0.5 then louder=0.8 — peak should be ~0.8
    samples = np.zeros(SR * 2, dtype=np.float32)
    samples[: 4 * WIN] = 0.5
    samples[4 * WIN : 8 * WIN] = 0.8
    _, _, peak = f(samples, onset_rms=0.02, guard_ms=0, min_run=4)
    _check("onset-peak-max", abs(peak - 0.8) < 0.02, f"got peak={peak}")


_test_onset_peak()


# ---------------------------------------------------------------------------
# latency_ms
# ---------------------------------------------------------------------------

def _test_latency_ms():
    lm = _mod.latency_ms
    Prompt = _mod.SpokenPrompt
    Reply = _mod.CapturedReply

    # onset_at=None → None
    p = Prompt(text="hello", started_at=1000.0, ended_at=1002.0)
    r_none = Reply(onset_at=None, ended_at=None, wav_path="", peak_rms=0.0)
    _check("lat-none", lm(p, r_none) is None)

    # Normal positive latency: onset at 1002.5 → 500ms
    r_pos = Reply(onset_at=1002.5, ended_at=1003.0, wav_path="", peak_rms=0.1)
    result = lm(p, r_pos)
    _check("lat-positive",    result is not None)
    _check("lat-positive-val", abs(result - 500.0) < 0.1, f"got {result}")

    # Rounded to 1 decimal place
    r_round = Reply(onset_at=1002.3333, ended_at=None, wav_path="", peak_rms=0.0)
    result2 = lm(p, r_round)
    _check("lat-round", result2 == round((1002.3333 - 1002.0) * 1000.0, 1),
           f"got {result2}")

    # Negative latency (onset before prompt.ended_at) — allowed, returned as-is
    r_neg = Reply(onset_at=1001.5, ended_at=None, wav_path="", peak_rms=0.0)
    result3 = lm(p, r_neg)
    _check("lat-negative", result3 is not None)
    _check("lat-negative-val", abs(result3 - (-500.0)) < 0.1, f"got {result3}")


_test_latency_ms()


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

total = _passed + _failed
print(
    f"voice-agent-test-harness-audio: {_passed}/{total} passed"
    f"{'' if _failed == 0 else f' — {_failed} FAILED'}"
)
sys.exit(0 if _failed == 0 else 1)
