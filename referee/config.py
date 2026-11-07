"""Where the Referee finds its Switch server.

Reads SWITCH_URL and SWITCH_REGISTRATION_TOKEN from the environment. For local
development it falls back to the Docker stack's deploy/.env.
"""

import json
import logging
import os
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
DEV_ENV = ROOT / "deploy/.env"
SAMPLE_FIXTURE = ROOT / "tickets/sample-invoice"
SAMPLE_TICKET_REPO = SAMPLE_FIXTURE / "repo"


def dev_env():
    if not DEV_ENV.exists():
        return {}
    return dict(
        line.split("=", 1)
        for line in DEV_ENV.read_text().splitlines()
        if "=" in line and not line.startswith("#")
    )


class Settings(dict):
    """A plain dict that keeps the registration token out of logs and test output."""

    def __repr__(self):
        return repr({k: ("***" if k == "registration_token" else v) for k, v in self.items()})


DEV_URL = "http://127.0.0.1:8000"
CONSOLE_DIR = Path.home() / "Library/Application Support/switchdash/local-switch-server"


def console_server():
    """Switch Console's local server as (url, registration token), or None if it isn't set up."""
    try:
        ports = json.loads((CONSOLE_DIR / "ports.json").read_text())
        env = (CONSOLE_DIR / "native/run/switch-core.env").read_text()
    except (OSError, ValueError):
        return None
    prefix = "AGENT_REGISTRATION_TOKEN="
    token = next((line[len(prefix):] for line in env.splitlines() if line.startswith(prefix)), None)
    if not token or "api" not in ports:
        return None
    return f"http://127.0.0.1:{ports['api']}", token


def settings():
    url = os.environ.get("SWITCH_URL", DEV_URL)
    # deploy/.env only describes the dev stack, so it's ignored for any other server.
    dev = dev_env() if url == DEV_URL else {}
    token = os.environ.get("SWITCH_REGISTRATION_TOKEN") or dev.get("AGENT_REGISTRATION_TOKEN")
    if not token:
        raise SystemExit("Set SWITCH_REGISTRATION_TOKEN, or start the dev stack in deploy/.")
    return Settings({
        "url": url,
        "dev": bool(dev),
        "registration_token": token,
        "name": os.environ.get("BENCH_REFEREE_NAME", "bench-referee"),
        "user": os.environ.get("BENCH_USER") or dev.get("MATTERMOST_USER"),
        "candidates": [c.strip() for c in os.environ.get("BENCH_CANDIDATES", "").split(",") if c.strip()],
        "fixture": os.environ.get("BENCH_FIXTURE", str(SAMPLE_FIXTURE)),
        "repo": os.environ.get("BENCH_REPO"),
        "sandbox": os.environ.get("BENCH_SANDBOX", "1") != "0",
        "role": os.environ.get("BENCH_ROLE", "fixer"),
        "response_seconds": float(os.environ.get("BENCH_RESPONSE_SECONDS", "300")),
        "pickers": [p.strip() for p in os.environ["BENCH_PICKERS"].split(",")] if os.environ.get("BENCH_PICKERS") else None,
        "workdir": os.environ.get("BENCH_WORKDIR", str(ROOT / ".bench/tryouts")),
        "minutes": float(os.environ.get("BENCH_TRYOUT_MINUTES", "20")),
    })


def open_addressing(config, agent_id):
    """Lets people in the room address the Referee, not only its owner.

    Only the owner can change this; an agent can't change its own policy. On the dev
    stack we sign in as the Gateway admin. Elsewhere, set it on the agent's page in
    Switch Console.
    """
    dev = dev_env() if config.get("dev") else {}
    if not dev.get("GATEWAY_ADMIN_EMAIL"):
        logging.getLogger("referee").warning(
            "Can't open addressing here: allow 'anyone' on %s's page in Switch Console", config["name"]
        )
        return False
    with httpx.Client(base_url=config["url"], timeout=15) as client:
        login = client.post(
            "/gateway/auth/login",
            json={"email": dev["GATEWAY_ADMIN_EMAIL"], "password": dev["GATEWAY_ADMIN_PASSWORD"]},
        )
        login.raise_for_status()
        token = login.json().get("access_token")
        client.put(
            f"/gateway/agents/{agent_id}/addressing-policy",
            json={"policy": None},
            headers={"Authorization": f"Bearer {token}"} if token else {},
        ).raise_for_status()
    return True
