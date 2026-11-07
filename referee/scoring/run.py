"""Runs a ticket's hidden tests against one candidate's commit.

The candidate's code is exported from its commit with `git archive`, so only
committed work counts and the live tryout folder is never touched. Tests run in
a Docker container with no network, read-only code and resource limits. The
candidate's own conftest.py and pytest config are ignored, so it can't change
how the hidden tests run or what they report.

Limit: the hidden tests run in the same process as the candidate's code, so code
written to patch pytest from the inside could fake a pass. tamper_signs() flags
added source lines that reach for the test machinery, and the scorecard shows them.
"""

import posixpath
import re
import shutil
import subprocess
import tempfile
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

IMAGE = "bench-scorer:1"
DOCKERFILE_DIR = Path(__file__).resolve().parent
TIMEOUT_SECONDS = 120
PYTEST_INI = "[pytest]\npythonpath = {code}\naddopts = -p no:cacheprovider --noconftest\n"


# Added source lines that reach into the test machinery. Test files may import pytest.
TAMPER = re.compile(r"\b(_?pytest|junit|results\.xml|atexit|sys\.modules|builtins|/out\b|/hidden\b|sitecustomize)")
TEST_FILE = re.compile(r"(^|/)(tests?/|test_[^/]*$|[^/]*_test\.py$)")


def tamper_signs(repo, base, commit):
    """Added lines outside test files that touch pytest, its reports or the sandbox paths."""
    diff = subprocess.run(
        ["git", "diff", "--unified=0", base, commit], cwd=repo, capture_output=True, text=True, check=True
    ).stdout
    signs, current = [], None
    for line in diff.splitlines():
        if line.startswith("+++ "):
            current = line[6:] if line.startswith("+++ b/") else None
        elif line.startswith("+") and current and not TEST_FILE.search(current):
            if TAMPER.search(line) or current.endswith(("conftest.py", "pytest.ini", "sitecustomize.py")):
                signs.append(f"{current}: {line[1:].strip()[:100]}")
    return tuple(signs)


JUNK_FILE = re.compile(r"(^|/)(__pycache__/|conftest\.py$)|\.pyc$")


def code_lines(repo, base, commit):
    """Lines added or removed in source files, not counting blanks, comments, tests or junk.

    Comments don't count, so an agent isn't marked down for explaining its change.
    """
    diff = subprocess.run(
        ["git", "diff", "--unified=0", base, commit], cwd=repo, capture_output=True, text=True, check=True
    ).stdout
    count, counting = 0, False
    for line in diff.splitlines():
        if line.startswith("diff --git"):
            name = line.rsplit(" b/", 1)[-1]
            counting = not TEST_FILE.search(name) and not JUNK_FILE.search(name)
        elif line.startswith(("+++", "---")):
            continue
        elif counting and line[:1] in "+-":
            text = line[1:].strip()
            count += bool(text) and not text.startswith("#")
    return count


@dataclass(frozen=True)
class TestRun:
    passed: int
    total: int
    failures: tuple[str, ...]
    sandboxed: bool
    error: str | None = None
    output: str = ""


def docker_ready():
    if not shutil.which("docker"):
        return False
    if subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True).returncode == 0:
        return True
    built = subprocess.run(["docker", "build", "-q", "-t", IMAGE, str(DOCKERFILE_DIR)], capture_output=True)
    return built.returncode == 0


def export_commit(repo, commit, dest):
    archive = subprocess.run(["git", "archive", commit], cwd=repo, capture_output=True, check=True)
    subprocess.run(["tar", "-x", "-C", str(dest)], input=archive.stdout, check=True)


def parse_junit(path):
    root = ET.parse(path).getroot()
    cases = root.iter("testcase")
    total, failures = 0, []
    for case in cases:
        total += 1
        if any(child.tag in ("failure", "error") for child in case):
            failures.append(case.get("name"))
        elif any(child.tag == "skipped" for child in case):
            # A skip can't count as a pass: the candidate's code could cause it.
            failures.append(case.get("name") + " (skipped)")
    return total, failures


def run_hidden_tests(repo, commit, hidden_dir, sandbox=True, import_paths=(".",)):
    """`import_paths` are folders inside the candidate's code to import from, e.g. "src"."""
    with tempfile.TemporaryDirectory(prefix="bench-score-") as tmp:
        tmp = Path(tmp)
        code, tests, out = tmp / "code", tmp / "hidden", tmp / "out"
        for folder in (code, out):
            folder.mkdir()
        export_commit(repo, commit, code)
        shutil.copytree(hidden_dir, tests, ignore=shutil.ignore_patterns("__pycache__"))
        sandboxed = sandbox and docker_ready()
        container = f"bench-score-{uuid.uuid4().hex[:12]}"
        if sandboxed:
            (out / "pytest.ini").write_text(
                PYTEST_INI.format(code=" ".join(posixpath.normpath(f"/candidate/{p}") for p in import_paths))
            )
            out.chmod(0o777)
            command = [
                "docker", "run", "--rm", "--name", container, "--network", "none", "--read-only", "--tmpfs", "/tmp",
                "--memory", "512m", "--cpus", "1", "--pids-limit", "128",
                "-v", f"{code}:/candidate:ro", "-v", f"{tests}:/hidden:ro", "-v", f"{out}:/out",
                IMAGE, "python", "-m", "pytest", "-q", "-c", "/out/pytest.ini", "--rootdir", "/hidden",
                "--junitxml", "/out/results.xml", "/hidden",
            ]
            cwd = None
        else:
            (out / "pytest.ini").write_text(PYTEST_INI.format(code=" ".join(str(code / p) for p in import_paths)))
            command = [
                "python", "-m", "pytest", "-q", "-c", str(out / "pytest.ini"), "--rootdir", str(tests),
                "--junitxml", str(out / "results.xml"), str(tests),
            ]
            # Run from the tests folder, so a module in the candidate's code can't shadow pytest.
            cwd = tests
        try:
            completed = subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            if sandboxed:
                # Killing the docker client leaves the container running, so stop it by name.
                subprocess.run(["docker", "kill", container], capture_output=True)
            return TestRun(0, 0, (), sandboxed, f"hidden tests took over {TIMEOUT_SECONDS}s")
        results = out / "results.xml"
        if not results.exists():
            return TestRun(0, 0, (), sandboxed, (completed.stderr or completed.stdout).strip()[-500:])
        total, failures = parse_junit(results)
        output = (completed.stdout or completed.stderr).strip()[-2000:]
        return TestRun(total - len(failures), total, tuple(failures), sandboxed, output=output)
