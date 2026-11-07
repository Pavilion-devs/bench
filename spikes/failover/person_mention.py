"""The demo path: a person types @fixer in Mattermost while the role changes hands.

Run: uv run --no-project --with httpx python spikes/failover/person_mention.py
Writes person_mention_results.json next to this file.
"""

import json
import signal
import time
import uuid

import httpx

from probe import BASE, ENV, HERE, MATTERMOST, ROLE, RUN, Lab


class Person:
    """The Mattermost user, posting in the Bench room's real channel."""

    def __init__(self):
        login = httpx.post(
            f"{MATTERMOST}/api/v4/users/login",
            json={"login_id": ENV["MATTERMOST_USER"], "password": ENV["MATTERMOST_USER_PASSWORD"]},
            timeout=15,
        )
        login.raise_for_status()
        self.headers = {"Authorization": f"Bearer {login.headers['Token']}"}
        team = httpx.get(
            f"{MATTERMOST}/api/v4/teams/name/{ENV['MATTERMOST_TEAM_NAME']}",
            headers=self.headers,
            timeout=15,
        ).json()
        channels = httpx.get(
            f"{MATTERMOST}/api/v4/users/me/teams/{team['id']}/channels",
            headers=self.headers,
            timeout=15,
        ).json()
        self.channel = next(
            c["id"] for c in channels if f"failover-{RUN}" in (c.get("display_name") or c["name"]).lower()
        )

    def mention_role(self):
        marker = f"person-{uuid.uuid4().hex[:6]}"
        response = httpx.post(
            f"{MATTERMOST}/api/v4/posts",
            headers=self.headers,
            json={"channel_id": self.channel, "message": f"@{ROLE} please look at {marker}"},
            timeout=15,
        )
        response.raise_for_status()
        time.sleep(3)
        return marker


def open_addressing(lab):
    """Lets any room member address the test agents, as a Bench template would."""
    client = httpx.Client(base_url=BASE, timeout=15)
    login = client.post(
        "/gateway/auth/login",
        json={"email": ENV["GATEWAY_ADMIN_EMAIL"], "password": ENV["GATEWAY_ADMIN_PASSWORD"]},
    )
    login.raise_for_status()
    token = login.json().get("access_token")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    statuses = {}
    for name in ("a", "b"):
        response = client.put(
            f"/gateway/agents/{lab.creds[name]['id']}/addressing-policy",
            json={"policy": None},
            headers=headers,
        )
        statuses[name] = response.status_code
        response.raise_for_status()
    return statuses


def flags(agents, marker):
    return {
        agent.name: [e["data"]["payload"].get("addressed") for e in agent.messages_containing(marker)]
        for agent in agents
    }


def main():
    lab = Lab()
    person = Person()
    out = {"run": RUN}
    a, b = lab.spawn("a"), lab.spawn("b")
    try:
        a.op("assume_role", role=ROLE)
        out["1_default_policy"] = {
            "holder": lab.holders(),
            "addressed": flags([a, b], person.mention_role()),
        }

        out["2_policy_set_open"] = open_addressing(lab)
        out["3_open_policy"] = {
            "holder": lab.holders(),
            "addressed": flags([a, b], person.mention_role()),
        }

        a.op("release_role")
        assert b.op("assume_role", role=ROLE)[0] == 200
        out["4_after_handoff_to_b"] = {
            "holder": lab.holders(),
            "addressed": flags([a, b], person.mention_role()),
        }

        b.stop(signal.SIGKILL)
        start = time.time()
        while a.op("assume_role", role=ROLE)[0] != 200:
            if time.time() - start > 30:
                raise TimeoutError("a never took over")
            time.sleep(0.25)
        out["5_after_kill_failover_to_a"] = {
            "takeover_s": round(time.time() - start, 2),
            "holder": lab.holders(),
            "addressed": flags([a], person.mention_role()),
        }
    finally:
        a.close()
        b.close()
        lab.referee.close()
    (HERE / "person_mention_results.json").write_text(json.dumps(out, indent=2))
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
