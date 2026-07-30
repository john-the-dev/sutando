#!/usr/bin/env python3
"""Regression coverage for CLIENT_PORT-aware web-client health checks."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src" / "health-check.py"
spec = importlib.util.spec_from_file_location("health_check_web_port", SRC)
hc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hc)

failures: list[str] = []


def check(condition: bool, label: str) -> None:
    print(("ok  " if condition else "FAIL") + " " + label)
    if not condition:
        failures.append(label)


with tempfile.TemporaryDirectory() as tmp:
    env_path = Path(tmp) / ".env"

    check(
        hc.resolve_web_client_port(env={}, env_path=env_path) == {"port": 8080},
        "missing CLIENT_PORT uses the startup default",
    )

    env_path.write_text("CLIENT_PORT=8081\n")
    check(
        hc.resolve_web_client_port(env={}, env_path=env_path) == {"port": 8081},
        "canonical dotenv CLIENT_PORT is honored",
    )
    check(
        hc.resolve_web_client_port(env={"CLIENT_PORT": "9090"}, env_path=env_path) == {"port": 9090},
        "process environment overrides dotenv",
    )

    env_path.write_text('export CLIENT_PORT="8181" # local conflict\n')
    check(
        hc.resolve_web_client_port(env={}, env_path=env_path) == {"port": 8181},
        "export, quotes, and comments match shell dotenv syntax",
    )

    for invalid in ("", "not-a-port", "0", "65536"):
        result = hc.resolve_web_client_port(env={"CLIENT_PORT": invalid}, env_path=env_path)
        check("error" in result, f"invalid CLIENT_PORT={invalid!r} fails closed")

source = SRC.read_text()
check(
    'check_port(web_config["port"], "web-client", probe=True)' in source
    and 'check_port(8080, "web-client", probe=True)' not in source,
    "run_all_checks probes the resolved port instead of hardcoded 8080",
)

print()
if failures:
    print(f"FAIL — {len(failures)}: {failures}")
    sys.exit(1)
print("PASS — web-client health follows CLIENT_PORT")
