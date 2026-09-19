"""Real session-setup journeys kept behind their own host-tool target."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from nx_workspace import shares_workspace_install
from published_tools import ONETASKGRAPH_BIN

from orchestrator.root import REPO_ROOT

#: The variable session setup's last step reads its checkout list from, in place of the
#: tracked one. Named here so a run of the real setup provisions the stand-ins below and
#: never this host's registered siblings — the one thing a journey may not do to them.
REGISTERED_CHECKOUTS_OVERRIDE = "ORCHESTRATOR_REPOS_BOOTSTRAP_CHECKOUTS"


def _stand_in(root: Path, *, marks: Path, exit_status: int = 0) -> Path:
    """A real checkout whose `just bootstrap` leaves a mark and exits as told."""
    root.mkdir()
    (root / "justfile").write_text(
        f'bootstrap:\n    @echo "{root.name}" >> "{marks}"\n    @exit {exit_status}\n',
        encoding="utf-8",
    )
    for command in (
        ["git", "init", "-q", "--initial-branch", "main"],
        ["git", "add", "-A"],
        [
            "git",
            "-c",
            "user.name=Journey",
            "-c",
            "user.email=j@example.invalid",
            "commit",
            "-q",
            "-m",
            "seed",
        ],
    ):
        subprocess.run(command, cwd=root, check=True, capture_output=True)
    return root


@shares_workspace_install
def test_session_setup_keeps_the_locked_plan_store_and_plan_root_in_force(
    tmp_path: Path,
) -> None:
    """The real setup verifies the locked CLI, ensures the plan root, and reaches the siblings.

    The sibling half runs over two stand-ins the journey registers — one whose
    bootstrap succeeds and one whose bootstrap fails — so that what is asserted is the
    real `scripts/session-setup.sh` reaching the real `just repos-bootstrap`, naming the
    checkout that failed, and finishing with the exit status its own toolchain earned.
    """
    plans = REPO_ROOT / ".plans"
    marks = tmp_path / "marks"
    healthy = _stand_in(tmp_path / "healthy-sibling", marks=marks)
    failing = _stand_in(tmp_path / "failing-sibling", marks=marks, exit_status=3)
    listing = tmp_path / "checkouts"
    listing.write_text(f"{healthy}\n{failing}\n{tmp_path / 'absent-sibling'}\n", encoding="utf-8")

    provisioned = subprocess.run(
        ["bash", str(REPO_ROOT / "scripts" / "session-setup.sh")],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
        env={
            **os.environ,
            REGISTERED_CHECKOUTS_OVERRIDE: str(listing),
            "XDG_CACHE_HOME": str(tmp_path / "cache"),
        },
    )

    assert provisioned.returncode == 0, provisioned.stdout + provisioned.stderr
    adopted = (REPO_ROOT / "config" / "onetaskgraph.version").read_text().strip()
    assert f"ready (onetaskgraph: {adopted} at {ONETASKGRAPH_BIN})" in provisioned.stderr
    assert (plans / "tasks").is_dir()
    assert (plans / "projects").is_dir()

    # Both stand-ins were bootstrapped, each is reported by name with what happened to
    # it, the absent one is skipped the way the registration recipe skips it, and the
    # failure changed nothing about this session's own exit status.
    assert sorted(marks.read_text(encoding="utf-8").split()) == [failing.name, healthy.name]
    report = [line for line in provisioned.stderr.splitlines() if "-sibling" in line]
    assert any(line.split()[0] == "ran" and str(healthy) in line for line in report), report
    assert any(
        line.split()[:3] == ["failed", "exit", "3"] and str(failing) in line for line in report
    ), report
    assert any(
        line.split()[0] == "skip" and str(tmp_path / "absent-sibling") in line for line in report
    ), report
    assert "1 ran, 0 unchanged, 1 skipped, 0 refused, 1 failed" in provisioned.stderr
    assert (
        "session-setup: a registered sibling checkout's bootstrap failed or was refused"
        in provisioned.stderr
    )
