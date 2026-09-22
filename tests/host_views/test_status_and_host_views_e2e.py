"""What `just status` and `just host` say about the machine a run is running in.

Both recipes are the engine's views passed through untouched: `onepipeline status` and
`onepipeline host` print a `free space:` line per filesystem the runs root and the
lifecycle workspaces are on, above the `providers:` block `oneagentgraph health`
contributes. That placement is what `AGENTS.md`'s watch property 6 rests on — a
supervisor cuts `just status` at `providers:` and reads only what is above it — so these
journeys hold the recipes to printing the engine's line there, and to printing nothing
of this repository's beside the engine's view.

The engine is the real one and so are the recipes, the wrapper script and the shell: a
view reaches no model, and a doubled engine would prove nothing about a rendering. The run
roots are built rather than recorded for the reason `tests/e2e/probe_run_root.py` gives,
and one journey renders both views over a root a real `just orchestrate` wrote, which is
the control that makes the others statements about a shape the engine really produces.

llmlint: ignore-file[tool_output_is_signal] the rendered view is these viewing
commands' whole product, so the assertions are on what they printed.

llmlint: ignore-file[e2e_not_mocked] One journey — the real-launch control — points the
launch at `tests/e2e/fake_backend.py` instead of a paid provider, which is the one double
this repository's own invariant allows. Everything the launch then does is real: the
recipe, the wrapper script, the engine, the plan store, the run root it writes.

llmlint: ignore-file[tests_mirror_real_usage] Every journey but one reads a run root
`tests/e2e/probe_run_root.py` builds, because a view over a run is the subject and a launch
per journey would spend a whole run to give it one. The builder's records are reconciled
field by field against the installed engine by `tests/test_engine_contracts.py`, and
`test_the_views_answer_over_a_run_root_a_real_launch_wrote` renders both views over a root
`just orchestrate` wrote — the control that makes the others statements about a shape the
engine really produces.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from example_records import isolated_examples
from probe_run_root import Probe, run_name, run_root
from project_fixtures import helper
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: The paid provider's stand-in the real-launch control points the dispatch at, resolved
#: through `tests/e2e/project_fixtures.py` rather than from this module's own directory:
#: a stand-in derived from `__file__` stops existing the moment the module moves, and a
#: paid provider's stand-in that does not exist routes the turn to the real identity and
#: spends it.
FAKE_BACKEND = helper("fake_backend.py")

#: Where `just status`'s run-scoped lines stop and the host-wide provider report
#: begins. `AGENTS.md` tells a supervisor to cut a watch here before matching words in
#: what is above it, so a line below it is one no watch following that guidance sees.
PROVIDERS = "  providers:"

#: The engine's free-space line, indented as a run's own block is — the reading watch
#: property 6 depends on finding above `PROVIDERS`.
FREE_SPACE_LINE = "  free space: "

#: Durations and sizes move between two invocations seconds apart, and the comparison
#: journeys below run each view twice — once through the engine and once through the
#: recipe. Normalising them is what makes that comparison about the *lines* rather than
#: about how long the second one took or what the disk held a moment later.
DURATION = re.compile(r"\b\d+(?:h\d+m|m\d+s|[hms])\b")
FREE_SPACE = re.compile(r"\b\d+(?:\.\d+)? [KMGTP]?i?B\b|\b\d+(?:\.\d+)?% free\b")

#: The one thing a *reading* verb may leave behind: the fold checkpoint the engine
#: resumes a read from instead of replaying a run's whole journal. It is derived, it is
#: the run's own and goes when the run does, so a view that writes one has not changed
#: the run's record of itself — the property the last journey here is about.
#: `tests/test_engine_contracts.py` reconciles the name against the engine's `RunPaths`.
DERIVED_BY_A_READER = frozenset({"checkpoint.json"})


def _stable(line: str) -> str:
    """``line`` with every reading that moves between two invocations normalised."""
    return FREE_SPACE.sub("<size>", DURATION.sub("<age>", line))


def _environment(runs_root: Path) -> dict[str, str]:
    """The ambient values a supervisor's read reaches these recipes with."""
    environment = dict(os.environ)
    environment["ONEPIPELINE_RUNS_DIR"] = str(runs_root)
    # `scripts/onepipeline.sh` derives the reading session's identity from the harness
    # variable a real manager session carries, and ownership is a comparison — a view
    # that did not identify itself matches no run.
    environment["CLAUDE_CODE_SESSION_ID"] = "status-and-host-views-e2e"
    return environment


def _view(
    recipe: str, *arguments: str, runs_root: Path, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """One of the two views, through the real recipe."""
    return subprocess.run(
        ["just", recipe, *arguments],
        cwd=REPO_ROOT,
        env={**_environment(runs_root), **(env or {})},
        capture_output=True,
        text=True,
        timeout=e2e_timeout(180),
        check=False,
    )


def _engine(verb: str, *arguments: str, runs_root: Path) -> subprocess.CompletedProcess[str]:
    """The same view straight from the published verb."""
    environment = dict(os.environ)
    environment["ONEPIPELINE_RUNS_DIR"] = str(runs_root)
    return subprocess.run(
        ["uv", "run", "onepipeline", verb, *arguments],
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=e2e_timeout(180),
        check=False,
    )


def _above_providers(rendered: str) -> list[str]:
    """The run-scoped half of a view: everything above the line a watch is cut at."""
    lines = rendered.splitlines()
    for index, line in enumerate(lines):
        if line.startswith(PROVIDERS):
            return lines[:index]
    return lines


def _free_space(lines: list[str]) -> list[str]:
    return [line for line in lines if line.startswith(FREE_SPACE_LINE)]


def _assert_free_space_above_the_cut(rendered: str, view: str, runs_root: Path) -> None:
    """The engine's free-space line is printed, names the runs root, and sits above the cut."""
    everything = _free_space(rendered.splitlines())
    assert everything, (
        f"`just {view}` printed no `free space:` line, which the adopted engine prints for "
        f"the filesystem the runs root is on; it printed {rendered!r}"
    )
    assert any(str(runs_root) in line and "free)" in line for line in everything), (
        f"no free-space line names the runs root {runs_root} and how much of it is free: "
        f"{everything}"
    )
    assert _free_space(_above_providers(rendered)) == everything, (
        "a free-space line sits below the `providers:` line, which is where this "
        "repository's own guidance tells a supervisor to cut this view — no watch that "
        f"follows that guidance would see it: {rendered!r}"
    )


@pytest.fixture
def probe(tmp_path: Path) -> Probe:
    """A runs root holding one run, private to the journey that asked for it."""
    root = tmp_path / "runs"
    run = run_name()
    run_root(root, run)
    return Probe(root=root, run=run)


def test_the_status_view_reports_free_space_above_the_line_a_watch_is_cut_at(
    probe: Probe,
) -> None:
    """The resource whose exhaustion stops everything, where a supervisor can see it."""
    rendered = _view("status", probe.run, runs_root=probe.root)

    assert rendered.returncode == 0, rendered.stderr
    assert PROVIDERS in rendered.stdout, (
        "the status view's own provider report is the boundary a watch is told to cut at, "
        f"and it is not in what the view printed: {rendered.stdout!r}"
    )
    _assert_free_space_above_the_cut(rendered.stdout, "status", probe.root)


def test_the_host_view_reports_free_space_above_any_provider_report(probe: Probe) -> None:
    """The whole-host inventory carries the same line, above anything cut away."""
    rendered = _view("host", runs_root=probe.root)

    assert rendered.returncode == 0, rendered.stderr
    _assert_free_space_above_the_cut(rendered.stdout, "host", probe.root)


@pytest.mark.parametrize(
    ("verb", "takes_run_id"), [("status", True), ("host", False)], ids=("status", "host")
)
def test_each_recipe_prints_the_engine_s_run_scoped_view_and_nothing_else(
    probe: Probe, verb: str, takes_run_id: bool
) -> None:
    """Every line above the cut is the published view's, in order, with nothing added.

    What makes the free-space line the engine's rather than a filter's of this
    repository's: above `providers:`, which is where every reading of this repository's
    used to be added, the recipe's output is the engine's line for line. Below it is
    `oneagentgraph health`'s host report, stamped with the moment it was observed, so two
    invocations differ there by construction and are not compared.
    """
    named = (probe.run,) if takes_run_id else ()
    engine = _engine(verb, *named, runs_root=probe.root)
    recipe = _view(verb, *named, runs_root=probe.root)

    assert engine.returncode == 0, engine.stderr
    assert recipe.returncode == 0, recipe.stderr
    published = [_stable(line) for line in _above_providers(engine.stdout)]
    printed = [_stable(line) for line in _above_providers(recipe.stdout)]
    assert printed == published, (
        f"`just {verb}` printed something other than `onepipeline {verb}`'s own lines: "
        f"engine {engine.stdout!r}, recipe {recipe.stdout!r}"
    )


def test_a_refused_view_is_still_a_refusal(probe: Probe) -> None:
    """The published verb's refusal is carried out whole: its status, and an empty view."""
    refused = _view("status", "no-such-run", runs_root=probe.root)

    assert refused.returncode != 0, refused.stdout
    assert "no such run" in refused.stderr, refused.stderr
    assert refused.stdout.strip() == "", refused.stdout


def test_the_views_answer_over_a_run_root_a_real_launch_wrote(tmp_path: Path) -> None:
    """The free-space line over a runs root nothing here composed.

    The control for the built roots above: a real `just orchestrate` writes the run root,
    and both views answer over it exactly as they answer over the built ones — so a
    built root that stopped resembling what the engine writes fails here rather than
    quietly making every assertion above a statement about a shape nobody produces.

    `examples/` is in this project's key for this journey alone: the project it launches
    is a record there, copied before the launch so the tracked one stays as it was.
    """
    runs = tmp_path / "runs"
    # Launched from a copy, because the run writes its settlements back to its project;
    # the block's exit holds the tracked records unchanged.
    with isolated_examples(tmp_path) as examples:
        environment = {
            **_environment(runs),
            **examples.environment,
            "ONEAGENTGRAPH_ONEHARNESS_BIN": str(FAKE_BACKEND),
            "REAL_ONEHARNESS_BIN": shutil.which("oneharness") or "oneharness",
            "XDG_STATE_HOME": str(tmp_path / "state"),
        }
        launched = subprocess.run(
            ["just", "orchestrate", "examples:scheduler-research", "--detach"],
            cwd=REPO_ROOT,
            env=environment,
            text=True,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=e2e_timeout(300),
            check=False,
        )
        assert launched.returncode == 0, f"{launched.stdout}\n{launched.stderr}"
        written = [run.name for run in runs.iterdir() if (run / "launch.json").is_file()]
        assert written, f"the launch recorded no run under {runs}"
        try:
            status = _view("status", written[0], runs_root=runs)
            host = _view("host", runs_root=runs)

            assert status.returncode == 0, status.stderr
            assert host.returncode == 0, host.stderr
            _assert_free_space_above_the_cut(status.stdout, "status", runs)
            _assert_free_space_above_the_cut(host.stdout, "host", runs)
        finally:
            # Detached, so nothing else ends it. With the launch's own environment, because
            # stopping writes the settlement back to whichever root that names.
            _view("stop", written[0], runs_root=runs, env=environment)


def test_neither_view_changes_the_run_s_own_record_of_itself(probe: Probe) -> None:
    """Both views leave the run's record alone, which is what makes them safe beside live work."""
    before = _fingerprint(probe.root)

    assert _view("status", probe.run, runs_root=probe.root).returncode == 0
    assert _view("host", runs_root=probe.root).returncode == 0

    assert _fingerprint(probe.root) == before, (
        "these views are run beside live work and must leave the run's own record of "
        "itself exactly as they found it; something under the runs root that is not a "
        f"derived {'/'.join(sorted(DERIVED_BY_A_READER))} changed while they reported on it"
    )
    # The other half, and what keeps the exclusion above from being vacuous: the engine
    # really does write that record when a reader folds a run's state.
    left = {path.name for path in probe.root.rglob("*") if path.is_file()}
    assert left & DERIVED_BY_A_READER, (
        f"reading this run left no {'/'.join(sorted(DERIVED_BY_A_READER))} behind, so "
        "the exclusion above is excluding nothing"
    )


def _fingerprint(root: Path) -> dict[str, bytes]:
    """Every recorded file under a runs root, by path and by content, derived records aside."""
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.name not in DERIVED_BY_A_READER
    }
