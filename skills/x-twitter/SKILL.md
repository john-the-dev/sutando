---
name: x-twitter
description: "Post, search, read, and check engagement on X (Twitter) via API v2 — or read and engage (like, reply) with no API key by driving your real logged-in Chrome (browser mode)."
---

# X (Twitter)

Post, search, read, and monitor X from the command line.

Two backends:
- **API mode** (`x-post.py`) — full read + write (post, reply, media) via X API v2. Needs API keys.
- **Browser mode** (`x-browser.py`) — read **and** engage (like, reply), **no API key**.
  Drives your real, logged-in Google Chrome via AppleScript, so it sees X exactly as you do.

## Usage — API mode (`x-post.py`)

```bash
# Post
python3 skills/x-twitter/x-post.py post "Your tweet text"
python3 skills/x-twitter/x-post.py post "With video" --media /path/to/video.mp4
python3 skills/x-twitter/x-post.py post --reply-to 123456789 "Reply text"

# Search
python3 skills/x-twitter/x-post.py search "sutando agent"
python3 skills/x-twitter/x-post.py search "from:Chi_Wang_" --limit 5

# Read a tweet
python3 skills/x-twitter/x-post.py read 2040817066199195818

# Mentions & timeline
python3 skills/x-twitter/x-post.py mentions
python3 skills/x-twitter/x-post.py timeline

# Engagement (likes, retweets, views)
python3 skills/x-twitter/x-post.py engagement 2040817066199195818
```

## Usage — Browser mode (`x-browser.py`, no API key)

Drives your real, logged-in Google Chrome via AppleScript, so it reads **and
engages with** X exactly as you see it — no developer account, no keys. Reads
need no permissions beyond the Apple Events toggle; `reply` additionally needs
Accessibility permission (the submit is a real OS keystroke).

```bash
# The logged-in account (name + @handle)
python3 skills/x-twitter/x-browser.py whoami

# Visible tweets on your home timeline
python3 skills/x-twitter/x-browser.py home --limit 10

# A single tweet (id or full URL)
python3 skills/x-twitter/x-browser.py read 2040817066199195818

# Latest results for a search
python3 skills/x-twitter/x-browser.py search "sutando agent" --limit 10

# Engagement (opt-in writes — post publicly under your handle)
python3 skills/x-twitter/x-browser.py like 2040817066199195818
python3 skills/x-twitter/x-browser.py reply 2040817066199195818 "Nice thread"
```

Engagement notes:
- **`like`** is pure DOM (a synthetic click is honored by X) — reliable.
- **`reply`** is a hybrid: JS fills the composer, but the final **submit** needs
  a real OS keystroke (System Events Cmd+Return) because X ignores synthetic
  submit events. So `reply` additionally needs **Accessibility permission**, and
  it briefly brings Chrome to the foreground + activates the x.com tab to land
  the keystroke — don't run it while typing elsewhere. For bulk/headless writes,
  use API mode (`x-post.py`).

## Setup — API mode

1. Go to https://developer.x.com and sign in
2. Create a Project + App
3. Generate keys and add to `.env`:
   ```
   X_API_KEY=...
   X_API_SECRET=...
   X_ACCESS_TOKEN=...
   X_ACCESS_TOKEN_SECRET=...
   ```

## Setup — Browser mode

macOS + Google Chrome only. No keys needed.

1. Be logged into x.com in Chrome.
2. Enable Chrome > View > Developer > **"Allow JavaScript from Apple Events"**
   (one-time toggle; without it Chrome refuses `execute javascript`).

## Notes

- Free tier: 500 posts/month, search recent tweets (7 days)
- Video upload uses chunked upload (supports 4K)
- Always confirm post content with user before publishing
- Browser mode reads need no permissions beyond the Apple Events toggle.
  `like` works via DOM; `reply` works but needs Accessibility permission (the
  submit is a real OS keystroke) and foregrounds Chrome. Prefer API mode for
  bulk or headless writes.
