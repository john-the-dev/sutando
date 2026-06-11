#!/usr/bin/env python3
"""Tests for src/result_markers.py — parse_markers() + first_action()."""

import importlib.util
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("result_markers", _REPO / "src" / "result_markers.py")
_mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
sys.modules["result_markers"] = _mod
spec.loader.exec_module(_mod)  # type: ignore[union-attr]

parse_markers = _mod.parse_markers
first_action = _mod.first_action
Action = _mod.Action
ParseResult = _mod.ParseResult

_passed = 0
_failed = 0

def _check(label: str, condition: bool) -> None:
    global _passed, _failed
    if condition:
        _passed += 1
    else:
        _failed += 1
        print(f"FAIL: {label}")


# empty / plain
r = parse_markers("")
_check("empty → empty body", r.body == "")
_check("empty → no actions", r.actions == [])

r = parse_markers("Hello world")
_check("plain → body unchanged", r.body == "Hello world")
_check("plain → no actions", r.actions == [])

r = parse_markers("   \n  Line one\nLine two")
_check("plain multiline → body preserved", "Line one" in r.body)
_check("plain multiline → no actions", r.actions == [])

# SKIP: [no-send]
r = parse_markers("[no-send]")
_check("no-send → body empty", r.body == "")
_check("no-send → one action", len(r.actions) == 1)
_check("no-send → kind=skip", r.actions[0].kind == "skip")
_check("no-send → value=no-send", r.actions[0].value == "no-send")

r = parse_markers("[NO-SEND]\nsome content")
_check("no-send case-insensitive → skip", r.actions[0].kind == "skip")
_check("no-send swallows content", r.body == "")

r = parse_markers("  [no-send]  \nignored")
_check("no-send with leading whitespace → skip", r.actions[0].kind == "skip")

# SKIP: [REPLIED]
r = parse_markers("[REPLIED]")
_check("REPLIED → skip", r.actions[0].kind == "skip")
_check("REPLIED → value=REPLIED", r.actions[0].value == "REPLIED")

r = parse_markers("[REPLIED]\nsome body after")
_check("REPLIED swallows body", r.body == "")

# SKIP: [deduped:]
r = parse_markers("[deduped: task-99]")
_check("deduped → skip", r.actions[0].kind == "skip")
_check("deduped → value=deduped", r.actions[0].value == "deduped")
_check("deduped → extra=task-99", r.actions[0].extra == "task-99")

r = parse_markers("[deduped: task-99]\n[channel: 12345]\n[file: /x]")
_check("deduped terminal → no redirect", not any(a.kind == "redirect" for a in r.actions))
_check("deduped terminal → no attach", not any(a.kind == "attach" for a in r.actions))

r = parse_markers("[DEDUPED: abc-123]")
_check("deduped case-insensitive", r.actions[0].extra == "abc-123")

# REDIRECT
r = parse_markers("[channel: 12345678901234567]\nBody text")
_check("redirect → kind", r.actions[0].kind == "redirect")
_check("redirect → channel id", r.actions[0].value == "12345678901234567")
_check("redirect → body is rest", "Body text" in r.body)
_check("redirect → marker stripped", "[channel:" not in r.body)

r = parse_markers("[channel: CDEF1234]\nSlack channel")
_check("redirect slack channel id", r.actions[0].value == "CDEF1234")

r = parse_markers("  [channel: 99]  \nAfter")
_check("redirect leading whitespace", r.actions[0].kind == "redirect")

r = parse_markers("[channel: 99]")
_check("redirect no following body → ok", r.actions[0].kind == "redirect")

r = parse_markers("Some text\n[channel: 999]\nMore")
_check("redirect not first line → no redirect", not any(a.kind == "redirect" for a in r.actions))

# ATTACH
r = parse_markers("Here [file: /tmp/x.png] you go")
_check("file marker → attach", r.actions[0].kind == "attach")
_check("file marker → path", r.actions[0].value == "/tmp/x.png")
_check("file marker stripped", "[file:" not in r.body)

r = parse_markers("See [send: /home/user/report.pdf]")
_check("send alias → attach", r.actions[0].kind == "attach")
_check("send alias → path", r.actions[0].value == "/home/user/report.pdf")

r = parse_markers("[attach: /data/output.csv] attached")
_check("attach alias → attach", r.actions[0].kind == "attach")
_check("attach alias → path", r.actions[0].value == "/data/output.csv")

r = parse_markers("First [file: /a.txt] and [send: /b.txt] end")
attaches = [a for a in r.actions if a.kind == "attach"]
_check("multiple attaches → 2 actions", len(attaches) == 2)
_check("multiple attaches → first path", attaches[0].value == "/a.txt")
_check("multiple attaches → second path", attaches[1].value == "/b.txt")
_check("multiple attaches → markers stripped", "[file:" not in r.body and "[send:" not in r.body)

# REDIRECT + ATTACH combo
r = parse_markers("[channel: 42]\nSee [file: /report.pdf] for details")
_check("redirect+attach → redirect present", any(a.kind == "redirect" for a in r.actions))
_check("redirect+attach → attach present", any(a.kind == "attach" for a in r.actions))
_check("redirect+attach → body has text", "for details" in r.body)

# D7 header peeling
r = parse_markers("**[core: 2]**\nNormal body")
_check("D7 header → body includes reply", "Normal body" in r.body)
_check("D7 header alone → no actions", r.actions == [])

r = parse_markers("**[core: 1]**\n_(handled by pool core 1)_\nReply text")
_check("D7 italic sub-line → body includes reply", "Reply text" in r.body)

r = parse_markers("**[core: 3]**\n[no-send]\nbody")
_check("D7+skip → still skips", r.actions[0].kind == "skip")
_check("D7+skip → body empty", r.body == "")

r = parse_markers("**[core: 1]**\n[channel: 777]\nRedir body")
_check("D7+redirect → redirect found", any(a.kind == "redirect" for a in r.actions))
_check("D7+redirect → body has text", "Redir body" in r.body)
_check("D7+redirect → D7 header in body", "**[core: 1]**" in r.body)

# first_action
pr = ParseResult(body="x", actions=[
    Action(kind="skip", value="no-send"),
    Action(kind="attach", value="/a.txt"),
    Action(kind="attach", value="/b.txt"),
])

a = first_action(pr, "skip")
_check("first_action skip → found", a is not None and a.kind == "skip")

a = first_action(pr, "attach")
_check("first_action attach → first file", a is not None and a.value == "/a.txt")

a = first_action(pr, "redirect")
_check("first_action redirect → None when absent", a is None)

a = first_action(ParseResult(body="", actions=[]), "skip")
_check("first_action empty → None", a is None)

print(f"result-markers: {_passed}/{_passed + _failed} passed")
sys.exit(0 if _failed == 0 else 1)
