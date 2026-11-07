"""One git clone per candidate per ticket, and what the Referee reads back from it.

The Referee never trusts a candidate's account of its work. It reads the branch
itself, and commits any uncommitted changes it finds so the branch is the record.
"""

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

SNAPSHOT_AUTHOR = ["-c", "user.name=Bench Referee", "-c", "user.email=referee@bench.invalid"]


def git(cwd, *args):
    result = subprocess.run(
        ["git", *SNAPSHOT_AUTHOR, *args], cwd=cwd, capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


@dataclass(frozen=True)
class Snapshot:
    commit: str
    base: str
    files: tuple[str, ...]
    added: int
    removed: int
    referee_committed: bool
    numstat: tuple[tuple[str, int, int], ...] = ()

    @property
    def changed(self):
        return bool(self.files)


@dataclass(frozen=True)
class Workspace:
    path: Path
    branch: str
    base: str

    @classmethod
    def create(cls, repo, root, ticket_id, candidate, base="HEAD"):
        path = Path(root) / ticket_id / candidate
        if path.exists():
            shutil.rmtree(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "--quiet", str(repo), str(path)], check=True)
        base_commit = git(path, "rev-parse", base)
        branch = f"tryout/{ticket_id}/{candidate}"
        git(path, "checkout", "--quiet", "-b", branch, base_commit)
        return cls(path, branch, base_commit)

    def collect(self):
        """Reads the candidate's work. Commits leftovers on its branch first."""
        if git(self.path, "rev-parse", "--abbrev-ref", "HEAD") != self.branch:
            git(self.path, "checkout", "--quiet", self.branch)
        dirty = bool(git(self.path, "status", "--porcelain"))
        if dirty:
            git(self.path, "add", "-A")
            git(self.path, "commit", "--quiet", "-m", "Referee snapshot of uncommitted work")
        numstat = []
        for line in git(self.path, "diff", "--numstat", self.base, "HEAD").splitlines():
            plus, minus, name = line.split("\t", 2)
            # Binary files show "-" for both counts.
            numstat.append((name, int(plus) if plus.isdigit() else 0, int(minus) if minus.isdigit() else 0))
        return Snapshot(
            commit=git(self.path, "rev-parse", "HEAD"),
            base=self.base,
            files=tuple(name for name, _, _ in numstat),
            added=sum(plus for _, plus, _ in numstat),
            removed=sum(minus for _, _, minus in numstat),
            referee_committed=dirty,
            numstat=tuple(numstat),
        )


def init_repo(source, dest, force=()):
    """Makes a one-commit git repo from a folder of files, e.g. a ticket fixture.

    `force` lists paths to commit even if the repo's own .gitignore excludes them.
    """
    dest = Path(dest)
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(source, dest, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    git(dest, "init", "--quiet", "-b", "main")
    git(dest, "add", "-A")
    if force:
        git(dest, "add", "--force", "--", *force)
    git(dest, "commit", "--quiet", "-m", "Before the fix")
    return dest
