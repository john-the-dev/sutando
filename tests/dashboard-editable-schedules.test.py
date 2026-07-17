#!/usr/bin/env python3
"""Editable schedules: crons.json read/write/validate + merge-by-name (owner ask).

The dashboard now edits this host's crons.json from the UI (add / edit cron /
delete). These guard the backend that the POST/DELETE handlers call: validation
(cron shape, mutually-exclusive prompt/prompt_skill), atomic round-trip, and the
merge-onto-existing-by-name path an inline cron-only edit relies on.

Run: python3 tests/dashboard-editable-schedules.test.py   (exit 0/1)
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("dashboard_es", REPO / "src" / "dashboard.py")
dash = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dash)

# Exercise the REAL _crons_path() once, then redirect to a temp file (never
# touch the real per-host cron set).
_real_crons_path = dash._crons_path()
_tmp = Path(tempfile.mkdtemp(prefix="dash-es-")) / "crons.json"
dash._crons_path = lambda: _tmp

failures: list[str] = []


def check(name, cond, detail=""):
    print(("  ok  " if cond else "  FAIL ") + name + ((" — " + detail) if detail and not cond else ""))
    if not cond:
        failures.append(name)


# ── _html_attr + real _crons_path ─────────────────────────────────────────────
check("_html_attr escapes quote/angle/amp",
      dash._html_attr('a"<b>&') == "a&quot;&lt;b&gt;&amp;")
check("_crons_path ends with hosts/<host>/crons.json",
      str(_real_crons_path).endswith("crons.json") and "hosts" in str(_real_crons_path))

# ── validation ────────────────────────────────────────────────────────────────
check("valid job passes", dash._validate_job(
    {"name": "x", "cron": "*/10 * * * *", "prompt_skill": "morning-briefing"}) is None)
check("missing name rejected", dash._validate_job({"name": "", "cron": "* * * * *", "prompt": "y"}))
check("bad cron (4 fields) rejected", dash._validate_job({"name": "x", "cron": "* * * *", "prompt": "y"}))
check("both prompt+skill rejected", dash._validate_job(
    {"name": "x", "cron": "* * * * *", "prompt": "y", "prompt_skill": "z"}))
check("neither prompt nor skill rejected", dash._validate_job({"name": "x", "cron": "* * * * *"}))

# ── atomic round-trip ─────────────────────────────────────────────────────────
dash._write_crons([{"name": "a", "cron": "0 9 * * *", "prompt_skill": "morning-briefing"}])
check("write+read round-trips", dash._read_crons()[0]["name"] == "a")
check("write is atomic (no leftover tmp)", not _tmp.with_suffix(".json.tmp").exists())
check("read of missing file → []", (lambda: (_tmp.unlink(), dash._read_crons() == [])[1])())

# ── merge-by-name (simulate the POST handler's merge logic) ───────────────────
def _upsert(jobs, body):
    """Mirror do_POST's merge — cron-only edit inherits existing fields."""
    name = body["name"].strip()
    existing = next((j for j in jobs if j.get("name") == name), None)
    merged = dict(existing) if existing else {}
    merged["name"] = name
    for k in ("cron", "prompt", "prompt_skill", "description"):
        if k in body and str(body.get(k)).strip():
            merged[k] = str(body[k]).strip()
    if (body.get("prompt_skill") or "").strip():
        merged.pop("prompt", None)
    elif (body.get("prompt") or "").strip():
        merged.pop("prompt_skill", None)
    assert dash._validate_job(merged) is None, dash._validate_job(merged)
    jobs = [j for j in jobs if j.get("name") != name]
    jobs.append(merged)
    return jobs


jobs = [{"name": "briefing", "cron": "57 6 * * *", "prompt_skill": "morning-briefing"}]
# inline cron-only edit — must inherit prompt_skill, not fail validation
jobs = _upsert(jobs, {"name": "briefing", "cron": "30 7 * * *"})
j = next(x for x in jobs if x["name"] == "briefing")
check("cron-only edit updates cron", j["cron"] == "30 7 * * *")
check("cron-only edit preserves prompt_skill", j.get("prompt_skill") == "morning-briefing")
check("replace-by-name (no duplicate)", len([x for x in jobs if x["name"] == "briefing"]) == 1)
# switching type: supplying prompt drops prompt_skill
jobs = _upsert(jobs, {"name": "briefing", "cron": "30 7 * * *", "prompt": "Run: echo hi"})
j = next(x for x in jobs if x["name"] == "briefing")
check("switching to prompt drops prompt_skill", "prompt_skill" not in j and j.get("prompt") == "Run: echo hi")

# ── pure upsert_schedule / delete_schedule (what the HTTP handlers call) ───────
dash._write_crons([])
code, obj = dash.upsert_schedule({"name": "n1", "cron": "*/5 * * * *", "prompt_skill": "morning-briefing"})
check("upsert add → 200", code == 200 and obj.get("ok"))
check("upsert persisted the job", dash._read_crons()[0]["name"] == "n1")
code, obj = dash.upsert_schedule({"name": "n1", "cron": "0 9 * * *"})  # cron-only edit
check("upsert cron-only edit → 200 (inherits skill)", code == 200)
check("edit kept prompt_skill", dash._read_crons()[0].get("prompt_skill") == "morning-briefing")
code, obj = dash.upsert_schedule({"name": "", "cron": "* * * * *", "prompt": "x"})
check("upsert missing name → 400", code == 400)
code, obj = dash.upsert_schedule({"name": "bad", "cron": "nope", "prompt": "x"})
check("upsert bad cron → 400", code == 400)
code, obj = dash.upsert_schedule("not a dict")
check("upsert non-dict → 400", code == 400)
code, obj = dash.delete_schedule("n1")
check("delete existing → 200", code == 200 and obj.get("deleted") == "n1")
code, obj = dash.delete_schedule("ghost")
check("delete missing → 404", code == 404)

print()
if failures:
    print(f"FAIL — {len(failures)}: {failures}")
    sys.exit(1)
print("PASS — editable schedules backend")
