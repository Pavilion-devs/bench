"""Tests whether Bench's role failover works on a live Switch server.

Each agent is a separate OS process (see worker.py), so "kill" means SIGKILL.
Run: uv run --with httpx python spikes/failover/probe.py
Writes results.json next to this file.
"""

import json
import queue
import signal
import statistics
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

HERE = Path(__file__).parent
BASE = "http://127.0.0.1:8000"
MATTERMOST = "http://127.0.0.1:8065"
ROLE = "fixer"
ENV = dict(
    line.split("=", 1)
    for line in (HERE.parent.parent / "deploy/.env").read_text().splitlines()
    if "=" in line and not line.startswith("#")
)
RUN = uuid.uuid4().hex[:6]


def register(name):
    response = httpx.post(
        f"{BASE}/agents",
        headers={"Authorization": f"Bearer {ENV['AGENT_REGISTRATION_TOKEN']}"},
        json={
            "name": name,
            "description": "Bench failover test agent",
            "connector_type": "custom",
            "integration_profile": {
                "connection_model": "always_on",
                "message_exchange": True,
                "pre_invocation_mediation": [],
                "post_invocation_mediation": [],
                "event_reporting": [],
            },
            "overwrite": True,
        },
        timeout=15,
    )
    response.raise_for_status()
    return response.json()


class Agent:
    """A running worker process, with its stream events and operation replies."""

    def __init__(self, name, credentials):
        self.name = name
        self.events = []
        self.replies = {}
        self.ready = threading.Event()
        self.cond = threading.Condition()
        self.proc = subprocess.Popen(
            [sys.executable, str(HERE / "worker.py"), BASE, credentials["id"], credentials["api_key"]],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        threading.Thread(target=self._read, daemon=True).start()
        if not self.ready.wait(15):
            raise RuntimeError(f"{name} never got connection_state")

    def _read(self):
        for line in self.proc.stdout:
            message = json.loads(line)
            if message["type"] == "reply":
                with self.cond:
                    self.replies[message["id"]] = message
                    self.cond.notify_all()
            elif message["type"] == "event":
                self.events.append(message)
                if message["kind"] == "connection_state":
                    self.ready.set()

    def op(self, op, **args):
        request_id = uuid.uuid4().hex
        self.proc.stdin.write(json.dumps({"id": request_id, "op": op, "args": args}) + "\n")
        self.proc.stdin.flush()
        with self.cond:
            self.cond.wait_for(lambda: request_id in self.replies, timeout=30)
        reply = self.replies.get(request_id)
        if reply is None:
            raise TimeoutError(f"{self.name}: {op} got no reply")
        return reply["status"], reply["body"]

    def messages_containing(self, marker):
        found = []
        for event in self.events:
            if event["kind"] == "message" and marker in json.dumps(event["data"]):
                found.append(event)
        return found

    def stop(self, sig=signal.SIGKILL):
        self.proc.send_signal(sig)

    def close(self):
        if self.proc.poll() is None:
            self.proc.send_signal(signal.SIGCONT)
            self.proc.kill()
        self.proc.wait()


def result(body):
    return body.get("result") if isinstance(body, dict) else body


class Lab:
    def __init__(self):
        names = ["ref", "a", "b", "c"]
        self.creds = {n: register(f"bench-{n}-{RUN}") for n in names}
        self.names = {n: f"bench-{n}-{RUN}" for n in names}
        self.referee = Agent("ref", self.creds["ref"])
        status, body = self.referee.op(
            "create_room",
            name=f"failover-{RUN}",
            description="Bench failover test",
            agent_names=list(self.names.values()),
            user_names=[ENV.get("MATTERMOST_USER", "user")],
            channel_type="channel_public",
            roles=[{"name": ROLE, "instructions": "You fix tickets.", "exclusive": True}],
        )
        if status != 200:
            raise RuntimeError(f"create_room failed: {status} {body}")
        self.room = result(body)
        self.room_id = self.room.get("room_id") or self.room.get("id")
        self.referee.op("connect_to_room", room_id=self.room_id)

    def spawn(self, name):
        agent = Agent(name, self.creds[name])
        status, body = agent.op("connect_to_room", room_id=self.room_id)
        if status != 200:
            raise RuntimeError(f"{name} connect failed: {status} {body}")
        return agent

    def holders(self):
        status, body = self.referee.op("get_room_detail", room_id=self.room_id)
        if status != 200:
            raise RuntimeError(f"get_room_detail failed: {status} {body}")
        for role in result(body).get("roles", []):
            if role["name"] == ROLE:
                return [h["name"] if isinstance(h, dict) else h for h in role["held_by"]]
        raise RuntimeError("role not found")

    def wait_free(self, timeout=30):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if not self.holders():
                return True
            time.sleep(0.25)
        return False


def test_cannot_move_role(lab, out):
    """Is there any way for one agent to put a role on another?"""
    response = httpx.get(
        f"{BASE}/agents/{lab.creds['ref']['id']}/ops",
        headers={"Authorization": f"Bearer {lab.creds['ref']['api_key']}"},
        timeout=15,
    )
    operations = response.json()["operations"]
    role_ops = {
        name: list(spec.get("input_schema", {}).get("properties", {}))
        for name, spec in operations.items()
        if "role" in name
    }
    a = lab.spawn("a")
    b = lab.spawn("b")
    try:
        assert a.op("assume_role", role=ROLE)[0] == 200
        refused = b.op("assume_role", role=ROLE)
        out["move_role"] = {
            "role_operations_and_args": role_ops,
            "second_assume_while_held": {"status": refused[0], "body": refused[1]},
            "holders": lab.holders(),
        }
    finally:
        a.close()
        b.close()
    lab.wait_free()


def test_graceful_handoff(lab, out):
    """Holder releases, winner assumes. Does @role then reach only the winner?"""
    a, b = lab.spawn("a"), lab.spawn("b")
    try:
        a.op("assume_role", role=ROLE)
        marker1 = f"before-{uuid.uuid4().hex[:6]}"
        lab.referee.op("send_targeted_message", body=marker1, target_roles=[ROLE])
        start = time.time()
        a.op("release_role")
        status, body = b.op("assume_role", role=ROLE)
        elapsed = time.time() - start
        marker2 = f"after-{uuid.uuid4().hex[:6]}"
        _, sent = lab.referee.op("send_targeted_message", body=marker2, target_roles=[ROLE])
        time.sleep(2)
        addressed = lambda agent, marker: [
            e["data"]["payload"].get("addressed") for e in agent.messages_containing(marker)
        ]
        out["graceful_handoff"] = {
            "seconds": round(elapsed, 3),
            "assume_status": status,
            "role_instructions_returned": result(body),
            "holders": lab.holders(),
            "target_statuses": result(sent).get("target_statuses"),
            "before": {"a_addressed": addressed(a, marker1), "b_addressed": addressed(b, marker1)},
            "after": {"a_addressed": addressed(a, marker2), "b_addressed": addressed(b, marker2)},
        }
    finally:
        a.close()
        b.close()
    lab.wait_free()


def measure_failure(lab, sig, label):
    """Kill or freeze the holder. Times the Referee's view and a standby's takeover."""
    a, b = lab.spawn("a"), lab.spawn("b")
    try:
        assert a.op("assume_role", role=ROLE)[0] == 200
        time.sleep(1)
        start = time.time()
        a.stop(sig)
        seen_free = took_over = None
        refused = 0
        while time.time() - start < 40 and took_over is None:
            if seen_free is None and not lab.holders():
                seen_free = time.time() - start
            status, _ = b.op("assume_role", role=ROLE)
            if status == 200:
                took_over = time.time() - start
            else:
                refused += 1
            time.sleep(0.25)
        return {
            "failure": label,
            "referee_saw_free_s": round(seen_free, 2) if seen_free else None,
            "standby_took_over_s": round(took_over, 2) if took_over else None,
            "refused_attempts": refused,
            "holders_after": lab.holders(),
        }
    finally:
        a.close()
        b.close()
        lab.wait_free()


def test_failure_timing(lab, out, runs=5):
    samples = [measure_failure(lab, signal.SIGKILL, "kill -9") for _ in range(runs)]
    samples += [measure_failure(lab, signal.SIGSTOP, "frozen (SIGSTOP)") for _ in range(3)]
    takeovers = [s["standby_took_over_s"] for s in samples if s["standby_took_over_s"]]
    out["failure_timing"] = {
        "samples": samples,
        "takeover_s": {
            "min": min(takeovers, default=None),
            "median": statistics.median(takeovers) if takeovers else None,
            "max": max(takeovers, default=None),
        },
        "succeeded": f"{len(takeovers)}/{len(samples)}",
    }


def test_messages_during_gap(lab, out):
    """What happens to work addressed to @role while the holder is dead?"""
    a, b = lab.spawn("a"), lab.spawn("b")
    try:
        a.op("assume_role", role=ROLE)
        time.sleep(1)
        a.stop(signal.SIGKILL)
        kill_time = time.time()
        into_lease = f"ticket-into-dead-lease-{uuid.uuid4().hex[:6]}"
        _, sent1 = lab.referee.op("send_targeted_message", body=into_lease, target_roles=[ROLE])
        assert lab.wait_free()
        freed = time.time() - kill_time
        into_gap = f"ticket-into-empty-seat-{uuid.uuid4().hex[:6]}"
        _, sent2 = lab.referee.op("send_targeted_message", body=into_gap, target_roles=[ROLE])
        status, _ = b.op("assume_role", role=ROLE)
        after = f"ticket-after-takeover-{uuid.uuid4().hex[:6]}"
        _, sent3 = lab.referee.op("send_targeted_message", body=after, target_roles=[ROLE])
        time.sleep(2)
        _, history = b.op("read_context", limit=50)
        text = json.dumps(result(history))
        report = lambda marker: {
            "seen_on_standby_stream": bool(b.messages_containing(marker)),
            "addressed_flag": [
                e["data"]["payload"].get("addressed") for e in b.messages_containing(marker)
            ],
            "in_read_context": marker in text,
        }
        out["messages_during_gap"] = {
            "seconds_until_free": round(freed, 2),
            "assume_status": status,
            "sent_to_dead_holder_statuses": result(sent1).get("target_statuses"),
            "sent_to_empty_seat_statuses": result(sent2).get("target_statuses"),
            "sent_after_takeover_statuses": result(sent3).get("target_statuses"),
            "sent_to_dead_holder": report(into_lease),
            "sent_to_empty_seat": report(into_gap),
            "sent_after_takeover": report(after),
        }
    finally:
        a.close()
        b.close()
    lab.wait_free()


def test_race(lab, out, rounds=20):
    """Reproduce the reported race: two agents assume an empty seat at once."""
    a, b = lab.spawn("a"), lab.spawn("b")
    doubles = both_ok = 0
    try:
        with ThreadPoolExecutor(2) as pool:
            for _ in range(rounds):
                first = pool.submit(a.op, "assume_role", role=ROLE)
                second = pool.submit(b.op, "assume_role", role=ROLE)
                statuses = first.result()[0], second.result()[0]
                both_ok += statuses == (200, 200)
                doubles += len(lab.holders()) > 1
                a.op("release_role")
                b.op("release_role")
        out["race"] = {
            "rounds": rounds,
            "both_got_200": both_ok,
            "rounds_with_two_holders": doubles,
        }
    finally:
        a.close()
        b.close()
    lab.wait_free()


def test_single_promotion(lab, out, rounds=20):
    """Bench's design: the Referee promotes exactly one agent at a time."""
    a, b = lab.spawn("a"), lab.spawn("b")
    doubles = failures = 0
    try:
        holder, next_up = a, b
        holder.op("assume_role", role=ROLE)
        for _ in range(rounds):
            holder.op("release_role")
            failures += next_up.op("assume_role", role=ROLE)[0] != 200
            doubles += len(lab.holders()) != 1
            holder, next_up = next_up, holder
        out["single_promotion"] = {
            "rounds": rounds,
            "failed_assumes": failures,
            "rounds_without_exactly_one_holder": doubles,
        }
    finally:
        a.close()
        b.close()
    lab.wait_free()


def test_person_mentions_role(lab, out):
    """Does a person typing @fixer in Mattermost reach whoever holds the role?"""
    a = lab.spawn("a")
    try:
        a.op("assume_role", role=ROLE)
        login = httpx.post(
            f"{MATTERMOST}/api/v4/users/login",
            json={"login_id": ENV["MATTERMOST_USER"], "password": ENV["MATTERMOST_USER_PASSWORD"]},
            timeout=15,
        )
        login.raise_for_status()
        headers = {"Authorization": f"Bearer {login.headers['Token']}"}
        team = httpx.get(
            f"{MATTERMOST}/api/v4/teams/name/{ENV['MATTERMOST_TEAM_NAME']}", headers=headers, timeout=15
        ).json()
        channels = httpx.get(
            f"{MATTERMOST}/api/v4/users/me/teams/{team['id']}/channels", headers=headers, timeout=15
        ).json()
        channel = next(c for c in channels if f"failover-{RUN}" in (c.get("display_name") or c["name"]).lower())
        marker = f"person-{uuid.uuid4().hex[:6]}"
        post = httpx.post(
            f"{MATTERMOST}/api/v4/posts",
            headers=headers,
            json={"channel_id": channel["id"], "message": f"@{ROLE} please look at {marker}"},
            timeout=15,
        )
        post.raise_for_status()
        time.sleep(4)
        seen = a.messages_containing(marker)
        out["person_mentions_role"] = {
            "reached_holder": bool(seen),
            "addressed_flag": [e["data"]["payload"].get("addressed") for e in seen],
            "sample_payload": seen[0]["data"]["payload"] if seen else None,
        }
    finally:
        a.close()
    lab.wait_free()


def main():
    out = {"run": RUN, "switch_version": ENV.get("SWITCH_VERSION")}
    path = HERE / "results.json"
    lab = Lab()
    out["room"] = {"room_id": lab.room_id}
    tests = [
        test_cannot_move_role,
        test_graceful_handoff,
        test_failure_timing,
        test_messages_during_gap,
        test_race,
        test_single_promotion,
        test_person_mentions_role,
    ]
    try:
        for test in tests:
            print(f"running {test.__name__}", flush=True)
            try:
                test(lab, out)
            except Exception as error:
                out.setdefault("errors", {})[test.__name__] = repr(error)
                print(f"  ERROR: {error!r}", flush=True)
            path.write_text(json.dumps(out, indent=2))
    finally:
        lab.referee.close()
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
