"""What a drafted follow-up is, decided by `orchestrator/follow_up_drafts.py` alone.

The module is the one place a draft's shape is rendered, parsed and validated, so what is
proven here is that each of those halves agrees with the others: a draft renders to a record
that parses back to the same draft, a store's answer about that record validates to the
same draft, and every way a record or an input can depart from the shape is refused with a
sentence saying what to do. The command every party drafts through is driven end to end by
`tests/plan_tooling/test_follow_up_drafts_e2e.py` and from inside a real launch by
`tests/ask_seam/follow_up_drafts_launch/test_follow_up_drafts_launch_e2e.py`; this is the
in-process half those journeys cannot measure line by line.

Nothing below the module is doubled. The node a draft names is resolved against real
processes — this test's own, and a child it starts — read out of the real `/proc`, and git
state is read from real repositories in a temporary directory.

The dispatch registry has a second reader, `scripts/supervision-readings.py`, which cannot
import this package. The two are reconciled here by name and by answer: both read one
registry this test writes for processes that exist.
"""

from __future__ import annotations

import dataclasses
import importlib.util
import io
import json
import os
import subprocess
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from types import ModuleType

import pytest

from orchestrator import follow_up_drafts as drafts
from orchestrator.follow_up_drafts import Author, DispatchStamp, Draft, Refused
from orchestrator.project_store import frontmatter
from orchestrator.root import REPO_ROOT

#: A body carrying every required heading, with a fenced block whose own `## ` line is code.
BODY = (
    "## What happened\nThe sweep skipped a family.\n\n"
    "## Where\n`scripts/sweep.sh`.\n\n"
    "## Why it is out of scope\nThis node owns the drafts, not the sweep.\n\n"
    "## Evidence\n```\n## not a heading\n```\n"
)

#: The variable these tests export a draft root under. Deliberately not the name a launch
#: uses, which `scripts/follow-up-env.sh` alone composes: the module is handed the name.
ROOT_ENV = "FOLLOW_UP_DRAFTS_TEST_ROOT"

RUN = "run-a"
REPOSITORY = "github.com/nickderobertis/ai-orchestrator"


def _draft(**changes: object) -> Draft:
    """A valid draft, with ``changes`` applied."""
    held = Draft(
        title="The sweep skips a family",
        run=RUN,
        author=Author(kind="node", node="sweep-node", member=None),
        dispatch=DispatchStamp(
            scratch="/runs/run-a/scratch/1-0",
            session="s-0123456789ab",
            cwd="/work/tree",
            branch="onevcs/s-0123456789ab",
            head="0" * 40,
        ),
        repository=REPOSITORY,
        paths=("scripts/sweep.sh",),
        drafted_at="2026-09-13T18:22:05Z",
        body=drafts.checked_body(BODY),
    )
    return dataclasses.replace(held, **changes)


def test_a_draft_renders_to_a_record_that_parses_back_to_it() -> None:
    """Render and parse are one shape: nothing is lost or reinterpreted in between."""
    draft = _draft()
    rendered = drafts.render(draft)

    assert drafts.parse(rendered) == draft
    assert rendered.startswith(
        '---\ntitle: "The sweep skips a family"\nstatus: "todo"\nproject: "run-a"\nmetadata:\n'
    )


@pytest.mark.parametrize(
    ("author", "transcript"),
    [
        (Author("node", "sweep-node", None), "onepipeline transcript run-a sweep-node"),
        (Author("node", drafts.UNRESOLVED, None), "onepipeline transcript run-a"),
        (
            Author("monitor", None, "monitor"),
            'onepipeline monitor run-a --filter \'{"include":[{"member":"monitor"}]}\'',
        ),
        (
            Author("pacemaker", None, "check-in"),
            'onepipeline monitor run-a --filter \'{"include":[{"member":"check-in"}]}\'',
        ),
        (Author("manager", None, None), None),
    ],
    ids=["node", "unresolved", "monitor", "pacemaker", "manager"],
)
def test_each_author_kind_points_at_the_read_of_its_own_turns(
    author: Author, transcript: str | None
) -> None:
    """The pointer is derived from the author, and a stored one must be that derivation."""
    draft = _draft(author=author)
    assert draft.transcript == transcript
    assert drafts.record(draft)["transcript"] == transcript
    assert drafts.parse(drafts.render(draft)) == draft


def test_a_store_item_validates_to_the_same_draft() -> None:
    """What `onetaskgraph task show --json` reports is read by the same validation."""
    draft = _draft()
    item: dict[str, object] = {
        "title": draft.title,
        "content": draft.body,
        "status": {"category": "todo", "name": "todo"},
        "project": RUN,
        "metadata": {drafts.DRAFT_KEY: drafts.record(draft)},
    }
    assert drafts.from_store_item(item) == draft
    assert drafts.from_store_item(item | {"status": "todo"}) == draft
    with pytest.raises(Refused, match="`orchestrator.follow-up-draft` metadata"):
        drafts.from_store_item(item | {"metadata": "not a mapping"})


def test_a_draft_project_names_its_run() -> None:
    rendered = drafts.render_project(RUN)
    assert '"orchestrator.follow-up-drafts": {"schema": 1, "run": "run-a"}' in rendered
    assert 'title: "Follow-ups drafted in run run-a"' in rendered


@pytest.mark.parametrize(
    ("title", "stem"),
    [
        ("The sweep skips a family", "20260913T182205Z-the-sweep-skips-a-family"),
        ("!!!", "20260913T182205Z-follow-up"),
        ("a" * 47 + " b", "20260913T182205Z-" + "a" * 47),
    ],
    ids=["slug", "no-letters", "truncated-at-a-separator"],
)
def test_a_draft_id_is_its_time_then_a_bounded_slug_of_its_title(title: str, stem: str) -> None:
    assert _draft(title=title).stem == stem


def _record_of(draft: Draft) -> dict[str, object]:
    held: dict[str, object] = json.loads(json.dumps(drafts.record(draft)))
    return held


def _with(mutate: Callable[[dict[str, object]], None]) -> str:
    """The rendered record of a valid draft, with its metadata mutated."""
    draft = _draft()
    held = _record_of(draft)
    mutate(held)
    return frontmatter(
        {
            "title": draft.title,
            "status": "todo",
            "project": RUN,
            "metadata": {drafts.DRAFT_KEY: held},
        },
        draft.body,
    )


def _set(key: str, value: object) -> Callable[[dict[str, object]], None]:
    def mutate(held: dict[str, object]) -> None:
        held[key] = value

    return mutate


def _nested(outer: str, key: str, value: object) -> Callable[[dict[str, object]], None]:
    def mutate(held: dict[str, object]) -> None:
        inner = held[outer]
        assert isinstance(inner, dict)
        inner[key] = value

    return mutate


def _drop(key: str) -> Callable[[dict[str, object]], None]:
    def mutate(held: dict[str, object]) -> None:
        del held[key]

    return mutate


@pytest.mark.parametrize(
    ("mutate", "refusal"),
    [
        (_drop("paths"), "draft` metadata is not a mapping"),
        (_set("schema", True), "schema True"),
        (_set("schema", 2), "schema 2"),
        (_set("run", "run-b"), "is not its project"),
        (_set("author", {"kind": "node"}), "author is not a mapping"),
        (_nested("author", "kind", 3), "author kind is not a string"),
        (_nested("author", "kind", "robot"), "names no author kind"),
        (_nested("author", "node", None), "names None as its node"),
        (_nested("author", "node", 7), "author node is neither"),
        (_nested("author", "member", "monitor"), "--member names a dag-scope member"),
        (_nested("dispatch", "cwd", "relative"), "dispatch cwd is not an absolute path"),
        (_nested("dispatch", "head", 1), "dispatch head is neither"),
        (_nested("dispatch", "session", ""), "dispatch session is empty or carries"),
        (_nested("dispatch", "scratch", "/s\ncratch"), "dispatch scratch is empty or carries"),
        (_nested("author", "node", "impl\x1b[2J"), "author node is empty or carries"),
        (_set("repository", "https://github.com/a/b"), "not a normalized origin"),
        (_set("paths", "scripts"), "paths are not a list of strings"),
        (_set("paths", ["/etc/passwd"]), "is not a path inside the repository"),
        (_set("drafted_at", "yesterday"), "drafted_at is not an RFC 3339"),
        (_set("drafted_at", "2026-02-30T12:00:00Z"), "drafted_at is not an RFC 3339"),
        (_set("drafted_at", "2026-09-13T24:00:00Z"), "drafted_at is not an RFC 3339"),
        (_set("transcript", "onepipeline transcript run-a"), "is not the read its author has"),
    ],
)
def test_every_departure_of_a_stored_record_is_refused(
    mutate: Callable[[dict[str, object]], None], refusal: str
) -> None:
    """A record that departs from the shape anywhere is refused, naming where."""
    with pytest.raises(Refused, match=refusal.replace("(", r"\(")):
        drafts.parse(_with(mutate))


@pytest.mark.parametrize(
    ("text", "refusal"),
    [
        ("", "does not open with a `---` fence"),
        ("title: x\n", "does not open with a `---` fence"),
        ('---\ntitle: "x"\n', "frontmatter is never closed"),
        ("---\n  loose\n---\n", "a line this cannot read"),
        ("---\ntitle: not json\n---\n", "a value that is not JSON"),
        ('---\nmetadata:\n  "k": {broken\n---\n', "a value that is not JSON"),
        ('---\ntitle: 3\nstatus: "todo"\n---\nbody', "no string title and body"),
        ('---\ntitle: "x"\nstatus: "done"\n---\nbody', "status is 'done'"),
        ('---\ntitle: "x"\ntitle: "y"\n---\nbody', "states its 'title' field twice"),
        ('---\ntitle: "x"\nowner: "y"\n---\nbody', "a field this does not write, 'owner'"),
        ('---\nmetadata:\n  "other": 1\n---\nbody', "a field this does not write, 'other'"),
        ('---\nmetadata:\n  "k\\q": 1\n---\nbody', "a value that is not JSON"),
    ],
)
def test_a_record_this_did_not_write_is_refused_rather_than_guessed_at(
    text: str, refusal: str
) -> None:
    with pytest.raises(Refused, match=refusal):
        drafts.parse(text)


def test_a_draft_record_stating_its_metadata_twice_is_refused() -> None:
    """A second copy of the record is refused rather than silently read in place of the first."""
    rendered = drafts.render(_draft())
    opening = f'  "{drafts.DRAFT_KEY}"'
    entry = next(line for line in rendered.split("\n") if line.startswith(opening))
    with pytest.raises(Refused, match="field twice"):
        drafts.parse(rendered.replace(entry, f"{entry}\n{entry}"))


def test_a_stored_title_must_be_the_trimmed_one_line_title_it_was_written_as() -> None:
    draft = _draft()
    rendered = drafts.render(draft).replace(
        'title: "The sweep skips a family"', 'title: " The sweep skips a family"'
    )
    with pytest.raises(Refused, match="title is not trimmed"):
        drafts.parse(rendered)


@pytest.mark.parametrize(
    ("title", "refusal"),
    [
        ("   ", "the title is empty"),
        ("two\nlines", "line break or another control character"),
        ("x" * 121, "121 characters, over the 120"),
    ],
)
def test_a_title_a_draft_cannot_hold_is_refused(title: str, refusal: str) -> None:
    with pytest.raises(Refused, match=refusal):
        drafts.checked_title(title)


def test_a_title_is_held_trimmed() -> None:
    assert drafts.checked_title("  a title  ") == "a title"


def test_a_repository_is_held_as_its_normalized_origin() -> None:
    assert drafts.checked_repository("https://github.com/a/b.git") == "github.com/a/b"
    with pytest.raises(Refused, match="not a normalized origin"):
        drafts.checked_repository("a checkout path")


@pytest.mark.parametrize("path", ["", "/abs", "a/../b", "bad\x07path"])
def test_a_path_outside_the_repository_is_refused(path: str) -> None:
    with pytest.raises(Refused, match="is not a path inside the repository"):
        drafts.checked_paths([path])


def test_a_run_that_cannot_name_a_project_is_refused() -> None:
    assert drafts.checked_run(RUN) == RUN
    with pytest.raises(Refused, match="cannot name a draft project"):
        drafts.checked_run("../elsewhere")


@pytest.mark.parametrize(
    ("body", "refusal"),
    [
        ("No headings at all.", r"no `## What happened` heading;"),
        (
            "## What happened\nx\n\n## Why it is out of scope\ny\n\n## Evidence\nz\n",
            r"no `## Where` heading after `## What happened`",
        ),
        (
            "## Where\nx\n\n## What happened\ny\n\n## Why it is out of scope\nz\n## Evidence\nw",
            r"no `## Where` heading after `## What happened`",
        ),
        (
            "## What happened\n\n## Where\nx\n\n## Why it is out of scope\ny\n\n## Evidence\nz",
            r"`## What happened` section is empty",
        ),
        (
            "## What happened\nx\n## Where\ny\n## Why it is out of scope\nz\n"
            "## Evidence\n~~~\n## Evidence\n```\nstill fenced\n~~~~\n",
            None,
        ),
        (
            "## What happened\nx\n## Where\ny\n## Why it is out of scope\nz\n```\n## Evidence\n```",
            r"no `## Evidence` heading after `## Why it is out of scope`",
        ),
    ],
    ids=["none", "missing", "out-of-order", "empty", "fences-nest", "fenced-heading"],
)
def test_the_body_carries_every_heading_in_order_each_with_content(
    body: str, refusal: str | None
) -> None:
    """A `## ` line inside a code fence is code, and a closing fence must match its opener."""
    if refusal is None:
        assert drafts.checked_body(f"\n\n{body}\n\n") == body.strip()
        return
    with pytest.raises(Refused, match=refusal):
        drafts.checked_body(body)


@pytest.mark.parametrize(
    ("kind", "node", "member", "refusal"),
    [
        ("robot", None, None, "names no author kind"),
        ("monitor", None, None, "--as monitor needs --member"),
        ("pacemaker", None, "bad name!", "is not a dag-scope member name"),
        ("manager", None, "monitor", "--as manager is not one"),
        ("node", "", None, "names '' as its node"),
        ("manager", "a-node", None, "names 'a-node' as its node"),
    ],
)
def test_an_author_names_exactly_what_its_kind_names(
    kind: str, node: str | None, member: str | None, refusal: str
) -> None:
    with pytest.raises(Refused, match=refusal):
        drafts.checked_author(kind, node, member)


def _write_entry(runs: Path, run: str, name: str, content: object) -> None:
    registry = runs / run / drafts.DISPATCH_REGISTRY
    registry.mkdir(parents=True, exist_ok=True)
    text = content if isinstance(content, str) else json.dumps(content)
    (registry / name).write_text(text, encoding="utf-8")


def _entry(pid: int, node: str, started: str | None) -> dict[str, object]:
    return {"node": node, "pid": pid, "host": "here", "started": started}


@pytest.fixture
def child() -> Iterator[int]:
    """A process this test starts, so an ancestor walk has a real parent to find: this one."""
    started = subprocess.Popen(["sleep", "60"])  # noqa: S603, S607 - a child to walk up from
    try:
        yield started.pid
    finally:
        started.kill()
        started.wait()


def _live_start(pid: int) -> str:
    started = drafts.process_started(pid)
    assert started is not None, f"process {pid} has no start time to record"
    return f"{drafts.PROC_STAT_START}{started}"


def test_a_node_is_resolved_from_the_dispatch_a_process_descends_from(
    tmp_path: Path, child: int
) -> None:
    """The walk starts at the process itself, and a pid matches only on the kernel's start time."""
    _write_entry(
        tmp_path, RUN, "self.json", _entry(os.getpid(), "the-node", _live_start(os.getpid()))
    )

    assert drafts.resolve_node(RUN, runs_root=tmp_path, pid=child) == "the-node"
    assert drafts.resolve_node(RUN, runs_root=tmp_path, pid=os.getpid()) == "the-node"
    assert drafts.lineage(child)[:2] == [child, os.getpid()]


def test_a_recorded_start_time_the_live_process_disagrees_with_is_rejected(
    tmp_path: Path, child: int
) -> None:
    """A pid the kernel has handed to another process names no dispatch."""
    _write_entry(
        tmp_path, RUN, "stale.json", _entry(os.getpid(), "stale-node", f"{drafts.PROC_STAT_START}1")
    )

    assert drafts.resolve_node(RUN, runs_root=tmp_path, pid=child) == drafts.UNRESOLVED


def test_registry_entries_that_cannot_be_checked_are_left_out(tmp_path: Path) -> None:
    """Unreadable, unchecked, or incomplete entries name nothing rather than a guess."""
    pid = os.getpid()
    _write_entry(tmp_path, RUN, "broken.json", "{not json")
    _write_entry(tmp_path, RUN, "list.json", [pid])
    _write_entry(tmp_path, RUN, "unstamped.json", _entry(pid, "a", None))
    _write_entry(tmp_path, RUN, "other-spelling.json", _entry(pid, "b", "boot:1"))
    _write_entry(tmp_path, RUN, "no-node.json", _entry(pid, "", _live_start(pid)))
    _write_entry(tmp_path, RUN, "forged-node.json", _entry(pid, "a\nnode", _live_start(pid)))
    _write_entry(tmp_path, RUN, "not-a-time.json", _entry(pid, "e", "linux-proc-stat:soon"))
    _write_entry(
        tmp_path, RUN, "bool-pid.json", {"node": "c", "pid": True, "started": _live_start(pid)}
    )
    (tmp_path / RUN / drafts.DISPATCH_REGISTRY / "unreadable.json").mkdir()
    _write_entry(tmp_path, RUN, "good.json", _entry(pid + 1, "d", "linux-proc-stat:9"))

    assert drafts.registered_dispatches(tmp_path, RUN) == {
        pid + 1: drafts.RegisteredDispatch("d", "9")
    }
    assert drafts.registered_dispatches(tmp_path, "no-such-run") == {}
    assert drafts.resolve_node("no-such-run", runs_root=tmp_path, pid=pid) == drafts.UNRESOLVED


def test_a_process_that_is_gone_has_no_start_time_and_no_parent() -> None:
    gone = 2**22 + 12345
    assert drafts.process_started(gone) is None
    assert drafts.process_parent(gone) is None
    assert drafts.lineage(gone) == [gone]


def test_the_runs_root_is_the_engines_own_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(drafts.RUNS_ROOT_ENV, str(tmp_path))
    assert drafts.runs_root() == tmp_path
    monkeypatch.delenv(drafts.RUNS_ROOT_ENV)
    monkeypatch.chdir(tmp_path)
    named = drafts.runs_root()
    assert named is not None
    assert named.resolve() == (tmp_path / drafts.DEFAULT_RUNS_ROOT).resolve()


def test_a_runs_root_carrying_a_control_character_names_no_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not read as a path, and not replaced by the default: the node is left unresolved."""
    monkeypatch.setenv(drafts.RUNS_ROOT_ENV, f"{tmp_path}\nruns")
    assert drafts.runs_root() is None
    assert drafts.resolve_node(RUN, runs_root=None, pid=os.getpid()) == drafts.UNRESOLVED


def _supervision_readings() -> ModuleType:
    """`scripts/supervision-readings.py`, loaded by path: its name carries a hyphen."""
    path = REPO_ROOT / "scripts" / "supervision-readings.py"
    spec = importlib.util.spec_from_file_location("supervision_readings", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_both_readers_of_the_dispatch_registry_name_the_same_store() -> None:
    """Every engine-owned name the two readers share is one value, not two memories of it."""
    readings = _supervision_readings()
    for shared in (
        "DISPATCH_REGISTRY",
        "DISPATCH_NODE",
        "DISPATCH_PID",
        "DISPATCH_STARTED",
        "PROC_STAT_START",
        "RUNS_ROOT_ENV",
        "DEFAULT_RUNS_ROOT",
    ):
        assert getattr(drafts, shared) == getattr(readings, shared), (
            f"orchestrator/follow_up_drafts.py and scripts/supervision-readings.py disagree "
            f"about {shared}; one of them is reading a registry the engine does not write"
        )


@pytest.mark.parametrize("stale", [False, True], ids=["live", "stale"])
def test_both_readers_of_the_dispatch_registry_give_the_same_answer(
    tmp_path: Path, child: int, stale: bool
) -> None:
    """One registry, one process tree: the dispatch a process sits under is one answer.

    The supervision reading also accepts an entry recording no start time, as unverified;
    a draft does not, because it names a node permanently. So the registry here records
    start times, which is what the engine writes, and on that the two must agree.
    """
    readings = _supervision_readings()
    (tmp_path / RUN).mkdir()
    (tmp_path / RUN / readings.LAUNCH_RECORD).write_text("{}", encoding="utf-8")
    started = f"{drafts.PROC_STAT_START}1" if stale else _live_start(os.getpid())
    _write_entry(tmp_path, RUN, "self.json", _entry(os.getpid(), "the-node", started))

    by_process, _ = readings._dispatches(tmp_path)
    supervised = readings._under_dispatch(child, by_process)
    drafted = drafts.resolve_node(RUN, runs_root=tmp_path, pid=child)

    if stale:
        assert supervised is None
        assert drafted == drafts.UNRESOLVED
    else:
        assert supervised is not None and supervised.startswith(f"{RUN}/the-node ")
        assert drafted == "the-node"
    assert readings._started(child) == drafts.process_started(child)


def _git(cwd: Path, *arguments: str) -> None:
    subprocess.run(  # noqa: S603 - a repository this test owns
        ["git", "-C", str(cwd), *arguments],  # noqa: S607
        check=True,
        capture_output=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
        },
    )


def test_the_dispatch_stamp_reads_the_environment_and_the_working_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "-q", "-b", "work")
    _git(repository, "commit", "-q", "--allow-empty", "-m", "one")
    monkeypatch.setenv(drafts.NODE_SCRATCH_ENV, "/scratch")
    monkeypatch.setenv(drafts.SESSION_ENV, "s-0123456789ab")

    stamped = drafts.stamp_dispatch(repository)
    assert (stamped.scratch, stamped.session, stamped.cwd, stamped.branch) == (
        "/scratch",
        "s-0123456789ab",
        str(repository),
        "work",
    )
    assert stamped.head is not None and len(stamped.head) == 40

    _git(repository, "checkout", "-q", "--detach")
    assert drafts.stamp_dispatch(repository).branch is None

    monkeypatch.setenv(drafts.NODE_SCRATCH_ENV, "/scratch\nforged: line")
    monkeypatch.setenv(drafts.SESSION_ENV, "s-0123456789ab\x1b[2J")
    forged = drafts.stamp_dispatch(repository)
    assert (forged.scratch, forged.session) == (None, None), "a control character was stamped"

    monkeypatch.delenv(drafts.NODE_SCRATCH_ENV)
    monkeypatch.delenv(drafts.SESSION_ENV)
    outside = drafts.stamp_dispatch(tmp_path)
    assert (outside.scratch, outside.session, outside.branch, outside.head) == (None,) * 4

    monkeypatch.setenv("PATH", str(tmp_path / "no-git-here"))
    assert drafts.stamp_dispatch(repository).head is None


def test_a_draft_never_lands_on_another_and_its_project_is_written_once(tmp_path: Path) -> None:
    draft = _draft()
    first = drafts.write(tmp_path, draft)
    second = drafts.write(tmp_path, draft)

    assert first == (
        f"drafts:{RUN}/drafts/{draft.stem}",
        tmp_path / "tasks" / RUN / "drafts" / f"{draft.stem}.md",
    )
    assert second[0] == f"drafts:{RUN}/drafts/{draft.stem}-2"
    assert drafts.parse(second[1].read_text(encoding="utf-8")) == draft
    project = tmp_path / "projects" / f"{RUN}.md"
    assert project.read_text(encoding="utf-8") == drafts.render_project(RUN)
    assert sorted(path.name for path in tmp_path.rglob("*") if path.is_file()) == sorted(
        [f"{draft.stem}.md", f"{draft.stem}-2.md", f"{RUN}.md"]
    )


class _Terminal(io.StringIO):
    def isatty(self) -> bool:
        return True


@pytest.fixture
def drafting(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A process ready to draft: a root exported, a run named, a body on stdin."""
    root = tmp_path / "root"
    monkeypatch.setenv(ROOT_ENV, str(root))
    monkeypatch.setenv(drafts.RUN_ID_ENV, RUN)
    monkeypatch.setenv(drafts.RUNS_ROOT_ENV, str(tmp_path / "runs"))
    monkeypatch.delenv(drafts.NODE_SCRATCH_ENV, raising=False)
    monkeypatch.delenv(drafts.SESSION_ENV, raising=False)
    monkeypatch.setattr(sys, "stdin", io.StringIO(BODY))
    monkeypatch.chdir(tmp_path)
    return root


ARGUMENTS = ["--title", "The sweep skips a family", "--repository", REPOSITORY]


def test_the_command_writes_a_stamped_draft_and_names_it(
    drafting: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert drafts.main([*ARGUMENTS, "--path", "scripts/sweep.sh"], root_env=ROOT_ENV) == 0

    (written,) = (drafting / "tasks" / RUN / "drafts").iterdir()
    draft = drafts.parse(written.read_text(encoding="utf-8"))
    assert draft.author == Author("node", drafts.UNRESOLVED, None)
    assert draft.dispatch.cwd == str(drafting.parent)
    assert draft.paths == ("scripts/sweep.sh",)
    assert capsys.readouterr().out == f"drafted drafts:{RUN}/drafts/{written.stem} at {written}\n"


def test_a_manager_draft_names_no_node_and_no_transcript(drafting: Path) -> None:
    arguments = [*ARGUMENTS, "--as", "manager", "--run", "run-b"]
    assert drafts.main(arguments, root_env=ROOT_ENV) == 0
    (written,) = (drafting / "tasks" / "run-b" / "drafts").iterdir()
    draft = drafts.parse(written.read_text(encoding="utf-8"))
    assert (draft.author, draft.transcript) == (Author("manager", None, None), None)


@pytest.mark.parametrize(
    ("arguments", "prepare", "refusal"),
    [
        (["--as", "monitor"], None, "--as monitor needs --member"),
        ([], lambda m: m.delenv(drafts.RUN_ID_ENV), "no run can be named"),
        (["--title", " "], None, "the title is empty"),
        (["--repository", "nope"], None, "not a normalized origin"),
        (["--path", "/abs"], None, "not a path inside the repository"),
        ([], lambda m: m.setattr(sys, "stdin", _Terminal()), "stdin is a terminal"),
        (
            [],
            lambda m: m.setattr(sys, "stdin", io.StringIO("## Where\nx")),
            "no `## What happened`",
        ),
        ([], lambda m: m.delenv(ROOT_ENV), "the draft root is unset"),
        ([], lambda m: m.setenv(ROOT_ENV, "relative/root"), "is not an absolute path"),
        ([], lambda m: m.setenv(ROOT_ENV, "/drafts\nroot"), "carries a control character"),
    ],
    ids=[
        "member",
        "run",
        "title",
        "repository",
        "path",
        "terminal",
        "heading",
        "root-unset",
        "root-relative",
        "root-control",
    ],
)
def test_every_refusal_exits_2_says_what_to_do_and_writes_nothing(
    drafting: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    arguments: list[str],
    prepare: Callable[[pytest.MonkeyPatch], None] | None,
    refusal: str,
) -> None:
    if prepare is not None:
        prepare(monkeypatch)
    assert drafts.main([*ARGUMENTS, *arguments], root_env=ROOT_ENV) == drafts.REFUSED
    assert refusal in capsys.readouterr().err
    assert not drafting.exists()


def test_a_root_that_cannot_be_created_or_written_is_refused(
    drafting: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    blocker = drafting.parent / "a-file"
    blocker.write_text("", encoding="utf-8")
    monkeypatch.setenv(ROOT_ENV, str(blocker / "root"))
    assert drafts.main(ARGUMENTS, root_env=ROOT_ENV) == drafts.REFUSED
    assert "could not be created" in capsys.readouterr().err

    locked = drafting.parent / "locked"
    locked.mkdir(mode=0o500)
    monkeypatch.setenv(ROOT_ENV, str(locked))
    monkeypatch.setattr(sys, "stdin", io.StringIO(BODY))
    assert drafts.main(ARGUMENTS, root_env=ROOT_ENV) == drafts.REFUSED
    assert "may write into" in capsys.readouterr().err
    assert list(locked.iterdir()) == []


def test_a_working_directory_that_is_gone_is_refused(
    drafting: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    gone = drafting.parent / "gone"
    gone.mkdir()
    monkeypatch.chdir(gone)
    gone.rmdir()
    assert drafts.main(ARGUMENTS, root_env=ROOT_ENV) == drafts.REFUSED
    assert "working directory could not be read" in capsys.readouterr().err


def test_a_write_the_filesystem_refuses_is_a_failure_not_a_refusal(
    drafting: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    drafting.mkdir()
    (drafting / "tasks").write_text("", encoding="utf-8")
    assert drafts.main(ARGUMENTS, root_env=ROOT_ENV) == drafts.FAILED
    assert "could not be written under" in capsys.readouterr().err


def test_a_malformed_command_line_is_refused_with_the_way_to_the_contract(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exited:
        drafts.main(["--as", "robot"], root_env=ROOT_ENV)
    assert exited.value.code == drafts.REFUSED
    assert "run it with --help" in capsys.readouterr().err
