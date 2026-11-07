# Bench: build plan

Coding agents try out on a team's real tickets, in team chat. The winner takes the job. If it goes down, the next agent takes over and nobody changes how they work.

Entry for **Flip the Switch**, Track 01: Best Business Workflow. Built on [Switch](https://github.com/sandbox-quantum/switch).

## Status

| Item | State |
| --- | --- |
| Research and idea | Done |
| Role failover test, custom agents | Passed, see [Failover test](#failover-test) |
| Role failover test, real Claude Code and Codex agents | Done. A killed agent keeps its role indefinitely, but release-then-assume handoff works. See [Real agents in Console](#real-agents-in-console) |
| Milestone 2: one agent, one room | Done. `python -m referee` joins a room and answers tickets in a thread, tested live and from Mattermost |
| Milestone 3: sealed tryouts | Done. Tested live with scripted candidates, then with real Claude Code and Codex in Console: both fixed the sample bug |
| Milestone 4: scoring | Done. Hidden tests in a Docker sandbox, cheat tests pass, scorecard and standings as room documents. Real Claude and Codex runs scored |
| Milestone 5: role move | Done. Tested live with scripted agents, then for real in Console: the fixer role went to Claude Code, then handed off to Codex |
| Milestone 6: failover | Done. Tested live with scripted agents (kill to takeover about 6.5s, missed message re-sent), then on Console: stopping Codex handed the role to Claude in 6s |
| Milestone 7: real tickets | Done: 3 real fixes from public repos, checked in the sandbox. Real Console tryout on more-itertools: Claude 608/608, Codex made no change |
| Milestone 8: replay mode | Done. `uv run python -m replay` replays a real Console run on Console or the dev stack, no API keys. Tested on both |
| Everything else | Not started |

## The problem

Every team using coding agents asks which agent should do which job. Benchmarks answer that on someone else's code. Bench answers it on your own tickets, in the channel where the work already happens, and acts on the answer.

## The demo

1. **Ticket.** Someone posts a real ticket in `#payments`.
2. **Tryout.** A Referee agent opens one private room each for Claude Code, Codex and OpenCode. They can't copy each other, because they aren't members of each other's rooms.
3. **Scoring.** The Referee runs hidden tests itself, taken from the real merged fix, and posts a scorecard: tests passed, diff size, time.
4. **Decision.** A person picks the winner in chat. The old holder releases `@fixer` and the winner takes it. People keep tagging `@fixer`, and a different agent now answers.
5. **Failover.** Kill the winning agent on camera. The Referee notices within about 5 seconds, promotes the runner-up, and re-sends any ticket the dead agent missed.

Steps 4 and 5 are the moments no other entry has. Protect them when cutting scope.

## Why it needs Switch

Judges weigh this at 30%: would the project stop working without Switch?

| Bench needs | Switch feature |
| --- | --- |
| Three different agent providers in one team | Agent protocol and Console providers |
| Agents unable to see each other's attempts | Private rooms, created at runtime by the Referee |
| A job that outlives whoever holds it | Exclusive room role, leased to one agent |
| Standings the whole team can read | Room-scoped documents |
| Only the right people can pick a winner | Agent addressing policy |
| One-step setup for anyone | Agent and room templates |

## Architecture

```
#payments (people, Referee, role @fixer)
   |
   | ticket
   v
Referee ---- creates ----> #tryout-<id>-claude  (Claude Code)
   |                       #tryout-<id>-codex   (Codex)
   |                       #tryout-<id>-opencode (OpenCode)
   |
   |-- runs hidden tests in a sandbox, outside agent reach
   |-- writes scorecard and standings to room documents
   |-- tells the holder to release @fixer and the winner to assume it
   '-- watches held_by, promotes the next on the bench, re-sends missed tickets
```

**The Referee is deterministic code**, a custom agent on the Switch protocol. Scores come from running tests, never from a model's opinion.

**The Referee coordinates roles but can't assign them.** Only an agent can assume a role, and only for itself. Each candidate needs a way to obey "assume this role" reliably, see [What's still unproven](#whats-still-unproven).

## Components

| Component | Job |
| --- | --- |
| `referee/` | Custom protocol agent: tryout lifecycle, scoring, role moves, failover |
| `referee/scoring` | Runs hidden tests against each candidate's branch in a sandbox |
| `templates/` | Switch YAML for the agents and rooms |
| `tickets/` | Ticket fixtures from a public repo: the issue, the pre-fix commit, the fix's tests |
| `replay/` | Recorded runs, so judges can reproduce the demo without three provider logins |
| `tests/` | Unit tests, plus integration tests against a live Switch server |

## Build order

Each milestone must work before the next starts.

1. **Failover test.** Prove a role can move between agents and survive one dying. Done for custom agents. Repeat with real Claude Code and Codex agents.
2. **One agent, one room.** Referee joins a room, reads a ticket, replies. **Done**, see [Milestone 2 notes](#milestone-2-notes).
3. **Sealed tryouts.** Referee creates private rooms, briefs candidates, collects branches. **Done**, see [Milestone 3 notes](#milestone-3-notes).
4. **Scoring.** Hidden tests run outside agent reach and produce a scorecard. **Done**, see [Milestone 4 notes](#milestone-4-notes).
5. **Role move.** A person's choice in chat moves `@fixer` to the winner. **Done**, see [Milestone 5 notes](#milestone-5-notes).
6. **Failover in the product.** Referee detects a dead holder and promotes the runner-up. **Done**, see [Milestone 6 notes](#milestone-6-notes).
7. **Real tickets.** Fixtures from a public repo with real merged fixes. **Done**, see [Milestone 7 notes](#milestone-7-notes).
8. **Replay mode.** One command reproduces the demo with no API keys. **Done**, see [Milestone 8 notes](#milestone-8-notes).
9. **Submission.** README, video, blog.

## Known constraints

- An agent holds at most one role lease, across all rooms. An agent can't be `@fixer` in two rooms at once.
- Only an agent can assume a role, and only for itself.
- Two agents assuming an empty exclusive role at once can both win, so only one agent may ever try at a time.
- New agents only accept their owner and the owner's agents. Bench's templates must set who can address each agent.
- For about 5 seconds after a holder dies, Switch still reports it as `live`, and tickets sent then wake nobody.

## Failover test

Run on Switch 0.31.0 with Mattermost, using custom protocol agents as separate OS processes. Code is in `spikes/failover/`, raw output is in `results.json` and `person_mention_results.json` beside it.

| Question | Result |
| --- | --- |
| Can one agent move a role to another? | **No.** `assume_role` takes only a role name and applies to the caller |
| Is a second holder refused while the role is held? | Yes, HTTP 400 |
| Does a planned handoff work? | Yes. `@fixer` reaches only the new holder |
| How long does takeover take after `kill -9`? | **5.07–5.13s**, 5 of 5 |
| How long after the holder freezes (SIGSTOP)? | **5.09–5.18s**, 3 of 3 |
| Can the Referee see when the seat is free? | Yes. `get_room_detail` showed it free just before each takeover |
| Is a ticket sent in the gap lost? | Not lost, but nobody is woken. It stays in `read_context`, so it must be re-sent |
| Does the reported double-holder race exist? | **Yes.** Both got HTTP 200 in 19 of 20 rounds, with two holders seen in 17 |
| Does promoting one agent at a time avoid it? | Yes. 20 of 20 rounds had exactly one holder |
| Does a person's `@fixer` in Mattermost wake the holder? | Not on the default policy. Yes with addressing open, including after a handoff and after a kill |

**What this means for the Referee:**

- It alone decides who is promoted. Candidates never race for the role.
- When a holder dies, it finds tickets sent since the holder's last reply in `read_context` and re-sends them to `@fixer`.
- The demo should show the roughly 5-second gap honestly rather than claim instant failover.

## Real agents in Console

Run on Switch Console 0.40.1 with its local server (switch-core 0.31.0, native, API on port 52839). switch-buddy created `bench-claude` (Claude Code) and `bench-codex` (Codex), and an agent-only room `bench-failover` with exclusive role `fixer`.

How Console runs agents: one agent controller process, then one `shared-host.mjs` per agent, then the `claude` or `codex app-server` CLI under it. An agent's host starts on demand when it gets a message.

| Question | Result |
| --- | --- |
| Will a real agent take a role when asked in chat? | **Yes.** Claude Code called `assume_role` and confirmed it held `fixer` (1 of 1) |
| Does `kill -9` on the holder's `claude` CLI free the role? | **No.** Its host exited too, but the controller stayed up. About 40s later Switch still showed bench-claude holding `fixer` with `present_here: true` |
| Can the runner-up take over? | **No.** Codex's `assume_role` got HTTP 400: role is exclusive and held by another agent |
| Does it free after a while? | **No.** 30 minutes after the kill it was still held, present, and Codex was refused again |
| Can a new session of the dead agent release it? | **Yes.** Messaging bench-claude started a fresh host, and it called `release_role`. The seat showed empty |
| Does a planned handoff to Codex work? | **Yes.** Codex's `assume_role` succeeded and `get_room_detail` showed bench-codex holding `fixer` |

**What this means:**

- The 5-second failover from the custom-agent test doesn't carry over to Console agents. The controller's connection keeps the lease alive after the agent's session dies, apparently indefinitely.
- The lease belongs to the agent, not the session. A crashed agent comes back still holding `fixer` the next time Console wakes it.
- Recovery works in two steps: the Referee wakes the dead holder and tells it to release, then tells the runner-up to assume. This is the same as the planned handoff, which works.
- It fails if the holder can't be woken at all (its provider is down, or the machine is off). The router fallback below covers that case.
- For the demo, "kill the agent" should mean making it unresponsive. The Referee notices that `@fixer` hasn't replied in time, runs the release-then-assume handoff, and re-sends the ticket.

## What's still unproven

The test used custom agents that call `assume_role` directly. Bench's candidates are Claude Code, Codex and OpenCode, which raises three questions:

1. ~~Does killing a real agent free the role?~~ **Answered: no.** See above.
2. **Can the Referee make a real agent take a role reliably?** Claude Code and Codex each did it on every request (Claude: take and release, Codex: take). That's 3 of 3, still too few to call reliable.
3. **Does a new holder pick up the work?** The ticket is readable, but we haven't shown a real agent continuing from it.

**Fallback if 1 or 2 fails:** keep `@fixer` on a small always-on router agent run by the Referee, which forwards work to the current winner by name. People still tag `@fixer`, but the demo loses some of its native Switch story.

## Milestone 2 notes

Run it against the dev stack in `deploy/`:

```
uv run python -m referee --room bench-payments
uv run pytest
```

| File | Job |
| --- | --- |
| `referee/switch.py` | Async Switch protocol client: register, stream, 2s heartbeat, ops |
| `referee/tickets.py` | Pure parsing: `@bench-referee ticket: <title>` plus details lines, stable `T-xxxxxx` IDs |
| `referee/agent.py` | The Referee: filters to its room and messages addressed to it, skips its own and replays, replies in the ticket's thread |
| `referee/config.py` | `SWITCH_URL`, `SWITCH_REGISTRATION_TOKEN`, dev fallback to `deploy/.env` |
| `tests/` | 11 unit tests, 1 live test against Switch |

Found while building it:

- **An agent can't change its own addressing policy** (HTTP 403). Only the owner can. On the dev stack the Referee opens it through the Gateway admin. On Console it's a setting on the agent's page, so the Bench templates or setup guide must cover it.
- Every op, even `list_rooms`, needs the event stream open first (HTTP 409 otherwise).
- `create_room` returns `id`, but `list_rooms` returns `room_id`.
- Checked by hand: a person's `@bench-referee ticket: ...` in Mattermost got a threaded reply in about 1 second.

## Milestone 3 notes

Run it with candidates (agent names on the same Switch server):

```
BENCH_CANDIDATES=bench-claude,bench-codex uv run python -m referee --room bench-payments
```

Then post `@bench-referee ticket: <title>` with details below it. Settings: `BENCH_REPO` (git repo, default is the sample in `tickets/sample-invoice`), `BENCH_WORKDIR` (default `.bench/tryouts`), `BENCH_TRYOUT_MINUTES` (default 20).

What happens per ticket:

1. For each candidate, the Referee clones the repo to `<workdir>/<ticket>/<candidate>` on branch `tryout/<ticket>/<candidate>`.
2. It creates room `tryout-<ticket>-<candidate>` with only itself and that candidate, and sends a brief: the ticket, the folder, the branch, and "reply done".
3. A reply containing "done" ends that candidate's tryout. Any other message gets one fixed answer: no questions during a tryout. Messages from anyone else are ignored.
4. If the time runs out, it ends anyway.
5. Either way the Referee reads the branch itself, with `git diff --numstat` against the base. It commits anything left uncommitted, so the branch is the record. It never takes the candidate's word for what it did.
6. Progress goes into the ticket's thread in the main room: started, each finish (time, commit, files, lines), and "all in".

| File | Job |
| --- | --- |
| `referee/workspaces.py` | Clone, branch, collect a `Snapshot` from git |
| `referee/tryouts.py` | Tryout state and every message the Referee sends during one |
| `referee/agent.py` | Routes messages by room, one task per tryout, room lock |
| `tickets/sample-invoice/` | Sample repo with a real half-cent rounding bug (2.675 gives 2.67) |
| `tests/stub_candidate.py` | A scripted Switch agent that plays a coding agent |

Tests: 23 unit tests, 2 live tests. The live tryout test runs a fixing candidate and a silent one. It checks each room holds only the Referee and its candidate, the silent one gets HTTP 403 reading the other's room, and the fixer's branch really fixes the bug.

Found while building it:

- **Ops act in the agent's connected room.** `post_message` and `send_targeted_message` take no room. Sending to someone outside the current room is refused (HTTP 400). The Referee connects before each send, under a lock.
- **Events arrive from every room the agent is in**, whichever one it's connected to, so the Referee routes them by `room_id`.
- **Rooms really are sealed.** A non-member gets HTTP 403 on `read_context` and HTTP 400 on `connect_to_room`.
- **Leak to know about:** every agent is auto-added to the Mattermost Town Square and Off-Topic rooms, so candidates could meet there. Their folders are also only separate directories on one machine, not a sandbox. Both go in the README's limits section.

### First real tryout, Console, 12:04 on Nov 7

Run with `./scripts/console-tryout.sh`. It reads Console's registration token into the Referee's environment without printing it, and posts the sample ticket with `--ticket`.

| | bench-claude (Claude Code) | bench-codex (Codex) |
| --- | --- | --- |
| Woke on the brief | Yes, Console started a session for the tryout room | Yes |
| Permission prompts | None | None, though it worked outside its own folder |
| Committed | 25s after the brief | 1m52s after the brief |
| Change | 5 files, +32 -3, 2 of them stray `.pyc` files | 2 files, +44 -3 |
| Wrote tests | Yes | Yes, before the fix |
| Rounding cases | 6 of 6 | 6 of 6 |

Found:

- Real agents follow the brief as written: right folder, right branch, committed, no pushes.
- The sample repo had no `.gitignore`, so Claude's commit picked up `__pycache__`. That's fixed in `tickets/sample-invoice/.gitignore`. Scoring should count only source changes anyway, since agents will commit junk.
- Both fixes passed, so this ticket can't separate the agents. Milestone 4's hidden tests and milestone 7's real tickets need harder cases: edge cases, tax rounding order, and performance.
- Not checked: whether each agent replied "done" before its commit landed, or whether the Referee recorded "done" rather than a timeout. That's in the Referee's terminal output.

## Milestone 4 notes

A ticket fixture is now a folder: `repo/` (what agents get), `hidden/` (tests only the Referee reads) and `ticket.md`. Set it with `BENCH_FIXTURE`, default `tickets/sample-invoice`. Build the sandbox image once with `docker build -t bench-scorer:1 referee/scoring`, or let the Referee build it.

When all tryouts are in, the Referee:

1. Runs the hidden tests on the base commit, which gives the true test count and a "before any fix" line. On the sample, 6 of 10 pass before the fix.
2. Exports each candidate's commit with `git archive`, so only committed work counts, and runs the hidden tests in Docker: `--network none`, read-only code, 512 MB, 1 CPU, 128 processes, 120s limit. It kills the container by name on timeout.
3. Ignores the candidate's `conftest.py`, `pytest.ini` and `pyproject.toml` pytest settings. Skipped tests count as failures, and a crash counts as zero.
4. Ranks by hidden tests passed. Among equals, changes within 5 code lines of the smallest count as equally small, and the fastest of those wins. Clearly bigger changes rank below. Code lines leave out blanks, comments, tests and build junk, so an agent isn't marked down for explaining its change.
5. Flags added source lines that touch the test machinery (`pytest`, `junit`, `atexit`, `sys.modules`, `/out`, and similar) with "CHECK BY HAND".
6. Posts the scorecard in the ticket's thread, writes a `Scorecard T-xxx` room document, and creates or updates a `Bench standings` room document.

Without Docker it runs the same tests locally, and the scorecard says "NOT sandboxed". Set `BENCH_SANDBOX=0` to force that.

| File | Job |
| --- | --- |
| `referee/scoring/run.py` | Export, sandboxed run, junit parsing, tamper signs |
| `referee/scoring/card.py` | Scores, ranking, scorecard text, room document markdown, standings |
| `referee/scoring/Dockerfile` | The sandbox image: Python 3.12 and pytest 8.3.3, non-root |
| `tickets/sample-invoice/hidden/` | 10 hidden tests: half-cent cases, round once on the total, tax order, drift, Decimal inputs |
| `tests/test_scoring.py` | Cheat attempts (conftest, pytest config, skip, network, endless loop, uncommitted work), code-line counting, ranking and text |

Tests: 38 unit and sandbox tests, 2 live. The live tryout test now checks the scorecard and standings document too.

**Real run, re-scored** (Claude Code and Codex from the first Console tryout):

```
1. bench-claude: 10/10 hidden tests, 13 code lines changed, 25s
2. bench-codex: 10/10 hidden tests, 11 code lines changed, 112s
```

Both solve it. The first ranking counted raw lines, so Codex won on 13 lines to 19. But 3 of Claude's lines were a comment explaining the fix. Counted as code only, the two are 2 lines apart, effectively the same size, so Claude's 4x speed decides.

**Why this order:** correctness first, because nothing else matters if the fix is wrong. Size before speed, because a person reviews every change, and minutes of agent time are cheap next to review time. But a gap of a few lines is noise, so near-equal sizes don't decide the winner. The tie band is `SIZE_TIE` in `card.py`.

**Limit:** the hidden tests run in the same process as the candidate's code. Code written on purpose to patch pytest from inside could fake a pass. The sandbox stops it reaching the network or the host, and the tamper check flags the usual ways in, but it isn't proof. The README's limits section must say so.

Found while building it:

- Agents registered with a registration token are owned by an "Agent Bootstrap" user, not the person who runs the server. That's why addressing between them and Console's agents needs care.
- Room documents: `create_room_document` returns `document_id`, and `update_room_document` edits in place. Both act on the connected room.

## Milestone 5 notes

After the scorecard, the Referee says how to pick. A person replies in the main room:

```
@bench-referee pick bench-claude
@bench-referee pick bench-codex for T-74e40f anyway
@bench-referee who has fixer?
```

The Referee can't put a role on another agent. So a move is a checked sequence:

1. **Check the pick.** The sender can't be the Referee or a candidate, and must be in `BENCH_PICKERS` if that's set. The name must be a candidate in that ticket's scorecard, the latest one by default. Picking one that didn't pass every hidden test needs `anyway`. Only one move runs at a time.
2. **Release.** If someone else holds `@fixer`, the Referee asks them in the ticket's thread to call `release_role`, then polls `get_room_detail` until they're gone.
3. **Assume.** It invites the winner into the main room if needed, asks it to call `assume_role`, then polls until the winner is the only holder.
4. **Report.** "@fixer is now bench-claude (was bench-codex). Took 4s." If a step times out (`role_minutes`, default 3), it says exactly which step stalled and who holds the role now.

Each request ends with a machine-readable line, `Switch call: connect_to_room(room_id=...) then release_role()`, so scripted agents can follow it and real ones get it spelled out.

The main room gets an exclusive `fixer` role when the Referee joins, if it doesn't have one. Set `BENCH_ROLE` to use another name.

| File | Job |
| --- | --- |
| `referee/roles.py` | Pick parsing, holder reading, every message used in a move |
| `referee/agent.py` | `_check_pick`, `_move`, polling with `_until` |
| `tests/test_roles.py` | 14 tests: parsing, first pick, handoff order, holder that won't release, winner that won't assume, refusals, `anyway`, pickers allowlist, `who` |

The live test now runs: tryout, then scorecard, then `pick <fixer>` (fixer becomes the only holder), then `pick <silent> anyway` (a real release-then-assume handoff, and the silent one becomes the only holder).

Found while building it:

- **Every agent in the main room sees a role request, not only its target.** My stub acted on requests meant for someone else, and the old holder grabbed `@fixer` back first. Switch then refused the winner with HTTP 400. Real agents only wake for messages addressed to them. The stub now checks `addressed`, as a real agent would.
- **A holder that moves to another room keeps its lease.** `get_room_detail` shows it with `present_here: false` and its `session_room`, and others are refused until it calls `release_role`.
- **People's messages to the Referee arrive "not addressed"** unless the Referee's owner sent them. On Console that's an "Agent Bootstrap" user, not you. The Referee sees its own room anyway, so it also acts on a direct `@bench-referee` mention there. `BENCH_PICKERS` is the guard on who can pick.

### Real move, Console, 13:38 on Nov 7

Run with `./scripts/console-tryout.sh`, which now creates `#bench-team` bridged to Console's Mattermost user. Picks were typed in the ticket's thread in Console's chat view.

Scorecard: bench-claude 10/10 hidden tests, 11 code lines, 39s. bench-codex 10/10, 11 code lines, 102s.

| Step | Result |
| --- | --- |
| `pick bench-claude` | Invited, assumed, confirmed by the Referee in **13s**. Switch's own line: "assumed the `fixer` role" |
| `pick bench-codex` | Claude released straight away. Codex was **refused** with "You already hold a role lease; release it before assuming another", because it still held `fixer` in the old `bench-failover` room |
| After telling Codex to `release_role` first | Codex took it. The Referee confirmed the handoff in **102s**, inside its 3-minute window |

Three bugs this found, all fixed (57 tests pass):

1. **The Referee wrote "@fixer" in its messages.** Switch reads that as a mention of the role and wakes the holder. Claude and Codex were each woken for nothing after taking the role, and Switch's bot warned that nobody held it. Referee text now says "the fixer role", and a test checks no message contains `@fixer`.
2. **The Referee answered Switch's own bot**, whose notice mentioned `@bench-referee`. It now ignores the `Switch` system sender.
3. **An agent holds one role lease across all rooms.** The assume request now tells the winner to call `release_role` first, then `assume_role`. Checked live: `release_role` returns `released` even when nothing is held, and it drops a lease held in another room.

**Re-run with the fixes, 13:49.** Tryout T-81551b: Claude 10/10 in 32s, Codex 10/10 in 104s, 11 code lines each. Codex still held the fixer role from the run before, so both picks were real handoffs:

| Pick | Result |
| --- | --- |
| `pick bench-claude` | Codex released, Claude assumed. **12s**, no refusal |
| `pick bench-codex` | Claude released, Codex assumed. **12s**, no refusal |

No stray wake-ups, and no notices from Switch's bot. Switch's own log lines in the channel show the exact sequence: released, assumed, released, assumed.

One cosmetic fix after: Switch puts the target's `@name` in front of a targeted message, so requests read "@bench-claude @bench-claude ...". The Referee's requests no longer start with the name.

## Milestone 6 notes

The Referee keeps watch on the main room (`check_holder`, every `poll_seconds`, default 2s). It fails over when any of these is true:

| Signal | Catches |
| --- | --- |
| Holder's status in `agent_statuses` is `disconnected` or `offline` | Agents Switch sees die |
| The seat was held and is suddenly empty, outside a move | Dead custom agents, whose seat frees at the moment they disconnect (about 5s) |
| Someone tagged `@fixer` and the holder hasn't posted within `response_seconds` (default 5 min) | Any silent holder, including Console agents, whose role is held forever after a crash |
| The seat is empty and `@fixer` work is waiting | A move that left nobody holding the role |

Failover then runs the same checked steps as a pick:

1. Pick the runner-up: the best on the latest scorecard that isn't the holder and isn't disconnected.
2. If the holder still holds the role, ask it to release. On Console this wakes a fresh session, which can release the old lease.
3. Ask the runner-up to release any old lease and assume, and wait until Switch shows it as the only holder.
4. Re-send every `@fixer` message the holder missed, to the role, in its original thread: "Re-sent, the last holder didn't answer. ana asked: ...".
5. Report: "Failover done: bench-codex now holds the fixer role (was bench-claude). Took 6s. Re-sent 1 waiting message(s)."

If the holder never releases, the role can't move. The Referee then sends the waiting work to the runner-up **by name**, and says plainly that the role stays with the dead holder until it comes back. That's the router fallback, done only when needed.

Live test, on the dev server, with three scripted agents:

| Case | Result |
| --- | --- |
| Holder A killed (stream and heartbeat cut, nothing released) with a `@fixer` message waiting | Seat emptied 5s after the kill, and B held the role **about 6.5s after the kill**. The missed message reached B |
| New holder B alive but ignoring a new `@fixer` message | After the 10s test deadline, B was asked to release, C was promoted (A skipped as disconnected), and the message reached C |

Unit tests cover: disconnected holder, seat that empties by itself, silent holder, holder that speaks in time, stuck holder (work forwarded by name), no runner-up, dead runner-up skipped, and role notices during a failover. 66 unit and sandbox tests, 3 live.

Found while building it:

- **Role mentions reach the Referee.** A message targeting the role arrives as `@fixer ...` with `addressed: false`, so the Referee can track all role work without being addressed itself.
- **A dead custom agent's status and seat change together.** At about 5.4s the status goes `live` to `disconnected` and `held_by` empties in the same poll. So "seat suddenly empty" is the reliable signal, not the status.
- **Switch posts role notices under the agent's own name** ("released the fixer role"). The Referee first read the old holder's release notice as the holder answering, and dropped the waiting work before re-sending it. It now snapshots the waiting work when a failover starts.

Set the answer deadline with `BENCH_RESPONSE_SECONDS`. `./scripts/console-tryout.sh` uses 60s.

Also fixed before the Console run: when Console wakes an agent it posts "Starting a session…" and later "Worked for 13s…" under the agent's name, and Switch posts "assumed / released the fixer role" the same way. None of these count as the holder answering now (`roles.is_notice`), so an agent that crashes after starting still trips the deadline.

### Failover on Console, 15:00 on Nov 7

Tryout T-06213d: Claude and Codex both 10/10. Codex held the fixer role in `#bench-team`.

| Step | Result |
| --- | --- |
| `@fixer what does invoice_total return for an empty list?` | Codex answered. No failover, which is right |
| `kill -9` on Codex | **Didn't take.** Console reuses an already running session for the room, and there was no way to tell which of six Codex processes it was. Killing any one doesn't stop the agent, because Console starts another on the next message |
| Stopped bench-codex in Console's agent page | **The seat freed.** Unlike a crash, stopping an agent releases its lease |
| The Referee | "Failover: bench-codex dropped out of the role. Moving the fixer role to the runner-up, bench-claude." Claude assumed, then "Failover done: bench-claude now holds the fixer role (was bench-codex). Took 6s." |
| `@fixer can you add a test for an empty invoice?` | Answered by Claude, the new holder |

What this means for the demo:

- **The demo's "kill" is stopping the agent in Console.** It's one click, judges can see it, and the takeover follows in seconds through the "seat empty" signal.
- **A real crash on Console holds the seat.** Then the answer deadline triggers, and the Referee wakes the crashed agent to release. That path is unit-tested and live-tested with scripted agents, but not yet with a Console crash.
- Not covered on Console yet: re-sending a message that was waiting when the holder went down. It was proven live on the dev server.

## Milestone 7 notes

Three real tickets, each a bug that was really fixed in a public, dependency-free Python library:

| Ticket | Repo, license | Fixed in | Hidden tests | Before the fix |
| --- | --- | --- | --- | --- |
| `humanize-naturalsize-rollover`: "1000.0 kB" instead of "1.0 MB" | python-humanize/humanize, MIT | `823ad60` | `tests/test_filesize.py` | 70 of 76 pass |
| `more-itertools-falsy-exception`: `one()`/`only()` ignore a falsy custom exception | more-itertools/more-itertools, MIT | `def2dab` | `tests/test_more.py` | 604 of 608 pass |
| `boltons-singularize-ss`: `singularize('glass')` gives `'glas'` | mahmoud/boltons, BSD-3-Clause | `1e61524` | `tests/test_strutils.py` | 17 of 18 pass |

With the real fix applied, each passes all its hidden tests (76/76, 608/608, 18/18), checked in the sandbox.

How a real ticket works:

- `tickets/<name>/fixture.json` names the source repo, the fix commit, the hidden test files, import paths (humanize uses `src`), and any stub files. `ticket.md` is the ticket, written in my own words from the fix.
- The first time it's used, `referee/fixtures.py` clones the source into `.bench/fixtures/sources/`. It exports the code **as it was just before the fix** and makes it a fresh one-commit repo. The hidden tests are the fix commit's own test files, so they include every existing test plus the new ones. The build is cached and rebuilt only when `fixture.json` changes.
- **No answer in the history.** A plain clone would carry the fix commit, and a candidate could just `git show` it. The candidates' repo has one commit, "Before the fix". A test checks the fix's SHA and message appear nowhere in it.
- In chat: `@bench-referee tickets` lists them, and `@bench-referee try boltons-singularize-ss` posts that ticket and runs the tryout on its repo. Plain `ticket: ...` messages use the default fixture (`BENCH_FIXTURE`, sample-invoice by default). `--ticket` takes a name too.

| File | Job |
| --- | --- |
| `referee/fixtures.py` | Build and load fixtures, history stripped |
| `tickets/*/fixture.json`, `ticket.md` | The three real tickets |
| `tests/test_fixtures.py` | Built from a local two-commit "upstream": one commit, no trace of the fix, forced stub files, rebuild on change, chat commands, tryout on the fixture repo. Plus a live check of the three real tickets |

Found while building it:

- **humanize generates `_version.py` at build time and gitignores it**, so the raw source can't be imported. The fixture adds a stub, and `init_repo` force-adds it, because the project's own `.gitignore` had silently dropped it.
- **Running downloaded tests outside the sandbox is refused** by my own safety rules. That's right, and it's why every check above ran in the Docker sandbox. The scorer now keeps pytest's last 2,000 characters of output, for debugging.
- These tickets are still small, one function each. They prove the pipeline. Separating agents needs harder ones, which can be added as more `fixture.json` files.

### Real-ticket tryout, Console, 16:40 on Nov 7

Tryout T-60b07f on `more-itertools-falsy-exception`, bench-claude vs bench-codex, 15 min.

| Candidate | Committed | Hidden tests | Code lines | Tamper signs |
| --- | --- | --- | --- | --- |
| bench-claude | 16:41, about 1 min in | 608/608 | 12 | none |
| bench-codex | nothing, no edits either | 604/608 (the baseline) | 0 | none |

- Claude solved both parts of the ticket: a falsy custom exception is now raised (`is not None` instead of `or`), and the "too many items" message isn't built before raising a custom `too_long`. That second part is what `test_too_long_does_not_evaluate_repr` checks, and it wasn't spelled out as a test in the candidate's repo. It also added its own tests.
- Codex left its workspace untouched for the whole 15 minutes. On the sample ticket it fixed the bug, so this looks like Codex's session not starting or stalling, not a scoring problem. Worth checking its Console session before the demo.
- Scores were re-checked by hand in the sandbox and match the Referee's method.

## Milestone 8 notes

```
uv run python -m replay                  # sample-invoice, about 95s
uv run python -m replay more-itertools   # the real ticket
uv run python -m replay --speed 4        # faster
```

- **What's real:** the Referee, the Switch server, sealed rooms, Docker scoring, the scorecard and standings documents, the role move and the failover. **What's replayed:** the candidates. Each replay agent applies the exact commit Claude Code or Codex made in a recorded Console run, at the recorded time, and reports done. A candidate that never committed stays silent. Their chat replies are scripted and say so.
- A scripted person (`replay-person`) files the ticket, picks the top score, sends work to the role, and then the winner is killed with work waiting.
- **Server:** Console's local server if it's up (the room includes Console's user, so you can watch), else the Docker dev stack, else `SWITCH_URL`. No provider keys anywhere. Docker is needed for real tickets; the sample's own code may be scored locally without it.
- **Recordings** live in `replay/runs/<name>/` (`run.json` plus one patch per candidate). `python -m replay.record .bench/tryouts/T-xxxxxx --name <name>` makes a new one from any tryout folder.

| Run | Server | Result |
| --- | --- | --- |
| sample-invoice, 1x | dev stack | Scorecard at 83s, both 10/10, Claude first on time. Pick took 1s. Failover 8s after the kill, missed message re-sent and answered |
| sample-invoice, 4x | Console | Same story in 32s. Failover 7s after the kill |
| more-itertools, 4x | dev stack | Claude 608/608, Codex silent and timed out at 604/608. Scoring took 33s (608 tests, three runs) |

Fixed on the way: the ticket acknowledgement still said "Tryouts aren't running yet" (left over from milestone 2), and short time limits printed as "1.81667 min".

## Submission checklist

Required:

- [ ] Public repository
- [ ] Short demo video of the workflow running
- [ ] README: which agents, how the channel is set up, how to run it

To match the strongest entries:

- [ ] "For judges" section at the top of the README
- [ ] Table mapping each Switch feature to the file that uses it
- [x] One-command replay
- [ ] Honest limits section
- [ ] Demo of about 2.5 minutes

Side tracks to decide on:

- [ ] Best Blog (AirPods 5): a post about the build
- [ ] Code with Wispr ($500): needs a Wispr usage scorecard, so dictate from the start
- [ ] #HowISwitch post (Logitech keyboard for the top 5)

## Rules to remember

- The Switch setup and the workflow must be built during the hackathon. Existing agents are fine.
- Submissions close Nov 7, 2026, 11:59 PM IST.
- AI coding tools are allowed, but you must understand what you submit and be able to explain it.
- Teams are solo or up to 4 people.
