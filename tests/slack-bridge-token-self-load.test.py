#!/usr/bin/env python3
"""Structural regression test: Slack bridge self-loads channel .env (#1775).

PR #1775 added .env self-loading to slack-bridge.py so the bridge works
under the Electron backend-supervisor, which builds the child env from
process.env + workspace .env only (without injecting the channel .env that
the Settings page writes to $CLAUDE_CONFIG_DIR/channels/slack/.env).

discord-bridge.py and telegram-bridge.py already self-load their channel
.env; this aligns slack-bridge.py with that pattern.

Without the self-load, the supervisor-spawned bridge crash-loops:
  - Supervisor gates the bridge on `channelTokenPresent` (channel .env exists)
  - But doesn't inject channel .env into the child process env
  - Bridge sees SLACK_BOT_TOKEN/SLACK_APP_TOKEN as unset → exits → supervisor
    restarts → same crash (observed as 19+ identical lines in slack-bridge.log)

Key contracts:
  - Fallback fires ONLY when tokens are missing from the process env (env takes
    precedence over the file — no silent env-override from .env)
  - Both BOT_TOKEN and APP_TOKEN are loaded from the channel .env
  - Quote-stripping (`strip().strip('"').strip("'")`) mirrors discord/telegram
  - Uses `claude_home_path("channels", "slack", ".env")` for CCD-resolution

Run: python3 tests/slack-bridge-token-self-load.test.py
Exit 0 on pass, 1 on fail.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = (REPO / "src" / "slack-bridge.py").read_text()


def _token_load_block() -> str:
    """Return the source window from the initial token reads through the self-load block."""
    start = SRC.find("BOT_TOKEN = os.environ.get")
    if start < 0:
        return ""
    return SRC[start : start + 1200]


class TestSlackBridgeTokenSelfLoad(unittest.TestCase):

    def setUp(self):
        self._block = _token_load_block()
        self.assertGreater(len(self._block), 0, "BOT_TOKEN = os.environ.get not found in slack-bridge.py")

    # ------------------------------------------------------------------
    # Env-var-first: process env takes precedence over .env file
    # ------------------------------------------------------------------

    def test_bot_token_read_from_env_first(self):
        """SLACK_BOT_TOKEN must be read from os.environ before the file fallback."""
        self.assertIn(
            'BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")',
            self._block,
            'BOT_TOKEN must be initialized from os.environ.get("SLACK_BOT_TOKEN", "")',
        )

    def test_app_token_read_from_env_first(self):
        """SLACK_APP_TOKEN must be read from os.environ before the file fallback."""
        self.assertIn(
            'APP_TOKEN = os.environ.get("SLACK_APP_TOKEN", "")',
            self._block,
            'APP_TOKEN must be initialized from os.environ.get("SLACK_APP_TOKEN", "")',
        )

    # ------------------------------------------------------------------
    # Fallback gate: only trigger self-load when tokens are missing
    # ------------------------------------------------------------------

    def test_fallback_gated_on_missing_tokens(self):
        """The .env fallback must only run when BOT_TOKEN OR APP_TOKEN is empty.

        An unconditional self-load could silently override a valid process-env
        token with a stale .env value, causing auth failures in test environments
        or CI where the channel .env is not configured.
        """
        self.assertIn(
            "if not BOT_TOKEN or not APP_TOKEN:",
            self._block,
            "Channel .env fallback must be gated on 'if not BOT_TOKEN or not APP_TOKEN:'",
        )

    # ------------------------------------------------------------------
    # Path: must use claude_home_path for CCD-resolution
    # ------------------------------------------------------------------

    def test_fallback_path_uses_claude_home_path(self):
        """The channel .env must be resolved via claude_home_path() not a hardcoded path.

        Hardcoding ~/.claude would break nodes that relocate the config dir
        via $CLAUDE_CONFIG_DIR (the CCD pattern, PR #1525).
        """
        self.assertIn(
            'claude_home_path("channels", "slack", ".env")',
            self._block,
            'Channel .env must be loaded via claude_home_path("channels", "slack", ".env")',
        )

    # ------------------------------------------------------------------
    # Per-token guards: env var wins over .env file value
    # ------------------------------------------------------------------

    def test_bot_token_env_var_wins_over_file(self):
        """When BOT_TOKEN is already set (from env), the .env file value must be ignored."""
        self.assertIn(
            'startswith("SLACK_BOT_TOKEN=") and not BOT_TOKEN',
            self._block,
            "BOT_TOKEN from .env must be conditional on 'and not BOT_TOKEN' so env wins",
        )

    def test_app_token_env_var_wins_over_file(self):
        """When APP_TOKEN is already set (from env), the .env file value must be ignored."""
        self.assertIn(
            'startswith("SLACK_APP_TOKEN=") and not APP_TOKEN',
            self._block,
            "APP_TOKEN from .env must be conditional on 'and not APP_TOKEN' so env wins",
        )

    # ------------------------------------------------------------------
    # Quote stripping: mirrors discord/telegram pattern
    # ------------------------------------------------------------------

    def test_quote_stripping_applied(self):
        """Token values from .env must have surrounding quotes stripped.

        .env files may use BOT_TOKEN="xoxb-..." or BOT_TOKEN=xoxb-...; the
        .strip().strip('"').strip("'") chain handles both forms.
        """
        self.assertIn(
            ".strip().strip('\"').strip(\"'\")",
            self._block,
            "Token values must be quote-stripped with .strip().strip(\"'\").strip('\"')",
        )

    # ------------------------------------------------------------------
    # Both tokens required for startup
    # ------------------------------------------------------------------

    def test_both_tokens_required_or_exit(self):
        """If either token is still missing after the fallback, the bridge must exit 1."""
        # The final guard appears right after the self-load block.
        post_load = SRC[SRC.find("BOT_TOKEN = os.environ.get") : SRC.find("BOT_TOKEN = os.environ.get") + 1200]
        self.assertIn(
            "if not BOT_TOKEN or not APP_TOKEN:",
            post_load,
            "Bridge must check both tokens after the .env fallback and exit 1 if missing",
        )
        self.assertIn(
            "sys.exit(1)",
            post_load,
            "Bridge must call sys.exit(1) if SLACK_BOT_TOKEN or SLACK_APP_TOKEN is not set",
        )


if __name__ == "__main__":
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(
        unittest.TestLoader().loadTestsFromTestCase(TestSlackBridgeTokenSelfLoad)
    )
    sys.exit(0 if result.wasSuccessful() else 1)
