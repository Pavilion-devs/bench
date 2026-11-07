"""Replay mode: recordings, the replay candidate, and a full replay on the dev stack."""

import asyncio
import json
from pathlib import Path

import pytest

from referee import fixtures
from referee.workspaces import git
from replay.candidate import ReplayCandidate
from replay.record import RUNS, record

ROOT = Path(__file__).resolve().parent.parent


class Agent:
    name = "replay-x"

    def __init__(self):
        self.calls = []
        self._tasks = set()

    async def op(self, op_name, /, **args):
        self.calls.append((op_name, args))
        return {}


def make_repo(path, text):
    path.mkdir(parents=True)
    git(path, "init", "-q", "-b", "main")
    (path / "app.py").write_text(text)
    git(path, "add", "-A")
    git(path, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "Before the fix")
    return path


@pytest.fixture
def recorded(tmp_path):
    tryout = tmp_path / "T-123456"
    fixed = make_repo(tryout / "bench-claude", "x = 1\n")
    (fixed / "app.py").write_text("x = 2\n")
    git(fixed, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "Set x to 2")
    (fixed / "app.py").write_text("x = 3\n")
    git(fixed, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "Later work, not scored")
    make_repo(tryout / "bench-codex", "x = 1\n")
    spec = record(tryout, "demo", "demo", "test", dest=tmp_path / "runs")
    return spec, tmp_path / "runs/demo"


def test_record_keeps_the_scored_commit_and_marks_silent_ones(recorded):
    spec, run_dir = recorded
    claude, codex = spec["candidates"]
    assert (claude["name"], claude["message"], claude["patch"]) == ("claude", "Set x to 2", "claude.patch")
    assert claude["seconds"] >= 1
    assert "+x = 2" in (run_dir / "claude.patch").read_text()
    assert (codex["name"], codex["patch"], codex["seconds"]) == ("codex", None, None)
    assert json.loads((run_dir / "run.json").read_text()) == spec


def test_replay_candidate_applies_the_recorded_commit(recorded, tmp_path):
    spec, run_dir = recorded
    workspace = make_repo(tmp_path / "work", "x = 1\n")
    agent, notes = Agent(), []
    candidate = ReplayCandidate(agent, "ref", spec["candidates"][0] | {"seconds": 0}, run_dir, say=notes.append)
    asyncio.run(candidate._attempt("room-1", str(workspace)))
    assert (workspace / "app.py").read_text() == "x = 2\n"
    assert git(workspace, "log", "-1", "--format=%s") == "Set x to 2"
    assert agent.calls[-1] == ("send_targeted_message", {"body": "done", "target_names": ["ref"]})


def test_silent_candidate_stays_silent(recorded, tmp_path):
    spec, run_dir = recorded
    workspace = make_repo(tmp_path / "work", "x = 1\n")
    agent = Agent()
    asyncio.run(ReplayCandidate(agent, "ref", spec["candidates"][1], run_dir, say=lambda _: None)._attempt("r", str(workspace)))
    assert agent.calls == [] and git(workspace, "log", "--format=%s") == "Before the fix"


def test_candidate_answers_role_work_and_obeys_role_calls():
    agent = Agent()
    candidate = ReplayCandidate(agent, "ref", {}, ".", say=lambda _: None)

    async def go():
        call = "Switch call: connect_to_room(room_id=main) then release_role() then assume_role(role=\"fixer\")"
        await candidate._on_event("message", {"room_id": "main", "payload": {"addressed": True, "sender_name": "ref", "body": call}})
        await candidate._on_event(
            "message",
            {"room_id": "main", "payload": {"addressed": True, "sender_name": "ana", "message_id": "m1", "body": "@fixer help"}},
        )
        await candidate._on_event("message", {"room_id": "main", "payload": {"addressed": False, "sender_name": "ana", "body": "@fixer"}})
        await asyncio.gather(*candidate._tasks)

    asyncio.run(go())
    ops = [op for op, _ in agent.calls]
    assert ops.count("assume_role") == 1 and ops.count("post_message") == 1
    assert dict(agent.calls)["post_message"]["thread_id"] == "m1"


@pytest.mark.parametrize("name", sorted(p.name for p in RUNS.iterdir() if p.is_dir()))
def test_recordings_apply_to_their_ticket(name, tmp_path):
    spec = json.loads((RUNS / name / "run.json").read_text())
    if not (ROOT / "tickets" / spec["ticket"] / "repo").exists() and not (ROOT / ".bench/fixtures" / spec["ticket"]).exists():
        pytest.skip("real ticket not built yet")
    fx = fixtures.load(ROOT / "tickets" / spec["ticket"], ROOT / ".bench/fixtures")
    for candidate in spec["candidates"]:
        if candidate["patch"]:
            git(fx.repo, "apply", "--check", str(RUNS / name / candidate["patch"]))


@pytest.mark.live
def test_full_replay_on_the_dev_stack(monkeypatch):
    from replay.__main__ import replay
    from tests.test_live import server_up
    from referee.config import DEV_URL

    if not server_up(DEV_URL):
        pytest.skip("Switch dev stack is not running")
    monkeypatch.delenv("SWITCH_URL", raising=False)
    assert asyncio.run(replay("sample-invoice", speed=8, server="dev")) == 0
