"""A replay candidate: a real Switch agent that replays one recorded coding agent.

The code is what the real agent wrote: when the Referee briefs it, it waits the
recorded time, applies the recorded patch, commits with the recorded message and
reports done. A candidate recorded as silent stays silent. Everything around the
code (obeying role requests, answering work sent to the role) is scripted, and
the chat replies say so.
"""

import asyncio
import re
import subprocess
import time
from pathlib import Path

from referee import roles
from referee.switch import SwitchAgent

FOLDER = re.compile(r"^Work only in this folder: (.+)$", re.MULTILINE)
ROLE_CALL = re.compile(
    r"^Switch call: connect_to_room\(room_id=(?P<room>[\w-]+)\) then "
    r"(?:(?P<release>release_role\(\))|release_role\(\) then assume_role\(role=\"(?P<role>[\w-]+)\"\))$",
    re.MULTILINE,
)


class ReplayCandidate:
    def __init__(self, agent: SwitchAgent, referee_name, recording, run_dir, role="fixer", speed=1.0, say=print):
        self.agent = agent
        self.referee_name = referee_name
        self.recording = recording
        self.run_dir = Path(run_dir)
        self.role = role
        self.speed = speed
        self.say = say
        self.dead = False
        self.answered = []
        self.errors = []
        self._tasks = set()

    async def start(self):
        await self.agent.start(self._on_event)

    def die(self):
        """Drops the connection without releasing anything, as a crash would."""
        self.dead = True
        for task in [*self.agent._tasks, *self._tasks]:
            task.cancel()

    async def _on_event(self, kind, data):
        payload = data.get("payload", {})
        body, sender = payload.get("body") or "", payload.get("sender_name")
        # Like a real agent, act only on messages addressed to it.
        if self.dead or kind != "message" or sender == self.agent.name or not payload.get("addressed"):
            return
        if sender == self.referee_name:
            call = ROLE_CALL.search(body)
            if call:
                return self._spawn(self._role(call))
            folder = FOLDER.search(body)
            if folder:
                return self._spawn(self._attempt(data["room_id"], folder.group(1).strip()))
        if roles.mentions_role(body, self.role):
            self._spawn(self._answer(data["room_id"], payload.get("thread_id") or payload.get("message_id"), body))

    def _spawn(self, coroutine):
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._done)

    def _done(self, task):
        self._tasks.discard(task)
        if not task.cancelled() and task.exception():
            self.errors.append(repr(task.exception()))

    async def _role(self, call):
        await self.agent.op("connect_to_room", room_id=call["room"])
        await self.agent.op("release_role")
        if call["role"]:
            await self.agent.op("assume_role", role=call["role"])

    async def _attempt(self, room_id, folder):
        briefed = time.monotonic()
        if not self.recording.get("patch"):
            self.say(f"{self.agent.name} got its brief. In the recorded run it never committed, so it stays silent.")
            return
        await asyncio.sleep(self.recording["seconds"] / self.speed)
        patch = (self.run_dir / self.recording["patch"]).resolve()
        await asyncio.to_thread(git, folder, "apply", "--index", str(patch))
        await asyncio.to_thread(
            git, folder, "-c", f"user.name={self.agent.name}", "-c", "user.email=replay@bench.invalid",
            "commit", "-qm", self.recording["message"],
        )
        self.say(
            f"{self.agent.name} committed in its sealed room after {time.monotonic() - briefed:.0f}s: "
            f"\"{self.recording['message']}\""
        )
        await self.agent.op("connect_to_room", room_id=room_id)
        await self.agent.op("send_targeted_message", body="done", target_names=[self.referee_name])

    async def _answer(self, room_id, thread_id, body):
        await self.agent.op("connect_to_room", room_id=room_id)
        await self.agent.op(
            "post_message",
            body=f"On it. (Replay: {self.agent.name} is scripted here; its code is from the recorded run.)",
            thread_id=thread_id,
        )
        self.answered.append(body)


def git(folder, *args):
    subprocess.run(["git", *args], cwd=folder, capture_output=True, text=True, check=True)
