"""Builds the Bench demo video: cards, the architecture tour, the Console footage, narration.

    python3 video/make_video.py

Footage comes from .bench/recording/raw.mp4 (Console window captured frame by frame
while `python -m replay` ran). Narration is macOS `say`. Output: .bench/video/bench-demo.mp4
"""

import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / ".bench/recording/raw.mp4"
ARCH = ROOT / "docs/architecture/bench-architecture.png"
OUT = ROOT / ".bench/video"
OUT.mkdir(parents=True, exist_ok=True)
W, H, FPS = 1920, 1080, 30
FONT = "/System/Library/Fonts/Avenir Next.ttc"
VOICE = "Samantha"
BG, INK, MUTED, ACCENT = (18, 18, 20), (240, 240, 240), (150, 150, 155), (255, 138, 92)


def run(*cmd):
    subprocess.run(cmd, check=True, capture_output=True)


def font(size, bold=False):
    return ImageFont.truetype(FONT, size, index=2 if bold else 0)


def duration(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout)


def voice(name, text):
    aiff, wav = OUT / f"{name}.aiff", OUT / f"{name}.wav"
    run("say", "-v", VOICE, "-r", "182", "-o", str(aiff), text)
    run("ffmpeg", "-y", "-i", str(aiff), "-ar", "48000", "-ac", "2", str(wav))
    return wav, duration(wav)


def card(name, lines, seconds):
    """lines: [(text, size, colour, bold)]"""
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    heights = [font(s, b).getbbox(t)[3] + s * 0.35 for t, s, _, b in lines]
    y = (H - sum(heights)) / 2
    for (text, size, colour, bold), h in zip(lines, heights):
        f = font(size, bold)
        w = d.textlength(text, font=f)
        d.text(((W - w) / 2, y), text, font=f, fill=colour)
        y += h
    png = OUT / f"{name}.png"
    img.save(png)
    clip = OUT / f"{name}.mp4"
    run("ffmpeg", "-y", "-loop", "1", "-i", str(png), "-t", f"{seconds:.2f}", "-r", str(FPS),
        "-vf", "format=yuv420p,fade=t=in:d=0.3", "-c:v", "libx264", "-crf", "18", str(clip))
    return clip


def label(img, text):
    """Small lower-third naming what's on screen."""
    d = ImageDraw.Draw(img)
    f = font(34, True)
    w = d.textlength(text, font=f)
    d.rounded_rectangle((60, H - 120, 60 + w + 48, H - 52), radius=14, fill=(0, 0, 0))
    d.text((84, H - 113), text, font=f, fill=INK)


def tour(name, keys, seconds):
    """Pans over the architecture image. keys: [(t, x, y, w)] in preview units (1600 wide)."""
    src = Image.open(ARCH).convert("RGB")
    k = src.width / 1600
    frames = OUT / f"{name}_frames"
    frames.mkdir(exist_ok=True)
    n = int(seconds * FPS)

    def ease(a):
        return a * a * (3 - 2 * a)

    for i in range(n):
        t = i / FPS
        for (t0, *a), (t1, *b) in zip(keys, keys[1:]):
            if t0 <= t <= t1:
                p = ease((t - t0) / (t1 - t0)) if t1 > t0 else 1
                x, y, w = (a[j] + (b[j] - a[j]) * p for j in range(3))
                break
        else:
            _, x, y, w = keys[-1]
        h = w * 9 / 16
        box = tuple(int(v * k) for v in (x, y, x + w, y + h))
        src.crop(box).resize((W, H), Image.LANCZOS).save(frames / f"{i:05d}.jpg", quality=90)
    clip = OUT / f"{name}.mp4"
    run("ffmpeg", "-y", "-framerate", str(FPS), "-i", str(frames / "%05d.jpg"),
        "-vf", "format=yuv420p", "-c:v", "libx264", "-crf", "18", str(clip))
    return clip


def shot(name, start, length, crop, text, speed=1.0, out_len=None):
    """A piece of the Console footage, cropped to (x, y, w, h) in the 1920x1216 frame, fitted and labelled."""
    x, y, w, h = crop
    lab = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    label(lab, text)
    lab_png = OUT / f"{name}_label.png"
    lab.save(lab_png)
    clip = OUT / f"{name}.mp4"
    vf = f"[0:v]crop={w}:{h}:{x}:{y},scale={W}:{H}:flags=lanczos:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=0x121214,setpts=PTS/{speed}[v];[v][1:v]overlay=0:0,format=yuv420p"
    args = ["ffmpeg", "-y", "-ss", str(start), "-t", str(length), "-i", str(RAW), "-i", str(lab_png),
            "-filter_complex", vf, "-r", str(FPS), "-an", "-c:v", "libx264", "-crf", "18"]
    if out_len:
        args += ["-t", f"{out_len:.2f}"]
    run(*args, str(clip))
    return clip


def hold(clip, seconds):
    """Extends a clip to `seconds` by freezing its last frame."""
    have = duration(clip)
    if have >= seconds:
        return clip
    out = clip.with_name(clip.stem + "_held.mp4")
    run("ffmpeg", "-y", "-i", str(clip), "-vf", f"tpad=stop_mode=clone:stop_duration={seconds - have:.2f}",
        "-c:v", "libx264", "-crf", "18", str(out))
    return out


NARRATION = {
    "v1": "Every team using coding agents asks which agent should do which job. "
          "Bench answers it on your own tickets, in team chat, and then acts on the answer.",
    "v2a": "A person posts a ticket. Bench's tickets are real bugs from public repos: "
           "the code from just before the fix, with the fix's own tests kept hidden.",
    "v2b": "Everything runs through Switch. The Referee opens a sealed room for each candidate, "
           "Claude Code and Codex, so no agent can see another's attempt. "
           "The job itself is an exclusive room role, called fixer.",
    "v2c": "The Referee is plain, deterministic code. It runs the hidden tests in a Docker sandbox "
           "with no network, and ranks by tests passed, then size, then time. No model decides a score.",
    "v2d": "A person picks the winner in chat. And if the holder goes down, "
           "the Referee gives the role to the runner-up, and re-sends the work it missed.",
    "v3": "Here it is in Switch Console. The ticket comes in, and the Referee starts a sealed tryout. "
          "This is a replay of a real Console run: the commits are exactly what Claude Code and Codex wrote. "
          "Both pass all ten hidden tests. Claude ranks first: about the same size, and faster.",
    "v4": "A person replies: pick replay-claude. The Referee asks the winner to take the fixer role, "
          "then checks Switch. One second later, Claude holds fixer, and answers the next message to fixer.",
    "v5": "Now Claude is killed, with a message still waiting. About six seconds later, the Referee sees "
          "the seat is empty, moves fixer to the runner-up, Codex, and re-sends the missed message. "
          "Nobody changed how they work.",
    "v6": "We also ran Bench live, with real Claude Code and Codex on Switch Console. "
          "On a real more-itertools bug, Claude passed six hundred and eight of six hundred and eight hidden tests. "
          "You can replay the whole thing with one command, and no API keys.",
}


def main():
    vo = {k: voice(k, t) for k, t in NARRATION.items()}
    pad = 0.6
    parts = []  # (clip, voice wav or None, voice offset)

    title_len = vo["v1"][1] + 1.2
    parts.append((card("c_title", [("Bench", 150, INK, True),
                                   ("Coding agents try out for your team's real tickets.", 46, MUTED, False),
                                   ("The winner takes the job. The job survives a crash.", 46, ACCENT, False)],
                       title_len), "v1", 0.5))

    beats = ["v2a", "v2b", "v2c", "v2d"]
    lens = [vo[b][1] + pad for b in beats]
    starts = [sum(lens[:i]) for i in range(4)]
    total = sum(lens) + 0.5
    rows = [0, 230, 565, 757]
    keys = [(0, 0, 0, 1600)]
    for s, l, y in zip(starts, lens, rows):
        keys += [(s + 1.0, 0, y, 1100), (s + l, 500, y, 1100)]
    keys.append((total, 0, 0, 1600))
    arch = tour("c_arch", keys, total)
    parts.append((arch, None, 0))
    arch_voice = [(b, s + 0.4) for b, s in zip(beats, starts)]

    def section(name, text):
        return card(name, [(text, 72, INK, True)], 1.3)

    parts.append((section("c_s1", "1 · Sealed tryout, hidden tests"), None, 0))
    t1 = [shot("f_ticket", 31, 9, (340, 280, 1360, 765), "Ticket posted in Switch Console", speed=1.5),
          shot("f_tryout", 41, 3.2, (340, 240, 1580, 889), "Referee starts the sealed tryout"),
          shot("f_score", 88, 7, (300, 180, 1520, 855), "Scorecard: hidden tests in a sandbox")]
    need = vo["v3"][1] + pad
    have = sum(duration(c) for c in t1)
    t1[-1] = hold(t1[-1], duration(t1[-1]) + max(0, need - have))
    parts += [(t1[0], "v3", 0.2), (t1[1], None, 0), (t1[2], None, 0)]

    parts.append((section("c_s2", "2 · A person picks the winner"), None, 0))
    t2 = [shot("f_pick", 119, 5.5, (340, 130, 1400, 788), "pick replay-claude · fixer role moves"),
          shot("f_move", 125, 6.5, (1140, 300, 770, 740), "Referee confirms the move with Switch")]
    need = vo["v4"][1] + pad
    have = sum(duration(c) for c in t2)
    t2[-1] = hold(t2[-1], duration(t2[-1]) + max(0, need - have))
    parts += [(t2[0], "v4", 0.2), (t2[1], None, 0)]

    parts.append((section("c_s3", "3 · The holder dies. The runner-up takes over."), None, 0))
    t3 = shot("f_failover", 133, 9.5, (1140, 80, 770, 950), "Failover: role moved, missed message re-sent")
    t3 = hold(t3, vo["v5"][1] + pad)
    parts.append((t3, "v5", 0.2))

    end_len = vo["v6"][1] + 2.0
    parts.append((card("c_end", [("Bench", 130, INK, True),
                                 ("github.com/Pavilion-devs/bench", 48, ACCENT, False),
                                 ("uv run python -m replay", 48, INK, False),
                                 ("Agents try out · hidden tests keep score · people pick · the job survives a crash",
                                  34, MUTED, False)], end_len), "v6", 0.5))

    # Join the pictures, then lay the narration over them at the right times.
    listing = OUT / "list.txt"
    listing.write_text("".join(f"file '{c}'\n" for c, _, _ in parts))
    silent = OUT / "silent.mp4"
    run("ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listing), "-c:v", "libx264", "-crf", "18",
        "-r", str(FPS), "-pix_fmt", "yuv420p", str(silent))

    placements, t = [], 0.0
    for clip, v, off in parts:
        if v:
            placements.append((vo[v][0], t + off))
        if clip == arch:
            placements += [(vo[b][0], t + s) for b, s in arch_voice]
        t += duration(clip)
    inputs, filters = [], []
    for i, (wav, at) in enumerate(placements):
        inputs += ["-i", str(wav)]
        filters.append(f"[{i + 1}:a]adelay={int(at * 1000)}|{int(at * 1000)}[a{i}]")
    mix = "".join(f"[a{i}]" for i in range(len(placements)))
    filters.append(f"{mix}amix=inputs={len(placements)}:normalize=0,volume=1.0[aout]")
    final = OUT / "bench-demo.mp4"
    run("ffmpeg", "-y", "-i", str(silent), *inputs, "-filter_complex", ";".join(filters),
        "-map", "0:v", "-map", "[aout]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", str(final))
    print(f"{final} {duration(final):.1f}s")


if __name__ == "__main__":
    main()
