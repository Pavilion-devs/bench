"""Turns test runs into a ranking, a scorecard and standings. No I/O.

Ranking is fixed and explained on the card: most hidden tests passed first.
Among equals, changes within SIZE_TIE code lines of the smallest count as equally
small, and the fastest of those wins. A clearly bigger change ranks below them.
"""

import re
from dataclasses import dataclass

SIZE_TIE = 5
NOT_SOURCE = re.compile(r"(^|/)(tests?/|test_[^/]*$|[^/]*_test\.py$|conftest\.py$|__pycache__/)|\.pyc$")


def source_lines(snapshot):
    """Lines added plus removed, outside tests and build junk."""
    return sum(plus + minus for name, plus, minus in snapshot.numstat if not NOT_SOURCE.search(name))


@dataclass(frozen=True)
class Score:
    candidate: str
    passed: int
    total: int
    lines: int
    seconds: float
    outcome: str
    commit: str
    failures: tuple[str, ...] = ()
    error: str | None = None
    tamper: tuple[str, ...] = ()

    @property
    def solved(self):
        return self.total > 0 and self.passed == self.total and not self.error


def score(entry, run, total, tamper=(), lines=None):
    """One candidate's score. `total` is the hidden test count, from the base commit.

    `lines` is the code-line count from run.code_lines; without it, every changed
    line in source files counts.
    """
    passed = 0 if run.error else min(run.passed, total)
    return Score(
        candidate=entry.candidate,
        passed=passed,
        total=total,
        lines=source_lines(entry.snapshot) if lines is None else lines,
        seconds=entry.seconds,
        outcome=entry.outcome,
        commit=entry.snapshot.commit,
        failures=run.failures,
        error=run.error,
        tamper=tuple(tamper),
    )


def rank(scores):
    scores = list(scores)
    smallest = {}
    for s in scores:
        smallest[s.passed] = min(smallest.get(s.passed, s.lines), s.lines)

    def key(s):
        small = s.lines <= smallest[s.passed] + SIZE_TIE
        # Equally small changes race on time; bigger ones line up by size.
        return (-s.passed, not small, s.seconds if small else s.lines, s.seconds, s.candidate)

    return sorted(scores, key=key)


def duration(seconds):
    return f"{seconds:.0f}s" if seconds < 120 else f"{seconds / 60:.1f} min"


def line(position, s):
    text = f"{position}. {s.candidate}: {s.passed}/{s.total} hidden tests"
    if s.error:
        return text + f", tests couldn't run ({s.error.splitlines()[-1][:80]})"
    text += f", {s.lines} code lines changed, {duration(s.seconds)}"
    if s.outcome == "timed_out":
        text += ", ran out of time"
    if s.tamper:
        text += f". CHECK BY HAND: {len(s.tamper)} added line(s) touch the test machinery"
    return text


def scorecard(ticket, ranked, baseline, sandboxed):
    where = "in a sandbox with no network" if sandboxed else "locally, NOT sandboxed"
    lines = [
        f"Scorecard for {ticket.id}: {ticket.title}",
        f"Hidden tests run {where}. Before any fix, {baseline.passed} of {baseline.total} pass.",
        "",
    ]
    lines += [line(i, s) for i, s in enumerate(ranked, 1)]
    lines.append("")
    leader = ranked[0] if ranked else None
    if leader and leader.solved:
        lines.append(f"Top score: {leader.candidate}. A person makes the final pick.")
    elif leader and leader.passed:
        lines.append(f"Nobody passed every hidden test. Closest: {leader.candidate}.")
    else:
        lines.append("Nobody passed any hidden test that was failing before.")
    lines.append(
        f"Ranked by hidden tests passed. Then changes within {SIZE_TIE} code lines of the smallest "
        "count as equally small and the fastest wins; bigger changes rank below. Comments don't count."
    )
    return "\n".join(lines)


def scorecard_document(ticket, ranked, baseline, sandboxed):
    """Markdown for the ticket's room document."""
    rows = [
        f"| {i} | {s.candidate} | {s.passed}/{s.total} | {s.lines} | {duration(s.seconds)} | "
        f"{s.outcome.replace('_', ' ')} | `{s.commit[:7]}` |"
        for i, s in enumerate(ranked, 1)
    ]
    failing = [f"- {s.candidate}: " + ", ".join(s.failures) for s in ranked if s.failures]
    flagged = [f"- {s.candidate}: `{sign}`" for s in ranked for sign in s.tamper]
    where = "in a sandbox with no network" if sandboxed else "locally, not sandboxed"
    return "\n".join(
        [
            f"# {ticket.id}: {ticket.title}",
            "",
            f"Reported by {ticket.reporter}. Hidden tests run {where}; "
            f"{baseline.passed} of {baseline.total} pass before any fix.",
            "",
            "| Rank | Candidate | Hidden tests | Code lines | Time | Outcome | Commit |",
            "| --- | --- | --- | --- | --- | --- | --- |",
            *rows,
            *(["", "Failing tests:", *failing] if failing else []),
            *(["", "Lines to check by hand (they touch the test machinery):", *flagged] if flagged else []),
        ]
    )


def standings(results):
    """Markdown table of tops per candidate. `results` maps ticket id to its ranked scores."""
    tally = {}
    for ranked in results.values():
        for position, s in enumerate(ranked):
            row = tally.setdefault(s.candidate, {"tops": 0, "solved": 0, "tryouts": 0})
            row["tryouts"] += 1
            row["solved"] += s.solved
            row["tops"] += position == 0 and s.solved
    order = sorted(tally.items(), key=lambda kv: (-kv[1]["tops"], -kv[1]["solved"], kv[0]))
    rows = [f"| {name} | {r['tops']} | {r['solved']} | {r['tryouts']} |" for name, r in order]
    return "\n".join(
        [
            "# Bench standings",
            "",
            "| Candidate | Top scores | Solved | Tryouts |",
            "| --- | --- | --- | --- |",
            *rows,
        ]
    )
