"""Turns a chat message into a ticket, and the Referee's replies into text.

Pure functions, no I/O. A ticket is a message whose text, after any leading
@mentions, starts with the word "ticket":

    @bench-referee ticket: Invoice totals round the wrong way
    Totals of 0.005 round down. Seen in payments/invoice.py.

The first line is the title and the rest is the description.
"""

import hashlib
import re
from dataclasses import dataclass

LEADING_MENTIONS = re.compile(r"^(?:\s*@[\w.-]+[,:]?)+\s*")
TICKET_PREFIX = re.compile(r"^ticket\b[ \t]*[:\-–]?[ \t]*", re.IGNORECASE)


@dataclass(frozen=True)
class Ticket:
    id: str
    title: str
    description: str
    reporter: str
    message_id: str


def ticket_id(message_id):
    """A short ID that is the same every time for the same message."""
    return "T-" + hashlib.sha1(message_id.encode()).hexdigest()[:6]


def parse_ticket(body, reporter, message_id):
    """Returns a Ticket, or None if the message isn't one."""
    text = LEADING_MENTIONS.sub("", body or "", count=1).strip()
    match = TICKET_PREFIX.match(text)
    if not match:
        return None
    title, _, description = text[match.end():].partition("\n")
    title = title.strip()
    if not title:
        return None
    return Ticket(ticket_id(message_id), title, description.strip(), reporter, message_id)


def acknowledgement(ticket):
    lines = [f"Logged {ticket.id}: {ticket.title}"]
    if ticket.description:
        lines.append(f"Details: {len(ticket.description.splitlines())} line(s) received.")
    return "\n".join(lines)


def usage(referee_name):
    return (
        "I take tickets. Send one like this:\n"
        f"@{referee_name} ticket: Invoice totals round the wrong way\n"
        "Put any details on the lines after the title."
    )
