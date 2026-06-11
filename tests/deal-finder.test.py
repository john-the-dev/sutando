#!/usr/bin/env python3
"""Tests for skills/deal-finder/scripts/scan.py — pure helper functions.

Covers:
  a) parse_price()       — currency string → int or None
  b) extract_chip()      — highest M-series chip (M1–M9)
  c) extract_ram_gb()    — RAM in GB (requires explicit ram/memory context)
  d) extract_storage_gb() — storage in GB (SSD regex + TB conversion)
  e) passes()            — listing filter against criteria dict
  f) format_message()    — notification message rendering

Run: python3 tests/deal-finder.test.py
Exit: 0 on pass, 1 on fail.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "skills" / "deal-finder" / "scripts" / "scan.py"

spec = importlib.util.spec_from_file_location("deal_finder_scan", SCRIPT)
_mod = importlib.util.module_from_spec(spec)
sys.modules["deal_finder_scan"] = _mod
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


# ---------------------------------------------------------------------------
# (a) parse_price
# ---------------------------------------------------------------------------

def _test_parse_price():
    f = _mod.parse_price

    # Standard US price
    _check("pp-dollar",     f("$500") == 500)
    _check("pp-with-comma", f("$1,200") == 1200)

    # No currency symbol
    _check("pp-bare",       f("750") == 750)

    # Decimal stripped
    _check("pp-decimal",    f("$499.99") == 49999)  # all digits kept

    # Empty → None
    _check("pp-empty",      f("") is None)

    # None-like non-digit → None
    _check("pp-text-only",  f("Price on request") is None)

    # Mixed — digits extracted
    _check("pp-mixed",      f("about $600 OBO") == 600)


_test_parse_price()


# ---------------------------------------------------------------------------
# (b) extract_chip
# ---------------------------------------------------------------------------

def _test_extract_chip():
    f = _mod.extract_chip

    # Basic M2
    _check("ec-m2",         f("Mac mini M2 16GB") == "M2")

    # M3 Pro suffix
    _check("ec-m3-pro",     f("Mac mini M3 Pro 36GB") == "M3")

    # M1 Max suffix
    _check("ec-m1-max",     f("Apple M1 Max chip") == "M1")

    # Multiple chips → highest
    _check("ec-multi",      f("M1 or M2 available") == "M2")

    # M4 (latest)
    _check("ec-m4",         f("Mac mini M4 24GB") == "M4")

    # No chip → None
    _check("ec-none",       f("no chip mentioned") is None)

    # Empty → None
    _check("ec-empty",      f("") is None)

    # Model number not a chip: "M475" should not match
    _check("ec-reject-m475", f("Model M475 keyboard") is None)

    # Case insensitive
    _check("ec-lower",      f("mac mini m2 16gb ram") == "M2")


_test_extract_chip()


# ---------------------------------------------------------------------------
# (c) extract_ram_gb
# ---------------------------------------------------------------------------

def _test_extract_ram_gb():
    f = _mod.extract_ram_gb

    # Standard "16GB RAM"
    _check("er-16gb-ram",   f("16GB RAM") == 16)

    # "8 GB memory"
    _check("er-8gb-memory", f("8 GB memory") == 8)

    # "24GB unified memory"
    _check("er-unified",    f("24GB unified memory") == 24)

    # Without context word → NOT matched (Chi #648 regression guard)
    _check("er-no-context", f("256GB SSD") is None, "256GB SSD should not match as RAM")

    # Multiple RAM mentions → max
    _check("er-max",        f("8GB RAM or 16GB RAM") == 16)

    # Too small (< 4GB) → filtered out
    _check("er-too-small",  f("2 GB RAM") is None)

    # Empty → None
    _check("er-empty",      f("") is None)

    # Case insensitive
    _check("er-lower",      f("64gb ram") == 64)


_test_extract_ram_gb()


# ---------------------------------------------------------------------------
# (d) extract_storage_gb
# ---------------------------------------------------------------------------

def _test_extract_storage_gb():
    f = _mod.extract_storage_gb

    # Standard "512GB SSD"
    _check("es-512",        f("512GB SSD") == 512)

    # "256GB NVMe"
    _check("es-nvme",       f("256GB NVMe") == 256)

    # "1TB SSD" → 1024GB
    _check("es-1tb",        f("1TB SSD") == 1024)

    # "2TB storage" → 2048GB
    _check("es-2tb",        f("2TB storage") == 2048)

    # TB hint (TB_HINT) dominates
    _check("es-tb-hint",    f("2TB disk 256GB SSD") == 2048)

    # Minimum threshold (< 64GB) rejected
    _check("es-too-small",  f("32GB SSD") is None)

    # Empty → None
    _check("es-empty",      f("") is None)

    # "1TB" alone (TB hint)
    _check("es-1tb-bare",   f("1TB") == 1024)


_test_extract_storage_gb()


# ---------------------------------------------------------------------------
# (e) passes
# ---------------------------------------------------------------------------

CRITERIA = {
    "chips": ["m2", "m3", "m4"],
    "exclude_chips": [],
    "min_ram_gb": 16,
    "min_storage_gb": 256,
    "max_price_usd": 700,
    "soft_match_when_specs_missing": True,
}


def _listing(title, body="", price_int=None, url="https://example.com/1"):
    return {"title": title, "body": body, "price_int": price_int, "url": url}


def _test_passes():
    f = _mod.passes

    # Good listing → match
    ok, reason = f(CRITERIA, _listing("Mac mini M2 16GB RAM 512GB SSD", price_int=600))
    _check("pa-good-match",   ok, reason)
    _check("pa-good-reason",  reason == "match")

    # Title doesn't mention mac mini → reject
    ok, reason = f(CRITERIA, _listing("Mac Pro M2 16GB SSD"))
    _check("pa-no-mac-mini",  not ok)
    _check("pa-no-mini-reason", "mac mini" in reason)

    # Accessory listing → reject
    ok, reason = f(CRITERIA, _listing("Mac mini power cord", price_int=20))
    _check("pa-accessory",    not ok)
    _check("pa-acc-reason",   "accessory" in reason.lower())

    # Intel chip → reject
    ok, reason = f(CRITERIA, _listing("Mac mini i7-8700B 16GB 512GB SSD"))
    _check("pa-intel",        not ok)
    _check("pa-intel-reason", "intel" in reason.lower())

    # No chip detected → reject
    ok, reason = f(CRITERIA, _listing("Mac mini 16GB RAM 512GB SSD", price_int=500))
    _check("pa-no-chip",      not ok)
    _check("pa-no-chip-reason", "chip" in reason.lower())

    # Chip not in wanted set (M1 when criteria wants M2/M3/M4) → reject
    ok, reason = f(CRITERIA, _listing("Mac mini M1 16GB RAM 512GB SSD", price_int=400))
    _check("pa-wrong-chip",   not ok)

    # Excluded chip → reject
    crit_ex = dict(CRITERIA, exclude_chips=["m2"])
    ok, reason = f(crit_ex, _listing("Mac mini M2 16GB RAM 512GB SSD", price_int=500))
    _check("pa-excluded",     not ok)
    _check("pa-excluded-reason", "excluded" in reason)

    # RAM too low → reject
    ok, reason = f(CRITERIA, _listing("Mac mini M2 8GB RAM 512GB SSD", price_int=500))
    _check("pa-ram-low",      not ok)
    _check("pa-ram-reason",   "RAM" in reason or "ram" in reason.lower())

    # Storage too low → reject
    ok, reason = f(CRITERIA, _listing("Mac mini M2 16GB RAM 128GB SSD", price_int=500))
    _check("pa-storage-low",  not ok)

    # Price too high → reject
    ok, reason = f(CRITERIA, _listing("Mac mini M2 16GB RAM 512GB SSD", price_int=750))
    _check("pa-price-high",   not ok)
    _check("pa-price-reason", "price" in reason.lower() or "$" in reason)

    # Soft match when RAM missing (no context word) but soft_match=True
    ok, reason = f(CRITERIA, _listing("Mac mini M2 512GB SSD", price_int=500))
    _check("pa-soft-match",   ok, reason)
    _check("pa-soft-reason",  "soft" in reason)

    # chips list empty → any chip passes
    crit_any = dict(CRITERIA, chips=[])
    ok, reason = f(crit_any, _listing("Mac mini M1 16GB RAM 512GB SSD", price_int=400))
    _check("pa-any-chip",     ok, reason)


_test_passes()


# ---------------------------------------------------------------------------
# (f) format_message
# ---------------------------------------------------------------------------

def _test_format_message():
    f = _mod.format_message

    listing = {
        "title": "Mac mini M2 16GB",
        "price_str": "$650",
        "location": "San Jose",
        "url": "https://sfbay.craigslist.org/eby/sys/d/1234.html",
    }

    result = f(listing, "match", "2 hours ago")
    _check("fm-header",   "[Mac Mini Deal] $650 — Mac mini M2 16GB" in result)
    _check("fm-location", "San Jose" in result)
    _check("fm-age",      "2 hours ago" in result)
    _check("fm-url",      "1234.html" in result)
    # Normal "match" reason not shown as Note
    _check("fm-no-note",  "Note:" not in result)

    # Soft match shows Note
    result2 = f(listing, "soft match (specs missing — review manually)", "1 day ago")
    _check("fm-soft-note", "Note: soft match" in result2)

    # No location → Location line absent
    listing_no_loc = dict(listing, location="")
    result3 = f(listing_no_loc, "match", "just now")
    _check("fm-no-loc",   "Location:" not in result3)

    # Missing price_str → '?'
    listing_no_price = dict(listing, price_str=None)
    result4 = f(listing_no_price, "match", "now")
    _check("fm-no-price", "[Mac Mini Deal] ? —" in result4)


_test_format_message()


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

total = _passed + _failed
print(f"deal-finder: {_passed}/{total} passed"
      f"{'' if _failed == 0 else f' — {_failed} FAILED'}")
sys.exit(0 if _failed == 0 else 1)
