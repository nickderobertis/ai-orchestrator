"""A follow-up drafted through the real command, read back through the real plan store.

`scripts/follow-up-env.sh` is what a launch sources to hand every dispatch, the monitor and
the pacemaker the `drafts` source's root and plugin and the command they draft with;
`scripts/follow-up-draft.sh` is that command; `just follow-up` is the manager's recipe for
it. These journeys drive all three as their callers do, and read what they wrote back
through the installed `onetaskgraph` — the program the follow-up agent reads drafts with —
because a draft that renders correctly and that the store then reads differently is no
draft at all.

They sit in this project for the reason `tests/plan_tooling/test_plan_root_env.py` does:
every one of them spawns the installed plan-store CLI, and what they read is this
repository's configuration, scripts and package. The recorded run the member transcript
reads are driven against is a fixture of this repository, under this project's own key.

Nothing below the paid model is doubled, and no paid model is involved: the node a draft
names is resolved from a dispatch registry naming this test's own process, which is a real
ancestor of the command it starts, and the transcript a monitor draft points at is run
against the installed engine. What a real launch hands a real dispatch is
`tests/ask_seam/follow_up_drafts_launch/test_follow_up_drafts_launch_e2e.py`'s.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import follow_up_variables
import pytest
from onetaskgraph_sdk import TaskDetail
from published_tools import ONETASKGRAPH_BIN

from orchestrator import follow_up_drafts as drafts
from orchestrator import plan_store
from orchestrator.root import REPO_ROOT

#: The command every party drafts through, and the helper that exports it.
DRAFT_COMMAND = REPO_ROOT / "scripts" / "follow-up-draft.sh"
HELPER = follow_up_variables.HELPER

#: What `just follow-up` runs, which sources the same helper before the command.
RECIPE_SCRIPT = REPO_ROOT / "scripts" / "follow-up.sh"

#: A body carrying the four headings a draft is refused without.
BODY = (
    "## What happened\nThe sweep reports a family it never examined.\n\n"
    "## Where\n`scripts/sweep.sh`, its trailer.\n\n"
    "## Why it is out of scope\nThis node owns follow-up drafts.\n\n"
    "## Evidence\n```\n0 B reclaimed\n```\n"
)
REPOSITORY = "github.com/nickderobertis/ai-orchestrator"
TITLE = "The sweep trailer names an unexamined family"

#: A run this repository recorded, whose event store carries turns of both dag-scope
#: members — the monitor and the `check-in` pacemaker — for the member reads to narrow.
RECORDED_RUN = REPO_ROOT / "tests" / "fixtures" / "timeline-runs" / "dag-ui-truth-monitor-slice"

#: A line of the engine's concise event view, which opens on the event's timestamp.
EVENT_LINE = re.compile(r"^\d{4}-\d{2}-\d{2}T\S+\s")


def _path(*extra: str | None) -> str:
    """A search path holding this checkout's tools, the shell's, and anything named."""
    directories = [
        str(REPO_ROOT / ".venv" / "bin"),
        *(item for item in extra if item),
        "/usr/bin",
        "/bin",
    ]
    return os.pathsep.join(directories)


def _environment(**stated: str) -> dict[str, str]:
    """A curated environment: what a journey states, never what an enclosing launch set."""
    return {"PATH": _path(), "HOME": str(Path.home()), **stated}


def _exported(
    tmp_path: Path, *, helper: Path = HELPER, **stated: str
) -> subprocess.CompletedProcess[str]:
    """Source the helper as a launcher does, and print the three values it exported."""
    names = follow_up_variables.all_names()
    printed = "; ".join(f'printf "%s\\n" "${{{name}-}}"' for name in names)
    return subprocess.run(  # noqa: S603 - this repository's own helper, sourced as a launcher does
        ["bash", "-c", f'source "{helper}"; export_follow_up_drafts journey || exit $?; {printed}'],
        cwd=tmp_path,
        env=_environment(**stated),
        text=True,
        capture_output=True,
        check=False,
    )


def _values(exported: subprocess.CompletedProcess[str]) -> dict[str, str]:
    assert exported.returncode == 0, exported.stdout + exported.stderr
    return dict(zip(follow_up_variables.all_names(), exported.stdout.splitlines(), strict=True))


def test_a_launch_exports_the_resolved_root_the_plugin_and_the_drafting_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The root is the store's own answer for `drafts`, absolute, and the command runs."""
    root, plugin, command = follow_up_variables.all_names()
    values = _values(_exported(tmp_path))

    # What the store resolves with nothing in the environment, which is what the helper's
    # curated resolution read: an enclosing launch's own export would otherwise answer.
    monkeypatch.delenv(root, raising=False)
    monkeypatch.delenv(plugin, raising=False)
    assert values[root] == str(plan_store.source_root(drafts.SOURCE))
    assert Path(values[root]).is_absolute(), values[root]
    assert values[plugin] == plan_store.WRITABLE_PLUGIN
    assert values[command] == str(DRAFT_COMMAND)
    assert os.access(values[command], os.X_OK), f"{values[command]} is not runnable"


def test_a_root_and_plugin_the_caller_already_chose_are_the_ones_left(tmp_path: Path) -> None:
    """The caller's own spelling survives, which is what tells kept from re-derived.

    Spelled with a trailing separator, because the resolution reads the environment too
    and answers the same directory without one: a helper that re-exported its own answer
    over the caller's would pass a check made with a spelling the store does not change.
    """
    root, plugin, _ = follow_up_variables.all_names()
    chosen = tmp_path / "chosen"

    values = _values(_exported(tmp_path, **{root: f"{chosen}/", plugin: "local-md"}))

    assert (values[root], values[plugin]) == (f"{chosen}/", "local-md")
    assert chosen.is_dir(), "a chosen root that did not exist yet was not made"


def test_a_root_the_launch_may_not_write_into_refuses_the_launch(tmp_path: Path) -> None:
    root, _, _ = follow_up_variables.all_names()
    sealed = tmp_path / "sealed"
    sealed.mkdir(mode=0o500)
    try:
        exported = _exported(tmp_path, **{root: str(sealed)})
    finally:
        sealed.chmod(0o700)

    assert exported.returncode == 2, exported.stdout + exported.stderr
    assert str(sealed) in exported.stderr and root in exported.stderr, exported.stderr
    assert exported.stdout == "", "a refused launch went on to export something"


def test_a_checkout_without_the_drafting_command_refuses_the_launch(tmp_path: Path) -> None:
    """What is exported is a command an agent runs, so a checkout that lacks it cannot launch."""
    scripts = tmp_path / "checkout" / "scripts"
    scripts.mkdir(parents=True)
    helper = scripts / HELPER.name
    shutil.copy(HELPER, helper)
    (scripts / DRAFT_COMMAND.name).write_bytes(DRAFT_COMMAND.read_bytes())

    exported = _exported(tmp_path, helper=helper)

    assert exported.returncode == 2, exported.stdout + exported.stderr
    assert "drafting command is not an executable file" in exported.stderr, exported.stderr
    assert "chmod +x" in exported.stderr, exported.stderr


def _draft(
    *arguments: str,
    cwd: Path,
    body: str = BODY,
    path: str | None = None,
    **stated: str,
) -> subprocess.CompletedProcess[str]:
    """Run the drafting command as an agent does: its body on stdin, its environment stated."""
    environment = _environment(**stated)
    if path is not None:
        environment["PATH"] = path
    return subprocess.run(  # noqa: S603 - the real drafting command, as a party runs it
        [str(DRAFT_COMMAND), *arguments],
        cwd=cwd,
        env=environment,
        input=body,
        text=True,
        capture_output=True,
        check=False,
    )


def _drafting(tmp_path: Path, **stated: str) -> dict[str, str]:
    """The environment of a dispatch that can draft: a root, a run, and a runs root."""
    return {
        follow_up_variables.root_name(): str(tmp_path / "drafts"),
        drafts.RUN_ID_ENV: "journey-run",
        drafts.RUNS_ROOT_ENV: str(tmp_path / "runs"),
        **stated,
    }


def _written(tmp_path: Path, completed: subprocess.CompletedProcess[str]) -> tuple[str, Path]:
    """The qualified id and path the command said it wrote, checked against the disk."""
    assert completed.returncode == 0, completed.stdout + completed.stderr
    matched = re.fullmatch(r"drafted (?P<id>drafts:\S+) at (?P<path>\S+)\n", completed.stdout)
    assert matched is not None, completed.stdout
    path = Path(matched["path"])
    assert path.is_file(), f"the command named {path}, which it did not write"
    assert path.is_relative_to(tmp_path / "drafts"), path
    return matched["id"], path


def _git(cwd: Path, *arguments: str) -> None:
    subprocess.run(  # noqa: S603 - a repository this journey owns
        ["git", "-C", str(cwd), *arguments],  # noqa: S607
        check=True,
        capture_output=True,
        env=_environment(
            GIT_AUTHOR_NAME="journey",
            GIT_AUTHOR_EMAIL="journey@example.invalid",
            GIT_COMMITTER_NAME="journey",
            GIT_COMMITTER_EMAIL="journey@example.invalid",
        ),
    )


def test_a_draft_is_stamped_by_the_machinery_from_where_the_command_ran(tmp_path: Path) -> None:
    """Run and dispatch come from the command's environment and directory, never its flags.

    Drafted by a process no recorded dispatch of the run is an ancestor of — the runs root
    holds no such run — so the node is `unresolved` and the transcript reads the whole run,
    and the draft is written all the same. The node a real dispatch resolves to is read off
    a real launch's own registry by
    `tests/ask_seam/follow_up_drafts_launch/test_follow_up_drafts_launch_e2e.py`, and a stale
    registry entry is refused against real processes by
    `tests/test_follow_up_drafts.py`: neither is state this journey should manufacture.
    """
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    _git(worktree, "init", "-q", "-b", "onevcs/s-0123456789ab")
    _git(worktree, "commit", "-q", "--allow-empty", "-m", "work")
    stated = _drafting(
        tmp_path,
        **{
            drafts.NODE_SCRATCH_ENV: str(tmp_path / "scratch"),
            drafts.SESSION_ENV: "s-0123456789ab",
        },
    )

    qualified, path = _written(
        tmp_path,
        _draft(
            "--title",
            TITLE,
            "--repository",
            REPOSITORY,
            "--path",
            "scripts/sweep.sh",
            cwd=worktree,
            **stated,
        ),
    )

    draft = drafts.parse(path.read_text(encoding="utf-8"))
    assert qualified == f"drafts:journey-run/drafts/{path.stem}"
    assert path == tmp_path / "drafts" / "tasks" / "journey-run" / "drafts" / f"{draft.stem}.md"
    assert (draft.run, draft.author) == (
        "journey-run",
        drafts.Author("node", drafts.UNRESOLVED, None),
    )
    assert draft.dispatch.scratch == str(tmp_path / "scratch")
    assert draft.dispatch.session == "s-0123456789ab"
    assert Path(draft.dispatch.cwd).resolve() == worktree.resolve()
    assert draft.dispatch.branch == "onevcs/s-0123456789ab"
    assert draft.dispatch.head is not None and re.fullmatch(r"[0-9a-f]{40}", draft.dispatch.head)
    assert draft.transcript == "onepipeline transcript journey-run"
    assert (draft.repository, draft.paths, draft.body) == (
        REPOSITORY,
        ("scripts/sweep.sh",),
        BODY.strip(),
    )
    project = tmp_path / "drafts" / "projects" / "journey-run.md"
    assert project.read_text(encoding="utf-8") == drafts.render_project("journey-run")


def _store_show(qualified: str, *, cwd: Path, stated: dict[str, str]) -> dict[str, object]:
    """The installed store's answer about one task, read the way the follow-up agent reads it."""
    shown = subprocess.run(  # noqa: S603 - the installed plan-store CLI, reading a draft back
        [str(ONETASKGRAPH_BIN), "task", "show", qualified, "--json"],
        cwd=cwd,
        env=_environment(**stated),
        text=True,
        capture_output=True,
        check=False,
    )
    assert shown.returncode == 0, shown.stdout + shown.stderr
    answer = TaskDetail.model_validate_json(shown.stdout)
    assert len(answer.items) == 1, answer
    return answer.items[0].item.model_dump(mode="json")


@pytest.mark.parametrize(
    "reader",
    ["this checkout", "a directory with no configuration", "a checkout declaring no drafts source"],
)
def test_the_installed_store_reads_a_draft_back_with_every_value_and_type_intact(
    tmp_path: Path, reader: str
) -> None:
    """What the command wrote is what the store reports, from wherever a reader stands.

    Under the environment a launch exports, which is the whole of what a graph member's
    scratch or another repository's worktree has to find the `drafts` source by.
    """
    root, plugin, _ = follow_up_variables.all_names()
    exported = _values(_exported(tmp_path, **{root: str(tmp_path / "drafts")}))
    stated = {name: exported[name] for name in (root, plugin)}
    qualified, path = _written(
        tmp_path,
        _draft(
            "--title",
            TITLE,
            "--repository",
            REPOSITORY,
            "--path",
            "a/b",
            "--as",
            "monitor",
            "--member",
            "monitor",
            cwd=tmp_path,
            **_drafting(tmp_path),
        ),
    )
    cwd = {"this checkout": REPO_ROOT, "a directory with no configuration": tmp_path / "bare"}.get(
        reader
    )
    if cwd is None:
        cwd = tmp_path / "elsewhere"
        cwd.mkdir()
        (cwd / "onetaskgraph.yaml").write_text(
            "default_sources: [plans]\n"
            "sources:\n  plans:\n    plugin: local-md\n    config:\n      root: plans\n",
            encoding="utf-8",
        )
    cwd.mkdir(exist_ok=True)

    item = _store_show(qualified, cwd=cwd, stated=stated)

    held = json.loads(
        path.read_text(encoding="utf-8").split(f'  "{drafts.DRAFT_KEY}": ', 1)[1].split("\n", 1)[0]
    )
    metadata = item["metadata"]
    assert isinstance(metadata, dict)
    assert metadata[drafts.DRAFT_KEY] == held, "the store reported the draft's metadata differently"
    reported = metadata[drafts.DRAFT_KEY]
    assert type(reported["schema"]) is int and reported["schema"] == drafts.SCHEMA
    assert reported["author"] == {"kind": "monitor", "node": None, "member": "monitor"}
    assert reported["paths"] == ["a/b"] and reported["dispatch"]["session"] is None
    assert drafts.from_store_item(item) == drafts.parse(path.read_text(encoding="utf-8"))


def _event_lines(output: str) -> list[str]:
    return [line for line in output.splitlines() if EVENT_LINE.match(line)]


@pytest.mark.parametrize(("kind", "member"), [("monitor", "monitor"), ("pacemaker", "check-in")])
def test_a_member_drafts_transcript_reads_that_members_turns_off_the_installed_engine(
    tmp_path: Path, kind: str, member: str
) -> None:
    """The read a monitor or pacemaker draft points at shows that member's events and no other's.

    Run against a recorded run holding both members, so the narrowing is visible: the command
    the draft stores is run as a reader would run it, and its events are counted against the
    run's own store.
    """
    runs = tmp_path / "runs"
    shutil.copytree(
        RECORDED_RUN,
        runs / RECORDED_RUN.name,
        ignore=shutil.ignore_patterns("checkpoint.json", "summary.json"),
    )
    stated = _drafting(tmp_path)

    _, path = _written(
        tmp_path,
        _draft(
            "--title",
            TITLE,
            "--repository",
            REPOSITORY,
            "--as",
            kind,
            "--member",
            member,
            "--run",
            RECORDED_RUN.name,
            cwd=tmp_path,
            **stated,
        ),
    )
    draft = drafts.parse(path.read_text(encoding="utf-8"))
    assert (draft.author.kind, draft.author.node, draft.author.member) == (kind, None, member)
    assert draft.transcript is not None

    read = subprocess.run(  # noqa: S603 - the stored read, run as a reader runs it
        ["bash", "-c", draft.transcript],  # noqa: S607
        cwd=tmp_path,
        env=_environment(**{drafts.RUNS_ROOT_ENV: str(runs)}),
        text=True,
        capture_output=True,
        check=False,
    )

    assert read.returncode == 0, read.stdout + read.stderr
    recorded = [
        json.loads(line)
        for line in (runs / RECORDED_RUN.name / "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    own = [event for event in recorded if event.get("labels", {}).get("member") == member]
    assert own, f"the recorded run holds no event of member {member}, so nothing is measured"
    assert len(_event_lines(read.stdout)) == len(own), (
        f"`{draft.transcript}` showed {len(_event_lines(read.stdout))} events where member "
        f"{member} recorded {len(own)}:\n{read.stdout}"
    )


def test_a_manager_draft_points_at_no_transcript(tmp_path: Path) -> None:
    _, path = _written(
        tmp_path,
        _draft(
            "--title",
            TITLE,
            "--repository",
            REPOSITORY,
            "--as",
            "manager",
            "--run",
            "manager-run",
            cwd=tmp_path,
            **_drafting(tmp_path),
        ),
    )
    draft = drafts.parse(path.read_text(encoding="utf-8"))
    assert (draft.run, draft.author, draft.transcript) == (
        "manager-run",
        drafts.Author("manager", None, None),
        None,
    )


def test_concurrent_drafters_each_land_their_own_draft_and_one_project(tmp_path: Path) -> None:
    """Eight processes of one run drafting the same title at once: eight files, one project."""
    stated = _environment(**_drafting(tmp_path))
    started = [
        subprocess.Popen(  # noqa: S603 - the real drafting command, several at once
            [str(DRAFT_COMMAND), "--title", TITLE, "--repository", REPOSITORY],
            cwd=tmp_path,
            env=stated,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(8)
    ]
    finished = [process.communicate(BODY, timeout=120) for process in started]

    assert all(process.returncode == 0 for process in started), finished
    written = sorted((tmp_path / "drafts" / "tasks" / "journey-run" / "drafts").iterdir())
    assert len(written) == 8 and all(path.suffix == ".md" for path in written), written
    assert all(drafts.parse(path.read_text(encoding="utf-8")).title == TITLE for path in written)
    assert sorted(path.name for path in (tmp_path / "drafts" / "projects").iterdir()) == [
        "journey-run.md"
    ]
    assert not [path for path in (tmp_path / "drafts").rglob(".*")], (
        "a temporary file was left behind"
    )


REFUSALS = [
    (
        "no run",
        ["--title", TITLE, "--repository", REPOSITORY],
        BODY,
        {drafts.RUN_ID_ENV: None},
        "no run can be named",
    ),
    (
        "root unset",
        ["--title", TITLE, "--repository", REPOSITORY],
        BODY,
        {"root": None},
        "the draft root is unset",
    ),
    (
        "root unwritable",
        ["--title", TITLE, "--repository", REPOSITORY],
        BODY,
        {"root": "sealed"},
        "may write into",
    ),
    ("empty title", ["--title", "", "--repository", REPOSITORY], BODY, {}, "the title is empty"),
    ("long title", ["--title", "x" * 121, "--repository", REPOSITORY], BODY, {}, "over the 120"),
    (
        "repository",
        ["--title", TITLE, "--repository", "a checkout"],
        BODY,
        {},
        "not a normalized origin",
    ),
    (
        "missing heading",
        ["--title", TITLE, "--repository", REPOSITORY],
        BODY.split("## Evidence")[0],
        {},
        "no `## Evidence` heading",
    ),
    (
        "empty heading",
        ["--title", TITLE, "--repository", REPOSITORY],
        BODY.replace("`scripts/sweep.sh`, its trailer.", ""),
        {},
        "`## Where` section is empty",
    ),
    (
        "member missing",
        ["--title", TITLE, "--repository", REPOSITORY, "--as", "pacemaker"],
        BODY,
        {},
        "--as pacemaker needs --member",
    ),
    (
        "member disagrees",
        ["--title", TITLE, "--repository", REPOSITORY, "--member", "monitor"],
        BODY,
        {},
        "--as node is not one",
    ),
]


@pytest.mark.parametrize(
    ("arguments", "body", "changes", "refusal"),
    [row[1:] for row in REFUSALS],
    ids=[row[0] for row in REFUSALS],
)
def test_every_refusal_exits_2_names_what_to_do_and_writes_nothing(
    tmp_path: Path, arguments: list[str], body: str, changes: dict[str, str | None], refusal: str
) -> None:
    stated = _drafting(tmp_path)
    root = follow_up_variables.root_name()
    sealed = tmp_path / "sealed"
    sealed.mkdir(mode=0o500)
    for name, value in changes.items():
        key = root if name == "root" else name
        if value is None:
            stated.pop(key)
        else:
            stated[key] = str(sealed)
    try:
        refused = _draft(*arguments, cwd=tmp_path, body=body, **stated)
    finally:
        sealed.chmod(0o700)

    assert refused.returncode == 2, refused.stdout + refused.stderr
    assert refused.stderr.startswith("follow-up-draft: refused: "), refused.stderr
    assert refusal in refused.stderr, refused.stderr
    assert refused.stdout == ""
    assert not (tmp_path / "drafts").exists(), "a refused draft wrote under the root"
    assert list(sealed.iterdir()) == [], "a refused draft wrote under the root"


def test_help_states_the_whole_contract_and_that_a_draft_never_replaces_the_channel(
    tmp_path: Path,
) -> None:
    """A dispatch whose task predates drafts learns everything from this one read."""
    shown = subprocess.run(  # noqa: S603 - the real drafting command's help
        [str(DRAFT_COMMAND), "--help"],
        cwd=tmp_path,
        env=_environment(),
        text=True,
        capture_output=True,
        check=False,
    )

    assert shown.returncode == 0, shown.stderr
    text = " ".join(shown.stdout.split())
    for flag in ("--title", "--repository", "--path", "--as", "--member", "--run"):
        assert flag in text, f"--help does not state {flag}"
    for heading in drafts.HEADINGS:
        assert f"## {heading}" in shown.stdout, f"--help does not state the `## {heading}` heading"
    for stamped in (
        "run",
        "author.kind",
        "author.node",
        "author.member",
        "dispatch.scratch",
        "dispatch.session",
        "dispatch.cwd",
        "dispatch.branch",
        "dispatch.head",
        "transcript",
        "drafted_at",
    ):
        assert re.search(rf"^\s+{re.escape(stamped)}\s", shown.stdout, re.MULTILINE), (
            f"--help does not say {stamped} is stamped"
        )
    assert "NON-BLOCKING follow-up" in text
    assert "Anything blocking" in text and "goes over the channel immediately instead" in text
    assert (
        '"$ORCHESTRATOR_ASK_MANAGER"' in text and "`finding`" in text and "check-in update" in text
    )
    assert "is read from stdin, never from an argument" in text


def _just() -> str:
    found = shutil.which("just")
    if found is None:
        pytest.skip("just is not installed")
    return found


def _recipe(tmp_path: Path, *arguments: str, body: str = BODY) -> subprocess.CompletedProcess[str]:
    """`just follow-up`, run from this checkout as a manager runs it."""
    just = _just()
    return subprocess.run(  # noqa: S603 - the real recipe, as a manager runs it
        [just, "follow-up", *arguments],
        cwd=REPO_ROOT,
        env=_environment(**{follow_up_variables.root_name(): str(tmp_path / "drafts")})
        | {"PATH": _path(str(Path(just).parent))},
        input=body,
        text=True,
        capture_output=True,
        check=False,
    )


def test_the_managers_recipe_writes_a_manager_draft_for_the_named_run(tmp_path: Path) -> None:
    _, path = _written(
        tmp_path,
        _recipe(
            tmp_path,
            "recipe-run",
            "--title",
            TITLE,
            "--repository",
            REPOSITORY,
            "--path",
            "justfile",
        ),
    )

    draft = drafts.parse(path.read_text(encoding="utf-8"))
    assert (draft.run, draft.author, draft.transcript, draft.paths) == (
        "recipe-run",
        drafts.Author("manager", None, None),
        None,
        ("justfile",),
    )
    assert path.parent == tmp_path / "drafts" / "tasks" / "recipe-run" / "drafts"


@pytest.mark.parametrize(
    ("arguments", "refusal"),
    [
        (("--title", TITLE, "--repository", REPOSITORY), "name the run this follow-up came from"),
        (
            ("recipe-run", "--title", TITLE, "--repository", REPOSITORY, "--as", "node"),
            "--as is this recipe's to decide",
        ),
    ],
    ids=["no-run", "another-author"],
)
def test_the_managers_recipe_refuses_what_is_not_a_manager_draft(
    tmp_path: Path, arguments: tuple[str, ...], refusal: str
) -> None:
    refused = _recipe(tmp_path, *arguments)
    assert refused.returncode != 0, refused.stdout
    assert refusal in refused.stderr, refused.stderr
    assert not (tmp_path / "drafts").exists()


def test_the_managers_recipe_help_is_the_drafting_commands_help(tmp_path: Path) -> None:
    shown = _recipe(tmp_path, "--help", body="")

    assert shown.returncode == 0, shown.stdout + shown.stderr
    for heading in drafts.HEADINGS:
        assert f"## {heading}" in shown.stdout, f"`just follow-up --help` omits `## {heading}`"
    assert not (tmp_path / "drafts").exists()


def test_the_managers_recipe_help_names_a_missing_drafting_command_and_its_repair(
    tmp_path: Path,
) -> None:
    """A checkout holding the recipe but not the command it defers to refuses with the fix."""
    scripts = tmp_path / "checkout" / "scripts"
    scripts.mkdir(parents=True)
    recipe = scripts / RECIPE_SCRIPT.name
    shutil.copy(RECIPE_SCRIPT, recipe)
    recipe.chmod(0o755)

    refused = subprocess.run(  # noqa: S603 - this checkout's own recipe script, missing its command
        [str(recipe), "--help"],
        cwd=tmp_path,
        env=_environment(),
        text=True,
        capture_output=True,
        check=False,
    )

    assert refused.returncode == 2, refused.stdout + refused.stderr
    assert refused.stderr.startswith("follow-up: "), refused.stderr
    assert str(scripts / DRAFT_COMMAND.name) in refused.stderr, refused.stderr
    assert "restore it from the repository" in refused.stderr, refused.stderr
    assert refused.stdout == ""


def _a_checkout_without_its_package(tmp_path: Path, *tools: str) -> tuple[Path, str]:
    """The drafting command and its helper alone in a checkout, and a search path of ``tools``.

    No `.venv` and no `orchestrator` package, which is a checkout nobody provisioned; the
    search path holds only the named tools, each linked from where this host has it, so
    what the command can and cannot reach is exactly what the journey states.
    """
    scripts = tmp_path / "checkout" / "scripts"
    scripts.mkdir(parents=True)
    for source in (DRAFT_COMMAND, HELPER):
        copied = scripts / source.name
        shutil.copy(source, copied)
        copied.chmod(0o755)
    binaries = tmp_path / "bin"
    binaries.mkdir()
    for tool in tools:
        found = shutil.which(tool, path="/usr/bin:/bin")
        assert found is not None, f"this host has no {tool} to link"
        (binaries / tool).symlink_to(found)
    return scripts / DRAFT_COMMAND.name, str(binaries)


@pytest.mark.parametrize(
    ("tools", "refusal"),
    [
        (("bash", "dirname"), "no Python interpreter was found"),
        (("bash", "dirname", "python3"), "orchestrator package could not be imported"),
    ],
    ids=["no-interpreter", "no-package"],
)
def test_a_checkout_that_cannot_run_the_command_says_how_to_provision_it(
    tmp_path: Path, tools: tuple[str, ...], refusal: str
) -> None:
    """A draft that cannot be written is one sentence and its repair, never a traceback."""
    command, path = _a_checkout_without_its_package(tmp_path, *tools)

    refused = subprocess.run(  # noqa: S603 - the real drafting command, in an unprovisioned checkout
        [str(command), "--title", TITLE, "--repository", REPOSITORY],
        cwd=tmp_path,
        env={"PATH": path, "HOME": str(Path.home()), **_drafting(tmp_path)},
        input=BODY,
        text=True,
        capture_output=True,
        check=False,
    )

    assert refused.returncode == 2, refused.stdout + refused.stderr
    assert refusal in refused.stderr and "just bootstrap" in refused.stderr, refused.stderr
    assert "Traceback" not in refused.stderr, refused.stderr
    assert not (tmp_path / "drafts").exists()


@pytest.mark.parametrize(
    ("command", "arguments", "prog"),
    [(DRAFT_COMMAND, (), "follow-up-draft"), (RECIPE_SCRIPT, ("helper-run",), "follow-up")],
    ids=["drafting-command", "managers-recipe"],
)
@pytest.mark.parametrize(
    ("helper", "refusal"),
    [
        (None, "required helper is not a readable regular file"),
        ("return 1\n", "is readable but could not be loaded"),
    ],
    ids=["missing-helper", "unloadable-helper"],
)
def test_a_checkout_whose_drafting_helper_is_broken_says_how_to_restore_it(
    tmp_path: Path,
    command: Path,
    arguments: tuple[str, ...],
    prog: str,
    helper: str | None,
    refusal: str,
) -> None:
    """Both entry points refuse before anything is drafted when the seam's helper is gone.

    The command and the recipe are this checkout's own, copied beside a helper that is
    absent or that fails as it is sourced, and run by path as a party runs them.
    """
    scripts = tmp_path / "checkout" / "scripts"
    scripts.mkdir(parents=True)
    for source in {command, DRAFT_COMMAND}:
        copied = scripts / source.name
        shutil.copy(source, copied)
        copied.chmod(0o755)
    if helper is not None:
        (scripts / HELPER.name).write_text(helper, encoding="utf-8")

    refused = subprocess.run(  # noqa: S603 - this checkout's own entry point, beside a broken helper
        [str(scripts / command.name), *arguments, "--title", TITLE, "--repository", REPOSITORY],
        cwd=tmp_path,
        env=_environment(**_drafting(tmp_path)),
        input=BODY,
        text=True,
        capture_output=True,
        check=False,
    )

    assert refused.returncode == 2, refused.stdout + refused.stderr
    assert refused.stderr.startswith(f"{prog}: "), refused.stderr
    assert refusal in refused.stderr and "just bootstrap" in refused.stderr, refused.stderr
    assert str(scripts / HELPER.name) in refused.stderr, refused.stderr
    assert refused.stdout == ""
    assert not (tmp_path / "drafts").exists()


def test_a_root_that_cannot_hold_the_draft_fails_and_names_the_directory_to_repair(
    tmp_path: Path,
) -> None:
    """A root accepted as writable that still cannot store the draft is a failure, not a refusal.

    Its `tasks` entry is a file, so the draft's directory cannot be made below it: the
    real command exits with the module's failure status, names the root, and leaves it as
    it found it.
    """
    stated = _drafting(tmp_path)
    root = Path(stated[follow_up_variables.root_name()])
    root.mkdir()
    (root / "tasks").write_text("not a directory\n", encoding="utf-8")

    failed = _draft("--title", TITLE, "--repository", REPOSITORY, cwd=tmp_path, **stated)

    assert failed.returncode == drafts.FAILED, failed.stdout + failed.stderr
    assert f"could not be written under {root}" in failed.stderr, failed.stderr
    assert "repair that directory" in failed.stderr, failed.stderr
    assert "Traceback" not in failed.stderr, failed.stderr
    assert failed.stdout == ""
    assert sorted(entry.name for entry in root.iterdir()) == ["tasks"]
