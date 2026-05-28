#!/bin/bash
# Render text to speech via OpenAI tts-1-hd. Reads OPENAI_API_KEY from .env.
# Usage: synthesize.sh [--voice <name>] [--out <path>] -- "text"
set -euo pipefail

VOICE="coral"
OUT=""
ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --voice) VOICE="$2"; shift 2 ;;
    --out)   OUT="$2"; shift 2 ;;
    --) shift; ARGS+=("$@"); break ;;
    *) ARGS+=("$1"); shift ;;
  esac
done

TEXT="${ARGS[*]-}"
[[ -n "$TEXT" ]] || { echo "Usage: synthesize.sh [--voice <name>] [--out <path>] -- \"text\"" >&2; exit 2; }

REPO="$(cd "$(dirname "$0")/../../.." && pwd)"
# results/ lives at workspace per docs/workspace-contract.md; .env at repo
# per §5 (Code-adjacent secrets). v3 audit #1149-class.
WORKSPACE="${SUTANDO_WORKSPACE:-$HOME/.sutando/workspace}"
KEY="${OPENAI_API_KEY:-$(grep -E '^OPENAI_API_KEY=' "$REPO/.env" 2>/dev/null | cut -d= -f2-)}"
KEY="${KEY%\"}"; KEY="${KEY#\"}"; KEY="${KEY%\'}"; KEY="${KEY#\'}"
[[ -n "$KEY" ]] || { echo "OPENAI_API_KEY missing" >&2; exit 1; }

[[ -n "$OUT" ]] || OUT="$WORKSPACE/results/openai-tts-$(date +%s).mp3"
mkdir -p "$(dirname "$OUT")"

curl -sSf https://api.openai.com/v1/audio/speech \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d "$(python3 -c 'import json,sys;print(json.dumps({"model":"tts-1-hd","voice":sys.argv[1],"input":sys.argv[2]}))' "$VOICE" "$TEXT")" \
  -o "$OUT"

echo "$OUT"
