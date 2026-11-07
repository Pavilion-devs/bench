"""Generates docs/architecture/bench-architecture.excalidraw (hand-drawn style, Excalifont).

    python3 docs/architecture/make_diagram.py
"""
import json
import random
from pathlib import Path

random.seed(7)
FONT = 5  # Excalifont
INK = "#1e1e1e"
MUTED = "#5c5c5c"
ACCENT = "#c55838"
GREEN = "#17392b"
els = []


def _id():
    return "".join(random.choice("abcdefghijklmnopqrstuvwxyz0123456789") for _ in range(16))


def base(kind, x, y, w, h, **kw):
    e = {
        "id": _id(), "type": kind, "x": x, "y": y, "width": w, "height": h, "angle": 0,
        "strokeColor": kw.get("stroke", INK), "backgroundColor": kw.get("bg", "transparent"),
        "fillStyle": "solid", "strokeWidth": kw.get("sw", 1), "strokeStyle": kw.get("style", "solid"),
        "roughness": 1, "opacity": 100, "groupIds": [], "frameId": None, "roundness": None,
        "seed": random.randint(1, 2**31), "version": 1, "versionNonce": random.randint(1, 2**31),
        "isDeleted": False, "boundElements": [], "updated": 1, "link": None, "locked": False,
    }
    return e


def text_size(text, size):
    lines = text.split("\n")
    w = max(len(l) for l in lines) * size * 0.52
    h = len(lines) * size * 1.25
    return w, h


def text(x, y, s, size=16, align="left", color=INK, container=None, w=None):
    tw, th = text_size(s, size)
    if w is None:
        # Free text is drawn into a box of this width, so be generous (Excalifont runs wide).
        caps = s == s.upper() and any(c.isalpha() for c in s)
        w = max(len(l) for l in s.split("\n")) * size * (0.78 if caps else 0.62)
    e = base("text", x, y, w, th, stroke=color)
    e.update({
        "text": s, "originalText": s, "fontSize": size, "fontFamily": FONT, "textAlign": align,
        "verticalAlign": "middle" if container else "top", "containerId": container,
        "lineHeight": 1.25, "autoResize": True,
    })
    els.append(e)
    return e


def box(x, y, w, h, label=None, size=16, style="solid", bg="transparent", stroke=INK, sw=1, color=INK):
    r = base("rectangle", x, y, w, h, style=style, bg=bg, stroke=stroke, sw=sw)
    els.append(r)
    if label:
        tw, th = text_size(label, size)
        t = text(x + (w - tw) / 2, y + (h - th) / 2, label, size, "center", color, container=r["id"], w=tw)
        r["boundElements"].append({"type": "text", "id": t["id"]})
    return r


def arrow(x1, y1, x2, y2, style="solid", label=None, lx=None, ly=None, size=14, color=INK):
    a = base("arrow", x1, y1, abs(x2 - x1), abs(y2 - y1), style=style, stroke=color)
    a.update({
        "points": [[0, 0], [x2 - x1, y2 - y1]], "lastCommittedPoint": None,
        "startBinding": None, "endBinding": None, "startArrowhead": None, "endArrowhead": "arrow",
        "elbowed": False,
    })
    els.append(a)
    if label:
        text(lx if lx is not None else (x1 + x2) / 2 + 10, ly if ly is not None else (y1 + y2) / 2 - 10, label, size, color=MUTED)
    return a


BLUE = "#1f5f99"

# ------------------------------------------------------------------ title
text(60, 50, "BENCH\nARCHITECTURE", 40)
text(60, 160, "agents try out, people pick, the job survives", 18, color=MUTED)

# ------------------------------------------------------------------ inputs
box(520, 30, 1040, 210)
text(1010, 42, "Inputs", 16)
box(545, 75, 320, 140, "Ticket · posted in team chat\n@bench-referee ticket: ...\nor: try more-itertools-...\nfiled by a person")
box(880, 75, 320, 140, "Real fixes from public repos\nhumanize · more-itertools · boltons\ncode from just before the fix\none commit, no history")
box(1215, 75, 320, 140, "Candidates\nClaude Code · Codex\nreal agents in Switch Console\nor replay agents (no keys)")
arrow(1040, 240, 1040, 300, label="ticket + fixture · per ticket", lx=1055, ly=258)

# ------------------------------------------------------------------ switch
box(60, 300, 1500, 330, stroke=BLUE, sw=2)
text(300, 312, "Switch · rooms, roles, documents · the only way people and agents reach each other", 16, color=BLUE)
sy = 355
box(90, sy, 330, 140, "#bench-team\npeople + Referee + candidates\nexclusive room role: fixer\none lease per agent")
box(455, sy, 330, 140, "Sealed tryout rooms\none per candidate, made at runtime\nmembers: Referee + that agent\nanyone else reading: 403")
box(820, sy, 330, 140, "Room documents\nScorecard per ticket\nBench standings\nupdated in place")
box(1185, sy, 345, 140, "Presence + role leases\nlive · disconnected\nheld_by on the role\naddressing policy")
box(90, 515, 690, 90, "Agent protocol: register · SSE event stream · heartbeat every 2s\nops carry the connection id · targeted messages to names or roles", style="dashed", size=15)
box(820, 515, 710, 90, "Mattermost bridge: people chat in Console as usual\nthey tag @fixer, never an agent's name", style="dashed", size=15)

box(1620, 345, 280, 150, "Switch Console\nlocal server\nClaude Code + Codex agents\nno extra API keys")
arrow(1620, 420, 1565, 420, style="dashed")

arrow(810, 630, 810, 690, label="briefs · commits · done · picks · role events", lx=825, ly=648)

# ------------------------------------------------------------------ referee
box(60, 690, 1500, 330, stroke=GREEN, sw=2)
text(300, 702, "Referee · custom Switch agent in Python · deterministic: no model decides a score", 16, color=GREEN)
ry = 745
r1 = box(90, ry, 330, 140, "Tryout\nbriefs each candidate alone\nown git workspace each\ntime limit, then collects")
r2 = box(455, ry, 330, 140, "Score\nhidden tests = the fix's own tests\nonly the commit counts\nskips count as failures")
r3 = box(820, ry, 330, 140, "Rank\n1. hidden tests passed\n2. size, 5-line tie band\n3. time")
r4 = box(1185, ry, 345, 140, "Move the role\nperson: pick <name>\nold holder releases\nwinner assumes · checked")
arrow(420, ry + 70, 455, ry + 70)
arrow(785, ry + 70, 820, ry + 70)
arrow(1150, ry + 70, 1185, ry + 70)
box(90, 905, 690, 90, "Watchdog every 2s: holder disconnected · seat emptied\nor no answer in time → runner-up takes the role", style="dashed", size=15)
box(820, 905, 710, 90, "failover: release · assume the runner-up · re-send missed messages\nstuck holder? forward the work by name instead", style="dashed", size=15, color=ACCENT, stroke=ACCENT)

box(1620, 735, 280, 160, "Docker sandbox\nbench-scorer:1 · pytest\nno network · read-only code\n512 MB · 1 CPU · 120 s", size=15)
arrow(1620, 815, 1535, 815, style="dashed")
text(1630, 905, "tamper signs flagged\nCHECK BY HAND", 13, color=MUTED)

# ------------------------------------------------------------------ decision
arrow(810, 1020, 810, 1080)
box(560, 1080, 500, 80, "tryout   ·   pick   ·   failover", size=30)
box(90, 1080, 420, 80, "Tests: 86 · unit + live on a real Switch server\nreal ticket on Console: Claude 608/608", style="dashed", size=14)
box(1110, 1080, 450, 80, "On Console: pick to new holder in 12s\nstop the holder → runner-up in 6s", style="dashed", size=14)

# ------------------------------------------------------------------ outputs
oy = 1260
arrow(700, 1160, 330, oy, label="every ticket", lx=420, ly=1190)
arrow(810, 1160, 810, oy, label="every pick", lx=825, ly=1200)
arrow(920, 1160, 1290, oy, label="every crash", lx=1100, ly=1190)
box(90, oy, 480, 120, "Scorecard in the ticket's thread\nranked · how to pick · sandboxed or not\nstandings document for the team")
box(600, oy, 420, 120, "@fixer in #bench-team\npeople tag the role\nwhoever holds it answers")
box(1050, oy, 510, 120, "Failover notice\nwho dropped, who took over, how long\nre-sent N waiting messages")
box(1620, oy + 10, 280, 100, "The team\ntags @fixer · picks in chat\nnever changes how it works")
arrow(1620, oy + 60, 1560, oy + 60)

# ------------------------------------------------------------------ tagline
text(90, 1440, "AGENTS TRY OUT\nHIDDEN TESTS KEEP SCORE\nPEOPLE PICK\nTHE JOB SURVIVES A CRASH", 34)
text(1050, 1450, "ticket → sealed tryout → hidden tests → scorecard → pick → @fixer → failover\nSwitch · Claude Code · Codex · Docker\nuv run python -m replay", 15, color=MUTED)

scene = {
    "type": "excalidraw", "version": 2, "source": "https://excalidraw.com",
    "elements": els, "appState": {"viewBackgroundColor": "#ffffff", "gridSize": None}, "files": {},
}
out = Path(__file__).with_name("bench-architecture.excalidraw")
out.write_text(json.dumps(scene, ensure_ascii=False, indent=1))
print(f"{len(els)} elements -> {out}")
