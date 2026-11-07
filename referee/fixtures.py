"""Ticket fixtures: what candidates work on, and the hidden tests that score them.

A fixture is a folder under tickets/ with a ticket.md and either:

- repo/ and hidden/ folders, like the hand-made sample, or
- a fixture.json pointing at a real fix in a public repository:

    {
      "source": "https://github.com/mahmoud/boltons",
      "fix": "<full sha of the fix commit>",
      "hidden_tests": ["tests/test_strutils.py"],
      "import_paths": ["."],
      "add_files": {"path": "contents"},
      "license": "BSD-3-Clause"
    }

For a real fix, candidates get the code as it was just before the fix, as a fresh
one-commit repo, so its history can't show them the answer. The hidden tests are
the fix commit's own test files.
"""

import hashlib
import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .workspaces import init_repo


@dataclass(frozen=True)
class Fixture:
    name: str
    ticket: str
    repo: Path
    hidden: Path | None
    import_paths: tuple[str, ...] = (".",)
    source: str | None = None


def available(tickets_dir):
    return sorted(p.name for p in Path(tickets_dir).iterdir() if (p / "ticket.md").exists())


def load(folder, cache):
    """Builds the fixture once into `cache` and returns it."""
    folder, cache = Path(folder), Path(cache) / Path(folder).name
    ticket = (folder / "ticket.md").read_text()
    spec_file = folder / "fixture.json"
    if not spec_file.exists():
        hidden = folder / "hidden"
        repo = init_repo(folder / "repo", cache / "repo")
        return Fixture(folder.name, ticket, repo, hidden if hidden.is_dir() else None)
    spec = json.loads(spec_file.read_text())
    stamp = hashlib.sha256(spec_file.read_bytes()).hexdigest()
    stamp_file = cache / "stamp"
    built = stamp_file.read_text() if stamp_file.exists() else None
    if built != stamp:
        _build(spec, cache)
        stamp_file.write_text(stamp)
    return Fixture(
        folder.name, ticket, cache / "repo", cache / "hidden", tuple(spec.get("import_paths", ["."])), spec["source"]
    )


def _git(*args, cwd=None):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, check=True).stdout


def _build(spec, cache):
    source = cache.parent / "sources" / spec["source"].rstrip("/").split("/")[-1]
    if not source.exists():
        source.parent.mkdir(parents=True, exist_ok=True)
        _git("clone", "--quiet", "--filter=blob:none", spec["source"], str(source))
    fix = spec["fix"]
    if cache.exists():
        shutil.rmtree(cache)
    cache.mkdir(parents=True)
    with tempfile.TemporaryDirectory() as tmp:
        code = Path(tmp) / "code"
        code.mkdir()
        archive = _git("archive", f"{fix}^", cwd=source)
        subprocess.run(["tar", "-x", "-C", str(code)], input=archive, check=True)
        for name, contents in spec.get("add_files", {}).items():
            (code / name).parent.mkdir(parents=True, exist_ok=True)
            (code / name).write_text(contents)
        # Generated files like _version.py are often gitignored by the project itself.
        init_repo(code, cache / "repo", force=list(spec.get("add_files", {})))
    hidden = cache / "hidden"
    hidden.mkdir()
    for test in spec["hidden_tests"]:
        (hidden / Path(test).name).write_bytes(_git("show", f"{fix}:{test}", cwd=source))
