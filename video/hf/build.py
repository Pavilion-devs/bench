"""Builds Bench's demo film as one HyperFrames composition (ported from the Prime film's scripts).

Illustrated scenes come from the Excalidraw export (docs/architecture): a title, the architecture
built up box by box, three flashes, an end card. Footage is the Console window capture
(.bench/recording/raw.mp4). Narration: Kokoro am_michael (video/vo/*.wav). Subtitles on top.

    python3 video/hf/build.py && cd video/hf && npx --yes hyperframes@0.8.206 render
"""
import base64
import json
import re
import subprocess
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parent.parent
SVG = (ROOT / "docs/architecture/bench-architecture.svg").read_text()
ELS = json.loads((ROOT / "docs/architecture/bench-architecture.excalidraw").read_text())["elements"]
RAW = ROOT / ".bench/recording/raw.mp4"
LINES = json.loads((HERE.parent / "narration.json").read_text())

vb = re.search(r'viewBox="0 0 (\d+) (\d+)"', SVG)
W, H = int(vb.group(1)), int(vb.group(2))
DX, DY = -50, -20  # scene coords -> SVG coords (export padding)
FW, FH = 1920, 1080
INK, PAPER, GREY = "#1e1e1e", "#ffffff", "#6b6b6b"
BLUE, GREEN, ORANGE, RED = "#1971c2", "#2f9e44", "#e8590c", "#e03131"

(HERE / "assets/fonts").mkdir(parents=True, exist_ok=True)
b64 = re.search(r"font/woff2;base64,([^)]+)\)", SVG).group(1)
(HERE / "assets/fonts/excalifont.woff2").write_bytes(base64.b64decode(b64))
(HERE / "assets/architecture.svg").write_text(SVG)
FONT_FACE = """@font-face { font-family: "Excalifont"; src: url("assets/fonts/excalifont.woff2") format("woff2"); }"""

body = SVG[SVG.index("</defs>") + 7: SVG.rindex("</svg>")]
body = re.sub(r'^<rect x="0" y="0" width="\d+" height="\d+" fill="#ffffff"></rect>', "", body)
parts, depth, start = [], 0, None
for m in re.finditer(r"<(/?)g\b[^>]*>", body):
    if m.group(1):
        depth -= 1
        if depth == 0:
            parts.append(body[start:m.end()])
    else:
        if depth == 0:
            start = m.start()
        depth += 1
assert len(parts) == len(ELS), (len(parts), len(ELS))
INLINE = "".join(p.replace("<g ", f'<g class="el e{i}" ', 1) for i, p in enumerate(parts))


def vo_len(name):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                        str(HERE / f"assets/vo/{name}.wav")], capture_output=True, text=True, check=True)
    return float(r.stdout)


def bbox(i, pad=8):
    e = ELS[i]
    return (e["x"] + DX - pad, e["y"] + DY - pad, e["width"] + 2 * pad, e["height"] + 2 * pad)


TOP = 140


def cam(x0, y0, x1, y1, fill=0.92):
    fh = FH - TOP - 150  # keep the subtitle band clear
    s = min(FW * fill / (x1 - x0), fh * fill / (y1 - y0))
    return {"x": round(FW / 2 - (x0 + x1) / 2 * s, 1), "y": round(TOP + fh / 2 - (y0 + y1) / 2 * s, 1), "scale": round(s, 4)}


def scam(x0, y0, x1, y1, fill=0.92):
    """Camera on scene coordinates."""
    return cam(x0 + DX, y0 + DY, x1 + DX, y1 + DY, fill)


FULL = cam(0, 0, W, H, fill=0.98)


def sel(*ranges):
    ids = []
    for r in ranges:
        ids += list(range(r[0], r[1] + 1)) if isinstance(r, tuple) else [r]
    return ", ".join(f"#b-svg .e{i}" for i in ids)


def scene(cid, dur, css, html, js):
    return f"""<template>
  <style>
    {FONT_FACE}
    #root {{ position: absolute; inset: 0; background: {PAPER}; overflow: hidden; font-family: "Excalifont", sans-serif; color: {INK}; }}
{css}
  </style>
  <div id="root" data-composition-id="{cid}" data-width="{FW}" data-height="{FH}" data-duration="{dur}">
{html}
  </div>
  <script>
    const tl = gsap.timeline({{ paused: true }});
{js}
    window.__timelines["{cid}"] = tl;
  </script>
</template>
"""


# ---------------------------------------------------------------- title
TITLE_DUR = round(vo_len("s1") + 1.0, 2)
title = scene("title", TITLE_DUR, f"""
    #t-wrap {{ position: absolute; inset: 0; transform-origin: 50% 45%; }}
    #t-name {{ position: absolute; left: 0; right: 0; top: 300px; text-align: center; font-size: 240px; line-height: 1; }}
    #t-line {{ position: absolute; left: 620px; top: 560px; width: 680px; height: 34px; overflow: visible; }}
    #t-sub {{ position: absolute; left: 0; right: 0; top: 630px; text-align: center; font-size: 50px; }}
    #t-foot {{ position: absolute; left: 0; right: 0; top: 760px; text-align: center; font-size: 32px; color: {GREY}; }}
""", f"""
    <div id="t-wrap" class="clip" data-start="0" data-duration="{TITLE_DUR}">
      <div id="t-name">BENCH</div>
      <svg id="t-line" viewBox="0 0 680 34"><path id="t-stroke" d="M6 20 C 140 8, 340 30, 674 12" fill="none" stroke="{ORANGE}" stroke-width="8" stroke-linecap="round"/></svg>
      <div id="t-sub">agents try out · people pick · the job survives a crash</div>
      <div id="t-foot">Switch · Claude Code · Codex · Docker</div>
    </div>
""", f"""
    tl.fromTo("#t-name", {{ opacity: 0, filter: "blur(12px)", y: 30 }}, {{ opacity: 1, filter: "blur(0px)", y: 0, duration: 0.6, ease: "expo.out" }}, 0.2);
    tl.fromTo("#t-stroke", {{ strokeDasharray: 700, strokeDashoffset: 700 }}, {{ strokeDashoffset: 0, duration: 0.6, ease: "power3.out" }}, 0.7);
    tl.fromTo("#t-sub", {{ opacity: 0, filter: "blur(6px)" }}, {{ opacity: 1, filter: "blur(0px)", duration: 0.4, ease: "power4.out" }}, 1.0);
    tl.fromTo("#t-foot", {{ opacity: 0 }}, {{ opacity: 1, duration: 0.4, ease: "power2.out" }}, 1.5);
    tl.fromTo("#t-wrap", {{ scale: 1 }}, {{ scale: 1.06, duration: {TITLE_DUR}, ease: "none" }}, 0);
    tl.to("#t-name, #t-line, #t-sub, #t-foot", {{ opacity: 0, filter: "blur(8px)", duration: 0.25, ease: "power3.in", stagger: 0.04 }}, {TITLE_DUR - 0.45});
""")

# ---------------------------------------------------------------- build-up
L = {k: vo_len(k) for k in ("s2a", "s2b", "s2c", "s2d")}
B1 = round(L["s2a"] + 0.9, 2)
B2 = round(B1 + L["s2b"] + 0.9, 2)
B3 = round(B2 + L["s2c"] + 0.9, 2)
B4 = round(B3 + L["s2d"] + 0.9, 2)
BUILD_DUR = round(B4 + 3.0, 2)
VO_BEATS = {"s2a": 0.5, "s2b": B1 + 0.4, "s2c": B2 + 0.4, "s2d": B3 + 0.4}
BEATS = [
    (0.3, scam(40, 20, 1580, 300, 0.97), "Inputs · what goes in", INK, [(0, 1), 2, 3, (4, 5), (6, 7), (8, 9), (10, 11)]),
    (B1, scam(40, 290, 1910, 640), "Switch · rooms, roles, documents", BLUE,
     [12, 13, (14, 15), (16, 17), (18, 19), (20, 21), (22, 23), (24, 25), (26, 27), 28, (29, 30)]),
    (B2, scam(40, 680, 1910, 1030), "Referee · deterministic, no model decides", GREEN,
     [31, 32, (33, 34), 41, (35, 36), 42, (37, 38), 43, (39, 40), (44, 45), (46, 47), (48, 49), 50, 51]),
    (B3, scam(40, 1010, 1910, 1390), "Outcomes · tryout · pick · failover", ORANGE,
     [52, (53, 54), (55, 56), (57, 58), 59, 60, 61, 62, 63, 64, (65, 66), (67, 68), (69, 70), (71, 72), 73]),
    (B4, FULL, "", INK, [(74, 75)]),
]
b_js = [f'    tl.set("#b-cam", {json.dumps(BEATS[0][1])}, 0);']
chips = []
HALVES = {1: (40, 290, 1000, 640, 900, 1910), 2: (40, 680, 1000, 1030, 900, 1910), 3: (40, 1010, 1000, 1390, 900, 1910)}
for n, (t, c, label, colour, groups) in enumerate(BEATS):
    if n in HALVES:
        x0, y0, x1, y1, x2, x3 = HALVES[n]
        nxt = BEATS[n + 1][0]
        left, right = scam(x0, y0, x1, y1, 0.95), scam(x2, y0, x3, y1, 0.95)
        b_js.append(f'    tl.to("#b-cam", {{ ...{json.dumps(left)}, duration: 1.1, ease: "power2.inOut" }}, {t});')
        b_js.append(f'    tl.to("#b-cam", {{ ...{json.dumps(right)}, duration: 2.2, ease: "power2.inOut" }}, {round(t + (nxt - t) * 0.5, 2)});')
    elif n:
        b_js.append(f'    tl.to("#b-cam", {{ ...{json.dumps(c)}, duration: 1.1, ease: "power2.inOut" }}, {t});')
    reveal_at = t + (0.9 if n else 0)
    step = min(0.3, max(0.12, (3.5 if n < 4 else 1) / max(1, len(groups))))
    for k, g in enumerate(groups):
        r = g if isinstance(g, tuple) else (g, g)
        b_js.append(f'    tl.fromTo("{sel(r)}", {{ opacity: 0, filter: "blur(6px)" }}, '
                    f'{{ opacity: 1, filter: "blur(0px)", duration: 0.35, ease: "expo.out" }}, {round(reveal_at + k * step, 2)});')
    if label:
        end = BEATS[n + 1][0] if n + 1 < len(BEATS) else BUILD_DUR
        chips.append(f'    <div id="b-chip{n}" class="b-chip clip" data-start="{t}" data-duration="{round(end - t, 2)}" '
                     f'style="border-color:{colour};color:{colour}">{label}</div>')
        b_js.append(f'    tl.fromTo("#b-chip{n}", {{ opacity: 0, filter: "blur(6px)" }}, {{ opacity: 1, filter: "blur(0px)", duration: 0.3, ease: "power4.out" }}, {t + 0.2});')

build = scene("build", BUILD_DUR, f"""
    #b-cam {{ position: absolute; left: 0; top: 0; width: {W}px; height: {H}px; transform-origin: 0 0; }}
    #b-cam svg {{ width: {W}px; height: {H}px; display: block; }}
    #b-band {{ position: absolute; left: 0; top: 0; width: {FW}px; height: {TOP + 20}px; z-index: 4;
              background: linear-gradient(to bottom, {PAPER} 72%, rgba(255,255,255,0)); }}
    .b-chip {{ position: absolute; left: 96px; top: 52px; padding: 10px 22px; border: 3px solid; border-radius: 14px;
              background: {PAPER}; font-size: 32px; z-index: 5; }}
""", f"""
    <div id="b-frame" class="clip" data-start="0" data-duration="{BUILD_DUR}" data-hold data-layout-allow-overflow><div id="b-cam">
      <svg id="b-svg" viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg">{INLINE}</svg>
    </div></div>
    <div id="b-band" class="clip" data-start="0" data-duration="{BUILD_DUR}" data-layout-ignore data-layout-allow-occlusion></div>
""" + "\n".join(chips), "\n".join(b_js))


# ---------------------------------------------------------------- flashes
def flash(cid, label, colour, ids):
    holes = [bbox(i) for i in ids]
    mask = "".join(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="black"/>' for x, y, w, h in holes)
    rings = "".join(f'<rect class="{cid}-ring" x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="none" '
                    f'stroke="{colour}" stroke-width="5" pathLength="100"/>' for x, y, w, h in holes)
    return scene(cid, 3, f"""
    #{cid}-stage {{ position: absolute; left: 0; top: 0; width: {W}px; height: {H}px; transform-origin: 0 0; }}
    #{cid}-stage img, #{cid}-stage svg {{ position: absolute; inset: 0; width: {W}px; height: {H}px; }}
    #{cid}-chip {{ position: absolute; left: 96px; top: 64px; padding: 10px 22px; border: 3px solid {colour}; color: {colour};
                  border-radius: 14px; background: {PAPER}; font-size: 38px; }}
""", f"""
    <div id="{cid}-frame" class="clip" data-start="0" data-duration="3" data-layout-allow-overflow><div id="{cid}-stage">
      <div id="{cid}-move" style="position:absolute;inset:0">
        <img id="{cid}-img" src="assets/architecture.svg" alt="Bench architecture" />
        <svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg">
          <defs><mask id="{cid}-mask"><rect width="{W}" height="{H}" fill="white"/>{mask}</mask></defs>
          <rect id="{cid}-veil" width="{W}" height="{H}" fill="{PAPER}" mask="url(#{cid}-mask)"/>
          {rings}
        </svg>
      </div>
    </div></div>
    <div id="{cid}-chip" class="clip" data-start="0" data-duration="3">{label}</div>
""", f"""
    tl.set("#{cid}-stage", {json.dumps(FULL)}, 0);
    tl.fromTo("#{cid}-move", {{ opacity: 0, filter: "blur(8px)" }}, {{ opacity: 1, filter: "blur(0px)", duration: 0.25, ease: "power4.out" }}, 0);
    tl.fromTo("#{cid}-veil", {{ opacity: 0 }}, {{ opacity: 0.86, duration: 0.45, ease: "power2.inOut" }}, 0.35);
    tl.fromTo(".{cid}-ring", {{ strokeDasharray: "100 100", strokeDashoffset: 100 }}, {{ strokeDashoffset: 0, duration: 0.5, ease: "power3.out", stagger: 0.05 }}, 0.55);
    tl.fromTo("#{cid}-chip", {{ opacity: 0, filter: "blur(6px)" }}, {{ opacity: 1, filter: "blur(0px)", duration: 0.3, ease: "power4.out" }}, 0.3);
    tl.to("#{cid}-move, #{cid}-chip", {{ opacity: 0, filter: "blur(8px)", duration: 0.2, ease: "power3.in" }}, 2.75);
""")


FLASHES = [
    ("tryout", "1 · Sealed tryout, hidden tests", GREEN, [16, 33, 35, 37, 48, 65]),
    ("pick", "2 · A person picks the winner", BLUE, [14, 39, 67, 71]),
    ("failover", "3 · The holder dies. The runner-up takes over.", RED, [20, 44, 46, 69]),
]

# ---------------------------------------------------------------- end card
END_DUR = round(vo_len("s6") + 2.5, 2)
es = 0.5
end = scene("endcard", END_DUR, f"""
    #end-stage {{ position: absolute; left: 0; top: 0; width: {W}px; height: {H}px; transform-origin: 0 0; }}
    #end-img {{ position: absolute; inset: 0; width: {W}px; height: {H}px; }}
    #end-name {{ position: absolute; left: 1080px; top: 130px; font-size: 150px; line-height: 1; }}
    .end-l {{ position: absolute; left: 1084px; font-size: 42px; }}
    #end-cmd {{ position: absolute; left: 1084px; top: 640px; padding: 14px 24px; border: 3px solid {INK}; border-radius: 14px; font-size: 36px; }}
    #end-url {{ position: absolute; left: 1084px; top: 760px; font-size: 34px; color: {ORANGE}; }}
""", f"""
    <div id="end-frame" class="clip" data-start="0" data-duration="{END_DUR}" data-layout-allow-overflow><div id="end-stage">
      <img id="end-img" src="assets/architecture.svg" alt="Bench architecture" />
    </div></div>
    <div id="end-name" class="clip" data-start="0" data-duration="{END_DUR}">BENCH</div>
    <div id="end-l1" class="end-l clip" style="top:320px" data-start="0" data-duration="{END_DUR}">AGENTS TRY OUT</div>
    <div id="end-l2" class="end-l clip" style="top:375px" data-start="0" data-duration="{END_DUR}">HIDDEN TESTS KEEP SCORE</div>
    <div id="end-l3" class="end-l clip" style="top:430px" data-start="0" data-duration="{END_DUR}">PEOPLE PICK</div>
    <div id="end-l4" class="end-l clip" style="top:485px;color:{ORANGE}" data-start="0" data-duration="{END_DUR}">THE JOB SURVIVES A CRASH</div>
    <div id="end-cmd" class="clip" data-start="0" data-duration="{END_DUR}">uv run python -m replay</div>
    <div id="end-url" class="clip" data-start="0" data-duration="{END_DUR}">github.com/Pavilion-devs/bench</div>
""", f"""
    tl.fromTo("#end-stage", {json.dumps(FULL)}, {{ x: 60, y: {round((FH - 150 - H * es) / 2, 1)}, scale: {es}, duration: 0.9, ease: "power2.inOut" }}, 0);
    tl.fromTo("#end-name", {{ opacity: 0, filter: "blur(10px)" }}, {{ opacity: 1, filter: "blur(0px)", duration: 0.45, ease: "expo.out" }}, 0.7);
    tl.fromTo(".end-l", {{ opacity: 0, filter: "blur(6px)", x: -20 }}, {{ opacity: 1, filter: "blur(0px)", x: 0, duration: 0.3, ease: "power4.out", stagger: 0.25 }}, 1.1);
    tl.fromTo("#end-cmd", {{ opacity: 0, scale: 0.9 }}, {{ opacity: 1, scale: 1, duration: 0.4, ease: "back.out(2)" }}, 2.3);
    tl.fromTo("#end-url", {{ opacity: 0 }}, {{ opacity: 1, duration: 0.4, ease: "power2.out" }}, 2.7);
""")

files = {"title": title, "build": build, "endcard": end}
for cid, label, colour, ids in FLASHES:
    files[cid] = flash(cid, label, colour, ids)
for name, html in files.items():
    (HERE / f"compositions/{name}.html").write_text(html)

# ---------------------------------------------------------------- footage
SHOTS_DIR = HERE / "assets/shots"
# (id, start, length, speed, crop x,y,w,h in the 1920x1216 capture, zoom, focus x, focus y, tag)
S1 = [
    ("f-ticket", 31, 9, 1.5, (340, 230, 1360, 765), 1.25, .5, .75, ""),
    ("f-tryout", 41, 3.2, 1.0, (340, 240, 1580, 889), 1.3, .78, .6, ""),
    ("f-score", 88, 9, 1.0, (300, 180, 1520, 855), 1.35, .42, .62, "⏩ 79 s later"),
]
S2 = [
    ("f-pick", 119, 5.5, 1.0, (340, 130, 1400, 788), 1.3, .3, .45, ""),
    ("f-move", 125, 6.5, 1.0, (1140, 300, 770, 740), 1.15, .5, .6, ""),
]
S3 = [("f-failover", 133, 9.5, 1.0, (1140, 80, 770, 950), 1.2, .5, .55, "")]


def cut(shot, length=None):
    sid, start, ln, speed, (x, y, w, h) = shot[:5]
    ln = length or ln
    out = SHOTS_DIR / f"{sid}.mp4"
    vf = (f"crop={w}:{h}:{x}:{y},scale={FW}:{FH}:flags=lanczos:force_original_aspect_ratio=decrease,"
          f"pad={FW}:{FH}:(ow-iw)/2:(oh-ih)/2:color=0x0f0f10,setpts=PTS/{speed},fps=30,format=yuv420p")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{start}", "-t", f"{ln}", "-i", str(RAW),
                    "-vf", vf, "-c:v", "libx264", "-crf", "16", "-an", str(out)], check=True)
    return round(ln / speed, 3)


hosts, clips, tweens, audio, caps = [], [], [], [], []
t = 0.0


def host(cid, dur):
    global t
    hosts.append(f'      <div id="{cid}" data-composition-id="{cid}" data-composition-src="compositions/{cid}.html" '
                 f'data-start="{round(t, 3)}" data-duration="{dur}" data-track-index="1" data-track-kind="graphics" '
                 f'data-width="{FW}" data-height="{FH}"></div>')
    t += dur


def voice(name, at):
    d = vo_len(name)
    audio.append(f'      <audio id="vo-{name}" class="clip" src="assets/vo/{name}.wav" data-start="{round(at, 3)}" '
                 f'data-duration="{round(d, 3)}" data-track-index="5" data-volume="1"></audio>')
    # Subtitles: sentence-sized chunks, timed by their share of the line's characters.
    text = LINES[name]
    chunks = []
    for s in re.split(r"(?<=[.?!])\s+", text):
        words = s.split()
        while len(" ".join(words)) > 62:
            cutp = len(words) // 2
            chunks.append(" ".join(words[:cutp]))
            words = words[cutp:]
        chunks.append(" ".join(words))
    total = sum(len(c) for c in chunks)
    c0 = at
    for c in chunks:
        cd = d * len(c) / total
        i = len(caps)
        caps.append(f'      <div id="cap{i}" class="clip cap" data-start="{round(c0, 3)}" data-duration="{round(cd, 3)}" '
                    f'data-track-index="6"><span>{c}</span></div>')
        c0 += cd


def shots(seq, min_total=0.0):
    global t
    start = t
    seq = list(seq)
    durs = [cut(s) for s in seq]
    short = max(0.0, min_total - sum(durs))
    if short:
        last = seq[-1]
        durs[-1] = cut(last, last[2] + short * last[3])
    for s, d in zip(seq, durs):
        sid, *_, zoom, fx, fy, tag = s
        at = round(t, 3)
        clips.append(f'      <div class="shot-wrap" id="w-{sid}"><video id="{sid}" class="clip shot" src="assets/shots/{sid}.mp4" '
                     f'data-start="{at}" data-duration="{round(d, 3)}" data-track-index="2" muted playsinline></video></div>')
        tweens.append(f'      tl.fromTo("#w-{sid}", {{ opacity: 0 }}, {{ opacity: 1, duration: 0.25, ease: "power2.out" }}, {at});')
        if zoom > 1.0:
            ox, oy = fx * FW, fy * FH
            tweens.append(f'      tl.set("#w-{sid}", {{ transformOrigin: "{ox:.0f}px {oy:.0f}px", scale: 1 }}, {at});')
            tweens.append(f'      tl.to("#w-{sid}", {{ scale: {zoom}, duration: 0.8, ease: "power3.inOut" }}, {round(at + 0.4, 3)});')
        if tag:
            clips.append(f'      <div id="tag-{sid}" class="clip tag" data-start="{at}" data-duration="2" data-track-index="3">{tag}</div>')
            tweens.append(f'      tl.fromTo("#tag-{sid}", {{ opacity: 0, y: -10 }}, {{ opacity: 1, y: 0, duration: 0.25, ease: "power2.out" }}, {at});')
        t += d
    return start


voice("s1", 0.4)
host("title", TITLE_DUR)
build_at = t
host("build", BUILD_DUR)
for name, beat in VO_BEATS.items():
    voice(name, build_at + beat)

host("tryout", 3)
a = shots(S1, vo_len("s3") + 1.0)
voice("s3", a + 0.3)

host("pick", 3)
a = shots(S2, vo_len("s4") + 1.0)
voice("s4", a + 0.3)

host("failover", 3)
a = shots(S3, vo_len("s5") + 1.0)
voice("s5", a + 0.3)

end_at = t
host("endcard", END_DUR)
voice("s6", end_at + 1.0)
total = round(t, 3)

(HERE / "index.html").write_text(f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width={FW}, height={FH}" />
    <script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
    <style>
      @font-face {{ font-family: "Excalifont"; src: url("assets/fonts/excalifont.woff2") format("woff2"); }}
      * {{ margin: 0; padding: 0; box-sizing: border-box; }}
      html, body {{ margin: 0; width: {FW}px; height: {FH}px; overflow: hidden; background: #ffffff; }}
      #root {{ position: relative; width: 100%; height: 100%; background: #0f0f10; overflow: hidden; }}
      .shot-wrap {{ position: absolute; left: 0; top: 0; width: {FW}px; height: {FH}px; overflow: hidden; }}
      .shot {{ position: absolute; left: 0; top: 0; width: {FW}px; height: {FH}px; object-fit: cover; }}
      .tag {{ position: absolute; right: 56px; top: 40px; padding: 8px 18px; border-radius: 12px; z-index: 20;
              background: #ffffff; color: #1e1e1e; font-family: "Excalifont", sans-serif; font-size: 32px; }}
      .cap {{ position: absolute; left: 0; right: 0; bottom: 54px; text-align: center; z-index: 30; }}
      .cap span {{ display: inline-block; max-width: 1500px; padding: 12px 28px; border-radius: 14px;
                  background: rgba(15,15,16,0.86); color: #ffffff; font-family: "Helvetica Neue", Arial, sans-serif;
                  font-size: 42px; font-weight: 600; line-height: 1.25; }}
    </style>
  </head>
  <body>
    <div id="root" data-composition-id="main" data-start="0" data-duration="{total}" data-width="{FW}" data-height="{FH}">
{chr(10).join(clips)}
{chr(10).join(hosts)}
{chr(10).join(audio)}
{chr(10).join(caps)}
    </div>
    <script>
      window.__timelines = window.__timelines || {{}};
      const tl = gsap.timeline({{ paused: true }});
{chr(10).join(tweens)}
      window.__timelines["main"] = tl;
    </script>
  </body>
</html>
""")
print(f"total {total}s, build {BUILD_DUR}s, {len(caps)} captions")
