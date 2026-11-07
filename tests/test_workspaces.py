from pathlib import Path

from referee.workspaces import Workspace, git, init_repo

SAMPLE = Path(__file__).resolve().parent.parent / "tickets/sample-invoice/repo"


def make(tmp_path, candidate="cand"):
    repo = init_repo(SAMPLE, tmp_path / "origin")
    return repo, Workspace.create(repo, tmp_path / "tryouts", "T-abc123", candidate)


def test_clone_is_on_its_own_branch(tmp_path):
    repo, ws = make(tmp_path)
    assert ws.branch == "tryout/T-abc123/cand"
    assert git(ws.path, "rev-parse", "--abbrev-ref", "HEAD") == ws.branch
    assert ws.base == git(repo, "rev-parse", "HEAD")
    assert (ws.path / "invoice/totals.py").exists()


def test_no_work_means_no_changes(tmp_path):
    _, ws = make(tmp_path)
    snap = ws.collect()
    assert not snap.changed
    assert snap.commit == ws.base


def test_committed_work_is_read_from_git(tmp_path):
    _, ws = make(tmp_path)
    (ws.path / "invoice/totals.py").write_text("x = 1\n")
    git(ws.path, "commit", "-qam", "fix")
    snap = ws.collect()
    assert snap.files == ("invoice/totals.py",)
    assert snap.added == 1 and snap.removed > 0
    assert not snap.referee_committed


def test_uncommitted_work_is_snapshotted(tmp_path):
    _, ws = make(tmp_path)
    (ws.path / "invoice/new.py").write_text("a = 1\nb = 2\n")
    snap = ws.collect()
    assert snap.referee_committed
    assert snap.files == ("invoice/new.py",)
    assert snap.added == 2
    assert git(ws.path, "status", "--porcelain") == ""


def test_switching_branch_away_is_undone(tmp_path):
    _, ws = make(tmp_path)
    git(ws.path, "checkout", "-q", "-b", "elsewhere")
    (ws.path / "x.txt").write_text("hi\n")
    git(ws.path, "add", "-A")
    git(ws.path, "commit", "-qm", "off branch")
    snap = ws.collect()
    assert not snap.changed


def test_candidates_get_separate_clones(tmp_path):
    repo = init_repo(SAMPLE, tmp_path / "origin")
    a = Workspace.create(repo, tmp_path / "t", "T-1", "a")
    b = Workspace.create(repo, tmp_path / "t", "T-1", "b")
    (a.path / "only-a.txt").write_text("a\n")
    assert not (b.path / "only-a.txt").exists()
