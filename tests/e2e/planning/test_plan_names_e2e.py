"""`just plan` and `just finish-plan` store names that open with what the plan is about.

The navigation lists runs and projects by their stored names, often truncated after a couple
of dozen characters, so a planning or finishing run whose name opens with the plan's slug
cannot be told from its siblings. Each recipe titles the project it creates, and names the
flow it launches it in, from the plan's own title followed by one word: `plan` takes that
title from the brief's H1, and `finish-plan` from the plan project's stored title. A flow
name takes only letters, digits, `.`, `_` and `-`, so the flow is named for the title with
every other character a `-`. Project ids keep their slug-and-timestamp form, because no view
lists them.

The recipes, their wrapper scripts and the shell are real, and so is the plan store: the
projects read back here are the ones the pinned `onetaskgraph` created from this checkout's
own `templates/planning/`. The published CLIs the store does not stand in for are doubled at
their boundary: the engine, which records each call's argument vector one word to an entry
and runs a flow's program as `flow run` does, and `llmlint`, which prints the checklist
`just plan` resolves.

llmlint: ignore-file[e2e_not_mocked] The engine is doubled at the published CLI's boundary,
since a real `onepipeline start` would launch agents; what it records is the argument vector
the recipe handed it, which is the flow name under test.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import NamedTuple

import plan_root_variable
import pytest
from delegation_checkout import ENGINE, PINNED_ENGINE, run_recipe
from delegation_checkout import at as engine_at
from delegation_checkout import delegation_checkout as checkout_for
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

TITLE = "Make run names readable"
NATIVE = "run-names-readable-2026-11-16"
PLAN = f"authoring:{NATIVE}"
BRIEF = "briefs/run-names-readable.md"

#: The engine: one JSON argument vector per call, the release the pin names on `--version`,
#: and a flow's program run after its `--`, as the engine's `flow run` runs it — refusing, as
#: the pinned engine does, a `--name` outside the characters a flow id is minted from.
ARGV_RECORDING_ENGINE = f"""#!/usr/bin/env python3
import json, os, re, sys

argv = sys.argv[1:]
with open(os.environ["TRACE_FILE"], "a", encoding="utf-8") as trace:
    trace.write(json.dumps(argv) + "\\n")
match argv:
    case ["--version"]:
        print("onepipeline {PINNED_ENGINE}")
    case ["flow", "run", *rest]:
        if not re.fullmatch(r"[A-Za-z0-9._-]+", rest[rest.index("--name") + 1]):
            sys.exit("error: --name takes letters, digits, `.`, `_` and `-`; nothing was run")
        program = rest[rest.index("--") + 1 :]
        os.execvp(program[0], program)
"""

#: `llmlint rules`, answering the checklist `just plan` composes into the draft's task.
CHECKLIST_LLMLINT = """#!/usr/bin/env bash
printf -- '- **plan_rule**: the plan is written to the checklist.\\n'
"""


def _brief(title: str | None) -> str:
    heading = f"# {title}\n\n" if title is not None else ""
    return (
        f"{heading}Plan project: {PLAN}\n\n"
        "## What\n\nName every run by what its plan changes.\n\n"
        "## Why\n\nA supervisor picks a run out of a truncated list by its first words.\n"
    )


class Bench(NamedTuple):
    """A checkout running the real recipes over the real store, with the engine traced."""

    checkout: Path
    trace: Path
    environment: dict[str, str]


@pytest.fixture
def bench(tmp_path: Path) -> Bench:
    checkout, trace = checkout_for(tmp_path)
    shutil.copytree(REPO_ROOT / "templates/planning", checkout / "templates/planning")
    store = checkout / ".venv/bin/onetaskgraph"
    store.write_text(f'#!/bin/sh\nexec {REPO_ROOT / ".venv/bin/onetaskgraph"} "$@"\n')
    store.chmod(0o755)
    llmlint = checkout / "bin/llmlint"
    llmlint.write_text(CHECKLIST_LLMLINT)
    llmlint.chmod(0o755)
    Path(engine_at(checkout, ENGINE)).write_text(ARGV_RECORDING_ENGINE)
    (checkout / ".plans").mkdir()
    config_home = tmp_path / "config-home"
    config_home.mkdir()
    environment = {
        plan_root_variable.plugin_name(): "local-md",
        # No user layer of this host's reaches the store: only the authoring root above.
        "XDG_CONFIG_HOME": str(config_home),
    }
    return Bench(checkout, trace, environment)


def _just(bench: Bench, *arguments: str) -> subprocess.CompletedProcess[str]:
    return run_recipe(bench.checkout, bench.trace, *arguments, env=bench.environment)


def _store(bench: Bench, *arguments: str) -> dict[str, object]:
    """The real store's JSON answer, over the checkout's authoring root."""
    environment = {
        **os.environ,
        **bench.environment,
        plan_root_variable.name(): str(bench.checkout / ".plans"),
    }
    answered = subprocess.run(  # noqa: S603 - the pinned store this checkout installs
        [str(REPO_ROOT / ".venv/bin/onetaskgraph"), *arguments, "--json", "--no-interactive"],
        cwd=bench.checkout,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(120),
        check=False,
    )
    assert answered.returncode == 0, answered.stdout + answered.stderr
    found = json.loads(answered.stdout)
    assert isinstance(found, dict), found
    return found


class Created(NamedTuple):
    """A project a recipe created, as the store holds it."""

    project: str
    title: str


def _created(bench: Bench, kind: str) -> Created:
    """The one `<kind>` project the recipe created: its qualified id and its stored title."""
    listed = _store(bench, "project", "list", "--source", "authoring")
    found = [
        Created(str(one["id"]), str(one["item"]["title"]))
        for one in listed["items"]
        if f"-{kind}-" in str(one["id"])
    ]
    assert len(found) == 1, listed
    return found[0]


def _engine_calls(bench: Bench) -> list[list[str]]:
    if not bench.trace.is_file():
        return []
    return [json.loads(line) for line in bench.trace.read_text(encoding="utf-8").splitlines()]


def _flow_name(bench: Bench) -> str:
    flows = [call for call in _engine_calls(bench) if call[:2] == ["flow", "run"]]
    assert len(flows) == 1, _engine_calls(bench)
    return flows[0][flows[0].index("--name") + 1]


def _started(bench: Bench) -> list[str]:
    return [call[1] for call in _engine_calls(bench) if call[:1] == ["start"]]


def test_plan_titles_its_planning_project_and_flow_with_the_briefs_h1(bench: Bench) -> None:
    (bench.checkout / BRIEF).write_text(_brief(TITLE), encoding="utf-8")

    planned = _just(bench, "plan", BRIEF)

    assert planned.returncode == 0, planned.stdout + planned.stderr
    project, title = _created(bench, "planning")
    assert title == f"{TITLE} planning"
    assert _flow_name(bench) == "Make-run-names-readable-planning"
    assert re.fullmatch(rf"authoring:{NATIVE}-planning-\d{{14}}", project), project
    assert _started(bench) == [project]
    # The draft's task names the title the planner gives the plan and its spikes project.
    members = _store(bench, "task", "list", "--source", "authoring", "--project", project)
    (draft,) = [
        one for one in members["items"] if one["item"]["metadata"]["onepipeline.id"] == "draft"
    ]
    shown = _store(bench, "task", "show", str(draft["id"]))
    task = " ".join(str(shown["items"][0]["item"]["content"]).split())
    assert f"titled with the brief's H1, `{TITLE}`" in task, task
    assert f"title it with the plan's title plus ` spikes`, `{TITLE} spikes`" in task, task


def test_plan_refuses_a_brief_with_no_title_before_it_creates_or_launches_anything(
    bench: Bench,
) -> None:
    (bench.checkout / BRIEF).write_text(_brief(None), encoding="utf-8")

    refused = _just(bench, "plan", BRIEF)

    assert refused.returncode == 2, refused.stdout + refused.stderr
    assert f"plan: {BRIEF} has no '# <title>' heading" in refused.stderr, refused.stderr
    assert _store(bench, "project", "list", "--source", "authoring")["items"] == []
    assert _engine_calls(bench) == []


def test_finish_plan_titles_its_finishing_project_and_flow_with_the_plans_title(
    bench: Bench,
) -> None:
    _store(bench, "project", "create", "authoring", "--id", NATIVE, "--title", TITLE)

    finished = _just(bench, "finish-plan", PLAN)

    assert finished.returncode == 0, finished.stdout + finished.stderr
    project, title = _created(bench, "finishing")
    assert title == f"{TITLE} finishing"
    assert _flow_name(bench) == "Make-run-names-readable-finishing"
    assert re.fullmatch(rf"authoring:{NATIVE}-finishing-\d{{14}}", project), project
    assert _started(bench) == [project]


@pytest.mark.parametrize(
    "name",
    [f"{TITLE} planning", "Make-run-names-readable-planning", "Make: run, names", "a.b_c-d"],
)
def test_the_engine_double_refuses_a_flow_name_exactly_when_the_pinned_engine_does(
    tmp_path: Path, name: str
) -> None:
    """The double's flow-name check is held to the pinned engine's own, name by name.

    The engine checks a name only for a flow some session owns, so each call names one; the
    flow it registers lives under the throwaway runs root this test names.
    """
    double = tmp_path / "engine"
    double.write_text(ARGV_RECORDING_ENGINE)
    double.chmod(0o755)
    environment = {**os.environ, "TRACE_FILE": str(tmp_path / "trace"), "HOME": str(tmp_path)}
    for inherited in ("ONEPIPELINE_LAUNCHER_SESSION", "ONEPIPELINE_FLOW", "ONEPIPELINE_LAUNCHER"):
        environment.pop(inherited, None)
    environment["XDG_STATE_HOME"] = str(tmp_path / "state")
    environment["ONEPIPELINE_RUNS_DIR"] = str(tmp_path / "runs")
    answers = [
        subprocess.run(  # noqa: S603 - the pinned engine and its double, each running `true`
            [engine, "flow", "run", "--session", "names-probe", "--name", name, "--", "true"],
            env=environment,
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=e2e_timeout(60),
            check=False,
        ).returncode
        == 0
        for engine in (engine_at(REPO_ROOT, ENGINE), str(double))
    ]
    assert answers[0] == answers[1], (name, answers)
