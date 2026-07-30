#!/usr/bin/env python3
"""Stream a source to YouTube Live over RTMP via ffmpeg.

MVP: push a chosen source to YouTube's RTMP ingest using a stream key.
No Google API call is required for a basic broadcast — a persistent
YouTube "stream key" plus auto-start enabled on the stream is enough to go
live. (Programmatic broadcast lifecycle via the YouTube Live Streaming API is
a documented follow-up; see SKILL.md.)

Commands:
    start   Begin streaming (backgrounds ffmpeg, writes a PID file).
    stop    Stop the running stream.
    status  Report whether a stream is running.

Sources (--source):
    test              lavfi test pattern + 1 kHz tone (no assets/camera needed — best for e2e).
    file:<path>       stream a media file (add --loop to repeat).
    image:<path>      a static image "slate" + silent audio.
    screen            macOS avfoundation screen capture (+ default audio if present).

The stream key is read with this precedence (per skills/MANIFEST.md):
    --stream-key  >  $YOUTUBE_STREAM_KEY  >  vault YOUTUBE_STREAM_KEY
The key is NEVER printed — logs and the ffmpeg command are redacted.
"""

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path

PID_FILE = "/tmp/sutando-youtube-live.pid"
DEFAULT_INGEST_BASE = "rtmp://a.rtmp.youtube.com/live2"
_REDACTION = "<STREAM_KEY>"


def _log(event, **kw):
    print(json.dumps({"_log": event, **kw}), file=sys.stderr)


def _ffmpeg_bin():
    """Resolve the ffmpeg binary robustly across hosts (Homebrew arm/intel, PATH)."""
    for cand in ("ffmpeg", "/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"):
        found = shutil.which(cand) if "/" not in cand else (cand if os.path.exists(cand) else None)
        if found:
            return found
    return None


def _load_manifest_config():
    """Read the skill manifest's `config` block for defaults (bitrate, resolution, etc.)."""
    manifest = Path(__file__).resolve().parent.parent / "manifest.json"
    try:
        data = json.loads(manifest.read_text())
        return data.get("config", {}) or {}
    except Exception:
        return {}


def _resolve_stream_key(cli_key):
    """CLI > env > vault. Returns the key string or None (never logs it)."""
    if cli_key:
        return cli_key
    env_key = os.environ.get("YOUTUBE_STREAM_KEY")
    if env_key:
        return env_key
    # Vault is optional at import time — only reach for it if needed.
    try:
        repo = next(
            p for p in Path(__file__).resolve().parents
            if (p / "src" / "vault_intercept.py").is_file()
        )
        sys.path.insert(0, str(repo / "src"))
        from vault_intercept import get_vault_key  # type: ignore
        return get_vault_key("YOUTUBE_STREAM_KEY")
    except (StopIteration, KeyError, ImportError):
        return None


def build_ffmpeg_cmd(source, stream_key, cfg, ingest_base=DEFAULT_INGEST_BASE, loop=False):
    """Construct the ffmpeg argv for a source. `stream_key` may be the redaction
    placeholder for dry-run/printing. Returns a list[str]."""
    ffmpeg = _ffmpeg_bin() or "ffmpeg"
    res = str(cfg.get("resolution", "1280x720"))
    fps = str(cfg.get("fps", 30))
    vbitrate = str(cfg.get("video_bitrate", "4500k"))
    abitrate = str(cfg.get("audio_bitrate", "128k"))
    # YouTube wants a keyframe every ~2s → gop = 2 * fps.
    gop = str(int(float(fps)) * 2)

    cmd = [ffmpeg, "-hide_banner", "-loglevel", "warning"]

    if source == "test":
        cmd += ["-re", "-f", "lavfi", "-i", f"testsrc2=size={res}:rate={fps}",
                "-f", "lavfi", "-i", "sine=frequency=1000:sample_rate=44100"]
    elif source.startswith("file:"):
        path = source[len("file:"):]
        if loop:
            cmd += ["-stream_loop", "-1"]
        cmd += ["-re", "-i", path]
    elif source.startswith("image:"):
        path = source[len("image:"):]
        cmd += ["-loop", "1", "-framerate", fps, "-i", path,
                "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"]
    elif source == "screen":
        # macOS avfoundation: "<video_index>:<audio_index>". "1:0" = main display + default mic;
        # ":none" style is not portable, so callers without audio should pass --source test.
        screen_spec = str(cfg.get("avf_screen_spec", "1:0"))
        cmd += ["-f", "avfoundation", "-framerate", fps, "-i", screen_spec]
    else:
        raise ValueError(f"unknown source: {source!r}")

    # Encode: H.264 + AAC, YouTube-compatible.
    cmd += [
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-b:v", vbitrate, "-maxrate", vbitrate, "-bufsize", str(cfg.get("buffer_size", "9000k")),
        "-g", gop, "-keyint_min", gop,
        "-c:a", "aac", "-b:a", abitrate, "-ar", "44100",
    ]
    cmd += ["-f", "flv", f"{ingest_base}/{stream_key}"]
    return cmd


def _redacted_str(cmd, real_key):
    out = []
    for a in cmd:
        if real_key and real_key in a:
            a = a.replace(real_key, _REDACTION)
        out.append(a)
    return " ".join(out)


def _running_pid():
    if not os.path.exists(PID_FILE):
        return None
    try:
        pid = int(Path(PID_FILE).read_text().strip())
    except (ValueError, OSError):
        return None
    try:
        os.kill(pid, 0)
        return pid
    except OSError:
        return None


def cmd_start(args):
    if _running_pid():
        print(json.dumps({"ok": False, "error": "a stream is already running; stop it first",
                          "pid": _running_pid()}))
        return 1
    if _ffmpeg_bin() is None:
        print(json.dumps({"ok": False, "error": "ffmpeg not found on PATH"}))
        return 1
    cfg = _load_manifest_config()
    ingest_base = os.environ.get("YOUTUBE_INGEST_BASE") or cfg.get("ingest_base", DEFAULT_INGEST_BASE)
    key = _resolve_stream_key(args.stream_key)
    if not key:
        print(json.dumps({"ok": False, "error": "no stream key — set it with "
                          "`vault set YOUTUBE_STREAM_KEY <key>` (or pass --stream-key / $YOUTUBE_STREAM_KEY)"}))
        return 1
    try:
        cmd = build_ffmpeg_cmd(args.source, key, cfg, ingest_base=ingest_base, loop=args.loop)
    except ValueError as e:
        print(json.dumps({"ok": False, "error": str(e)}))
        return 1

    if args.dry_run:
        print(json.dumps({"ok": True, "dry_run": True, "command": _redacted_str(cmd, key),
                          "source": args.source, "ingest_base": ingest_base}))
        return 0

    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    Path(PID_FILE).write_text(str(proc.pid))
    _log("youtube_live_started", pid=proc.pid, source=args.source)
    print(json.dumps({"ok": True, "started": True, "pid": proc.pid, "source": args.source,
                      "note": "streaming to YouTube; if the stream shows offline, confirm auto-start is "
                              "enabled on the YouTube stream or start the broadcast in Studio"}))
    return 0


def cmd_stop(_args):
    pid = _running_pid()
    if not pid:
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
        print(json.dumps({"ok": True, "stopped": False, "note": "no stream was running"}))
        return 0
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError as e:
        print(json.dumps({"ok": False, "error": f"failed to stop pid {pid}: {e}"}))
        return 1
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)
    _log("youtube_live_stopped", pid=pid)
    print(json.dumps({"ok": True, "stopped": True, "pid": pid}))
    return 0


def cmd_status(_args):
    pid = _running_pid()
    print(json.dumps({"ok": True, "running": bool(pid), "pid": pid}))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Stream a source to YouTube Live via ffmpeg.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_start = sub.add_parser("start", help="Begin streaming to YouTube Live.")
    p_start.add_argument("--source", default="test",
                         help="test | file:<path> | image:<path> | screen (default: test)")
    p_start.add_argument("--loop", action="store_true", help="Loop a file source forever.")
    p_start.add_argument("--stream-key", default=None,
                         help="Override the stream key (else $YOUTUBE_STREAM_KEY, else vault).")
    p_start.add_argument("--dry-run", action="store_true",
                         help="Print the (key-redacted) ffmpeg command without streaming.")
    p_start.set_defaults(func=cmd_start)

    sub.add_parser("stop", help="Stop the running stream.").set_defaults(func=cmd_stop)
    sub.add_parser("status", help="Report whether a stream is running.").set_defaults(func=cmd_status)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
