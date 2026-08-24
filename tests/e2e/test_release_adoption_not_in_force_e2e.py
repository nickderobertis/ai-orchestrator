"""Prove, against the installed artifacts, that release adoption is not in force here.

`AGENTS.md`'s "Sequencing a node behind a release" describes a mechanism no release
this host installs contains. That is an unusual thing for this repository's prose to
do, and it is only honest while somebody can tell it apart from a description of what
a run does — so the claim is measured rather than asserted, against the two copies
that would have to carry it:

* the `onevcs` CLI the manager verbs run, which is `config/onevcs.version` and which
  would have to grow the `release` verb group before any of it could be configured;
* the `onevcs` the adopted engine **links**, which is what a dispatch publishes
  through and what an engine resolving a node's adoption mode would have to call —
  read out of the engine binary itself, because that is the artifact that runs.

The day either moves past the release carrying its half, this journey fails and the
section comes due: everything in it is written as upstream behaviour rather than as a
measurement of this host, and that framing is what has to change first.

Marked `reads_checkouts` for the reason that marker exists: its subject is an
installed producer rather than anything in this workspace, so no cache key here
describes it and a memoized verdict would replay a green straight across the upgrade
this exists to catch.
"""

from __future__ import annotations

import re
import subprocess

import pytest
from test_linked_engine_reconciliation_e2e import LINKED_IN_BINARY
from test_linked_libraries import Release
from test_release_adoption_guidance import (
    GUIDANCE_SECTION,
    NOT_IN_FORCE,
    RELEASE_SURFACE_FLOOR,
    section,
)

from orchestrator.root import REPO_ROOT

pytestmark = pytest.mark.reads_checkouts

#: The verb group that release exists to add. Absent from every earlier one.
RELEASE_VERB = "release"
#: How the recipes reach the CLI whose version `config/onevcs.version` pins — the same
#: `uv run` every manager verb in the justfile is a wrapper over, so what this journey
#: measures is the copy a manager would actually get.
ONEVCS = ("uv", "run", "onevcs")
#: The engine binary a dispatch runs, installed by `scripts/python-install.sh` into
#: this checkout's own environment.
ENGINE = REPO_ROOT / ".venv" / "bin" / "onepipeline"


def _onevcs(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([*ONEVCS, *arguments], cwd=REPO_ROOT, text=True, capture_output=True)


def test_the_cli_the_manager_verbs_run_has_no_release_surface() -> None:
    """The pinned CLI cannot answer a release question, let alone acknowledge one."""
    adopted = (REPO_ROOT / "config" / "onevcs.version").read_text("utf-8").strip()
    assert Release.parse(adopted, "config/onevcs.version") < RELEASE_SURFACE_FLOOR, (
        f"config/onevcs.version reads {adopted}, at or past the {RELEASE_SURFACE_FLOOR} "
        f"that carries the release surface; {GUIDANCE_SECTION!r} in AGENTS.md is "
        "written as upstream behaviour this host does not run, and that is no longer true"
    )

    reported = _onevcs("--version")
    assert reported.returncode == 0, reported.stderr
    assert reported.stdout.strip() == f"onevcs {adopted}", (
        "the CLI a manager verb runs is not the one config/onevcs.version pins; every "
        "claim this journey makes about the pin is a claim about that copy"
    )

    listed = _onevcs("--help")
    assert listed.returncode == 0, listed.stderr
    commands = listed.stdout.split("Commands:", 1)[1].split("Options:", 1)[0]
    assert not re.search(rf"(?m)^\s+{RELEASE_VERB}\b", commands), (
        f"the pinned onevcs lists a `{RELEASE_VERB}` verb group. Release targets can be "
        f"declared on this host now, so {GUIDANCE_SECTION!r} must stop saying none of "
        "this is in force here"
    )

    attempted = _onevcs(RELEASE_VERB, "targets", "ai-orchestrator")
    assert attempted.returncode != 0
    assert f"unrecognized subcommand '{RELEASE_VERB}'" in attempted.stderr, attempted.stderr


def test_the_engine_a_dispatch_runs_links_an_onevcs_with_nothing_to_adopt_over() -> None:
    """A node's adoption mode resolves through the *linked* onevcs, which predates it.

    `config/onevcs.version` is the CLI a manager verb runs and says nothing about
    this: what a dispatch publishes through, and what an engine would ask whether a
    release has happened, is the copy the adopted engine links. Reading it out of the
    binary is what makes the answer independent of every file that merely describes
    it — the distinction this host has twice acted on the wrong side of.
    """
    assert ENGINE.is_file(), (
        f"{ENGINE} is not installed; run `just bootstrap` so this journey reads the "
        "engine a dispatch would run rather than skipping the question"
    )
    # No `onepipeline` command reports what it links, so there is no user-facing
    # interface to drive for this question. The registry paths cargo embeds are the
    # artifact's own answer; they are the measurement `AGENTS.md` hands an operator as
    # `strings | grep`; and `tests/e2e/test_linked_engine_reconciliation_e2e.py` —
    # whose expression this reuses rather than restates — reads them the same way.
    # llmlint: ignore[tests_mirror_real_usage] The binary is the only thing that answers.
    found = LINKED_IN_BINARY.findall(ENGINE.resolve().read_bytes())
    linked = {crate.decode(): version.decode() for crate, version in found}
    carried = linked.get("onevcs")
    assert carried is not None, "the engine binary carries no onevcs registry path"
    assert Release.parse(carried, str(ENGINE)) < RELEASE_SURFACE_FLOOR, (
        f"the adopted engine links onevcs {carried}, at or past the "
        f"{RELEASE_SURFACE_FLOOR} that carries the release surface. A dispatch can now "
        f"resolve a release, so {GUIDANCE_SECTION!r} in AGENTS.md is describing this "
        "host rather than upstream and has to be re-dated and re-measured"
    )


def test_the_section_says_so_in_the_words_this_journey_measures() -> None:
    """The measurement is only worth taking while the prose makes the claim it checks."""
    assert NOT_IN_FORCE in section(), (
        f"{GUIDANCE_SECTION!r} no longer opens by saying none of this is in force here. "
        "That sentence is what the two measurements above exist to keep true; without "
        "it a reader cannot tell this section from one describing a run"
    )
