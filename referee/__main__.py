"""Runs the Referee in one room: python -m referee --room bench-payments"""

import argparse
import asyncio
import logging

from pathlib import Path

from .agent import Bench, Referee
from . import fixtures
from .config import ROOT, open_addressing, settings
from .switch import SwitchAgent, register

TICKETS = ROOT / "tickets"


async def find_or_create_room(agent, name, user):
    for room in await agent.op("list_rooms"):
        if room.get("name") == name:
            return room["room_id"]
    room = await agent.op(
        "create_room",
        name=name,
        description="Bench: post tickets here for coding agents to try out on.",
        agent_names=[agent.name],
        user_names=[user] if user else [],
        channel_type="channel_public",
    )
    return room["id"]


async def main(room_name, ticket_file=None):
    config = settings()
    credentials = await register(
        config["url"], config["registration_token"], config["name"], "Bench Referee: runs tryouts and scores them."
    )
    agent = SwitchAgent(config["url"], credentials["id"], credentials["api_key"], config["name"])
    open_addressing(config, agent.agent_id)
    bench = Bench(
        config["candidates"],
        workdir=Path(config["workdir"]),
        minutes=config["minutes"],
        sandbox=config["sandbox"],
        role=config["role"],
        pickers=config["pickers"],
        response_seconds=config["response_seconds"],
        tickets_dir=TICKETS,
        fixture_cache=ROOT / ".bench/fixtures",
    )
    referee = Referee(agent, bench=bench)
    # Plain chat tickets run against the default fixture; "try <name>" picks another.
    if config["repo"]:
        bench.repo = Path(config["repo"])
    else:
        bench.default = referee.fixture(Path(config["fixture"]).name)
    try:
        await referee.start()
        await referee.join(await find_or_create_room(agent, room_name, config["user"]))
        serving = asyncio.create_task(referee.serve())
        if ticket_file in fixtures.available(TICKETS):
            chosen = referee.fixture(ticket_file)
            await referee.file_ticket(chosen.ticket, chosen)
        elif ticket_file:
            await referee.file_ticket(Path(ticket_file).read_text())
        await serving
    finally:
        await agent.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--room", default="bench-payments", help="room name, created if missing")
    parser.add_argument(
        "--ticket", help="a ticket to post and try out at start: a name under tickets/, or a file"
    )
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    args = parser.parse_args()
    asyncio.run(main(args.room, args.ticket))
