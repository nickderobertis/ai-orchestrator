"""Deterministic selection and documentation drift contracts for the paid smoke."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
from conftest import git

ROOT = Path(__file__).parents[1]


@pytest.mark.reads_docs
def test_smoke_selector_covers_exact_documented_launch_paths(tmp_path: Path) -> None:
    clone = tmp_path / "clone"
    git("clone", "-q", str(ROOT), str(clone))

    def commit(message: str) -> str:
        git("add", "-A", cwd=clone)
        git("commit", "-m", message, cwd=clone)
        return git("rev-parse", "HEAD", cwd=clone).strip()

    def selected(before: str, after: str) -> bool:
        proc = subprocess.run(
            [str(ROOT / "scripts/pre-push-smoke-needed.sh"), "origin/main"],
            cwd=clone,
            input=f"refs/heads/main {after} refs/heads/main {before}\n",
            text=True,
            capture_output=True,
        )
        assert proc.returncode in {0, 1}, proc.stderr
        return proc.returncode == 0

    base = git("rev-parse", "HEAD", cwd=clone).strip()
    (clone / "README.md").write_text("ordinary\n", encoding="utf-8")
    ordinary = commit("docs: ordinary")
    assert not selected(base, ordinary)

    previous = ordinary
    declared = subprocess.run(
        [str(ROOT / "scripts/pre-push-smoke-needed.sh"), "--print-paths"],
        text=True,
        capture_output=True,
        check=True,
    ).stdout.splitlines()
    for relative in (
        f"{path}launch-smoke-probe.sh" if path.endswith("/") else path for path in declared
    ):
        target = clone / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            target.read_text(encoding="utf-8") + "\n" if target.exists() else "# probe\n"
        )
        changed = commit(f"test: touch {relative}")
        assert selected(previous, changed), relative
        previous = changed
    assert selected("0" * 40, previous)
    assert not selected(previous, "0" * 40)

    documented_lists = {
        "docs/onejudge-integration.md": re.search(
            r"when the pushed endpoint diff touches (?P<paths>.+?);\s+every other",
            (ROOT / "docs/onejudge-integration.md").read_text(encoding="utf-8"),
            re.DOTALL,
        ),
    }
    for document, match in documented_lists.items():
        assert match is not None, f"{document} must retain its documented launch-path list"
        documented = re.findall(r"`([^`]+)`", match.group("paths"))
        assert documented == declared, (
            f"{document} launch paths must exactly match pre-push-smoke-needed.sh"
        )

    invalid = subprocess.run(
        [str(ROOT / "scripts/pre-push-smoke-needed.sh"), "not-a-revision"],
        cwd=clone,
        text=True,
        capture_output=True,
    )
    assert invalid.returncode == 2 and "not a commit" in invalid.stderr
    for update in (
        "refs/heads/main nope refs/heads/main nope\n",
        f"refs/heads/main {base}\n",
        f"refs/heads/main {base}  {'0' * 40}\n",
    ):
        malformed = subprocess.run(
            [str(ROOT / "scripts/pre-push-smoke-needed.sh"), "origin/main"],
            cwd=clone,
            input=update,
            text=True,
            capture_output=True,
        )
        assert malformed.returncode == 2 and "invalid ref update" in malformed.stderr
