"""Hold the manager's check-reading rule to the engine release this host pins."""

import re
import subprocess

import pytest
from registered_checkouts import discover_checkouts

from orchestrator.root import REPO_ROOT

MANAGER = REPO_ROOT / "AGENTS.md"
PIN = REPO_ROOT / "config" / "onepipeline.version"
ENGINE = "github.com/nickderobertis/onepipeline"


def _engine_source(relative: str) -> str:
    """One source file of the engine at the pinned tag, never its working tree."""
    discovered = discover_checkouts()
    checkout = discovered.held[ENGINE][0]
    tag = f"v{PIN.read_text(encoding='utf-8').strip()}"
    return subprocess.run(
        ["git", "-C", str(checkout), "show", f"{tag}:src/{relative}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


@pytest.mark.reads_checkouts
def test_manager_check_reading_matches_the_adopted_engine() -> None:
    """The reading rule quotes the pinned producer, including its requirement labels."""
    manager = " ".join(MANAGER.read_text(encoding="utf-8").split())
    passage = manager.split("A `check-failed` surface", 1)[1].split("Require each worker", 1)[0]
    vocabulary = _engine_source("vocabulary.rs")
    labels = re.findall(
        r'"([^"\n]+)"', vocabulary.split("pub const fn requirement_label", 1)[1].split("\n}", 1)[0]
    )
    assert len(labels) == 2, labels
    for label in labels:
        assert f"`{label}`" in passage

    profile = _engine_source("filter.rs").split("fn shipped_profile", 1)[1]
    planner = profile.split("DEFAULT_PROFILE =>", 1)[1].split('"detailed" =>', 1)[0]
    assert "stamped(Source::Vcs, crate::vocabulary::Significance::Attention)" in planner
    assert "`src/filter.rs`" in manager and "`attention` significance" in manager

    checks = _engine_source("checkwatch.rs")
    for kind in ("check-failed", "checks-unfollowed"):
        assert re.search(rf'pub const \w+_SURFACE_KIND: &str = "{kind}";', checks)
        assert f"`{kind}`" in manager
    failed = checks.split("fn failed_message", 1)[1].split("\n}", 1)[0]
    assert "requirement_label(reading.required)" in failed
    assert "reading.url" in failed
    unfollowed = checks.split("fn unfollowed_message", 1)[1].split("\n}", 1)[0]
    assert "after the checks timeout" in unfollowed
    assert "a later result of these is not shown" in unfollowed
    assert "later result is not shown" in passage
    for key in ("checks_running", "checks_unfollowed"):
        assert f'"{key}"' in checks.split("fn ending_sections", 1)[1]
    assert "crate::checkwatch::ending_sections" in _engine_source("views.rs")
    assert "`just results` lists checks still running" in passage

    release = _engine_source("release.rs")
    assert "held by {} (the release's own change request)" in release
    assert 'parts.push(format!("failing — {failing}"))' in release
    assert "requirement_label" in release
    assert '"held by" line names the release\'s own change request' in passage
