"""Saves a real tryout as a recording: python -m replay.record .bench/tryouts/T-06213d --name sample-invoice

For each candidate it keeps the commit the tryout scored (the first one after the
starting commit) as a patch, its message, and how long after the brief it landed.
A candidate that never committed is recorded as silent.
"""

import argparse
import json
import subprocess
from pathlib import Path

RUNS = Path(__file__).resolve().parent / "runs"


def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True).stdout


def record(tryout, name, ticket, note, rename=None, dest=RUNS):
    tryout, out = Path(tryout), Path(dest) / name
    out.mkdir(parents=True, exist_ok=True)
    rename = rename or {}
    candidates = []
    for workspace in sorted(p for p in tryout.iterdir() if (p / ".git").exists()):
        short = rename.get(workspace.name, workspace.name.removeprefix("bench-"))
        base = git(workspace, "rev-list", "--max-parents=0", "HEAD").split()[0]
        after = git(workspace, "rev-list", "--reverse", f"{base}..HEAD").split()
        entry = {"name": short, "recorded_as": workspace.name, "seconds": None, "message": None, "patch": None}
        if after:
            commit = after[0]
            # The workspace folder is made just before the brief goes out.
            briefed = workspace.stat().st_birthtime
            landed = int(git(workspace, "log", "-1", "--format=%ct", commit))
            patch = out / f"{short}.patch"
            patch.write_text(git(workspace, "diff", "--binary", base, commit))
            entry |= {
                "seconds": max(1, round(landed - briefed)),
                "message": git(workspace, "log", "-1", "--format=%s", commit).strip(),
                "patch": patch.name,
            }
        candidates.append(entry)
    spec = {"name": name, "ticket": ticket, "recorded": note, "tryout": tryout.name, "candidates": candidates}
    (out / "run.json").write_text(json.dumps(spec, indent=2) + "\n")
    return spec


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tryout", help="a tryout folder under .bench/tryouts")
    parser.add_argument("--name", required=True, help="name for the recording")
    parser.add_argument("--ticket", help="the ticket under tickets/ (default: same as --name)")
    parser.add_argument("--note", default="", help="where and when it was recorded")
    args = parser.parse_args()
    print(json.dumps(record(args.tryout, args.name, args.ticket or args.name, args.note), indent=2))
