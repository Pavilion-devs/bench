"""Tryout state, and the text the Referee sends during a tryout. No I/O."""

import asyncio
import re
import time
from dataclasses import dataclass, field

from .tickets import Ticket
from .workspaces import Snapshot, Workspace

DONE = re.compile(r"\bdone\b", re.IGNORECASE)


@dataclass
class Entry:
    """One candidate's attempt at one ticket."""

    candidate: str
    room_id: str
    workspace: Workspace
    started: float = field(default_factory=time.monotonic)
    finished: asyncio.Event = field(default_factory=asyncio.Event)
    outcome: str = "working"  # working, done, timed_out
    seconds: float | None = None
    snapshot: Snapshot | None = None


@dataclass
class Tryout:
    ticket: Ticket
    thread_id: str
    entries: dict[str, Entry] = field(default_factory=dict)
    scores: list = field(default_factory=list)
    fixture: object = None


def room_name(ticket, candidate):
    return f"tryout-{ticket.id}-{candidate}"


def says_done(body):
    return bool(DONE.search(body or ""))


def brief(ticket, workspace, referee_name):
    lines = [f"Tryout for {ticket.id}: {ticket.title}"]
    if ticket.description:
        lines += ["", ticket.description]
    lines += [
        "",
        f"Work only in this folder: {workspace.path}",
        f"It's a git clone on branch {workspace.branch}. Commit your fix there.",
        "Don't push, and don't read or change anything outside that folder.",
        "Other agents are trying the same ticket in their own rooms. You can't see them and they can't see you.",
        f"When you're finished, reply here to @{referee_name} with the word: done",
    ]
    return "\n".join(lines)


def no_questions():
    return "I can't answer questions during a tryout. Work from the ticket, and reply done when you're finished."


def started(ticket, candidates, minutes):
    return (
        f"Tryouts for {ticket.id} started: {', '.join(candidates)}. "
        f"Each works alone in its own room and has {time_limit(minutes)}."
    )


def time_limit(minutes):
    seconds = round(minutes * 60)
    return f"{seconds}s" if seconds < 120 else f"{seconds / 60:g} min"


def no_candidates(ticket):
    return f"{ticket.id} is logged, but no candidates are set up, so there's no tryout."


def could_not_start(candidate, error):
    return f"Couldn't start {candidate}'s tryout: {error}"


def describe(snapshot):
    if not snapshot.changed:
        return "no changes"
    files = f"{len(snapshot.files)} file" + ("s" if len(snapshot.files) != 1 else "")
    text = f"commit {snapshot.commit[:7]}, {files}, +{snapshot.added} -{snapshot.removed}"
    if snapshot.referee_committed:
        text += " (left uncommitted, I committed it)"
    return text


def finished(entry):
    elapsed = f"{entry.seconds:.0f}s" if entry.seconds < 120 else f"{entry.seconds / 60:.1f} min"
    if entry.outcome == "timed_out":
        return f"{entry.candidate} ran out of time after {elapsed}. It left: {describe(entry.snapshot)}."
    return f"{entry.candidate} finished in {elapsed}: {describe(entry.snapshot)}."


def all_in(tryout, scoring=True):
    with_work = [e.candidate for e in tryout.entries.values() if e.snapshot and e.snapshot.changed]
    after = "Running the hidden tests now." if scoring else "This ticket has no hidden tests, so there's no scorecard."
    return (
        f"All tryouts for {tryout.ticket.id} are in. {len(with_work)} of {len(tryout.entries)} "
        f"changed the code. {after}"
    )


def ticket_list(names, referee_name):
    if not names:
        return "There are no real tickets set up."
    return "Real tickets I can run:\n" + "\n".join(f"- {n}" for n in names) + f"\nStart one with: @{referee_name} try <name>"


def unknown_ticket(name, referee_name):
    return f"I don't have a ticket called {name}. Reply @{referee_name} tickets to see the list."
