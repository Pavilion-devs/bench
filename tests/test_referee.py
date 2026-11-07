"""The Referee's message handling and tryouts, with a fake Switch agent."""

import asyncio
from pathlib import Path

from referee.agent import Bench, Referee
from referee.workspaces import git, init_repo

ROOM = "room-1"
SAMPLE = Path(__file__).resolve().parent.parent / "tickets/sample-invoice/repo"


class FakeAgent:
    name = "bench-referee"

    def __init__(self):
        self.calls = []
        self.room = None
        self.rooms = 0

    async def op(self, op_name, /, **args):
        if op_name == "connect_to_room":
            self.room = args["room_id"]
        self.calls.append((op_name, self.room, args))
        if op_name == "create_room":
            self.rooms += 1
            return {"id": f"tryout-room-{self.rooms}"}
        if op_name == "create_room_document":
            return {"document_id": f"doc-{len(self.calls)}"}
        return {}

    def posts(self, room=None):
        return [
            (r, a) for op, r, a in self.calls
            if op in ("post_message", "send_targeted_message") and (room is None or r == room)
        ]


def event(body, sender="ana", message_id="sw_1", addressed=True, room=ROOM, thread_id=None):
    return {
        "room_id": room,
        "payload": {
            "addressed": addressed,
            "sender_name": sender,
            "message_id": message_id,
            "body": body,
            "thread_id": thread_id,
        },
    }


def handle(referee, *events):
    for e in events:
        asyncio.run(referee.handle(e))
    return referee.agent.posts()


def test_ticket_gets_threaded_ack():
    referee = Referee(FakeAgent(), ROOM)
    posts = handle(referee, event("@bench-referee ticket: Fix login"))
    room, args = posts[0]
    assert room == ROOM
    assert args["thread_id"] == "sw_1"
    assert "Fix login" in args["body"]
    assert "no candidates" in posts[1][1]["body"]
    assert len(referee.tickets) == 1


def test_reply_stays_in_existing_thread():
    posts = handle(Referee(FakeAgent(), ROOM), event("ticket: x", thread_id="sw_root"))
    assert posts[0][1]["thread_id"] == "sw_root"


def test_other_message_gets_usage():
    referee = Referee(FakeAgent(), ROOM)
    posts = handle(referee, event("@bench-referee hi"))
    assert "ticket:" in posts[0][1]["body"]
    assert referee.tickets == {}


def test_ignored_messages():
    posts = handle(
        Referee(FakeAgent(), ROOM),
        event("ticket: x", addressed=False, message_id="a"),
        event("ticket: x", sender="bench-referee", message_id="b"),
        event("ticket: x", room="other-room", message_id="c"),
    )
    assert posts == []


def test_direct_mention_counts_even_when_not_addressed():
    referee = Referee(FakeAgent(), ROOM)
    handle(referee, event("@bench-referee ticket: x", addressed=False, message_id="m1"))
    handle(referee, event("hey @bench-referee-2 ticket: y", addressed=False, message_id="m2"))
    assert [t.title for t in referee.tickets.values()] == ["x"]


def test_replayed_message_is_answered_once():
    posts = handle(Referee(FakeAgent(), ROOM), event("ticket: x"), event("ticket: x"))
    assert len(posts) == 2  # the ack and "no candidates", once


def run_tryout(tmp_path, candidates, act, minutes=1 / 60):
    """Files a ticket, lets act(referee) play the candidates, and waits for the end."""
    bench = Bench(candidates, init_repo(SAMPLE, tmp_path / "origin"), tmp_path / "tryouts", minutes)
    referee = Referee(FakeAgent(), ROOM, bench)

    async def scenario():
        await referee.handle(event("ticket: Fix rounding\nHalf cents round down."))
        while len(referee._entries_by_room) < len(candidates):
            await asyncio.sleep(0.01)
        await act(referee)
        await referee.wait_for_tryouts()

    asyncio.run(scenario())
    return referee, next(iter(referee.tryouts.values()))


def entry_event(referee, candidate, body, message_id):
    entry = next(e for e in referee._entries_by_room.values() if e.candidate == candidate)
    return entry, event(body, sender=candidate, message_id=message_id, room=entry.room_id, addressed=False)


def test_tryout_is_sealed_and_briefed(tmp_path):
    async def act(referee):
        for name in ("claude", "codex"):
            _, done = entry_event(referee, name, "done", f"d-{name}")
            await referee.handle(done)

    referee, tryout = run_tryout(tmp_path, ["claude", "codex"], act)
    creates = [a for op, _, a in referee.agent.calls if op == "create_room"]
    assert [c["agent_names"] for c in creates] == [["bench-referee", "claude"], ["bench-referee", "codex"]]
    for name, entry in tryout.entries.items():
        [(room, brief)] = [p for p in referee.agent.posts(entry.room_id) if p[1].get("target_names")]
        assert brief["target_names"] == [name]
        assert str(entry.workspace.path) in brief["body"]
        assert entry.workspace.branch in brief["body"]
    assert tryout.entries["claude"].workspace.path != tryout.entries["codex"].workspace.path


def test_done_collects_from_git_and_reports(tmp_path):
    async def act(referee):
        entry, done = entry_event(referee, "claude", "All done, fixed it.", "d1")
        (entry.workspace.path / "invoice/totals.py").write_text("fixed = True\n")
        git(entry.workspace.path, "commit", "-qam", "fix rounding")
        await referee.handle(done)

    referee, tryout = run_tryout(tmp_path, ["claude"], act, minutes=1)
    entry = tryout.entries["claude"]
    assert entry.outcome == "done"
    assert entry.snapshot.files == ("invoice/totals.py",)
    main = [a["body"] for _, a in referee.agent.posts(ROOM)]
    assert any(b.startswith("Tryouts for") for b in main)
    assert any(b.startswith("claude finished in") and "1 file" in b for b in main)
    assert main[-1].startswith("All tryouts") and "1 of 1 changed" in main[-1]
    assert all(a.get("thread_id") == "sw_1" for _, a in referee.agent.posts(ROOM))


def test_silent_candidate_times_out_with_uncommitted_work(tmp_path):
    async def act(referee):
        entry, _ = entry_event(referee, "codex", "", "unused")
        (entry.workspace.path / "notes.txt").write_text("half way\n")

    _, tryout = run_tryout(tmp_path, ["codex"], act)
    entry = tryout.entries["codex"]
    assert entry.outcome == "timed_out"
    assert entry.snapshot.referee_committed
    assert entry.snapshot.files == ("notes.txt",)


def test_questions_get_a_fixed_answer_and_others_are_ignored(tmp_path):
    async def act(referee):
        entry, question = entry_event(referee, "claude", "Which file is it in?", "q1")
        await referee.handle(question)
        stranger = event("done", sender="someone-else", message_id="s1", room=entry.room_id)
        await referee.handle(stranger)
        assert entry.outcome == "working"
        await referee.handle(entry_event(referee, "claude", "done", "d1")[1])

    referee, tryout = run_tryout(tmp_path, ["claude"], act, minutes=1)
    entry = tryout.entries["claude"]
    replies = [a["body"] for _, a in referee.agent.posts(entry.room_id) if "target_names" not in a]
    assert replies == ["I can't answer questions during a tryout. Work from the ticket, and reply done when you're finished."]
    assert entry.outcome == "done"


def test_late_done_after_timeout_changes_nothing(tmp_path):
    async def act(referee):
        pass

    referee, tryout = run_tryout(tmp_path, ["claude"], act)
    entry = tryout.entries["claude"]
    asyncio.run(referee.handle(entry_event(referee, "claude", "done", "late")[1]))
    assert entry.outcome == "timed_out"


class PostingAgent(FakeAgent):
    async def op(self, op_name, /, **args):
        result = await super().op(op_name, **args)
        return {"event_id": "sw_posted"} if op_name == "post_message" else result


def test_ticket_from_file_is_posted_then_handled():
    referee = Referee(PostingAgent(), ROOM)
    ticket = asyncio.run(referee.file_ticket("ticket: From a file\nDetails."))
    assert ticket.title == "From a file"
    posts = referee.agent.posts(ROOM)
    assert posts[0][1] == {"body": "ticket: From a file\nDetails."}
    assert posts[1][1]["thread_id"] == "sw_posted"
    # The Referee's own post coming back on the stream is ignored.
    assert asyncio.run(referee.handle(event("ticket: From a file", sender="bench-referee", message_id="sw_posted"))) is None
