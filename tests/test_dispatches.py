"""Drift gates for the two vocabularies `orchestrator.dispatches` cannot import.

Everything else this module reads it reads from a live process. These two it has to
restate, and a restatement with nothing reconciling it is how a view starts making a
confident wrong claim: a role added to `AgentRole` would be stamped on dispatches and
reported as "role unknown", and a renamed credential directory would make every
alternate2 turn report as `claude-code:primary`.
"""

from __future__ import annotations

from typing import get_args

from orchestrator import REPO_ROOT
from orchestrator.dispatches import (
    _ALTERNATE_CLAUDE_DIRS,
    _ALTERNATE_CODEX_DIR,
    _ROLE_EVIDENCE,
    DISPATCH_ROLES,
)
from orchestrator.labels import PERSONA_AGENT_ROLES, AgentRole


def test_every_role_a_dispatch_can_stamp_is_one_this_view_can_report() -> None:
    """`DISPATCH_ROLES` covers `AgentRole`, so no stamped role reads as unknown."""
    assert set(get_args(AgentRole)) <= DISPATCH_ROLES
    # And the personas that map to a role are themselves inside that vocabulary, so a
    # new persona role cannot enter through `labels` and land outside this view.
    assert set(PERSONA_AGENT_ROLES.values()) <= DISPATCH_ROLES


def test_the_alternate_credential_directories_match_their_one_source() -> None:
    """The directory names this view identifies an identity by are the wrappers' own.

    `scripts/claude-alt-config-dir.sh` and `scripts/codex-alt-home.sh` derive the paths
    every dispatched provider is given. A view process never sources either, so it
    recognises the *name* — and this is what keeps that name in step with them.
    """
    claude = (REPO_ROOT / "scripts" / "claude-alt-config-dir.sh").read_text(encoding="utf-8")
    codex = (REPO_ROOT / "scripts" / "codex-alt-home.sh").read_text(encoding="utf-8")
    # Each script names its directory as a bare `$HOME`-relative leaf, so the leaf is
    # what is reconciled — the same token this view matches on `PurePosixPath(...).name`.
    for directory in _ALTERNATE_CLAUDE_DIRS:
        assert f" {directory}" in claude, directory
    assert f'"$HOME/{_ALTERNATE_CODEX_DIR}"' in codex, _ALTERNATE_CODEX_DIR


def test_every_role_evidence_names_a_file_this_repository_actually_has() -> None:
    """The wrapper scripts and harness configs a turn is recognised by still exist.

    `_ROLE_EVIDENCE` matches on filenames rather than importing them, because it is
    reading another process's command line and a path is all it has. That makes each
    entry a restatement, and a renamed wrapper or config would silently stop being
    recognised — every turn it served would report as "role unknown" with nothing
    failing. This is what fails instead.
    """
    known = {path.name for path in REPO_ROOT.glob("oneharness*.toml")}
    known |= {path.name for path in (REPO_ROOT / "scripts").iterdir() if path.is_file()}
    missing = [evidence for evidence, _ in _ROLE_EVIDENCE if evidence not in known]
    assert not missing, f"role evidence names files this repository does not have: {missing}"
