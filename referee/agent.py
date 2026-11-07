"""The Referee: a deterministic Switch agent that runs Bench.

It takes tickets in one main room, opens a private tryout room per candidate,
briefs each one with its own git clone, and reads back what each one did.
"""

import asyncio
import logging
import re
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

from . import roles
from . import tryouts as text
from .scoring import card
from .scoring.run import code_lines, run_hidden_tests, tamper_signs
from .switch import OpError, SwitchAgent
from . import fixtures
from .tickets import LEADING_MENTIONS, acknowledgement, parse_ticket, usage
from .tryouts import Entry, Tryout
from .workspaces import Workspace

log = logging.getLogger("referee")
# Switch's own bot posts notices (e.g. "nobody holds this role") that mention agents.
SYSTEM_SENDERS = {"Switch"}


@dataclass
class Bench:
    """What the Referee needs to run tryouts. Empty candidates means log tickets only."""

    candidates: list[str] = field(default_factory=list)
    repo: Path | None = None
    workdir: Path = Path(".bench/tryouts")
    minutes: float = 20
    hidden: Path | None = None
    sandbox: bool = True
    # Real tickets: folder of fixtures, and where to build them.
    tickets_dir: Path | None = None
    fixture_cache: Path = Path(".bench/fixtures")
    # Used for plain chat tickets. Without it, repo and hidden above are used.
    default: "fixtures.Fixture | None" = None
    role: str = "fixer"
    role_minutes: float = 3
    # Names allowed to pick a winner. None means anyone in the room except candidates.
    pickers: list[str] | None = None
    poll_seconds: float = 2
    # Failover: how long the holder has to answer work addressed to the role.
    response_seconds: float = 300
    watch: bool = True


class Referee:
    def __init__(self, agent: SwitchAgent, room_id=None, bench: Bench | None = None):
        self.agent = agent
        self.room_id = room_id
        self.bench = bench or Bench()
        self.tickets = {}
        self.tryouts = {}
        self.results = {}
        self.room_name = None
        self._standings_doc = None
        self._moving = False
        self._holder = None
        self._holder_status = None
        self._pending = {}
        self._entries_by_room = {}
        self._seen = set()
        self._work = asyncio.Queue()
        self._tasks = set()
        # Switch sends posts to whichever room the agent is connected to, one at a time.
        self._room_lock = asyncio.Lock()
        self._current_room = None

    async def start(self):
        """Opens the connection. Every operation needs it, so this comes first."""
        await self.agent.start(self._on_event)

    async def join(self, room_id):
        self.room_id = room_id
        async with self.in_room(room_id):
            detail = await self.agent.op("get_room_detail", room_id=room_id)
            if not any(r.get("name") == self.bench.role for r in detail.get("roles", [])):
                await self.agent.op(
                    "define_role",
                    name=self.bench.role,
                    instructions=(
                        f"You handle work addressed to @{self.bench.role} in this room. The Bench Referee "
                        "gives this role to whoever wins a tryout."
                    ),
                    exclusive=True,
                )
        self.room_name = detail.get("name", room_id)
        log.info("%s is in room %s with role @%s", self.agent.name, self.room_name, self.bench.role)

    async def serve(self):
        watchdog = asyncio.create_task(self._watch()) if self.bench.watch else None
        try:
            while True:
                await self.handle(await self._work.get())
        finally:
            for task in [*self._tasks, *([watchdog] if watchdog else [])]:
                task.cancel()

    async def wait_for_tryouts(self):
        while self._tasks:
            await asyncio.gather(*list(self._tasks))

    @asynccontextmanager
    async def in_room(self, room_id):
        async with self._room_lock:
            if self._current_room != room_id:
                await self.agent.op("connect_to_room", room_id=room_id)
                self._current_room = room_id
            yield

    async def post(self, room_id, body, thread_id=None):
        async with self.in_room(room_id):
            args = {"body": body}
            if thread_id:
                args["thread_id"] = thread_id
            await self.agent.op("post_message", **args)

    async def _on_event(self, kind, data):
        # The stream callback only queues work, so a slow reply never stalls the stream.
        if kind == "message":
            await self._work.put(data)

    async def handle(self, event):
        """Acts on one message event if it is meant for the Referee."""
        payload = event.get("payload", {})
        message_id = payload.get("message_id")
        sender = payload.get("sender_name")
        if sender == self.agent.name or sender in SYSTEM_SENDERS or message_id in self._seen:
            return None
        room = event.get("room_id")
        if room == self.room_id:
            self._note_role_work(payload)
        if room in self._entries_by_room:
            self._seen.add(message_id)
            return await self._candidate_message(self._entries_by_room[room], payload)
        if room != self.room_id or not (payload.get("addressed") or self._mentions_me(payload.get("body"))):
            return None
        self._seen.add(message_id)
        return await self._main_room_message(
            payload.get("body"), payload.get("sender_name"), message_id, payload.get("thread_id") or message_id
        )

    async def file_ticket(self, body, fixture=None):
        """Posts a ticket into the main room itself and handles it, e.g. from a file."""
        async with self.in_room(self.room_id):
            posted = await self.agent.op("post_message", body=body)
        message_id = posted["event_id"]
        self._seen.add(message_id)
        return await self._main_room_message(body, self.agent.name, message_id, message_id, fixture)

    def fixture(self, name):
        """Loads a real ticket by name, building it the first time."""
        return fixtures.load(Path(self.bench.tickets_dir) / name, self.bench.fixture_cache)

    def _default_fixture(self):
        if self.bench.default:
            return self.bench.default
        if not self.bench.repo:
            return None
        return fixtures.Fixture("default", "", Path(self.bench.repo), self.bench.hidden)

    def _mentions_me(self, body):
        # Switch marks a person's message "not addressed" unless the Referee's owner sent it,
        # but the Referee sees its own room anyway, so a direct @mention counts. Who may
        # pick is still limited by Bench.pickers.
        return bool(re.search(rf"(^|\s)@{re.escape(self.agent.name)}\b", body or ""))

    async def _main_room_message(self, body, sender, message_id, thread, fixture=None):
        command = LEADING_MENTIONS.sub("", body or "", count=1).strip()
        if self.bench.tickets_dir and re.fullmatch(r"tickets\??", command, re.IGNORECASE):
            await self.post(self.room_id, text.ticket_list(fixtures.available(self.bench.tickets_dir), self.agent.name), thread)
            return None
        tried = re.fullmatch(r"try\s+([\w.-]+)", command, re.IGNORECASE) if self.bench.tickets_dir else None
        if tried:
            name = tried.group(1)
            if name not in fixtures.available(self.bench.tickets_dir):
                await self.post(self.room_id, text.unknown_ticket(name, self.agent.name), thread)
                return None
            chosen = await asyncio.to_thread(self.fixture, name)
            return await self.file_ticket(chosen.ticket, chosen)
        pick = roles.parse_pick(body)
        if pick:
            return await self._pick(pick, sender, thread)
        if roles.asks_who(body):
            await self.post(self.room_id, roles.who(self.bench.role, await self._holders()), thread)
            return None
        ticket = parse_ticket(body, sender, message_id)
        if not ticket:
            await self.post(self.room_id, usage(self.agent.name), thread)
            return None
        self.tickets[ticket.id] = ticket
        log.info("logged %s from %s: %s", ticket.id, ticket.reporter, ticket.title)
        await self.post(self.room_id, acknowledgement(ticket), thread)
        fixture = fixture or self._default_fixture()
        if not self.bench.candidates or not fixture:
            await self.post(self.room_id, text.no_candidates(ticket), thread)
            return ticket
        tryout = Tryout(ticket, thread, fixture=fixture)
        self.tryouts[ticket.id] = tryout
        task = asyncio.create_task(self._run_tryout(tryout))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return ticket

    async def _candidate_message(self, entry, payload):
        if payload.get("sender_name") != entry.candidate or entry.outcome != "working":
            return None
        if text.says_done(payload.get("body")):
            await self._finish(entry, "done")
        else:
            await self.post(entry.room_id, text.no_questions())
        return entry

    async def _run_tryout(self, tryout):
        ticket = tryout.ticket
        for candidate in self.bench.candidates:
            try:
                tryout.entries[candidate] = await self._open(ticket, candidate, tryout.fixture.repo)
            except (OpError, OSError, RuntimeError) as error:
                log.warning("could not start %s on %s: %s", candidate, ticket.id, error)
                await self.post(self.room_id, text.could_not_start(candidate, error), tryout.thread_id)
        if not tryout.entries:
            return
        await self.post(
            self.room_id,
            text.started(ticket, list(tryout.entries), self.bench.minutes),
            tryout.thread_id,
        )
        waits = [self._wait(tryout, entry) for entry in tryout.entries.values()]
        await asyncio.gather(*waits)
        await self.post(self.room_id, text.all_in(tryout, scoring=bool(tryout.fixture.hidden)), tryout.thread_id)
        if tryout.fixture.hidden:
            await self._score(tryout)

    async def _score(self, tryout):
        """Runs the hidden tests on every commit, then posts and files the scorecard."""
        entries = list(tryout.entries.values())
        first = entries[0].workspace
        fixture = tryout.fixture
        baseline = await asyncio.to_thread(
            run_hidden_tests, first.path, first.base, fixture.hidden, self.bench.sandbox, fixture.import_paths
        )
        runs = await asyncio.gather(
            *(
                asyncio.to_thread(
                    run_hidden_tests,
                    e.workspace.path,
                    e.snapshot.commit,
                    fixture.hidden,
                    self.bench.sandbox,
                    fixture.import_paths,
                )
                for e in entries
            )
        )
        tampers = [
            await asyncio.to_thread(tamper_signs, e.workspace.path, e.workspace.base, e.snapshot.commit)
            for e in entries
        ]
        sizes = [
            await asyncio.to_thread(code_lines, e.workspace.path, e.workspace.base, e.snapshot.commit)
            for e in entries
        ]
        ranked = card.rank(
            card.score(e, run, baseline.total, tamper, size)
            for e, run, tamper, size in zip(entries, runs, tampers, sizes)
        )
        sandboxed = baseline.sandboxed and all(run.sandboxed for run in runs)
        tryout.scores = ranked
        self.results[tryout.ticket.id] = ranked
        log.info("scored %s: %s", tryout.ticket.id, [(s.candidate, s.passed, s.total) for s in ranked])
        summary = card.scorecard(tryout.ticket, ranked, baseline, sandboxed)
        summary += "\n" + roles.how_to_pick(self.agent.name, self.bench.role, ranked)
        await self.post(self.room_id, summary, tryout.thread_id)
        await self._file_documents(tryout.ticket, ranked, baseline, sandboxed)

    async def _file_documents(self, ticket, ranked, baseline, sandboxed):
        async with self.in_room(self.room_id):
            await self.agent.op(
                "create_room_document",
                name=f"Scorecard {ticket.id}",
                description=f"Bench scorecard for {ticket.id}: {ticket.title}",
                instructions="Written by the Bench Referee from hidden test runs. Don't edit.",
                content=card.scorecard_document(ticket, ranked, baseline, sandboxed),
            )
            standings = card.standings(self.results)
            if self._standings_doc:
                await self.agent.op("update_room_document", document_id=self._standings_doc, content=standings)
            else:
                created = await self.agent.op(
                    "create_room_document",
                    name="Bench standings",
                    description="Which candidate tops each ticket's hidden tests",
                    instructions="Written by the Bench Referee. Don't edit.",
                    content=standings,
                )
                self._standings_doc = created["document_id"]

    async def _open(self, ticket, candidate, repo):
        workspace = await asyncio.to_thread(Workspace.create, repo, self.bench.workdir, ticket.id, candidate)
        room = await self.agent.op(
            "create_room",
            name=text.room_name(ticket, candidate),
            description=f"Bench tryout: {candidate} on {ticket.id}",
            agent_names=[self.agent.name, candidate],
            internal_only=True,
        )
        entry = Entry(candidate, room["id"], workspace)
        self._entries_by_room[entry.room_id] = entry
        async with self.in_room(entry.room_id):
            await self.agent.op(
                "send_targeted_message",
                body=text.brief(ticket, workspace, self.agent.name),
                target_names=[candidate],
            )
        log.info("briefed %s on %s in %s", candidate, ticket.id, workspace.path)
        return entry

    async def _wait(self, tryout, entry):
        try:
            await asyncio.wait_for(entry.finished.wait(), timeout=self.bench.minutes * 60)
        except TimeoutError:
            await self._finish(entry, "timed_out")
        await self.post(self.room_id, text.finished(entry), tryout.thread_id)

    async def _finish(self, entry, outcome):
        if entry.outcome != "working":
            return
        # Claimed before the first await, so a late "done" and a timeout can't both finish it.
        entry.outcome = outcome
        entry.seconds = time.monotonic() - entry.started
        entry.snapshot = await asyncio.to_thread(entry.workspace.collect)
        entry.finished.set()
        log.info("%s %s: %s", entry.candidate, outcome, text.describe(entry.snapshot))

    async def _holders(self):
        detail = await self.agent.op("get_room_detail", room_id=self.room_id)
        return roles.holders(detail, self.bench.role)

    def _note_role_work(self, payload):
        """Tracks work addressed to the role, and clears it once the holder speaks."""
        sender, message_id = payload.get("sender_name"), payload.get("message_id")
        if sender and sender == self._holder:
            if not roles.is_notice(payload.get("body")):
                self._pending.clear()
        elif roles.mentions_role(payload.get("body"), self.bench.role) and message_id not in self._pending:
            self._pending[message_id] = {
                "sender": sender,
                "body": payload.get("body"),
                "thread": payload.get("thread_id") or message_id,
                "at": time.monotonic(),
            }

    async def _watch(self):
        """Fails over when the holder drops out, or doesn't answer work in time."""
        while True:
            await asyncio.sleep(self.bench.poll_seconds)
            try:
                await self.check_holder()
            except OpError as error:
                log.warning("watch: %s", error)

    async def check_holder(self):
        """One watchdog pass. Returns the failover reason, or None."""
        if self._moving:
            return None
        detail = await self.agent.op("get_room_detail", room_id=self.room_id)
        current = roles.holders(detail, self.bench.role)
        holder = current[0] if current else None
        status = detail.get("agent_statuses", {}).get(holder) if holder else None
        previous = self._holder
        if (holder, status) != (previous, self._holder_status):
            log.info("@%s holder %s, status %s", self.bench.role, holder, status)
        self._holder, self._holder_status = holder, status
        oldest = min((p["at"] for p in self._pending.values()), default=None)
        late = oldest is not None and time.monotonic() - oldest > self.bench.response_seconds
        if holder and status in roles.DEAD_STATUSES:
            reason, who = f"is {status}", holder
        elif holder and late:
            reason, who = f"didn't answer within {self.bench.response_seconds / 60:g} min", holder
        elif previous and not holder:
            # A dead custom agent's seat frees by itself, at the moment it disconnects.
            reason, who = "dropped out of the role", previous
        elif not holder and self._pending and self.results:
            reason, who = "holds the role, but work is waiting", None
        else:
            return None
        await self._failover(who, reason)
        return reason

    async def _failover(self, holder, reason):
        """Moves the role to the runner-up and re-sends what the holder missed."""
        ticket_id = list(self.results)[-1] if self.results else None
        statuses = (await self.agent.op("get_room_detail", room_id=self.room_id)).get("agent_statuses", {})
        runner_up = next(
            (
                s.candidate
                for s in self.results.get(ticket_id, [])
                if s.candidate != holder and statuses.get(s.candidate) not in roles.DEAD_STATUSES
            ),
            None,
        )
        thread = next(iter(self._pending.values()))["thread"] if self._pending else None
        if not runner_up:
            await self.post(self.room_id, roles.no_runner_up(holder, self.bench.role, reason), thread)
            self._pending.clear()
            return
        self._moving = True
        started = time.monotonic()
        # Snapshot the waiting work now. During the move Switch posts role notices under
        # the holder's name, which would otherwise look like the holder answering.
        waiting = dict(self._pending)
        self._pending.clear()
        try:
            log.info("failover: %s %s, promoting %s with %d waiting", holder, reason, runner_up, len(waiting))
            await self.post(self.room_id, roles.failover_started(holder, self.bench.role, runner_up, reason), thread)
            held = roles.holders(await self.agent.op("get_room_detail", room_id=self.room_id), self.bench.role)
            released = holder not in held or await self._release(holder, thread, report=False)
            assumed = released and await self._assume(runner_up, self.tickets[ticket_id], thread)
            # Work sent to the role while the seat was empty reached nobody either.
            waiting.update(self._pending)
            self._pending.clear()
            waiting = list(waiting.values())
            if not assumed:
                # The role couldn't move, so the waiting work goes to the runner-up by name.
                for item in waiting:
                    await self._ask(runner_up, roles.resend(item["sender"], item["body"]), item["thread"])
                if not released:
                    await self.post(
                        self.room_id, roles.forwarded(holder, self.bench.role, runner_up, len(waiting)), thread
                    )
                return
            self._holder = runner_up
            async with self.in_room(self.room_id):
                for item in waiting:
                    await self.agent.op(
                        "send_targeted_message",
                        body=roles.resend(item["sender"], item["body"]),
                        target_roles=[self.bench.role],
                        thread_id=item["thread"],
                    )
            await self.post(
                self.room_id,
                roles.failover_done(runner_up, self.bench.role, holder, time.monotonic() - started, len(waiting)),
                thread,
            )
        finally:
            self._moving = False

    async def _pick(self, pick, sender, thread):
        """Checks a person's pick, then starts moving the role. Returns the Pick or None."""
        reason, ticket_id = self._check_pick(pick, sender)
        if reason:
            await self.post(self.room_id, roles.refused(reason), thread)
            return None
        self._moving = True
        task = asyncio.create_task(self._move(pick, self.tickets[ticket_id], thread))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return pick

    def _check_pick(self, pick, sender):
        """Returns (reason it's refused, None) or (None, the ticket id it's for)."""
        if sender == self.agent.name or sender in self.bench.candidates:
            return "candidates can't pick the winner.", None
        if self.bench.pickers is not None and sender not in self.bench.pickers:
            return f"{sender} isn't one of the people who pick winners here.", None
        if self._moving:
            return f"The {self.bench.role} role is already being moved. Wait for that to finish.", None
        ticket_id = pick.ticket_id or (list(self.results)[-1] if self.results else None)
        if ticket_id not in self.results:
            if pick.ticket_id:
                return f"there's no scorecard for {pick.ticket_id} yet.", None
            return "no tryout has been scored yet.", None
        scores = {s.candidate: s for s in self.results[ticket_id]}
        if pick.candidate not in scores:
            return f"{pick.candidate} didn't try out for {ticket_id}.", None
        chosen = scores[pick.candidate]
        if not chosen.solved and not pick.anyway:
            return (
                f"{pick.candidate} passed {chosen.passed} of {chosen.total} hidden tests on {ticket_id}. "
                "Add 'anyway' to pick it regardless."
            ), None
        return None, ticket_id

    async def _move(self, pick, ticket, thread):
        role, winner = self.bench.role, pick.candidate
        started = time.monotonic()
        try:
            async with self.in_room(self.room_id):
                detail = await self.agent.op("get_room_detail", room_id=self.room_id)
            current = roles.holders(detail, role)
            if current == [winner]:
                await self.post(self.room_id, roles.already(winner, role), thread)
                return
            holder = ", ".join(current) or None
            await self.post(self.room_id, roles.moving(winner, role, holder), thread)
            for name in current:
                if not await self._release(name, thread):
                    return
            if not await self._assume(winner, ticket, thread):
                return
            self._holder = winner
            log.info("@%s moved to %s", role, winner)
            await self.post(self.room_id, roles.moved(winner, role, holder, time.monotonic() - started), thread)
        finally:
            self._moving = False

    async def _release(self, name, thread, report=True):
        """Asks the holder to give up the role, and waits until Switch shows it did."""
        role, minutes = self.bench.role, self.bench.role_minutes
        await self._ask(name, roles.release_request(name, role, self.room_name, self.room_id), thread)
        if await self._until(lambda held: name not in held):
            return True
        if report:
            await self.post(self.room_id, roles.did_not_release(name, role, minutes), thread)
        return False

    async def _assume(self, winner, ticket, thread, report=True):
        """Invites the winner if needed, asks it to take the role, and waits for Switch."""
        role, minutes = self.bench.role, self.bench.role_minutes
        detail = await self.agent.op("get_room_detail", room_id=self.room_id)
        if winner not in detail.get("agent_names", []):
            await self.agent.op("invite_agent_to_room", room_id=self.room_id, agent_name=winner)
        await self._ask(winner, roles.assume_request(winner, role, self.room_name, self.room_id, ticket), thread)
        if await self._until(lambda held: held == [winner]):
            return True
        if report:
            await self.post(self.room_id, roles.did_not_assume(winner, role, minutes), thread)
        return False

    async def _ask(self, name, body, thread):
        async with self.in_room(self.room_id):
            await self.agent.op("send_targeted_message", body=body, target_names=[name], thread_id=thread)

    async def _until(self, done):
        """Polls the room until done(holders) is true, or the role move times out."""
        deadline = time.monotonic() + self.bench.role_minutes * 60
        while time.monotonic() < deadline:
            if done(await self._holders()):
                return True
            await asyncio.sleep(self.bench.poll_seconds)
        return done(await self._holders())
