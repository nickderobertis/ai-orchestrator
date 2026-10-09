"""One plan-authoring root, resolved once by the launch and read by every dispatch of it.

`onetaskgraph.yaml` roots this repository's `authoring` source at the relative `.plans`,
which the adopted store resolves against the directory of the document it read that root
from — and a planning launch's dispatches read the copy in a worktree of their own, so
the root each of them resolves is that worktree's. `scripts/plan-root-env.sh` is what
makes that root a configured fact instead of a guess: it resolves the source through
`orchestrator/plan_store.py`, refuses a root no plan could be authored into, and exports
the one name the plan store reads it back under, at the layer that beats a document.

These journeys drive that helper for real, in this checkout, because this checkout is
its subject: the value it composes is what *this* tree's plan store resolves, and a copy
of the script beside a temporary directory would be answering about neither. What varies
per journey is the environment, which is the whole of the helper's own input.

**Every journey here spawns the installed plan-store CLI**, which is what puts them in
this project rather than in the Python suite beside it: `plan-tooling` owns the host-tool
journeys over this repository's plan surface, and what a plan-authoring root resolves to
is the first thing a `just plan` launch asks that surface. The project's own key covers
what they read — the helper, the package it resolves through, the store configuration,
and the pin that decides which CLI is installed.

The one gate that reads no host tool lives elsewhere, under the workspace-wide key its
question needs: `tests/test_plan_root_composition.py` asks which tracked file composes
the name, and an answer keyed on this project's narrower set would miss a second
composition added outside it.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

import follow_up_variables
import plan_root_variable
import pytest
from published_tools import ONETASKGRAPH_BIN

from orchestrator import plan_store
from orchestrator.root import REPO_ROOT

#: The whole subject here is what *this checkout* resolves the `authoring` source to, and
#: every journey below compares the helper's exported answer against `plan_store`'s own.
#: The suite's autouse isolation would put one temporary root on both sides of that
#: comparison — and the helper keeps a root already in the environment by design, so a
#: launch that had stopped resolving anything at all would go on passing here.
pytestmark = pytest.mark.real_plan_store_roots(
    "authoring",
    reason=(
        "these journeys measure the resolution itself: the helper keeps a root already in "
        "the environment, so an isolated root would be compared against itself and a "
        "launch that resolved nothing would still pass"
    ),
)


def _drive(
    *, helper: Path | None = None, path: str | None = None, **environment: str
) -> subprocess.CompletedProcess[str]:
    """Source the helper as a launcher does, and report the value it exported.

    A curated environment rather than this process's, so what a journey measures is the
    input it stated. `PATH` carries this checkout's own `.venv/bin` by default, which is
    where the plan-store CLI the resolution spawns is installed, and `HOME` is kept
    because a resolution that could not find one would be answering about a different
    host. `helper` and `path` are for the one journey whose subject is a *different*
    checkout: what the helper resolves is decided by where the helper itself is.
    """
    sourced = helper or plan_root_variable.HELPER
    name = plan_root_variable.name()
    program = f'source "{sourced}"; export_plan_authoring_root test; printf "%s" "${{{name}-}}"'
    return subprocess.run(  # noqa: S603 - this repository's own helper, driven as a launcher does
        ["bash", "-c", program],
        cwd=REPO_ROOT,
        env={
            "PATH": path or f"{REPO_ROOT / '.venv' / 'bin'}:/usr/bin:/bin",
            "HOME": str(Path.home()),
            **environment,
        },
        text=True,
        capture_output=True,
        check=False,
    )


def test_the_helper_exports_the_root_this_checkouts_plan_store_resolves() -> None:
    """The value is the store's own answer, not a directory name joined onto a path."""
    driven = _drive()

    assert driven.returncode == 0, driven.stdout + driven.stderr
    exported = Path(driven.stdout)
    assert exported == plan_store.source_root("authoring"), (
        f"the helper exported {exported}, which is not the directory this checkout's plan "
        "store resolves the `authoring` source to, so a dispatch of a planning launch "
        "would author its plan where the launching checkout does not read"
    )
    assert exported.is_absolute(), (
        f"the helper exported the relative {exported}, which a dispatch would resolve "
        "against its own worktree rather than against the checkout that launched it"
    )


def test_a_root_that_does_not_exist_yet_is_made_rather_than_refused(tmp_path: Path) -> None:
    """A host that has never planned has no plan root, and its first launch makes one.

    The other half of the refusal below it: creating a missing root is what lets a fresh
    checkout launch at all, so a helper that treated absence as a fault would refuse
    every first launch — and one that treated an *uncreatable* root as absent would hand
    a planner a directory nothing can write.
    """
    unplanned = tmp_path / "never-planned" / "plans"

    driven = _drive(**{plan_root_variable.name(): str(unplanned)})

    assert driven.returncode == 0, driven.stdout + driven.stderr
    assert Path(driven.stdout) == unplanned, driven.stdout
    assert unplanned.is_dir(), (
        "the launch exported a plan-authoring root it had not made, so the first thing a "
        "dispatched planner writes there would fail"
    )


def test_a_root_already_in_the_environment_is_the_one_the_helper_leaves(tmp_path: Path) -> None:
    """A caller who pointed the root elsewhere keeps it: the helper resolves, never repoints."""
    elsewhere = tmp_path / "somebody-elses-plans"
    elsewhere.mkdir()

    driven = _drive(**{plan_root_variable.name(): str(elsewhere)})

    assert driven.returncode == 0, driven.stdout + driven.stderr
    assert Path(driven.stdout) == elsewhere, (
        f"the helper replaced a root its caller had already set with {driven.stdout!r}"
    )


def test_a_root_that_is_not_a_directory_is_refused_before_the_launch_goes_on(
    tmp_path: Path,
) -> None:
    """A refusal names the path and the variable, so an operator can repair one of the two."""
    occupied = tmp_path / "plans"
    occupied.write_text("not a plan store\n", encoding="utf-8")

    driven = _drive(**{plan_root_variable.name(): str(occupied)})

    assert driven.returncode == 2, driven.stdout + driven.stderr
    assert str(occupied) in driven.stderr, driven.stderr
    assert plan_root_variable.name() in driven.stderr, driven.stderr


def test_a_root_the_launch_may_not_write_into_is_refused_before_it_goes_on(
    tmp_path: Path,
) -> None:
    """The case a bare `mkdir` passes over: the directory is there and is read-only."""
    sealed = tmp_path / "plans"
    sealed.mkdir(mode=0o500)

    try:
        driven = _drive(**{plan_root_variable.name(): str(sealed)})
    finally:
        sealed.chmod(0o700)

    assert driven.returncode == 2, driven.stdout + driven.stderr
    assert str(sealed) in driven.stderr, driven.stderr
    assert plan_root_variable.name() in driven.stderr, driven.stderr


def test_a_root_that_cannot_be_created_is_refused_before_the_launch_goes_on(
    tmp_path: Path,
) -> None:
    """A root that is merely absent is made; one whose parent forbids it refuses the launch.

    The pair is what means anything: creating a missing root is what lets a host that has
    never planned launch at all, so the refusal has to be the case where creating it is
    the thing that failed rather than the case where it was simply not there.
    """
    sealed = tmp_path / "sealed"
    sealed.mkdir(mode=0o500)
    root = sealed / "plans"

    try:
        driven = _drive(**{plan_root_variable.name(): str(root)})
    finally:
        sealed.chmod(0o700)

    assert driven.returncode == 2, driven.stdout + driven.stderr
    assert str(root) in driven.stderr, driven.stderr
    assert "Permission denied" in driven.stderr, driven.stderr
    assert not root.exists(), "a root the helper reported it could not create is there"


def _a_checkout_of_its_own(tmp_path: Path) -> tuple[Path, Path]:
    """A checkout the helper can resolve, and a search path holding what it reaches for.

    Everything the resolution touches and nothing else: the helper, the package it
    imports, and the store configuration that declares the `authoring` source. The
    checkout carries no `.venv`, which is what makes the interpreter fallback the branch
    taken, and the search path carries this checkout's own interpreter under its plain
    name so that fallback lands somewhere real on any host. `bash` and `dirname` are on
    it too, because a journey that removes the interpreter needs a path holding
    everything the helper reaches for *but* it — otherwise the shell it is sourced by,
    or the resolution of the checkout it is in, is what fails instead.
    """
    checkout = tmp_path / "checkout"
    (checkout / "scripts").mkdir(parents=True)
    shutil.copy(plan_root_variable.HELPER, checkout / "scripts")
    # The package without its prose: what the resolution imports is code, and this
    # journey belongs to the tier whose cache key deliberately drops markdown, so
    # copying a document would be reading one.
    shutil.copytree(
        REPO_ROOT / "orchestrator",
        checkout / "orchestrator",
        ignore=shutil.ignore_patterns("*.md"),
    )
    shutil.copy(REPO_ROOT / "onetaskgraph.yaml", checkout)
    binaries = tmp_path / "bin"
    binaries.mkdir()
    reached = [ONETASKGRAPH_BIN, Path(sys.executable).with_name("python3")]
    for external in ("bash", "dirname"):
        found = shutil.which(external)
        assert found is not None, f"this host has no {external}, which the helper is run by"
        reached.append(Path(found))
    for tool in reached:
        (binaries / tool.name).symlink_to(tool)
    return checkout, binaries


def test_a_checkout_without_its_own_interpreter_resolves_its_own_root_anyway(
    tmp_path: Path,
) -> None:
    """A checkout with no `.venv` falls back to a path `python3`, and still answers its own root.

    Both halves are the claim. The fallback is what `scripts/plan.sh` itself does one
    resolution earlier, so a helper that refused here would make two lines of one script
    disagree about what an unprovisioned checkout is. And what it must not do is answer
    about a *neighbouring* checkout: the interpreter it falls back to is this checkout's,
    whose environment has this repository's own `orchestrator` installed, so a resolution
    that read the package from wherever the interpreter came from would report the
    launching checkout's root for a launch made from somewhere else entirely.

    Built as the checkout it stands in for: the helper, the package it imports, and the
    store configuration that roots the source, with the plan-store CLI on the path and
    no `.venv` of its own.
    """
    checkout, binaries = _a_checkout_of_its_own(tmp_path)

    driven = _drive(
        helper=checkout / "scripts" / plan_root_variable.HELPER.name,
        path=f"{REPO_ROOT / '.venv' / 'bin'}:{binaries}:/usr/bin:/bin",
    )

    assert not (checkout / ".venv").exists(), "this checkout was provisioned after all"
    assert driven.returncode == 0, driven.stdout + driven.stderr
    assert Path(driven.stdout) == checkout / ".plans", (
        f"a helper in {checkout} resolved {driven.stdout}, which is not that checkout's "
        "own plan-authoring root"
    )


def test_a_source_no_plan_could_be_stored_in_is_refused_naming_its_plugin(
    tmp_path: Path,
) -> None:
    """A checkout whose `authoring` source is served by a plugin that is not a directory.

    The launch would otherwise carry on and hand its planner a root for a source nothing
    can author a Markdown record into — a board is not a directory — so the refusal names
    the plugin, which is the thing the operator has to change.
    """
    checkout, binaries = _a_checkout_of_its_own(tmp_path)
    document = checkout / "onetaskgraph.yaml"
    document.write_text(
        document.read_text(encoding="utf-8").replace(
            "  authoring:\n    plugin: local-md\n    config:\n      root: .plans\n",
            "  authoring:\n    plugin: github-projects\n    config:\n"
            "      owner: nickderobertis\n      project_number: 2\n"
            "      repository: nickderobertis/ai-orchestrator\n"
            "      token_env: GH_PROJECTS_TOKEN\n",
        ),
        encoding="utf-8",
    )

    driven = _drive(
        helper=checkout / "scripts" / plan_root_variable.HELPER.name,
        path=f"{REPO_ROOT / '.venv' / 'bin'}:{binaries}:/usr/bin:/bin",
    )

    assert driven.returncode == 2, driven.stdout + driven.stderr
    assert "github-projects" in driven.stderr, driven.stderr
    assert str(document) in driven.stderr, driven.stderr


def test_a_checkout_with_no_interpreter_at_all_is_refused_rather_than_left_silent(
    tmp_path: Path,
) -> None:
    """When the fallback lands on nothing either, the launcher says so and stops.

    The branch below the fallback: `python3` names no executable on the path, so the
    resolution never runs. Under `set -e` alone that is a command substitution failing
    with the shell's own words and no repair, which is what this refuses instead.
    """
    checkout, binaries = _a_checkout_of_its_own(tmp_path)
    (binaries / "python3").unlink()

    driven = _drive(
        helper=checkout / "scripts" / plan_root_variable.HELPER.name,
        path=str(binaries),
    )

    assert driven.returncode == 2, driven.stdout + driven.stderr
    assert "could not be resolved to a writable root" in driven.stderr, driven.stderr
    assert plan_root_variable.name() in driven.stderr, driven.stderr
    # The checkout it resolved is still that checkout's, so what failed is the
    # interpreter and not the search for the tree the helper is in.
    assert str(checkout / "onetaskgraph.yaml") in driven.stderr, driven.stderr


def test_the_helper_exports_the_authoring_plugin_beside_the_root(tmp_path: Path) -> None:
    """A launch names the source whole, and keeps a plugin its caller already set."""
    plugin = plan_root_variable.plugin_name()
    program = (
        f'source "{plan_root_variable.HELPER}"; export_plan_authoring_root test'
        f'; printf "%s" "${{{plugin}-}}"'
    )
    root = {plan_root_variable.name(): str(tmp_path / "plans")}
    for inherited in ({}, {plugin: plan_store.WRITABLE_PLUGIN}):
        driven = subprocess.run(  # noqa: S603 - this repository's own helper, as a launcher runs it
            ["bash", "-c", program],  # noqa: S607 - bash from the search path, as every caller
            cwd=REPO_ROOT,
            env={
                "PATH": f"{REPO_ROOT / '.venv' / 'bin'}:/usr/bin:/bin",
                "HOME": str(Path.home()),
                **root,
                **inherited,
            },
            text=True,
            capture_output=True,
            check=False,
        )
        assert driven.returncode == 0, driven.stdout + driven.stderr
        assert driven.stdout == plan_store.WRITABLE_PLUGIN, (inherited, driven.stdout)


def test_an_inherited_authoring_plugin_no_plan_could_be_stored_in_is_refused(
    tmp_path: Path,
) -> None:
    """The helper keeps a caller's plugin, so one naming a board is refused before the launch."""
    driven = _drive(
        **{
            plan_root_variable.name(): str(tmp_path / "plans"),
            plan_root_variable.plugin_name(): "github-projects",
        }
    )

    assert driven.returncode == 2, driven.stdout + driven.stderr
    assert "github-projects" in driven.stderr, driven.stderr


#: A source a host's `.env` overlays with one setting and no plugin, as this host's does:
#: this checkout's `onetaskgraph.yaml` completes it, and a configuration that declares no
#: such source leaves it half a source.
PARTIAL_OVERLAY = "ONETASKGRAPH_SOURCES__HELLOPATIENT__CONFIG__TEAM"

#: The report a spike writes: the `spike-report` template's required answers, nothing more.
REPORT_ANSWERS = {
    "spike": "spike-cache",
    "branch": "spike-cache",
    "harness": "`sh bench.sh`.",
    "method": "Timed both reads over the same fixture.",
    "candidates": [],
    "findings": [],
}


class ReportWrite(NamedTuple):
    """A report write from another repository: the process, and where it should land."""

    written: subprocess.CompletedProcess[str]
    root: Path
    project: str


def _report_from_another_repository(tmp_path: Path, **environment: str) -> ReportWrite:
    """Launch as a planning launch does, then write a spike's report from another repository.

    The plan store's settings are the ones a planning launch exports — the authoring source
    (`scripts/plan.sh`), the drafts source (`scripts/onepipeline.sh`), the template root
    and the non-interactive setting (`scripts/dispatch-env.sh`) — each by its own helper,
    with both roots pointed at this journey's temporary directory.
    The project is made from this checkout, where the planner makes it; the report is
    written from a checkout of another repository, where a spike dispatched into that
    repository runs, by the template resolve piped into `document create` its turn runs.
    That checkout declares sources of its own and no `authoring`, so the source is whatever
    the launch's environment says it is and nothing else.
    """
    foreign = tmp_path / "another-repository"
    foreign.mkdir()
    # noqa: S607 - git from the search path, the one every checkout here is made with.
    subprocess.run(  # noqa: S603 - a fixed git verb over this journey's own temporary directory
        ["git", "init", "-q", str(foreign)],  # noqa: S607
        check=True,
    )
    (foreign / "onetaskgraph.yaml").write_text(
        "sources:\n  tickets:\n    plugin: local-md\n    config:\n      root: .tickets\n",
        encoding="utf-8",
    )
    answers = tmp_path / "report.json"
    answers.write_text(json.dumps(REPORT_ANSWERS), encoding="utf-8")
    project = f"test-{tmp_path.name}"
    scripts = plan_root_variable.HELPER.parent
    program = (
        f'set -euo pipefail; source "{plan_root_variable.HELPER}"; export_plan_authoring_root test'
        f'; source "{scripts / "follow-up-env.sh"}"; export_follow_up_drafts test'
        f'; source "{scripts / "template-env.sh"}"; export_template_root test'
        "; export_noninteractive_plan_store test"
        '; "$1" project create authoring --id "$3" --title "$3" --json >/dev/null'
        '; template=$("$2" template resolve spike-report --json)'
        '; cd "$4"'
        '; printf "%s" "$template" | "$1" document create authoring --project "$3"'
        ' --title "Cache spike" --id spike-cache-report --answers "$5"'
        " --template-loader - --no-interactive --json"
    )
    written = subprocess.run(  # noqa: S603 - this repository's own helper and the pinned CLIs
        [
            "bash",
            "-c",
            program,
            "report",
            str(ONETASKGRAPH_BIN),
            str(REPO_ROOT / ".venv" / "bin" / "onepipeline"),
            project,
            str(foreign),
            str(answers),
        ],
        cwd=REPO_ROOT,
        env={
            "PATH": f"{REPO_ROOT / '.venv' / 'bin'}:/usr/bin:/bin",
            "HOME": str(Path.home()),
            plan_root_variable.name(): str(tmp_path / "plans"),
            follow_up_variables.root_name(): str(tmp_path / "drafts"),
            **environment,
        },
        text=True,
        capture_output=True,
        check=False,
    )
    return ReportWrite(written=written, root=tmp_path / "plans", project=project)


def test_a_spike_in_another_repository_writes_its_report_into_the_plans_own_project(
    tmp_path: Path,
) -> None:
    """The launch names the `authoring` source whole, so a foreign worktree can write to it.

    Exporting the root alone handed such a dispatch half a source, which the store refuses
    as `sources.authoring: missing field plugin` before writing anything.
    """
    report = _report_from_another_repository(tmp_path)
    written, root, project = report.written, report.root, report.project

    assert written.returncode == 0, written.stdout + written.stderr
    listed = subprocess.run(  # noqa: S603 - the pinned plan-store CLI, read as the manager reads
        [
            str(ONETASKGRAPH_BIN),
            "document",
            "list",
            "--source",
            "authoring",
            "--project",
            project,
            "--json",
        ],
        cwd=REPO_ROOT,
        env={
            "PATH": f"{REPO_ROOT / '.venv' / 'bin'}:/usr/bin:/bin",
            "HOME": str(Path.home()),
            plan_root_variable.name(): str(root),
        },
        text=True,
        capture_output=True,
        check=False,
    )
    assert listed.returncode == 0, listed.stdout + listed.stderr
    held = [str(item["id"]) for item in json.loads(listed.stdout)["items"]]
    assert held == ["authoring:spike-cache-report"], (
        f"the plan's own project holds {held}, not the report the spike wrote from another "
        "repository's worktree"
    )


def test_a_source_overlaid_without_its_plugin_is_left_for_the_store_to_refuse(
    tmp_path: Path,
) -> None:
    """Only `authoring` is completed: a half-overlaid source stays the plan store's refusal.

    Completing it here would mean a second copy of that source's definition, so the
    launch leaves the overlay as it found it, exports no plugin for it, and the write is
    refused naming that source. The refusal stands until the store stops rejecting an
    overlay of a source the command never names.
    """
    written = _report_from_another_repository(tmp_path, **{PARTIAL_OVERLAY: "ENG"}).written

    assert written.returncode != 0, written.stdout + written.stderr
    assert "sources.hellopatient: missing field `plugin`" in written.stderr, written.stderr
    assert "sources.authoring" not in written.stderr, written.stderr
