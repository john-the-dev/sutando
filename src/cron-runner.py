#!/usr/bin/env python3
"""OS-supervised cron runner — emits task files for due crons.json entries.

Why this exists
---------------
Sutando's recurring prompts (morning briefing, daily insight, the loop-eng
digest, etc.) were scheduled purely as in-session ``CronCreate`` jobs. Those
are best-effort: they only fire while the Claude REPL is idle at the fire
minute, they carry scheduler jitter, and they die with the session. On
2026-07-02 the 6:02 loop-engineering digest never delivered and the miss was
silent — the owner asked to "make the schedule reliably run".

This runner is the reliable path. It is invoked by launchd
(``com.sutando.cron-runner``) every 60s, independent of any Claude session.
Each tick it reads the per-host ``crons.json``, decides which entries are DUE
since their last recorded fire, and emits a task file into ``tasks/`` for each.
The streaming watcher hands that task to the running session, which executes
the prompt and delivers the result. Same OS-level → emit-task → process
pipeline the launchd health-check fallback already uses.

Ownership / no double-fire
--------------------------
Only entries explicitly flagged ``"launchd": true`` in crons.json are handled
here. The session ``/schedule-crons`` path skips those same entries, so exactly
one scheduler owns each cron — no duplicate deliveries. The session driver
(``main-loop`` / ``/proactive-loop``) is never launchd-owned; it drives the
session itself and is not a task.

Missed-fire recovery is bounded: if the machine was asleep/off across one or
more scheduled times, the entry fires exactly once on the next tick (catch-up),
never a backlog storm.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

# --- workspace + host resolution (mirror the rest of the codebase) ----------
# This file lives in src/, so its own directory IS the src/ dir — reach the
# sanctioned loader without walking up from __file__ (that anti-pattern is
# refused by scripts/lint-workspace-resolution.sh).
SRC_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SRC_DIR))

try:
    from workspace_default import resolve_workspace  # type: ignore
    WORKSPACE = Path(resolve_workspace())
except Exception:  # pragma: no cover — only fires outside a checkout (can't import workspace_default)
    # Defensive fallback for non-checkout installs — matches CLAUDE.md default.
    WORKSPACE = SRC_DIR.parent / "workspace"  # pragma: no cover


def host_slug() -> str:
    import socket
    return socket.gethostname().split(".")[0]


CRONS_FILE = WORKSPACE / "hosts" / host_slug() / "crons.json"
TASKS_DIR = WORKSPACE / "tasks"
STATE_FILE = WORKSPACE / "state" / "cron-runner-state.json"

# Look back at most this far when catching up a missed fire. Bounds work after
# long downtime and guarantees at most one catch-up emission per entry.
MAX_CATCHUP_SECONDS = 24 * 3600


# --- minimal 5-field cron matcher (no external deps) ------------------------
def _parse_field(field: str, lo: int, hi: int) -> set[int]:
    """Expand one cron field into the set of matching integers.

    Supports ``*``, ``*/N``, ``A``, ``A,B``, ``A-B``, and ``A-B/N`` — the full
    grammar used by Sutando's crons.json (e.g. ``*/5``, ``*/3`` day-of-month,
    ``1-5`` weekdays).
    """
    result: set[int] = set()
    for part in field.split(","):
        step = 1
        if "/" in part:
            part, step_s = part.split("/", 1)
            step = int(step_s)
        if part == "*":
            start, end = lo, hi
        elif "-" in part:
            start_s, end_s = part.split("-", 1)
            start, end = int(start_s), int(end_s)
        else:
            start = end = int(part)
        for v in range(start, end + 1, step):
            if lo <= v <= hi:
                result.add(v)
    return result


def cron_matches(expr: str, t: time.struct_time) -> bool:
    """True if the 5-field cron expression matches the given local time."""
    fields = expr.split()
    if len(fields) != 5:
        raise ValueError(f"cron expression must have 5 fields: {expr!r}")
    minute, hour, dom, month, dow = fields
    if t.tm_min not in _parse_field(minute, 0, 59):
        return False
    if t.tm_hour not in _parse_field(hour, 0, 23):
        return False
    if t.tm_mon not in _parse_field(month, 1, 12):
        return False
    # Standard cron DOM/DOW semantics: when both are restricted (not '*'), a
    # match on EITHER fires. tm_wday is Mon=0..Sun=6; cron uses Sun=0..Sat=6.
    cron_dow = (t.tm_wday + 1) % 7
    dom_restricted = dom != "*"
    dow_restricted = dow != "*"
    dom_ok = t.tm_mday in _parse_field(dom, 1, 31)
    dow_ok = cron_dow in _parse_field(dow, 0, 6)
    if dom_restricted and dow_restricted:
        return dom_ok or dow_ok
    return dom_ok and dow_ok


def due_since(expr: str, last_epoch: int, now_epoch: int) -> bool:
    """Did a fire-minute of ``expr`` occur in (last_epoch, now_epoch]?

    Iterates whole minutes across the window (bounded by MAX_CATCHUP_SECONDS)
    so a fire that landed while the machine was busy/asleep is still caught on
    the next tick.
    """
    window_start = max(last_epoch, now_epoch - MAX_CATCHUP_SECONDS)
    # Align to the next whole minute after window_start.
    m = (window_start // 60 + 1) * 60
    while m <= now_epoch:
        if cron_matches(expr, time.localtime(m)):
            return True
        m += 60
    return False


def _load_json(path: Path, default):
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def emit_task(name: str, entry: dict) -> Path:
    now_ms = int(time.time() * 1000)
    ts_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    # A launchd cron either carries a direct `prompt` or a `prompt_skill`
    # (invoked as a slash command), matching the schedule-crons contract.
    if entry.get("prompt_skill"):
        body_task = f"/{entry['prompt_skill']}"
    else:
        body_task = entry.get("prompt", "")
    task_id = f"task-cron-{name}-{now_ms}"
    body = (
        f"id: {task_id}\n"
        f"timestamp: {ts_iso}\n"
        f"task: {body_task}\n"
        f"source: cron\n"
        f"user_id: cron-runner\n"
        f"access_tier: owner\n"
        f"priority: low\n"
    )
    TASKS_DIR.mkdir(parents=True, exist_ok=True)
    path = TASKS_DIR / f"{task_id}.txt"
    path.write_text(body)
    return path


def run(now_epoch: int | None = None) -> list[str]:
    """One tick. Returns the list of cron names emitted this tick."""
    now_epoch = int(now_epoch if now_epoch is not None else time.time())
    crons = _load_json(CRONS_FILE, [])
    state = _load_json(STATE_FILE, {})
    emitted: list[str] = []

    for entry in crons:
        if not entry.get("launchd"):
            continue  # session-owned or not reliability-critical — skip
        name = entry.get("name")
        expr = entry.get("cron")
        if not name or not expr:
            continue
        last = int(state.get(name, now_epoch - 60))  # first run: only look back 1 tick
        try:
            if due_since(expr, last, now_epoch):
                emit_task(name, entry)
                emitted.append(name)
        except ValueError as e:
            print(f"cron-runner: skipping {name}: {e}", file=sys.stderr)
            continue
        state[name] = now_epoch

    if crons:  # only persist once we've actually read a config
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(state))
    return emitted


if __name__ == "__main__":
    names = run()
    if names:
        print("cron-runner emitted: " + ", ".join(names))
