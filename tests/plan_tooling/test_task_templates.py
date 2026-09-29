"""This host's task templates, as the pinned engine and plan store read them.

`templates/` is the host root: `templates.yaml` registers this host's own names,
`plan-task.md.j2` overrides the engine's built-in `plan-task`, `follow-up-task.md.j2`
supplies the follow-up agent's task, and `dispatch-appendix.md` is the operational notes
the plan-task template includes. Every check here runs the pinned
`onepipeline template` and `onetaskgraph` verbs for real, in the environment
`scripts/dispatch-env.sh` establishes for a launch — the one a dispatch is handed — so
what is asserted is what a planner's `resolve | task create` would meet.

Two sides of one ruling are held here too: every automated path is non-interactive on its
own terms, and nothing tracked changes what a person's own `onetaskgraph` does in this
checkout, which stays interactive. And `scripts/plan-brief.sh`, which is shell and cannot
render anything, keeps a copy of the sections a task has; it is reconciled here against a
real rendering.

These run the host's installed tools, so they sit in the `plan-tooling` project, keyed on
the templates, scripts and package they read.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

from orchestrator import criteria_guard, plan_store
from orchestrator.root import REPO_ROOT

ROOT = REPO_ROOT / "templates"
REGISTRATION = ROOT / "templates.yaml"
PLAN_TASK = ROOT / "plan-task.md.j2"
#: The follow-up agent's task, which this host registers and supplies at its own layer.
FOLLOW_UP = ROOT / "follow-up-task.md.j2"
APPENDIX = ROOT / "dispatch-appendix.md"
ENGINE = REPO_ROOT / ".venv" / "bin" / "onepipeline"
PLAN_STORE = REPO_ROOT / ".venv" / "bin" / "onetaskgraph"
DISPATCH_ENV = REPO_ROOT / "scripts" / "dispatch-env.sh"

#: The helper both planning launches read a manager's brief through, and its copy of the
#: sections a brief — which IS the dispatched task — must have.
PLAN_BRIEF = REPO_ROOT / "scripts" / "plan-brief.sh"
DECLARED_SECTIONS = re.compile(r"PLAN_REQUIRED_SECTIONS=\(([^)]*)\)")

#: The base every role-`task` template extends, as the engine embeds it.
BASE = "onepipeline/plan-task.md.j2"

#: A task's answers, one of each shape the template takes: a paragraph break inside a text
#: answer, a criterion that runs to a second line, and notes of the task's own.
ANSWERS: dict[str, object] = {
    "what": "Build the thing.\n\nIt lives beside the other thing.",
    "why": "The user asked for the thing in their own words.",
    "acceptance_criteria": [
        "The thing is built.",
        "The thing is proven end to end\nby a journey that drives it.",
    ],
    "additional_info": "The change request may be published early.",
}


def _launch_environment(home: Path) -> dict[str, str]:
    """What `scripts/dispatch-env.sh` builds a launch's environment into, read back whole.

    The resolvers run, then the whitelist is applied, exactly as `scripts/onepipeline.sh`'s
    launch arms do, over a shell holding nothing but a home, a search path and a launching
    shell's own answer for each template name — which the launch replaces.
    """
    script = (
        f'. "{DISPATCH_ENV}"\n'
        "export_dispatch_environment probe\n"
        "construct_dispatch_environment probe\n"
        "env -0\n"
    )
    home.mkdir(parents=True, exist_ok=True)
    built = subprocess.run(
        ["bash", "-c", script],
        env={
            "HOME": str(home),
            "PATH": os.environ["PATH"],
            "ONEPIPELINE_TEMPLATE_ROOT": str(home / "somebody-elses-templates"),
            "ONETASKGRAPH_INTERACTIVE": "true",
        },
        cwd=home,
        capture_output=True,
        check=False,
    )
    assert built.returncode == 0, built.stderr.decode()
    pairs = (entry.split("=", 1) for entry in built.stdout.decode().split("\0") if entry)
    return {name: value for name, value in pairs}


@pytest.fixture(scope="module")
def launch_environment(tmp_path_factory: pytest.TempPathFactory) -> dict[str, str]:
    return _launch_environment(tmp_path_factory.mktemp("launch") / "home")


def _run(
    command: list[str], environment: dict[str, str], stdin: str | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        env=environment,
        cwd=REPO_ROOT,
        input=stdin,
        text=True,
        capture_output=True,
        check=False,
    )


def _loader(environment: dict[str, str]) -> str:
    """The loader document `onepipeline template resolve plan-task --json` states."""
    resolved = _run([str(ENGINE), "template", "resolve", "plan-task", "--json"], environment)
    assert resolved.returncode == 0, resolved.stderr
    return resolved.stdout


def _render(environment: dict[str, str], answers: dict[str, object], tmp_path: Path) -> str:
    """One rendering, through the SDK every reader in `orchestrator/` goes through."""
    loader = tmp_path / "loader.json"
    loader.write_text(_loader(environment), encoding="utf-8")
    rendered = plan_store.sdk(
        plan_store.client().template_render(template_loader=str(loader), answers=answers)
    )
    return rendered.body


def test_the_host_root_holds_the_registration_the_template_and_the_one_appendix() -> None:
    """`templates/` is the root, and the appendix it includes exists nowhere else."""
    assert REGISTRATION.is_file() and PLAN_TASK.is_file() and APPENDIX.is_file()
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=REPO_ROOT, text=True, capture_output=True, check=True
    ).stdout.splitlines()
    copies = [path for path in tracked if Path(path).name == APPENDIX.name]
    assert copies == ["templates/dispatch-appendix.md"], copies
    assert Path("templates") / "dispatch-appendix.md" == criteria_guard.APPENDIX


def test_the_launch_environment_names_this_checkouts_root_and_never_prompts(
    launch_environment: dict[str, str],
) -> None:
    """Both names are the launch's own, over whatever the launching shell carried."""
    assert launch_environment["ONEPIPELINE_TEMPLATE_ROOT"] == str(ROOT)
    assert launch_environment["ONETASKGRAPH_INTERACTIVE"] == "false"


def test_the_engine_lists_this_hosts_names_through_the_launch_environment(
    launch_environment: dict[str, str],
) -> None:
    """`plan-task` from the host layer, and the two names only this host registers."""
    listed = _run([str(ENGINE), "template", "list", "--json"], launch_environment)

    assert listed.returncode == 0, listed.stderr
    document = json.loads(listed.stdout)
    assert document["registration"] == str(REGISTRATION)
    by_name = {entry["name"]: entry for entry in document["templates"]}
    assert set(by_name) == {"plan-task", "follow-up-task", "design-doc"}, by_name
    assert (by_name["plan-task"]["layer"], by_name["plan-task"]["path"]) == (
        "host",
        str(PLAN_TASK),
    )
    assert by_name["follow-up-task"]["role"] == "task"
    assert by_name["design-doc"]["role"] == "document"
    for name in ("follow-up-task", "design-doc"):
        assert by_name[name]["description"].strip(), by_name[name]


@pytest.mark.parametrize(
    ("name", "path"), [("plan-task", PLAN_TASK), ("follow-up-task", FOLLOW_UP)]
)
def test_the_engine_resolves_and_passes_each_host_template_through_the_launch_environment(
    launch_environment: dict[str, str], name: str, path: Path
) -> None:
    """Each task template this host supplies answers from the host layer, and is valid."""
    resolved = _run([str(ENGINE), "template", "resolve", name, "--json"], launch_environment)
    assert resolved.returncode == 0, resolved.stderr
    loader = json.loads(resolved.stdout)
    assert (loader["layer"], loader["reference"], loader["path"]) == (
        "host",
        f"onepipeline:{name}",
        str(path),
    )

    checked = _run([str(ENGINE), "template", "check", name], launch_environment)
    assert checked.returncode == 0, checked.stderr
    assert "host layer" in checked.stdout, checked.stdout


def test_a_registered_name_no_layer_supplies_is_refused_by_name(
    launch_environment: dict[str, str], tmp_path: Path
) -> None:
    """A root registering a name it holds no file for says so, naming it, rather than guessing."""
    root = tmp_path / "templates"
    root.mkdir()
    (root / REGISTRATION.name).write_text(
        "onepipeline_templates: 1\ntemplates:\n  unfilled-task:\n    role: task\n"
        "    description: A name registered with no file under this root.\n",
        encoding="utf-8",
    )

    refused = _run(
        [str(ENGINE), "template", "resolve", "unfilled-task", "--template-root", str(root)],
        launch_environment,
    )

    assert refused.returncode == 2, refused.stdout
    assert "no template for unfilled-task" in refused.stderr, refused.stderr


def test_the_template_extends_the_engines_base_and_opens_no_criteria_heading_itself() -> None:
    """The one criteria heading a task carries is the base's, so it cannot be repeated."""
    source = PLAN_TASK.read_text(encoding="utf-8")

    assert f'{{% extends "{BASE}" %}}' in source
    assert "## Acceptance criteria" not in source
    assert '{% include "dispatch-appendix.md" %}' in source


def test_the_variables_are_the_three_this_host_declares_and_the_inherited_criteria(
    launch_environment: dict[str, str],
) -> None:
    listed = _run(
        [str(PLAN_STORE), "template", "variables", "--template-loader", "-", "--json"],
        launch_environment,
        stdin=_loader(launch_environment),
    )

    assert listed.returncode == 0, listed.stderr
    variables = {one["name"]: one for one in json.loads(listed.stdout)["variables"]}
    assert set(variables) == {"what", "why", "additional_info", "acceptance_criteria"}
    for name in ("what", "why"):
        assert (variables[name]["type"], variables[name]["required"]) == ("text", True)
    assert variables["additional_info"]["required"] is False
    assert variables["additional_info"]["default"] == ""
    assert variables["acceptance_criteria"]["type"] == "list"
    assert variables["acceptance_criteria"]["required"] is True
    assert variables["acceptance_criteria"]["declared_in"] == BASE
    for variable in variables.values():
        assert variable["description"].strip(), variable


@pytest.mark.parametrize("missing", ("what", "why"))
def test_an_sdk_render_missing_a_required_answer_is_refused_naming_it_and_never_prompts(
    missing: str,
    launch_environment: dict[str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No terminal, no answer, and no interactive setting to lean on: the SDK's own flag.

    The launch environment is used only to resolve the loader document; the render is
    the SDK's own call from this process, whose environment has the variable cleared. So
    what keeps it from prompting is the SDK passing `--no-interactive` on every call —
    this call has no terminal, and a render that prompted would wait on a pipe instead of
    being refused.
    """
    monkeypatch.delenv("ONETASKGRAPH_INTERACTIVE", raising=False)
    answers = {name: value for name, value in ANSWERS.items() if name != missing}

    with pytest.raises(OSError) as refused:
        _render(launch_environment, answers, tmp_path)

    assert missing in str(refused.value), refused.value


def test_a_rendering_is_the_layout_every_plan_task_here_carries(
    launch_environment: dict[str, str], tmp_path: Path
) -> None:
    """What, Why, one criteria section, the task's own notes, then the appendix verbatim."""
    body = _render(launch_environment, ANSWERS, tmp_path)

    headings = re.findall(r"^## .+$", body, re.MULTILINE)
    appendix = APPENDIX.read_text(encoding="utf-8")
    appendix_headings = re.findall(r"^## .+$", appendix, re.MULTILINE)
    assert headings == [
        "## What",
        "## Why",
        "## Acceptance criteria",
        "## Additional info",
        *appendix_headings,
    ], headings
    assert body.endswith(appendix), "the appendix is not the rendering's last bytes, unedited"
    assert "\n- The thing is built.\n" in body
    assert "\n- The thing is proven end to end\n  by a journey that drives it.\n" in body
    assert criteria_guard.own_additional_info(body).strip() == ANSWERS["additional_info"]


def test_a_task_with_no_notes_of_its_own_renders_no_section_for_them(
    launch_environment: dict[str, str], tmp_path: Path
) -> None:
    answers = {name: value for name, value in ANSWERS.items() if name != "additional_info"}

    body = _render(launch_environment, answers, tmp_path)

    appendix = APPENDIX.read_text(encoding="utf-8")
    assert body.count("## Additional info") == appendix.count("## Additional info")
    assert criteria_guard.own_additional_info(body) == ""
    assert body.endswith(appendix)


def test_a_persons_own_plan_store_in_this_checkout_stays_interactive() -> None:
    """Nothing tracked here sets the setting, so the product's own default stands."""
    environment = {
        name: value for name, value in os.environ.items() if name != "ONETASKGRAPH_INTERACTIVE"
    }

    shown = _run([str(PLAN_STORE), "config", "show", "--json"], environment)

    assert shown.returncode == 0, shown.stderr
    (setting,) = [
        one for one in json.loads(shown.stdout)["settings"] if one["key"] == "interactive"
    ]
    assert setting["value"] is True, setting


#: The plan-store verbs that prompt for an unanswered variable, spelled as a command line
#: names them: `template render`, and a create or render of a task or a document.
PROMPTING_VERB = re.compile(
    r"\bonetaskgraph\b[^\n]*?\b(?:template\s+render|(?:task|document)\s+render"
    r"|(?:task|document)\s+create\b[^\n]*?--template)"
)


def _prompting_calls(text: str) -> list[str]:
    """Every logical line of ``text`` running a prompting verb, continuations joined."""
    joined = re.sub(r"\\\n\s*", " ", text)
    return [line for line in joined.splitlines() if PROMPTING_VERB.search(line)]


def test_the_scan_below_finds_a_prompting_verb_however_its_line_is_continued() -> None:
    """The detector is proven on a sample, so an empty answer below means no such call."""
    sample = (
        'onepipeline template resolve plan-task --json | "$store" onetaskgraph task create \\\n'
        "    authoring --template-loader - --project p --title t\n"
        "onetaskgraph task render authoring:x --template-loader - --no-interactive\n"
        "onetaskgraph task show authoring:x\n"
    )

    calls = _prompting_calls(sample)

    assert len(calls) == 2, calls
    assert [call for call in calls if "--no-interactive" not in call] == [calls[0]]


def test_every_prompting_verb_a_script_or_recipe_runs_names_no_interactive() -> None:
    """A prompt in a script or recipe would wait on a terminal nobody is at."""
    sources = [REPO_ROOT / "justfile", *sorted((REPO_ROOT / "scripts").glob("*.sh"))]
    sources += sorted((REPO_ROOT / ".githooks").iterdir())

    unflagged = [
        f"{path.relative_to(REPO_ROOT)}: {call.strip()}"
        for path in sources
        for call in _prompting_calls(path.read_text(encoding="utf-8"))
        if "--no-interactive" not in call
    ]

    assert not unflagged, unflagged


def test_the_sections_a_brief_must_have_are_the_ones_a_task_renders_with(
    launch_environment: dict[str, str], tmp_path: Path
) -> None:
    """`just plan` refuses exactly the sections a plan task is rendered with.

    Drift in either direction is silent and costly: a section dropped from the helper's
    copy lets a brief through that dispatches a planner with no review bar, and one added
    refuses a brief that is a perfectly good task. `## Additional info` is on neither side,
    because the template renders a task's own only when it has notes of its own.
    """
    declared = DECLARED_SECTIONS.search(PLAN_BRIEF.read_text(encoding="utf-8"))
    assert declared is not None, f"{PLAN_BRIEF.name} declares no PLAN_REQUIRED_SECTIONS"
    required = tuple(re.findall(r'"([^"]+)"', declared.group(1)))
    answers = {name: value for name, value in ANSWERS.items() if name != "additional_info"}

    headings = re.findall(r"^## .+$", _render(launch_environment, answers, tmp_path), re.M)

    assert required == tuple(headings[: headings.index("## Additional info")]), headings
