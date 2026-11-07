"""Scoring: the sandbox runner, cheat attempts, ranking and the scorecard."""

import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from referee.scoring import card, run
from referee.scoring.run import code_lines, run_hidden_tests, tamper_signs
from referee.tickets import Ticket
from referee.workspaces import Snapshot, git, init_repo

FIXTURE = Path(__file__).resolve().parent.parent / "tickets/sample-invoice"
HIDDEN = FIXTURE / "hidden"
FIX = '''"""Invoice totals for the payments service."""

from decimal import ROUND_HALF_UP, Decimal


def line_total(quantity, unit_price):
    return quantity * unit_price


def invoice_total(lines, tax_rate=0.0):
    subtotal = sum((Decimal(str(q)) * Decimal(str(p)) for q, p in lines), Decimal("0"))
    total = subtotal * (1 + Decimal(str(tax_rate)))
    return float(total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
'''
docker = pytest.mark.skipif(not run.docker_ready(), reason="Docker or the scorer image isn't available")


@pytest.fixture
def repo(tmp_path):
    return init_repo(FIXTURE / "repo", tmp_path / "repo")


def commit(repo, files, message="change"):
    for name, text in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", message)
    return git(repo, "rev-parse", "HEAD")


@docker
def test_base_fails_and_fix_passes_in_the_sandbox(repo):
    base = run_hidden_tests(repo, "HEAD", HIDDEN)
    assert base.sandboxed and base.total == 10 and base.passed < base.total
    fixed = run_hidden_tests(repo, commit(repo, {"invoice/totals.py": FIX}), HIDDEN)
    assert (fixed.passed, fixed.total, fixed.failures) == (10, 10, ())


def test_local_fallback_gives_the_same_answer(repo):
    fixed = run_hidden_tests(repo, commit(repo, {"invoice/totals.py": FIX}), HIDDEN, sandbox=False)
    assert not fixed.sandboxed and fixed.passed == fixed.total == 10


@docker
def test_only_committed_work_counts(repo):
    (repo / "invoice/totals.py").write_text(FIX)
    assert run_hidden_tests(repo, "HEAD", HIDDEN).passed < 10


@docker
def test_candidate_conftest_and_pytest_config_are_ignored(repo):
    cheat = {
        "conftest.py": "import pytest\n\n@pytest.hookimpl(tryfirst=True)\ndef pytest_runtest_call(item):\n    pass\n"
        "def pytest_collection_modifyitems(items):\n    items.clear()\n",
        "pytest.ini": "[pytest]\naddopts = -k nothing_matches\n",
        "pyproject.toml": "[tool.pytest.ini_options]\naddopts = '-k nothing_matches'\n",
    }
    result = run_hidden_tests(repo, commit(repo, cheat), HIDDEN)
    assert result.total == 10 and result.passed < 10


@docker
def test_skipping_counts_as_failing(repo):
    skipper = FIX + "\nimport pytest\npytest.skip('nope', allow_module_level=True)\n"
    result = run_hidden_tests(repo, commit(repo, {"invoice/totals.py": skipper}), HIDDEN)
    assert result.passed < 10


@docker
def test_sandbox_has_no_network(repo):
    probe = FIX + (
        "\nimport socket\ntry:\n    socket.create_connection(('1.1.1.1', 53), timeout=3)\n"
        "    NETWORK = True\nexcept OSError:\n    NETWORK = False\n"
    )
    hidden = repo.parent / "hidden-network"
    hidden.mkdir()
    (hidden / "test_net.py").write_text("from invoice.totals import NETWORK\n\ndef test_offline():\n    assert not NETWORK\n")
    result = run_hidden_tests(repo, commit(repo, {"invoice/totals.py": probe}), hidden)
    assert (result.passed, result.total) == (1, 1)


@docker
def test_endless_loop_is_stopped(repo, monkeypatch):
    monkeypatch.setattr(run, "TIMEOUT_SECONDS", 8)
    looping = FIX + "\nwhile True:\n    pass\n"
    result = run_hidden_tests(repo, commit(repo, {"invoice/totals.py": looping}), HIDDEN)
    assert result.error and "took over" in result.error
    names = subprocess.run(["docker", "ps", "--format", "{{.Names}}"], capture_output=True, text=True).stdout
    assert "bench-score-" not in names


def test_tamper_signs_flag_test_machinery_but_not_tests(repo):
    base = git(repo, "rev-parse", "HEAD")
    head = commit(
        repo,
        {
            "invoice/totals.py": FIX + "\nimport atexit\n",
            "invoice/helpers.py": "import sys\nsys.modules['x'] = None\n",
            "tests/test_more.py": "import pytest\n",
        },
    )
    signs = tamper_signs(repo, base, head)
    assert any("atexit" in s for s in signs)
    assert any("sys.modules" in s for s in signs)
    assert not any(s.startswith("tests/") for s in signs)
    clean = commit(repo, {"invoice/totals.py": FIX.replace("subtotal", "amount")})
    assert tamper_signs(repo, head, clean) == ()


def snap(numstat):
    return Snapshot("c" * 40, "b" * 40, tuple(n for n, _, _ in numstat), 0, 0, False, tuple(numstat))


def entry(name, numstat, seconds=10, outcome="done"):
    return SimpleNamespace(candidate=name, snapshot=snap(numstat), seconds=seconds, outcome=outcome)


def test_source_lines_ignore_tests_and_junk():
    s = snap([("invoice/totals.py", 10, 3), ("tests/test_totals.py", 40, 0), ("invoice/__pycache__/t.pyc", 0, 0)])
    assert card.source_lines(s) == 13


def test_ranking_order():
    ok = SimpleNamespace(passed=10, failures=(), error=None)
    part = SimpleNamespace(passed=7, failures=("a",), error=None)
    scores = [
        card.score(entry("slow-small", [("a.py", 2, 0)], seconds=100), ok, 10),
        card.score(entry("fast-small", [("a.py", 2, 0)], seconds=5), ok, 10),
        card.score(entry("big", [("a.py", 50, 0)], seconds=1), ok, 10),
        card.score(entry("partial", [("a.py", 1, 0)], seconds=1), part, 10),
    ]
    assert [s.candidate for s in card.rank(scores)] == ["fast-small", "slow-small", "big", "partial"]


def test_scorecard_and_documents():
    ticket = Ticket("T-1", "Fix rounding", "", "ana", "m")
    ok = SimpleNamespace(passed=10, failures=(), error=None)
    bad = SimpleNamespace(passed=0, failures=(), error="ImportError: boom")
    ranked = card.rank(
        [
            card.score(entry("claude", [("invoice/totals.py", 10, 2)], seconds=25), ok, 10),
            card.score(entry("codex", [("invoice/totals.py", 9, 3)], seconds=112), ok, 10, ["invoice/x.py: import atexit"]),
            card.score(entry("broken", [("invoice/totals.py", 1, 1)], outcome="timed_out"), bad, 10),
        ]
    )
    baseline = SimpleNamespace(passed=6, total=10)
    text = card.scorecard(ticket, ranked, baseline, sandboxed=True)
    assert "Before any fix, 6 of 10 pass" in text
    assert "1. claude: 10/10 hidden tests, 12 code lines changed, 25s" in text
    assert "CHECK BY HAND" in text
    assert "tests couldn't run (ImportError: boom)" in text
    assert "Top score: claude" in text
    assert "NOT sandboxed" in card.scorecard(ticket, ranked, baseline, sandboxed=False)
    doc = card.scorecard_document(ticket, ranked, baseline, True)
    assert "| 1 | claude | 10/10 | 12 | 25s | done |" in doc
    assert "- codex: `invoice/x.py: import atexit`" in doc
    table = card.standings({"T-1": ranked, "T-2": list(reversed(ranked))})
    assert "| claude | 1 | 2 | 2 |" in table
    assert "| broken | 0 | 0 | 2 |" in table


def test_errors_never_count_as_passes():
    broken = SimpleNamespace(passed=10, failures=(), error="crashed")
    assert card.score(entry("x", []), broken, 10).passed == 0


def test_near_equal_sizes_race_on_time():
    ok = SimpleNamespace(passed=10, failures=(), error=None)
    scores = [
        card.score(entry("small-slow", [], seconds=112), ok, 10, lines=8),
        card.score(entry("close-fast", [], seconds=25), ok, 10, lines=8 + card.SIZE_TIE),
        card.score(entry("big-fastest", [], seconds=5), ok, 10, lines=9 + card.SIZE_TIE),
    ]
    assert [s.candidate for s in card.rank(scores)] == ["close-fast", "small-slow", "big-fastest"]


def test_code_lines_skip_comments_blanks_tests_and_junk(repo):
    base = git(repo, "rev-parse", "HEAD")
    head = commit(
        repo,
        {
            "invoice/totals.py": (FIXTURE / "repo/invoice/totals.py").read_text()
            + "\n# why this works\n\nCENT = 1\n    # indented comment\nHALF = 2\n",
            "tests/test_extra.py": "def test_x():\n    assert True\n",
            "invoice/__pycache__/t.pyc": "junk\n",
        },
    )
    assert code_lines(repo, base, head) == 2
