#!/usr/bin/env bash
# Runs a Bench tryout on Switch Console's local server, with its real agents.
# Reads Console's registration token into the Referee's environment without printing it.
# The room is bridged to Console's Mattermost user, so you can pick a winner from Console.
set -euo pipefail

CONSOLE="$HOME/Library/Application Support/switchdash/local-switch-server"
PORT=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["api"])' "$CONSOLE/ports.json")
TOKEN=$(grep '^AGENT_REGISTRATION_TOKEN=' "$CONSOLE/native/run/switch-core.env" | cut -d= -f2-)

cd "$(dirname "$0")/.."
SWITCH_URL="http://127.0.0.1:$PORT" \
SWITCH_REGISTRATION_TOKEN="$TOKEN" \
BENCH_CANDIDATES="${BENCH_CANDIDATES:-bench-claude,bench-codex}" \
BENCH_TRYOUT_MINUTES="${BENCH_TRYOUT_MINUTES:-15}" \
BENCH_USER="${BENCH_USER:-user}" \
BENCH_RESPONSE_SECONDS="${BENCH_RESPONSE_SECONDS:-60}" \
  exec uv run python -m referee --room "${BENCH_ROOM:-bench-team}" \
    --ticket "${BENCH_TICKET:-tickets/sample-invoice/ticket.md}"
