"""Picks and role moves, with a fake Switch room that tracks who holds @fixer."""

import asyncio
from types import SimpleNamespace

from referee import roles
from referee.agent import Bench, Referee
from referee.tickets import Ticket

ROOM = "main-room"
TICKET = Ticket("T-aaaaaa", "Fix rounding", "", "ana", "sw_t")


class RoomAgent:
    """Fake Switch: one room with role 'fixer'. `obeys` says which agents follow requests."""

    name = "bench-referee"

    def __init__(self, holders=(), members=("bench-referee",), obeys=("claude", "codex"), has_role=True):
        self.holders = list(holders)
        self.members = list(members)
        self.obeys = set(obeys)
        self.has_role = has_role
        self.calls = []

    async def op(self, op_name, /, **args):
        self.calls.append((op_name, args))
        if op_name == "get_room_detail":
            role = [{"name": "fixer", "held_by": list(self.holders)}] if self.has_role else []
            return {"name": "bench-payments", "agent_names": list(self.members), "roles": role}
        if op_name == "define_role":
            self.has_role = True
        if op_name == "invite_agent_to_room":
            self.members.append(args["agent_name"])
        if op_name == "send_targeted_message" and args.get("target_names"):
            [target] = args["target_names"]
            if target in self.obeys and "release_role()" in args["body"] and target in self.holders:
                self.holders.remove(target)
            if target in self.obeys and "assume_role(" in args["body"] and target in self.members:
                self.holders.append(target)
        return {}

    def said(self):
        return [a["body"] for op, a in self.calls if op == "post_message"]

    def asked(self):
        return [(a["target_names"][0], a["body"]) for op, a in self.calls if op == "send_targeted_message"]


def scored(name, passed=10, total=10):
    return SimpleNamespace(candidate=name, passed=passed, total=total, solved=passed == total)


def referee_with(agent, minutes=0.02, **bench):
    referee = Referee(agent, ROOM, Bench(["claude", "codex"], role_minutes=minutes, poll_seconds=0.01, **bench))
    referee.room_name = "bench-payments"
    referee.tickets[TICKET.id] = TICKET
    referee.results[TICKET.id] = [scored("claude"), scored("codex", passed=7)]
    return referee


def pick(referee, body, sender="ana"):
    async def go():
        result = await referee.handle(
            {"room_id": ROOM, "payload": {"addressed": True, "sender_name": sender, "message_id": f"{sender}:{body}", "body": body}}
        )
        await referee.wait_for_tryouts()
        return result

    return asyncio.run(go())


def test_parse_pick():
    assert roles.parse_pick("@bench-referee pick claude") == roles.Pick("claude", None, False)
    assert roles.parse_pick("pick @codex for T-74e40f anyway") == roles.Pick("codex", "T-74e40f", True)
    assert roles.parse_pick("Pick claude.") == roles.Pick("claude", None, False)
    for body in ["pick", "pickles", "please pick claude", "pick claude and codex", "ticket: pick claude"]:
        assert roles.parse_pick(body) is None, body


def test_holders_handles_both_shapes():
    detail = {"roles": [{"name": "fixer", "held_by": [{"name": "a"}, "b"]}, {"name": "other", "held_by": ["c"]}]}
    assert roles.holders(detail, "fixer") == ["a", "b"]
    assert roles.holders({"roles": []}, "fixer") == []


def test_join_defines_the_role_once():
    agent = RoomAgent(has_role=False)
    referee = Referee(agent, bench=Bench())
    asyncio.run(referee.join(ROOM))
    asyncio.run(referee.join(ROOM))
    defines = [a for op, a in agent.calls if op == "define_role"]
    assert len(defines) == 1 and defines[0]["exclusive"] is True and defines[0]["name"] == "fixer"
    assert referee.room_name == "bench-payments"


def test_first_pick_invites_and_assumes():
    agent = RoomAgent()
    pick(referee_with(agent), "@bench-referee pick claude")
    assert agent.holders == ["claude"]
    assert ("invite_agent_to_room", {"room_id": ROOM, "agent_name": "claude"}) in agent.calls
    [(target, body)] = agent.asked()
    assert target == "claude" and 'assume_role(role="fixer")' in body
    assert agent.said()[-1].startswith("claude now holds the fixer role. Took")


def test_handoff_releases_then_assumes_in_order():
    agent = RoomAgent(holders=["codex"], members=["bench-referee", "codex", "claude"])
    pick(referee_with(agent), "pick claude")
    assert agent.holders == ["claude"]
    assert [(t, "assume_role(" in b) for t, b in agent.asked()] == [("codex", False), ("claude", True)]
    assert agent.said()[0] == "Moving the fixer role from codex to claude. I'll confirm once Switch shows the change."
    assert agent.said()[-1].startswith("claude now holds the fixer role (was codex).")
    assert not [c for c in agent.calls if c[0] == "invite_agent_to_room"]


def test_holder_that_never_releases_keeps_the_role():
    agent = RoomAgent(holders=["codex"], members=["bench-referee", "codex", "claude"], obeys=["claude"])
    referee = referee_with(agent)
    pick(referee, "pick claude")
    assert agent.holders == ["codex"]
    assert [t for t, _ in agent.asked()] == ["codex"]
    assert agent.said()[-1].startswith("codex didn't give up the fixer role")
    assert referee._moving is False


def test_winner_that_never_assumes_leaves_the_seat_empty():
    agent = RoomAgent(holders=["codex"], members=["bench-referee", "codex", "claude"], obeys=["codex"])
    pick(referee_with(agent), "pick claude")
    assert agent.holders == []
    assert agent.said()[-1].startswith("claude didn't take the fixer role")


def test_already_holding():
    agent = RoomAgent(holders=["claude"], members=["bench-referee", "claude"])
    pick(referee_with(agent), "pick claude")
    assert agent.said() == ["claude already holds the fixer role."]
    assert agent.asked() == []


def test_refused_picks():
    cases = [
        ("pick claude", "codex", "candidates can't pick"),
        ("pick nobody", "ana", "nobody didn't try out for T-aaaaaa"),
        ("pick codex", "ana", "codex passed 7 of 10 hidden tests"),
        ("pick claude for T-bbbbbb", "ana", "no scorecard for T-bbbbbb"),
    ]
    for body, sender, reason in cases:
        agent = RoomAgent()
        assert pick(referee_with(agent), body, sender) is None
        assert reason in agent.said()[0], body
        assert agent.holders == []


def test_anyway_overrides_a_failing_score():
    agent = RoomAgent()
    pick(referee_with(agent), "pick codex anyway")
    assert agent.holders == ["codex"]


def test_pickers_allowlist():
    agent = RoomAgent()
    referee = referee_with(agent, pickers=["lead"])
    pick(referee, "pick claude", sender="ana")
    assert "ana isn't one of the people who pick" in agent.said()[0]
    pick(referee, "pick claude", sender="lead")
    assert agent.holders == ["claude"]


def test_no_scored_tryout_yet():
    agent = RoomAgent()
    referee = referee_with(agent)
    referee.results.clear()
    pick(referee, "pick claude")
    assert "no tryout has been scored yet" in agent.said()[0]


def test_who():
    agent = RoomAgent(holders=["claude"])
    pick(referee_with(agent), "@bench-referee who has fixer?")
    assert agent.said() == ["The fixer role is held by claude."]


def test_referee_text_never_mentions_the_role():
    """Switch reads "@fixer" as a role mention and wakes the holder, so it must never appear."""
    ticket = TICKET
    texts = [
        roles.how_to_pick("bench-referee", "fixer", [scored("claude")]),
        roles.release_request("codex", "fixer", "room", "id"),
        roles.assume_request("claude", "fixer", "room", "id", ticket),
        roles.moving("claude", "fixer", "codex"),
        roles.moved("claude", "fixer", "codex", 3),
        roles.already("claude", "fixer"),
        roles.did_not_release("codex", "fixer", 3),
        roles.did_not_assume("claude", "fixer", 3),
        roles.who("fixer", ["claude"]),
        roles.who("fixer", []),
    ]
    assert not [t for t in texts if "@fixer" in t]


def test_assume_request_drops_old_leases_first():
    body = roles.assume_request("claude", "fixer", "room", "id", TICKET)
    assert body.index("release_role") < body.index("assume_role")


def test_switch_system_notices_are_ignored():
    agent = RoomAgent()
    referee = referee_with(agent)
    pick(referee, "@bench-referee no agent currently holds the fixer role", sender="Switch")
    assert agent.said() == []


class WatchedRoom(RoomAgent):
    """Adds agent statuses and role-targeted messages to the fake room."""

    def __init__(self, statuses=None, **kwargs):
        super().__init__(**kwargs)
        self.statuses = statuses or {}

    async def op(self, op_name, /, **args):
        result = await super().op(op_name, **args)
        if op_name == "get_room_detail":
            result["agent_statuses"] = dict(self.statuses)
        return result

    def to_role(self):
        return [a for op, a in self.calls if op == "send_targeted_message" and a.get("target_roles")]

    def asked(self):
        return [(a["target_names"][0], a["body"]) for op, a in self.calls if op == "send_targeted_message" and a.get("target_names")]


def watched(agent, response_seconds=60):
    referee = referee_with(agent, response_seconds=response_seconds)
    referee._holder = agent.holders[0] if agent.holders else None
    return referee


def work(referee, body="@fixer the export button is broken", sender="ana", message_id="w1", age=0):
    referee._note_role_work({"sender_name": sender, "body": body, "message_id": message_id, "thread_id": None})
    referee._pending[message_id]["at"] -= age


def test_disconnected_holder_fails_over_and_resends():
    agent = WatchedRoom(
        holders=["claude"], members=["bench-referee", "claude", "codex"], statuses={"claude": "disconnected"}
    )
    referee = watched(agent)
    work(referee)
    # A dead custom agent's seat frees on its own; here the fake frees it on the release request.
    assert asyncio.run(referee.check_holder()) == "is disconnected"
    assert agent.holders == ["codex"]
    [resent] = agent.to_role()
    assert resent["target_roles"] == ["fixer"] and "ana asked: @fixer the export button is broken" in resent["body"]
    assert agent.said()[0].startswith("Failover: claude is disconnected. Moving the fixer role to the runner-up, codex.")
    assert agent.said()[-1].startswith("Failover done: codex now holds the fixer role (was claude).")
    assert "Re-sent 1 waiting message(s)" in agent.said()[-1]
    assert referee._pending == {}


def test_seat_that_empties_by_itself_fails_over():
    agent = WatchedRoom(holders=["claude"], members=["bench-referee", "claude", "codex"])
    referee = watched(agent)
    agent.holders = []
    assert asyncio.run(referee.check_holder()) == "dropped out of the role"
    assert agent.holders == ["codex"]
    assert not [t for t, _ in agent.asked() if t == "claude"]


def test_silent_holder_is_released_after_the_deadline():
    agent = WatchedRoom(holders=["claude"], members=["bench-referee", "claude", "codex"], statuses={"claude": "live"})
    referee = watched(agent, response_seconds=60)
    work(referee, age=30)
    assert asyncio.run(referee.check_holder()) is None
    work(referee, message_id="w2", age=61)
    assert asyncio.run(referee.check_holder()).startswith("didn't answer within 1 min")
    assert agent.holders == ["codex"]
    assert len(agent.to_role()) == 2


def test_holder_that_speaks_clears_waiting_work():
    agent = WatchedRoom(holders=["claude"], statuses={"claude": "live"})
    referee = watched(agent, response_seconds=60)
    work(referee, age=61)
    referee._note_role_work({"sender_name": "claude", "body": "on it", "message_id": "r1"})
    assert asyncio.run(referee.check_holder()) is None
    assert agent.holders == ["claude"]


def test_stuck_holder_means_work_goes_to_runner_up_by_name():
    agent = WatchedRoom(
        holders=["claude"], members=["bench-referee", "claude", "codex"], obeys=["codex"], statuses={"claude": "live"}
    )
    referee = watched(agent, response_seconds=60)
    work(referee, age=61)
    asyncio.run(referee.check_holder())
    assert agent.holders == ["claude"]
    forwarded = [(t, b) for t, b in agent.asked() if t == "codex"]
    assert forwarded and "ana asked:" in forwarded[-1][1]
    assert agent.to_role() == []
    assert agent.said()[-1].startswith("claude still holds the fixer role and isn't answering")


def test_no_runner_up():
    agent = WatchedRoom(holders=["claude"], statuses={"claude": "disconnected"})
    referee = watched(agent)
    referee.results[TICKET.id] = [scored("claude")]
    asyncio.run(referee.check_holder())
    assert "there's no runner-up" in agent.said()[0]


def test_healthy_holder_with_no_work_is_left_alone():
    agent = WatchedRoom(holders=["claude"], statuses={"claude": "live"})
    referee = watched(agent)
    assert asyncio.run(referee.check_holder()) is None
    assert agent.calls == [("get_room_detail", {"room_id": ROOM})]


def test_role_mentions():
    assert roles.mentions_role("@fixer help", "fixer")
    assert roles.mentions_role("hey (@fixer) help", "fixer")
    assert not roles.mentions_role("the fixer role", "fixer")
    assert not roles.mentions_role("@fixers", "fixer")
    assert not roles.mentions_role("email@fixer.com", "fixer")


def test_dead_runner_up_is_skipped():
    agent = WatchedRoom(
        holders=["claude"],
        members=["bench-referee", "claude", "codex", "opencode"],
        obeys=["claude", "opencode"],
        statuses={"claude": "disconnected", "codex": "disconnected"},
    )
    referee = watched(agent)
    referee.results[TICKET.id] = [scored("claude"), scored("codex"), scored("opencode")]
    asyncio.run(referee.check_holder())
    assert agent.holders == ["opencode"]


def test_holder_role_notice_during_failover_keeps_the_waiting_work():
    class NoticeRoom(WatchedRoom):
        async def op(self, op_name, /, **args):
            result = await super().op(op_name, **args)
            if op_name == "send_targeted_message" and "release_role()" in args.get("body", "") and not args.get("target_roles"):
                # Switch's notice of the release arrives under the holder's own name.
                referee._note_role_work({"sender_name": "claude", "body": "released the fixer role", "message_id": "n1"})
            return result

    agent = NoticeRoom(holders=["claude"], members=["bench-referee", "claude", "codex"], statuses={"claude": "live"})
    referee = watched(agent, response_seconds=60)
    work(referee, age=61)
    asyncio.run(referee.check_holder())
    assert agent.holders == ["codex"]
    assert len(agent.to_role()) == 1


def test_console_and_switch_notices_are_not_answers():
    for body in [
        "Starting a session to handle this — one moment.",
        "Worked for 13s. 3 tool calls.",
        "assumed the `fixer` role.",
        "released the fixer role.",
    ]:
        assert roles.is_notice(body), body
    assert not roles.is_notice("Here's the fix: round half-up.")

    agent = WatchedRoom(holders=["claude"], members=["bench-referee", "claude", "codex"], statuses={"claude": "live"})
    referee = watched(agent, response_seconds=60)
    work(referee, age=61)
    referee._note_role_work({"sender_name": "claude", "body": "Starting a session to handle this", "message_id": "s1"})
    assert asyncio.run(referee.check_holder()).startswith("didn't answer")
