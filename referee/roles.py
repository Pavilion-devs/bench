"""Picks and role moves: parsing a person's pick, and what the Referee says. No I/O.

A pick looks like this, in the main room:

    @bench-referee pick bench-claude
    @bench-referee pick bench-codex for T-74e40f anyway

The Referee can't put a role on another agent; only an agent can take a role,
for itself. So a move is a request to the old holder to release, then a request
to the winner to assume, each checked against the room before going on.

The Referee never writes "@" before the role's name: Switch reads that as a
mention of the role and wakes whoever holds it.
"""

import re
from dataclasses import dataclass

from .tickets import LEADING_MENTIONS

PICK = re.compile(
    r"^pick\s+@?(?P<name>[\w-]+(?:\.[\w-]+)*)(?:\s+for\s+(?P<ticket>T-[0-9a-f]{6}))?(?P<anyway>\s+anyway)?\s*[.!]?\s*$",
    re.IGNORECASE,
)
WHO = re.compile(r"^(who|who'?s|whos)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Pick:
    candidate: str
    ticket_id: str | None
    anyway: bool


def parse_pick(body):
    text = LEADING_MENTIONS.sub("", body or "", count=1).strip()
    match = PICK.match(text)
    if not match:
        return None
    return Pick(match["name"], match["ticket"], bool(match["anyway"]))


def asks_who(body):
    return bool(WHO.match(LEADING_MENTIONS.sub("", body or "", count=1).strip()))


def holders(room_detail, role):
    for entry in room_detail.get("roles", []):
        if entry.get("name") == role:
            return [h["name"] if isinstance(h, dict) else h for h in entry.get("held_by") or []]
    return []


def how_to_pick(referee_name, role, ranked):
    if not ranked:
        return ""
    return f"To give the {role} role to one of them, reply: @{referee_name} pick {ranked[0].candidate}"


# Switch puts the target's @name in front of a targeted message, so these don't.
def release_request(holder, role, room_name, room_id):
    return (
        f"Please give up the {role} role in room {room_name}. "
        "Call the Switch tool release_role. Don't do anything else.\n"
        f"Switch call: connect_to_room(room_id={room_id}) then release_role()"
    )


def assume_request(winner, role, room_name, room_id, ticket):
    # An agent holds one role lease across all rooms, so the winner drops any it
    # holds elsewhere first. release_role is harmless when it holds none.
    return (
        f"You won the tryout for {ticket.id}, so you get the {role} role in room {room_name}. "
        "Join that room if you need to. Call the Switch tool release_role first, to drop any role you "
        f"hold in another room, then assume_role with role \"{role}\". Don't do anything else.\n"
        f'Switch call: connect_to_room(room_id={room_id}) then release_role() then assume_role(role="{role}")'
    )


def moving(winner, role, holder):
    was = f" from {holder}" if holder else ""
    return f"Moving the {role} role{was} to {winner}. I'll confirm once Switch shows the change."


def moved(winner, role, holder, seconds):
    was = f" (was {holder})" if holder else ""
    return f"{winner} now holds the {role} role{was}. Took {seconds:.0f}s."


def already(winner, role):
    return f"{winner} already holds the {role} role."


def did_not_release(holder, role, minutes):
    return (
        f"{holder} didn't give up the {role} role within {minutes:g} min, so it still holds it and nothing changed. "
        "If it's down, its role stays held until it comes back."
    )


def did_not_assume(winner, role, minutes):
    return (
        f"{winner} didn't take the {role} role within {minutes:g} min. Nobody holds it now. Pick again to retry."
    )


def refused(reason):
    return f"Can't do that pick: {reason}"


def who(role, current):
    if not current:
        return f"Nobody holds the {role} role right now."
    return f"The {role} role is held by {', '.join(current)}."


DEAD_STATUSES = {"disconnected", "offline"}
# Posts made under an agent's name that aren't the agent answering: Console's session
# notices, and Switch's role notices.
NOTICE = re.compile(
    r"^\s*(Starting a session\b|Worked for \d|(assumed|released) the `?[\w-]+`? role\b)", re.IGNORECASE
)


def is_notice(body):
    return bool(NOTICE.match(body or ""))


def mentions_role(body, role):
    return bool(re.search(rf"(^|[\s(])@{re.escape(role)}\b", body or ""))


def failover_started(holder, role, runner_up, reason):
    who = holder or "Nobody"
    return f"Failover: {who} {reason}. Moving the {role} role to the runner-up, {runner_up}."


def failover_done(runner_up, role, holder, seconds, resent):
    was = f" (was {holder})" if holder else ""
    sent = f" Re-sent {resent} waiting message(s) to it." if resent else ""
    return f"Failover done: {runner_up} now holds the {role} role{was}. Took {seconds:.0f}s.{sent}"


def forwarded(holder, role, runner_up, count):
    return (
        f"{holder} still holds the {role} role and isn't answering, so I can't move it. "
        f"I sent its {count} waiting message(s) to {runner_up} directly. The role stays with {holder} "
        "until it comes back and releases it."
    )


def no_runner_up(holder, role, reason):
    return f"{holder or 'Nobody'} {reason}, and there's no runner-up on the latest scorecard to give the {role} role to."


def resend(sender, body):
    # No "@role" here: Switch adds the mention itself when the message targets the role.
    return f"Re-sent, the last holder didn't answer. {sender} asked: {body}"
