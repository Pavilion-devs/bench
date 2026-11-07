"""Real-ticket fixtures, built from a local two-commit repo standing in for GitHub."""

import asyncio
import json
from pathlib import Path

import pytest

from referee import fixtures
from referee.agent import Bench, Referee
from referee.scoring import run
from referee.workspaces import git

ROOT = Path(__file__).resolve().parent.parent
REAL = ["boltons-singularize-ss", "humanize-naturalsize-rollover", "more-itertools-falsy-exception"]


def commit(repo, files, message):
    for name, text in files.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(text)
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", message)
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def upstream(tmp_path):
    repo = tmp_path / "upstream"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    commit(
        repo,
        {
            ".gitignore": "src/pkg/_version.py\n",
            "src/pkg/__init__.py": "from ._version import V\n\ndef double(x):\n    return x + x + 1\n",
            "tests/test_pkg.py": "from pkg import double\n\ndef test_one():\n    assert double(1) in (2, 3)\n",
        },
        "start",
    )
    fix = commit(
        repo,
        {
            "src/pkg/__init__.py": "from ._version import V\n\ndef double(x):\n    return x + x\n",
            "tests/test_pkg.py": "from pkg import double\n\ndef test_one():\n    assert double(1) in (2, 3)\n\n"
            "def test_two():\n    assert double(2) == 4\n",
        },
        "THE FIX: double was off by one",
    )
    return repo, fix


@pytest.fixture
def tickets(tmp_path, upstream):
    repo, fix = upstream
    folder = tmp_path / "tickets" / "pkg-double"
    folder.mkdir(parents=True)
    (folder / "ticket.md").write_text("ticket: double() is off by one\nSee src/pkg.\n")
    (folder / "fixture.json").write_text(
        json.dumps(
            {
                "source": str(repo),
                "fix": fix,
                "hidden_tests": ["tests/test_pkg.py"],
                "import_paths": ["src"],
                "add_files": {"src/pkg/_version.py": "V = 1\n"},
            }
        )
    )
    return folder.parent


def test_built_repo_is_one_commit_from_before_the_fix(tickets, tmp_path):
    fx = fixtures.load(tickets / "pkg-double", tmp_path / "cache")
    assert fx.import_paths == ("src",) and fx.ticket.startswith("ticket: double()")
    log = git(fx.repo, "log", "--format=%s")
    assert log == "Before the fix"
    assert "x + x + 1" in (fx.repo / "src/pkg/__init__.py").read_text()
    assert git(fx.repo, "ls-files", "src/pkg/_version.py") == "src/pkg/_version.py"
    assert "def test_two" in (fx.hidden / "test_pkg.py").read_text()
    assert "def test_two" not in (fx.repo / "tests/test_pkg.py").read_text()


def test_no_trace_of_the_fix_in_the_candidates_repo(tickets, tmp_path, upstream):
    _, fix = upstream
    fx = fixtures.load(tickets / "pkg-double", tmp_path / "cache")
    objects = git(fx.repo, "rev-list", "--all", "--objects")
    assert fix not in objects
    assert "THE FIX" not in git(fx.repo, "log", "--all", "--format=%B")


def test_rebuilds_only_when_the_spec_changes(tickets, tmp_path):
    cache = tmp_path / "cache"
    fx = fixtures.load(tickets / "pkg-double", cache)
    marker = fx.repo / "marker"
    marker.write_text("kept")
    fixtures.load(tickets / "pkg-double", cache)
    assert marker.exists()
    spec = tickets / "pkg-double" / "fixture.json"
    spec.write_text(spec.read_text().replace('"V = 1\\n"', '"V = 2\\n"'))
    fixtures.load(tickets / "pkg-double", cache)
    assert not marker.exists()


def test_hidden_tests_fail_before_the_fix(tickets, tmp_path):
    fx = fixtures.load(tickets / "pkg-double", tmp_path / "cache")
    result = run.run_hidden_tests(fx.repo, "HEAD", fx.hidden, sandbox=False, import_paths=fx.import_paths)
    assert (result.passed, result.total, result.failures) == (1, 2, ("test_two",))


def test_available_lists_folders_with_a_ticket():
    names = fixtures.available(ROOT / "tickets")
    assert "sample-invoice" in names and set(REAL) <= set(names)


class Agent:
    name = "bench-referee"

    def __init__(self):
        self.calls = []

    async def op(self, op_name, /, **args):
        self.calls.append((op_name, args))
        if op_name == "post_message":
            return {"event_id": f"sw_{len(self.calls)}"}
        if op_name == "create_room":
            return {"id": f"room-{len(self.calls)}"}
        if op_name == "create_room_document":
            return {"document_id": f"doc-{len(self.calls)}"}
        return {}

    def said(self):
        return [a["body"] for op, a in self.calls if op == "post_message"]


def say(referee, body):
    event = {"room_id": "main", "payload": {"addressed": True, "sender_name": "ana", "message_id": body, "body": body}}
    return asyncio.run(referee.handle(event))


def test_tickets_and_try_commands(tickets, tmp_path):
    bench = Bench(tickets_dir=tickets, fixture_cache=tmp_path / "cache")
    referee = Referee(Agent(), "main", bench)
    say(referee, "@bench-referee tickets")
    assert "- pkg-double" in referee.agent.said()[0]
    say(referee, "@bench-referee try nope")
    assert "I don't have a ticket called nope" in referee.agent.said()[-1]
    ticket = say(referee, "@bench-referee try pkg-double")
    assert ticket.title == "double() is off by one"
    assert referee.agent.said()[-3].startswith("ticket: double() is off by one")


def test_tryout_uses_the_fixture_repo(tickets, tmp_path):
    bench = Bench(["cand"], tickets_dir=tickets, fixture_cache=tmp_path / "cache", workdir=tmp_path / "t", minutes=1 / 60)
    referee = Referee(Agent(), "main", bench)

    async def go():
        await referee.handle(
            {"room_id": "main", "payload": {"addressed": True, "sender_name": "ana", "message_id": "m", "body": "try pkg-double"}}
        )
        await referee.wait_for_tryouts()

    asyncio.run(go())
    [tryout] = referee.tryouts.values()
    workspace = tryout.entries["cand"].workspace
    assert "x + x + 1" in (workspace.path / "src/pkg/__init__.py").read_text()
    assert tryout.fixture.import_paths == ("src",)
    [score] = tryout.scores
    assert (score.passed, score.total) == (1, 2)


@pytest.mark.live
@pytest.mark.parametrize("name", REAL)
def test_real_ticket_fails_before_the_fix(name):
    if not run.docker_ready():
        pytest.skip("Docker or the scorer image isn't available")
    fx = fixtures.load(ROOT / "tickets" / name, ROOT / ".bench/fixtures")
    result = run.run_hidden_tests(fx.repo, "HEAD", fx.hidden, import_paths=fx.import_paths)
    assert result.sandboxed and not result.error
    assert 0 < len(result.failures) < result.total
    assert git(fx.repo, "log", "--format=%s") == "Before the fix"
