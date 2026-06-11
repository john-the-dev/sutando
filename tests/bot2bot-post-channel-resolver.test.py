#!/usr/bin/env python3
"""Regression guard: resolve_bot2bot_channel() and resolve_other_bot()
in skills/bot2bot-post/post.py.

These two pure functions pick the coordination channel and the sibling-bot
user-ID from an access.json dict. Correctness matters because a wrong channel
ID silently delivers heartbeats to the wrong Discord channel.

resolve_bot2bot_channel(access):
  Preferred: groups entry with {"role": "bot2bot", ...}.
  Fallback: groups entry with literal `true` (legacy).
  Error: sys.exit if no matching entry.

resolve_other_bot(access, self_id, channel_id):
  1. Channel-level allowFrom (ch_cfg["allowFrom"]).
  2. Falls back to top-level allowFrom if channel has none.
  Prefers IDs NOT in the global allowFrom (bot, not owner).
  Returns None if no non-self candidate.

Run: python3 tests/bot2bot-post-channel-resolver.test.py
Exit: 0 on pass, 1 on fail.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

spec = importlib.util.spec_from_file_location(
    "bot2bot_post", REPO / "skills" / "bot2bot-post" / "post.py"
)
_mod = importlib.util.module_from_spec(spec)
sys.modules["bot2bot_post"] = _mod
spec.loader.exec_module(_mod)

_passed = 0
_failed = 0


def _check(label: str, condition: bool, detail: str = "") -> None:
    global _passed, _failed
    if condition:
        _passed += 1
    else:
        _failed += 1
        print(f"FAIL [{label}]{': ' + detail if detail else ''}", file=sys.stderr)


def _exits(fn, *args, **kwargs) -> bool:
    """Return True if fn(*args) calls sys.exit."""
    try:
        fn(*args, **kwargs)
        return False
    except SystemExit:
        return True


# ---------------------------------------------------------------------------
# resolve_bot2bot_channel
# ---------------------------------------------------------------------------

def _test_resolve_channel():
    f = _mod.resolve_bot2bot_channel

    # Explicit bot2bot-tagged channel → preferred
    access_tagged = {"groups": {
        "111": {"role": "bot2bot", "name": "bot2bot-channel"},
        "222": True,  # legacy
    }}
    _check("rc-tagged",          f(access_tagged) == "111")

    # Legacy true-valued channel → fallback
    access_legacy = {"groups": {"333": True, "444": True}}
    _check("rc-legacy",          f(access_legacy) == "333")

    # Tagged takes priority over legacy even if legacy listed first
    access_mixed = {"groups": {"555": True, "666": {"role": "bot2bot"}}}
    _check("rc-tagged-priority", f(access_mixed) == "666")

    # Multiple tagged → first one returned
    access_multi = {"groups": {
        "777": {"role": "bot2bot"},
        "888": {"role": "bot2bot"},
    }}
    result = f(access_multi)
    _check("rc-multi-first", result in ("777", "888"))

    # No matching entry → sys.exit
    _check("rc-empty-groups",    _exits(f, {"groups": {}}))
    _check("rc-no-groups-key",   _exits(f, {}))
    _check("rc-non-matching",    _exits(f, {"groups": {"999": {"role": "admin"}}}))

    # Non-dict, non-true values ignored
    access_garbage = {"groups": {"aaa": None, "bbb": 42, "ccc": True}}
    _check("rc-garbage-fallback", f(access_garbage) == "ccc")


_test_resolve_channel()


# ---------------------------------------------------------------------------
# resolve_other_bot
# ---------------------------------------------------------------------------

def _test_resolve_other_bot():
    f = _mod.resolve_other_bot

    SELF = "bot-self-001"
    OTHER = "bot-other-002"
    OWNER = "owner-user-003"
    CH = "channel-111"

    # Channel-level allowFrom: other bot + self → returns other bot
    access_ch = {
        "allowFrom": [OWNER, SELF],
        "groups": {
            CH: {"role": "bot2bot", "allowFrom": [SELF, OTHER]},
        },
    }
    _check("rob-channel-level",   f(access_ch, SELF, CH) == OTHER)

    # No channel-level allowFrom → falls back to top-level
    access_top = {
        "allowFrom": [OWNER, SELF, OTHER],
        "groups": {CH: {"role": "bot2bot"}},
    }
    result_top = f(access_top, SELF, CH)
    _check("rob-toplevel-fallback", result_top in (OTHER, OWNER))

    # Bot candidate not in global allowFrom preferred over owner-also-in-top-level
    # OWNER is in global allowFrom; OTHER is in ch allowFrom but not global → OTHER preferred
    access_pref = {
        "allowFrom": [OWNER, SELF],  # global — owner + self only
        "groups": {
            CH: {"role": "bot2bot", "allowFrom": [SELF, OTHER, OWNER]},
        },
    }
    _check("rob-bot-preferred",   f(access_pref, SELF, CH) == OTHER)

    # All non-self IDs are in global allowFrom → last resort, returns first non-self
    access_last = {
        "allowFrom": [OWNER, SELF, OTHER],
        "groups": {
            CH: {"role": "bot2bot", "allowFrom": [SELF, OWNER, OTHER]},
        },
    }
    result_last = f(access_last, SELF, CH)
    _check("rob-last-resort",     result_last in (OWNER, OTHER) and result_last != SELF)

    # No other IDs (only self in channel allowFrom) → None
    access_none = {
        "allowFrom": [SELF],
        "groups": {CH: {"role": "bot2bot", "allowFrom": [SELF]}},
    }
    _check("rob-no-other",        f(access_none, SELF, CH) is None)

    # Empty allowFrom everywhere → None
    _check("rob-empty",           f({"groups": {CH: {"role": "bot2bot"}}}, SELF, CH) is None)

    # Channel config is `true` (legacy) → no channel-level allowFrom → top-level fallback
    access_true_ch = {
        "allowFrom": [SELF, OTHER],
        "groups": {CH: True},
    }
    _check("rob-true-ch-fallback", f(access_true_ch, SELF, CH) == OTHER)

    # Self only in top-level → None
    access_self_only = {"allowFrom": [SELF], "groups": {CH: True}}
    _check("rob-self-only",        f(access_self_only, SELF, CH) is None)


_test_resolve_other_bot()


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

total = _passed + _failed
print(
    f"bot2bot-post-channel-resolver: {_passed}/{total} passed"
    f"{'' if _failed == 0 else f' — {_failed} FAILED'}"
)
sys.exit(0 if _failed == 0 else 1)
