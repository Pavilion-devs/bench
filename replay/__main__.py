"""Replays the Bench demo on a Switch server, with no provider logins or API keys.

    uv run python -m replay                     # sample-invoice recording
    uv run python -m replay more-itertools      # the real more-itertools ticket

The Referee, scoring, role moves and failover are the real thing, on a real Switch
server. The candidates are replay agents: they apply the code that Claude Code and
Codex actually wrote in a recorded Console run, at the recorded times. A scripted
person files the ticket, picks the winner and sends work to the role. Then the
winner is killed, and the runner-up takes over.

It uses Switch Console's local server if it's running, else the Docker dev stack
in deploy/, else SWITCH_URL and SWITCH_REGISTRATION_TOKEN.
"""

import argparse
import asyncio
import json
import os
import re
import sys
import time

import httpx

from referee import roles
from referee.__main__ import TICKETS, find_or_create_room
from referee.agent import Bench, Referee
from referee.config import DEV_ENV, DEV_URL, ROOT, console_server, settings
from referee.scoring import run as scoring
from referee.switch import SwitchAgent, register

from .candidate import ReplayCandidate
from .record import RUNS

OWN_CODE = {"sample-invoice"}  # tickets whose code is ours, safe to test outside Docker


def up(url):
    try:
        return httpx.get(f"{url}/health", timeout=2).status_code == 200
    except httpx.HTTPError:
        return False


def pick_server(choice):
    if os.environ.get("SWITCH_URL"):
        return f"the server at {os.environ['SWITCH_URL']}", False
    console = console_server()
    if choice in ("auto", "console") and console and up(console[0]):
        # Into this process's environment only; never printed.
        os.environ["SWITCH_URL"], os.environ["SWITCH_REGISTRATION_TOKEN"] = console
        os.environ.setdefault("BENCH_USER", "user")
        return "Switch Console's local server", True
    if choice in ("auto", "dev") and DEV_ENV.exists() and up(DEV_URL):
        return "the Docker dev stack", False
    raise SystemExit(
        "No Switch server found. Start Switch Console (its local server), or the dev stack in deploy/, "
        "or set SWITCH_URL and SWITCH_REGISTRATION_TOKEN."
    )


class Narrator:
    def __init__(self):
        self.start = time.monotonic()

    def _stamp(self):
        return f"[{time.monotonic() - self.start:6.1f}s]"

    def message(self, sender, body):
        first, *rest = (body or "").splitlines() or [""]
        print(f"{self._stamp()} {sender}: {first}")
        for line in rest:
            print(f"{' ' * 10}{line}")
        sys.stdout.flush()

    def note(self, text):
        print(f"{self._stamp()} -- {text}", flush=True)

    def step(self, text):
        print(f"\n== {text}", flush=True)


async def replay(name, speed, server):
    run_dir = RUNS / name
    if not (run_dir / "run.json").exists():
        raise SystemExit(f"No recording called {name}. Have: {', '.join(sorted(p.name for p in RUNS.iterdir()))}")
    spec = json.loads((run_dir / "run.json").read_text())
    where, on_console = pick_server(server)
    config = settings()
    sandbox = scoring.docker_ready()
    if not sandbox and spec["ticket"] not in OWN_CODE:
        raise SystemExit(
            f"The {name} recording runs another project's tests, which only run in the Docker sandbox. "
            "Start Docker, or replay sample-invoice."
        )

    log = Narrator()
    log.step(f"Replaying {name}: {spec['recorded']} (tryout {spec['tryout']})")
    log.note(f"Switch server: {where}. Scoring: {'Docker sandbox, no network' if sandbox else 'locally, no Docker'}.")

    names = {"referee": "replay-referee", "person": "replay-person"}
    names |= {c["name"]: f"replay-{c['name']}" for c in spec["candidates"]}
    agents = {}
    for key, agent_name in names.items():
        creds = await register(config["url"], config["registration_token"], agent_name, "Bench replay agent")
        agents[key] = SwitchAgent(config["url"], creds["id"], creds["api_key"], agent_name)

    longest = max((c["seconds"] or 0) for c in spec["candidates"]) / speed
    bench = Bench(
        [names[c["name"]] for c in spec["candidates"]],
        workdir=ROOT / ".bench/replays",
        minutes=(longest + 30) / 60,
        sandbox=sandbox,
        tickets_dir=TICKETS,
        fixture_cache=ROOT / ".bench/fixtures",
        role_minutes=1,
        poll_seconds=1,
        response_seconds=30,
    )
    referee = Referee(agents["referee"], bench=bench)
    log.note(f"Building the {spec['ticket']} ticket repo (cached after the first run).")
    bench.default = await asyncio.to_thread(referee.fixture, spec["ticket"])
    candidates = {
        names[c["name"]]: ReplayCandidate(agents[c["name"]], names["referee"], c, run_dir, speed=speed, say=log.note)
        for c in spec["candidates"]
    }

    room = {}
    inbox = asyncio.Queue()

    async def person_event(kind, data):
        if kind == "message" and data.get("room_id") == room.get("id"):
            payload = data["payload"]
            log.message(payload.get("sender_name"), payload.get("body"))
            await inbox.put(payload)

    async def expect(test, timeout, what):
        try:
            async with asyncio.timeout(timeout):
                while True:
                    payload = await inbox.get()
                    if test(payload.get("sender_name"), payload.get("body") or ""):
                        return payload.get("body") or ""
        except TimeoutError:
            raise SystemExit(f"Replay stopped: no {what} within {timeout:.0f}s.") from None

    def from_referee(*starts):
        return lambda sender, body: sender == names["referee"] and body.startswith(starts)

    def answer_from(agent_name):
        return lambda sender, body: sender == agent_name and not roles.is_notice(body)

    person = agents["person"]

    async def say(body, **target):
        # Switch doesn't echo an agent's own messages back to it, so print them here.
        log.message(person.name, ("@" + target["target_roles"][0] + " " if "target_roles" in target else "") + body)
        await person.op("send_targeted_message", body=body, **target)

    serving = None
    try:
        await referee.start()
        await person.start(person_event)
        for candidate in candidates.values():
            await candidate.start()
        room_name = f"bench-replay-{time.strftime('%H%M%S')}"
        room["id"] = await find_or_create_room(agents["referee"], room_name, config["user"])
        await agents["referee"].op("invite_agent_to_room", room_id=room["id"], agent_name=person.name)
        await referee.join(room["id"])
        serving = asyncio.create_task(referee.serve())
        await person.op("connect_to_room", room_id=room["id"])
        log.note(f"Room #{room_name} is ready" + (". Watch it live in Switch Console." if on_console else "."))

        log.step("1. A person files a ticket. The Referee runs a sealed tryout and scores it.")
        ticket = (TICKETS / spec["ticket"] / "ticket.md").read_text().strip()
        await say(ticket, target_names=[names["referee"]])
        scorecard = await expect(from_referee("Scorecard"), bench.minutes * 60 + 180, "scorecard")
        ranked = re.findall(r"^\d+\. ([\w-]+):", scorecard, re.MULTILINE)
        winner, runner_up = ranked[0], ranked[1] if len(ranked) > 1 else None

        log.step(f"2. The person picks {winner}. The Referee moves the {bench.role} role to it.")
        await say(f"pick {winner}", target_names=[names["referee"]])
        moved = await expect(from_referee(f"{winner} now holds", f"{winner} didn't"), 120, "role move")
        if "didn't" in moved:
            raise SystemExit("Replay stopped: the role move failed.")
        await say("Another report of this bug came in. Can you take it?", target_roles=[bench.role])
        await expect(answer_from(winner), 30, f"answer from {winner}")

        if not runner_up:
            log.note("Only one candidate, so there's no failover to show.")
            return 0
        log.step(f"3. {winner} goes down with work waiting. The runner-up, {runner_up}, takes over.")
        candidates[winner].die()
        killed = time.monotonic()
        log.note(f"Killed {winner}: its connection dropped, no goodbye.")
        await say("One more: a customer says it still breaks for them. Please check.", target_roles=[bench.role])
        done = await expect(from_referee("Failover done", "Failover:"), 60, "failover")
        if not done.startswith("Failover done"):
            done = await expect(from_referee("Failover done"), 60, "failover")
        took = time.monotonic() - killed
        await expect(answer_from(runner_up), 30, f"answer from {runner_up}")

        log.step("Done")
        log.note(f"Winner: {winner}. Failover to {runner_up} took {took:.0f}s from the kill; the missed message was re-sent.")
        log.note(f"Room #{room_name} keeps the scorecard and standings documents. Tryout folders: .bench/replays/")
        return 0
    finally:
        if serving:
            serving.cancel()
            await asyncio.gather(serving, return_exceptions=True)
        for agent in agents.values():
            await agent.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("recording", nargs="?", default="sample-invoice", help="a folder under replay/runs")
    parser.add_argument("--speed", type=float, default=1.0, help="replay the agents' timings this many times faster")
    parser.add_argument("--server", choices=["auto", "console", "dev"], default="auto")
    args = parser.parse_args()
    sys.exit(asyncio.run(replay(args.recording, args.speed, args.server)))


if __name__ == "__main__":
    main()
