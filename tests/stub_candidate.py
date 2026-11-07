"""A scripted candidate: a real Switch agent that plays a coding agent in tests.

It reads the folder from the Referee's brief, runs `work(path)` on it, commits,
and replies done in its tryout room. With no `work`, it stays silent. It also
follows the Referee's role requests, unless `obeys_roles` is False.
"""

import asyncio
import subprocess

from referee.switch import SwitchAgent
from replay.candidate import FOLDER, ROLE_CALL


class StubCandidate:
    def __init__(self, agent: SwitchAgent, referee_name, work=None, obeys_roles=True):
        self.agent = agent
        self.referee_name = referee_name
        self.work = work
        self.obeys_roles = obeys_roles
        self.briefs = []
        self.role_requests = []
        self.errors = []
        self.addressed = []
        self._tasks = set()

    async def start(self):
        await self.agent.start(self._on_event)

    async def _on_event(self, kind, data):
        payload = data.get("payload", {})
        if kind == "message" and payload.get("addressed"):
            self.addressed.append(payload.get("body") or "")
        # Like a real agent, act only on messages addressed to us. Role requests go to
        # the main room, where other candidates see them too.
        if kind != "message" or payload.get("sender_name") != self.referee_name or not payload.get("addressed"):
            return
        role_call = ROLE_CALL.search(payload.get("body") or "")
        if role_call:
            self.role_requests.append(role_call.group(0))
            if self.obeys_roles:
                self._spawn(self._role(role_call))
            return
        match = FOLDER.search(payload.get("body") or "")
        if not match:
            return
        self.briefs.append((data["room_id"], payload["body"]))
        if self.work:
            self._spawn(self._attempt(data["room_id"], match.group(1).strip()))

    def _spawn(self, coroutine):
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._done)

    def _done(self, task):
        self._tasks.discard(task)
        if not task.cancelled() and task.exception():
            self.errors.append(repr(task.exception()))

    def die(self):
        """Drops the connection without releasing anything, as a crash would."""
        for task in self.agent._tasks:
            task.cancel()

    async def _role(self, call):
        await self.agent.op("connect_to_room", room_id=call["room"])
        await self.agent.op("release_role")
        if call["role"]:
            await self.agent.op("assume_role", role=call["role"])

    async def _attempt(self, room_id, folder):
        await asyncio.to_thread(self.work, folder)
        await asyncio.to_thread(
            subprocess.run,
            ["git", "-c", "user.name=stub", "-c", "user.email=stub@bench.invalid", "commit", "-qam", "Fix"],
            cwd=folder,
            check=True,
        )
        await self.agent.op("connect_to_room", room_id=room_id)
        await self.agent.op("send_targeted_message", body="done", target_names=[self.referee_name])
