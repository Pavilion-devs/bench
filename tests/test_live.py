"""The Referee on a real Switch server. Needs the dev stack: see deploy/.

A second custom agent plays the person filing the ticket. The person-in-Mattermost
path is checked by hand, since it needs a chat login.
"""

import asyncio
import sys
import time
import uuid
from pathlib import Path

import httpx
import pytest

from referee.__main__ import find_or_create_room
from referee import roles
from referee.agent import Bench, Referee
from referee.config import SAMPLE_FIXTURE
from referee.config import SAMPLE_TICKET_REPO as SAMPLE
from referee.config import settings
from referee.switch import OpError, SwitchAgent, register
from referee.workspaces import init_repo
from tests.stub_candidate import StubCandidate

pytestmark = pytest.mark.live


def server_up(url):
    try:
        return httpx.get(f"{url}/health", timeout=2).status_code == 200
    except httpx.HTTPError:
        return False


@pytest.fixture(scope="module")
def config():
    config = settings()
    if not server_up(config["url"]):
        pytest.skip("Switch dev stack is not running")
    return config


async def connect(config, name):
    credentials = await register(config["url"], config["registration_token"], name, "Bench live test")
    agent = SwitchAgent(config["url"], credentials["id"], credentials["api_key"], name)
    inbox = asyncio.Queue()

    async def on_event(kind, data):
        if kind == "message":
            await inbox.put(data["payload"])

    await agent.start(on_event)
    return agent, inbox


async def next_from(inbox, sender, starts="", timeout=10):
    async with asyncio.timeout(timeout):
        while True:
            payload = await inbox.get()
            if payload["sender_name"] == sender and payload["body"].startswith(starts):
                return payload


async def scenario(config):
    run = uuid.uuid4().hex[:6]
    credentials = await register(config["url"], config["registration_token"], f"bench-referee-{run}", "Bench live test")
    ref_agent = SwitchAgent(config["url"], credentials["id"], credentials["api_key"], f"bench-referee-{run}")
    reporter, inbox = await connect(config, f"bench-reporter-{run}")
    referee = Referee(ref_agent)
    task = None
    try:
        await referee.start()
        room_id = await find_or_create_room(ref_agent, f"bench-live-{run}", None)
        assert await find_or_create_room(ref_agent, f"bench-live-{run}", None) == room_id
        await ref_agent.op("invite_agent_to_room", room_id=room_id, agent_name=reporter.name)
        await referee.join(room_id)
        task = asyncio.create_task(referee.serve())
        await reporter.op("connect_to_room", room_id=room_id)

        ticket = await reporter.op(
            "send_targeted_message",
            body="ticket: Invoice totals round the wrong way\nSeen in invoice.py.",
            target_names=[ref_agent.name],
        )
        assert ticket["target_statuses"] == {ref_agent.name: "live"}
        ack = await next_from(inbox, ref_agent.name, "Logged")

        await reporter.op("send_targeted_message", body="what do you do?", target_names=[ref_agent.name])
        help_reply = await next_from(inbox, ref_agent.name, "I take tickets")
        return referee, ticket, ack, help_reply
    finally:
        if task:
            task.cancel()
        await asyncio.gather(*(t for t in [task] if t), return_exceptions=True)
        await reporter.close()
        await ref_agent.close()


def test_referee_answers_tickets(config):
    referee, ticket, ack, help_reply = asyncio.run(scenario(config))
    [logged] = referee.tickets.values()
    assert logged.title == "Invoice totals round the wrong way"
    assert ack["body"].startswith(f"Logged {logged.id}: Invoice totals round the wrong way")
    assert ack["thread_id"] == ticket["event_id"]
    assert "ticket:" in help_reply["body"]


def fix_rounding(folder):
    path = Path(folder) / "invoice/totals.py"
    source = path.read_text()
    source = source.replace(
        '"""Invoice totals for the payments service."""\n',
        '"""Invoice totals for the payments service."""\n\nfrom decimal import ROUND_HALF_UP, Decimal\n',
    )
    source = source.replace(
        "    return round(subtotal * (1 + tax_rate), 2)",
        '    total = Decimal(str(subtotal)) * (1 + Decimal(str(tax_rate)))\n'
        '    return float(total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))',
    )
    path.write_text(source)


async def tryout_scenario(config, tmp_path):
    run = uuid.uuid4().hex[:6]
    names = {k: f"bench-{k}-{run}" for k in ("referee", "fixer", "silent", "reporter")}
    agents = {}
    for key, name in names.items():
        credentials = await register(config["url"], config["registration_token"], name, "Bench live test")
        agents[key] = SwitchAgent(config["url"], credentials["id"], credentials["api_key"], name)
    bench = Bench(
        [names["fixer"], names["silent"]],
        init_repo(SAMPLE, tmp_path / "origin"),
        tmp_path / "tryouts",
        minutes=0.25,
        hidden=SAMPLE_FIXTURE / "hidden",
        role_minutes=0.5,
        poll_seconds=0.5,
    )
    referee = Referee(agents["referee"], bench=bench)
    fixer = StubCandidate(agents["fixer"], names["referee"], work=fix_rounding)
    silent = StubCandidate(agents["silent"], names["referee"])
    reporter_inbox = asyncio.Queue()

    async def reporter_event(kind, data):
        if kind == "message":
            await reporter_inbox.put(data["payload"])

    task = None
    try:
        await referee.start()
        await fixer.start()
        await silent.start()
        await agents["reporter"].start(reporter_event)
        room_id = await find_or_create_room(agents["referee"], f"bench-live-{run}", None)
        await agents["referee"].op("invite_agent_to_room", room_id=room_id, agent_name=names["reporter"])
        await referee.join(room_id)
        task = asyncio.create_task(referee.serve())
        await agents["reporter"].op("connect_to_room", room_id=room_id)
        await agents["reporter"].op(
            "send_targeted_message",
            body="ticket: Invoice totals round half-cents the wrong way\n2.675 becomes 2.67, expected 2.68.",
            target_names=[names["referee"]],
        )
        async with asyncio.timeout(60):
            while True:
                payload = await reporter_inbox.get()
                if payload["sender_name"] == names["referee"] and payload["body"].startswith("Scorecard"):
                    scorecard = payload["body"]
                    break
        async def say(body):
            await agents["reporter"].op("send_targeted_message", body=body, target_names=[names["referee"]])
            async with asyncio.timeout(60):
                while True:
                    payload = await reporter_inbox.get()
                    if payload["sender_name"] == names["referee"] and (
                        "now holds the fixer role" in payload["body"] or "didn't" in payload["body"]
                    ):
                        return payload["body"]

        async def holders():
            return roles.holders(await agents["referee"].op("get_room_detail", room_id=room_id), "fixer")

        moves = [(await say(f"pick {names['fixer']}"), await holders())]
        moves.append((await say(f"pick {names['silent']} anyway"), await holders()))

        [tryout] = referee.tryouts.values()
        rooms = {name: e.room_id for name, e in tryout.entries.items()}
        details = {name: await agents["referee"].op("get_room_detail", room_id=r) for name, r in rooms.items()}
        try:
            await agents["silent"].op("read_context", room_id=rooms[names["fixer"]])
            peek = "allowed"
        except OpError as error:
            peek = error.status
        return names, tryout, details, peek, fixer, silent, scorecard, referee._standings_doc, moves
    finally:
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        for agent in agents.values():
            await agent.close()


def test_sealed_tryout(config, tmp_path):
    names, tryout, details, peek, fixer, silent, scorecard, standings_doc, moves = asyncio.run(
        tryout_scenario(config, tmp_path)
    )
    fixed = tryout.entries[names["fixer"]]
    quiet = tryout.entries[names["silent"]]

    for name, detail in details.items():
        assert sorted(detail["agent_names"]) == sorted([names["referee"], name])
    assert peek == 403
    assert len(fixer.briefs) == 1 and len(silent.briefs) == 1

    assert fixed.outcome == "done"
    assert fixed.snapshot.files == ("invoice/totals.py",)
    assert not fixed.snapshot.referee_committed
    assert quiet.outcome == "timed_out"
    assert not quiet.snapshot.changed

    [top, last] = tryout.scores
    assert (top.candidate, top.passed, top.total) == (names["fixer"], 10, 10)
    assert (last.candidate, last.passed) == (names["silent"], 6)
    assert f"1. {names['fixer']}: 10/10 hidden tests" in scorecard
    assert standings_doc
    assert f"pick {names['fixer']}" in scorecard

    (first, after_first), (second, after_second) = moves
    assert first.startswith(f"{names['fixer']} now holds the fixer role.") and after_first == [names["fixer"]]
    assert second.startswith(f"{names['silent']} now holds the fixer role (was {names['fixer']})."), (
        second, silent.errors, fixer.errors
    )
    assert after_second == [names["silent"]]
    assert any(r.endswith("then release_role()") for r in fixer.role_requests)

    sys.path.insert(0, str(fixed.workspace.path))
    try:
        from invoice.totals import invoice_total

        assert invoice_total([(1, 2.675)]) == 2.68
    finally:
        sys.path.remove(str(fixed.workspace.path))
        sys.modules.pop("invoice.totals", None)
        sys.modules.pop("invoice", None)


async def failover_scenario(config):
    from referee.scoring.card import Score
    from referee.tickets import Ticket

    run = uuid.uuid4().hex[:6]
    names = {k: f"bench-{k}-{run}" for k in ("referee", "a", "b", "c", "person")}
    agents = {}
    for key, name in names.items():
        credentials = await register(config["url"], config["registration_token"], name, "Bench live test")
        agents[key] = SwitchAgent(config["url"], credentials["id"], credentials["api_key"], name)
    bench = Bench(
        [names[k] for k in "abc"], role_minutes=0.5, poll_seconds=0.5, response_seconds=10, minutes=1
    )
    referee = Referee(agents["referee"], bench=bench)
    stubs = {k: StubCandidate(agents[k], names["referee"]) for k in "abc"}
    inbox = asyncio.Queue()

    async def person_event(kind, data):
        if kind == "message" and data["payload"]["sender_name"] == names["referee"]:
            await inbox.put(data["payload"]["body"])

    async def said(start, timeout=40):
        async with asyncio.timeout(timeout):
            while True:
                body = await inbox.get()
                if body.startswith(start):
                    return body

    task = None
    try:
        await referee.start()
        for stub in stubs.values():
            await stub.start()
        await agents["person"].start(person_event)
        room_id = await find_or_create_room(agents["referee"], f"bench-failover-{run}", None)
        for k in ("a", "b", "c", "person"):
            await agents["referee"].op("invite_agent_to_room", room_id=room_id, agent_name=names[k])
        await referee.join(room_id)
        ticket = Ticket("T-000001", "Sample", "", names["person"], "m")
        referee.tickets[ticket.id] = ticket
        referee.results[ticket.id] = [
            Score(names[k], 10, 10, 5, 10.0 * i, "done", "c" * 40) for i, k in enumerate("abc", 1)
        ]
        await agents["a"].op("connect_to_room", room_id=room_id)
        await agents["a"].op("assume_role", role="fixer")
        await agents["person"].op("connect_to_room", room_id=room_id)
        task = asyncio.create_task(referee.serve())
        await asyncio.sleep(1.5)

        # 1. The holder dies with work waiting.
        await agents["person"].op("send_targeted_message", body="the export button is broken", target_roles=["fixer"])
        await asyncio.sleep(1)
        stubs["a"].die()
        killed = time.monotonic()
        first = await said("Failover done")
        first_seconds = time.monotonic() - killed
        after_first = roles.holders(await agents["referee"].op("get_room_detail", room_id=room_id), "fixer")
        await asyncio.sleep(1)
        resent_to_b = [m for m in stubs["b"].addressed if "export button" in m]

        # 2. The new holder is alive but never answers.
        await agents["person"].op("send_targeted_message", body="totals are off by a cent", target_roles=["fixer"])
        second = await said("Failover done")
        after_second = roles.holders(await agents["referee"].op("get_room_detail", room_id=room_id), "fixer")
        await asyncio.sleep(1)
        resent_to_c = [m for m in stubs["c"].addressed if "off by a cent" in m]
        return names, first, first_seconds, after_first, resent_to_b, second, after_second, resent_to_c
    finally:
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        for key, agent in agents.items():
            await agent.close()


def test_failover(config):
    names, first, first_seconds, after_first, resent_to_b, second, after_second, resent_to_c = asyncio.run(
        failover_scenario(config)
    )
    assert after_first == [names["b"]], first
    assert f"{names['b']} now holds the fixer role (was {names['a']})" in first
    assert "Re-sent 1 waiting message(s)" in first
    assert resent_to_b and "asked: @fixer the export button is broken" in resent_to_b[0]
    assert "didn't answer" not in first, "death should be noticed before the answer deadline"
    assert first_seconds < 10

    assert after_second == [names["c"]], second
    assert f"(was {names['b']})" in second
    assert resent_to_c, second
