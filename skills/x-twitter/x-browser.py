#!/usr/bin/env python3
"""Browser-mode X (Twitter) reader — drives your real, logged-in Chrome.

No API key required. Uses AppleScript to control the actual Google Chrome app
(not a headless browser), so it reads X with your existing logged-in session.

Requirements (macOS + Google Chrome):
  - Chrome > View > Developer > "Allow JavaScript from Apple Events" must be ON.
    (One-time toggle; without it, Chrome refuses `execute javascript`.)
  - You must be logged into x.com in Chrome.

Commands:
  x-browser.py whoami                 # the logged-in account (name + @handle)
  x-browser.py home [--limit N]       # visible tweets on your home timeline
  x-browser.py read <tweet-id|url>    # a single tweet's text + author
  x-browser.py search "<query>"       # latest results for a search (--limit N)

This is READ-ONLY by design. Posting/liking/following via DOM automation is
fragile and risks tripping X's automation defenses, so it is intentionally not
implemented here — use the API path (x-post.py) for writes.
"""
import sys
import json
import time
import base64
import argparse
import subprocess

X_HOSTS = ("x.com", "twitter.com")


class BrowserError(RuntimeError):
    pass


def _osascript(script: str, timeout: int = 20) -> str:
    try:
        p = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise BrowserError("osascript timed out talking to Chrome")
    out = (p.stdout or "").strip()
    err = (p.stderr or "").strip()
    if p.returncode != 0:
        raise BrowserError(err or "osascript failed")
    return out


def _chrome_running() -> bool:
    try:
        p = subprocess.run(["pgrep", "-x", "Google Chrome"],
                           capture_output=True, text=True)
        return p.returncode == 0
    except Exception:
        return False


def run_js(js: str, timeout: int = 20) -> str:
    """Run a JS snippet in the first x.com/twitter.com tab; return its value.

    JS is passed base64-encoded and eval'd inside the tab, so we never fight
    AppleScript/Python quote escaping. The snippet's final expression is the
    return value.
    """
    b64 = base64.b64encode(js.encode("utf-8")).decode("ascii")
    script = f'''
tell application "Google Chrome"
  set theTab to missing value
  repeat with w in windows
    repeat with t in tabs of w
      set u to URL of t
      if u contains "x.com" or u contains "twitter.com" then
        set theTab to t
        exit repeat
      end if
    end repeat
    if theTab is not missing value then exit repeat
  end repeat
  if theTab is missing value then return "__NO_X_TAB__"
  return execute theTab javascript "eval(atob('{b64}'))"
end tell
'''
    res = _osascript(script, timeout=timeout)
    if res == "__NO_X_TAB__":
        raise BrowserError("no x.com tab is open in Chrome")
    if res.startswith("__JSERR__"):
        raise BrowserError("page JS error: " + res[len("__JSERR__"):])
    return res


def ensure_tab(url: str, settle: float = 4.0, max_wait: float = 15.0) -> None:
    """Point an x.com tab at `url` (reuse one if present, else open a new tab),
    then wait for document.readyState == 'complete' plus a short settle for the
    React SPA to render."""
    if not _chrome_running():
        raise BrowserError("Google Chrome is not running")
    b64 = base64.b64encode(url.encode("utf-8")).decode("ascii")
    script = f'''
tell application "Google Chrome"
  if (count of windows) is 0 then make new window
  set theTab to missing value
  repeat with w in windows
    repeat with t in tabs of w
      set u to URL of t
      if u contains "x.com" or u contains "twitter.com" then
        set theTab to t
        exit repeat
      end if
    end repeat
    if theTab is not missing value then exit repeat
  end repeat
  set target to (do shell script "python3 -c \\"import base64,sys;sys.stdout.write(base64.b64decode('{b64}').decode())\\"")
  if theTab is missing value then
    set theTab to make new tab at end of tabs of front window with properties {{URL:target}}
  else
    set URL of theTab to target
  end if
end tell
'''
    _osascript(script)
    # Poll for load completion.
    deadline = time.time() + max_wait
    while time.time() < deadline:
        try:
            state = run_js("document.readyState", timeout=10)
        except BrowserError:
            state = ""
        if state == "complete":
            break
        time.sleep(0.5)
    time.sleep(settle)


def _extract_tweets_js(limit: int) -> str:
    return (
        "(function(){try{var out=[];"
        "var arts=document.querySelectorAll('article[data-testid=\\\"tweet\\\"]');"
        "for(var i=0;i<arts.length&&out.length<" + str(limit) + ";i++){var a=arts[i];"
        "var u=a.querySelector('[data-testid=\\\"User-Name\\\"]');"
        "var tx=a.querySelector('[data-testid=\\\"tweetText\\\"]');"
        "var tm=a.querySelector('time');"
        "out.push({user:u?u.innerText.replace(/\\n/g,' '):'',"
        "text:tx?tx.innerText:'',time:tm?tm.getAttribute('datetime'):''});}"
        "return JSON.stringify(out);}catch(e){return '__JSERR__'+e.message;}})()"
    )


def cmd_whoami() -> int:
    ensure_tab("https://x.com/home")
    js = (
        "(function(){try{var b=document.querySelector('[data-testid=\\\"SideNav_AccountSwitcher_Button\\\"]');"
        "return JSON.stringify({account:b?b.innerText.replace(/\\n/g,' | '):'(not found — logged out?)'});"
        "}catch(e){return '__JSERR__'+e.message;}})()"
    )
    data = json.loads(run_js(js))
    print(data["account"])
    return 0


def cmd_home(limit: int) -> int:
    ensure_tab("https://x.com/home")
    tweets = json.loads(run_js(_extract_tweets_js(limit)))
    if not tweets:
        print("(no tweets visible — try scrolling or re-running)")
        return 0
    for t in tweets:
        print(f"{t['user']}\n  {t['text']}\n  [{t['time']}]\n")
    return 0


def cmd_read(ref: str) -> int:
    if ref.startswith("http"):
        url = ref
    else:
        url = f"https://x.com/i/web/status/{ref}"
    ensure_tab(url)
    js = (
        "(function(){try{var a=document.querySelector('article[data-testid=\\\"tweet\\\"]');"
        "if(!a)return JSON.stringify({error:'tweet not found'});"
        "var u=a.querySelector('[data-testid=\\\"User-Name\\\"]');"
        "var tx=a.querySelector('[data-testid=\\\"tweetText\\\"]');"
        "var tm=a.querySelector('time');"
        "return JSON.stringify({user:u?u.innerText.replace(/\\n/g,' '):'',"
        "text:tx?tx.innerText:'',time:tm?tm.getAttribute('datetime'):''});"
        "}catch(e){return '__JSERR__'+e.message;}})()"
    )
    data = json.loads(run_js(js))
    if data.get("error"):
        print(data["error"])
        return 1
    print(f"{data['user']}\n{data['text']}\n[{data['time']}]")
    return 0


def cmd_search(query: str, limit: int) -> int:
    from urllib.parse import quote
    url = f"https://x.com/search?q={quote(query)}&f=live"
    ensure_tab(url)
    tweets = json.loads(run_js(_extract_tweets_js(limit)))
    if not tweets:
        print("(no results visible)")
        return 0
    for t in tweets:
        print(f"{t['user']}\n  {t['text']}\n  [{t['time']}]\n")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Browser-mode X reader (real Chrome, no API key)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("whoami")
    p_home = sub.add_parser("home")
    p_home.add_argument("--limit", type=int, default=10)
    p_read = sub.add_parser("read")
    p_read.add_argument("ref", help="tweet id or full URL")
    p_search = sub.add_parser("search")
    p_search.add_argument("query")
    p_search.add_argument("--limit", type=int, default=10)
    args = ap.parse_args()

    try:
        if args.cmd == "whoami":
            return cmd_whoami()
        if args.cmd == "home":
            return cmd_home(args.limit)
        if args.cmd == "read":
            return cmd_read(args.ref)
        if args.cmd == "search":
            return cmd_search(args.query, args.limit)
    except BrowserError as e:
        msg = str(e)
        print(f"browser-mode error: {msg}", file=sys.stderr)
        if "Allow JavaScript from Apple Events" in msg or "execute" in msg.lower():
            print("hint: enable Chrome > View > Developer > "
                  "'Allow JavaScript from Apple Events'", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
