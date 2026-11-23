"""`just groom`, driven as a manager runs it, over a stand-in board and small repositories.

Only this host's half: what `onefollowup groom` decides of a verdict — which ticket closes,
which duplicate survives, what each comment and record says — is proven in onefollowup's own
repository. This journey holds the recipe to running that verb as onefollowup's own flow: one
`follow-up-runs` plan from `templates/follow-up-groom.md.j2` under `onepipeline flow run`,
whose `prepare`, `verify`, `check` and `publish` steps are the recipe again, and whose verify
step runs one attached verifying run per shard prepare wrote, from that shard's own task.

The destination is a local Markdown folder standing in for the `followups` board, holding
four open tickets about one small git repository that `https://github.com/` is pointed at by
the journey's own git configuration and which a scratch `onevcs` registry declares public: one
its `main` has since fixed, two sharing one root cause, and one that stands. **The paid model
alone is doubled**: `tests/e2e/fake_codex.py` stands in for the follow-up agent, whose turn
runs `scripted_agent.py groom`, writing each ticket's verdict and running the real
`onefollowup groom check`.

A second `just groom` straight after finds every ticket recorded and unchanged, so it
selects nothing and writes nothing to the destination.

llmlint: ignore-file[test_tiers_split_by_project_not_by_marker] `xdist_group` selects an xdist
worker under this project's `--dist loadgroup`, keeping the module-scoped pass on one
worker; it selects no test tier.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Iterator, Mapping
from enum import StrEnum
from pathlib import Path
from typing import Literal, NamedTuple

import follow_up_store
import follow_up_variables
import pytest
import short_state
import yaml
from harness_indirections import established_indirections
from project_fixtures import helper
from scratch_identity import seeded
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

pytestmark = [pytest.mark.usefixtures("module_tmpdir"), pytest.mark.xdist_group("groom")]

FAKE_CODEX = helper("fake_codex.py")
PAID_PROVIDER_GUARD = helper("no-paid-provider")
AGENT = Path(__file__).with_name("scripted_agent.py")
INDIRECTION_CALLER = "tests/e2e/follow_ups/test_groom_e2e.py"
SESSION = "e2e-groom"

#: What an enclosing dispatch exports that would decide these launches for them.
INHERITED_PREFIXES = ("ONEFOLLOWUP_",)
INHERITED = (
    "ONEPIPELINE_LAUNCHER",
    "ONEPIPELINE_LAUNCHER_SESSION",
    "ONEPIPELINE_RUN_ID",
    "ONEPIPELINE_RUN_ROOT",
    "ONEPIPELINE_NODE_ID",
    "ONEPIPELINE_NODE_SCRATCH_DIR",
    "ONEPIPELINE_FLOW",
    "ONEPIPELINE_HOOK",
    "ONEVCS_SESSION",
    "ORCHESTRATOR_ASK_MANAGER",
    "ONETASKGRAPH_DEFAULT_SOURCES",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_SESSION_ID",
    "CODEX_THREAD_ID",
    "CODEX_SESSION_ID",
    "GIT_CONFIG_GLOBAL",
    "ONEVCS_HOME",
    *follow_up_variables.all_names(),
    follow_up_store.RUNS_ROOT,
)

#: The repository every ticket is about, as a ticket names it.
REPOSITORY: Repository = "github.com/acme/widget"

#: The marker the shard's task carries, from this host's `follow-up-task` layer.
GROOM_MARKER = "Verify one shard of grooming batch"

#: A commit of the journey's repository, as git names it.
type Commit = str
#: A ticket's title, which the doubled agent keys its verdicts by.
type Title = str
#: A repository as a ticket names it, `host/owner/name`.
type Repository = str


class Item(StrEnum):
    """The tickets on the board, by item id."""

    FIXED = "fixed"
    SURVIVOR = "survivor"
    DUPLICATE = "duplicate"
    KEPT = "kept"


FIXED, SURVIVOR, DUPLICATE, KEPT = Item.FIXED, Item.SURVIVOR, Item.DUPLICATE, Item.KEPT
#: Each ticket's title.
TITLES: dict[Item, Title] = {
    FIXED: "widget: parsing an empty file crashes",
    SURVIVOR: "widget: the cache is never invalidated",
    DUPLICATE: "widget: stale values after an edit",
    KEPT: "widget: the CLI has no --version flag",
}


class Groomed(NamedTuple):
    """One pass of bare `just groom`, and what it left."""

    first: subprocess.CompletedProcess[str]
    second: subprocess.CompletedProcess[str]
    after_first: dict[str, str]
    after_second: dict[str, str]
    seeded: dict[str, str]
    store: follow_up_store.Store
    runs: Path
    fix: Commit


def _git(directory: Path, environment: Mapping[str, str], *arguments: str) -> str:
    ran = subprocess.run(  # noqa: S603 - git over the journey's own repository
        ["git", *arguments],  # noqa: S607 - git from the search path
        cwd=directory,
        env=dict(environment),
        text=True,
        capture_output=True,
        check=True,
    )
    return ran.stdout.strip()


def _repository(root: Path, environment: Mapping[str, str]) -> Commit:
    """The repository, two commits on `main`: the second fixes the empty-file crash."""
    path = root / "repos" / "github.com" / "acme" / "widget.git"
    (path / "src").mkdir(parents=True)
    _git(path, environment, "init", "-q", "--initial-branch=main")
    (path / "src" / "parse.py").write_text("def parse(text):\n    return text[0]\n", "utf-8")
    (path / "src" / "cache.py").write_text("CACHE = {}\n", "utf-8")
    _git(path, environment, "add", "--all")
    _git(path, environment, "commit", "-qm", "the first commit")
    (path / "src" / "parse.py").write_text(
        "def parse(text):\n    return text[0] if text else None\n", "utf-8"
    )
    _git(path, environment, "commit", "-qam", "fix: parse an empty file")
    return _git(path, environment, "rev-parse", "HEAD")


class Verdict(NamedTuple):
    """One ticket's verdict record, less the `schema_version` and `ticket` the agent adds,
    its `duplicate_of` naming the other ticket by title."""

    verdict: Literal["fixed", "keep"]
    confidence: Literal["high", "medium", "low"]
    evidence: str
    evidence_paths: tuple[str, ...]
    deciding_commit: Commit | None = None
    duplicate_of: Title | None = None
    root_cause: str | None = None
    seam_repository: Repository | None = None


def _verdicts(fix: Commit) -> dict[Title, Verdict]:
    """What the doubled agent judges of each ticket, keyed by its title."""
    cache = "The cache is never invalidated on an edit."
    return {
        TITLES[FIXED]: Verdict(
            "fixed",
            "high",
            "src/parse.py returns None for empty text since the fix commit.",
            ("src/parse.py",),
            deciding_commit=fix,
        ),
        TITLES[SURVIVOR]: Verdict(
            "keep",
            "high",
            "src/cache.py still holds one module-level dict nothing clears.",
            ("src/cache.py", "src/parse.py"),
            duplicate_of=TITLES[DUPLICATE],
            root_cause=cache,
            seam_repository=REPOSITORY,
        ),
        TITLES[DUPLICATE]: Verdict(
            "keep",
            "high",
            "Stale values come from the cache src/cache.py never clears.",
            ("src/cache.py",),
            duplicate_of=TITLES[SURVIVOR],
            root_cause=cache,
            seam_repository=REPOSITORY,
        ),
        TITLES[KEPT]: Verdict(
            "keep", "high", "No module of the repository defines a --version flag.", ("src",)
        ),
    }


def _seed(store: follow_up_store.Store) -> dict[str, str]:
    """The four open tickets, at a status grooming may close, created oldest first."""
    tasks = store.board / "tasks"
    tasks.mkdir(parents=True, exist_ok=True)
    for item, title in TITLES.items():
        (tasks / f"{item}.md").write_text(
            f"---\ntitle: {json.dumps(title)}\nstatus: backlog\npriority: low\n"
            f"repositories: [{REPOSITORY}]\n---\nReported against {REPOSITORY}.\n",
            encoding="utf-8",
        )
    return _board(store)


def _board(store: follow_up_store.Store) -> dict[str, str]:
    return {
        path.stem: path.read_text("utf-8") for path in sorted((store.board / "tasks").glob("*.md"))
    }


def _environment(root: Path) -> tuple[dict[str, str], follow_up_store.Store]:
    environment = {
        name: value
        for name, value in os.environ.items()
        if name not in INHERITED and not name.startswith(INHERITED_PREFIXES)
    }
    store = follow_up_store.isolated(root)
    environment = store.over(environment)
    environment["CLAUDE_CODE_SESSION_ID"] = SESSION
    # The store classifies a ticket by the repositories it names, and one no registry
    # declares public is private, which the stand-in board refuses: so the repository is
    # registered public in a scratch `onevcs` registry of the journey's own.
    environment["ONEVCS_HOME"] = str(seeded(root / "identity", public=(REPOSITORY,)).home)
    environment["ONEPIPELINE_RUNS_DIR"] = str(root / "runs")
    environment["XDG_STATE_HOME"] = str(short_state.state_home(root))
    # The repositories a ticket names are fetched from `https://github.com/...`, which the
    # journey's own git configuration points at its local repositories.
    gitconfig = root / "gitconfig"
    gitconfig.write_text(
        "[user]\n\tname = Groom Journey\n\temail = groom@example.invalid\n"
        f'[url "file://{root / "repos" / "github.com"}/"]\n\tinsteadOf = https://github.com/\n'
        '[uploadpack]\n\tallowFilter = true\n[protocol "file"]\n\tallow = always\n',
        encoding="utf-8",
    )
    environment["GIT_CONFIG_GLOBAL"] = str(gitconfig)
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEHARNESS_BIN_CODEX"] = str(FAKE_CODEX)
    environment["PATH"] = f"{PAID_PROVIDER_GUARD}{os.pathsep}{environment['PATH']}"
    environment.update(established_indirections(INDIRECTION_CALLER))
    return environment, store


def _just(environment: Mapping[str, str], *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - this checkout's own recipe
        ["just", *arguments],  # noqa: S607 - `just` from the search path, as a manager runs it
        cwd=REPO_ROOT,
        env=dict(environment),
        text=True,
        capture_output=True,
        timeout=e2e_timeout(900),
        check=False,
    )


@pytest.fixture(scope="module")
def groomed(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Groomed]:
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    root = tmp_path_factory.mktemp("groom")
    environment, store = _environment(root)
    fix = _repository(root, environment)
    verdicts = root / "verdicts.json"
    verdicts.write_text(
        json.dumps({title: verdict._asdict() for title, verdict in _verdicts(fix).items()}),
        encoding="utf-8",
    )
    agent = root / "agent-commands.json"
    agent.write_text(
        json.dumps(
            {
                GROOM_MARKER: [
                    [sys.executable, str(AGENT), "groom", str(REPO_ROOT)] + [str(verdicts)]
                ]
            }
        ),
        encoding="utf-8",
    )
    environment["FAKE_CODEX_RUN_ON_MARKER"] = str(agent)
    environment["FAKE_CODEX_RUN_ON_MARKER_LOG"] = str(root / "agent-ran.jsonl")
    seeded = _seed(store)
    first = _just(environment, "groom")
    after_first = _board(store)
    second = _just(environment, "groom")
    yield Groomed(first, second, after_first, _board(store), seeded, store, root / "runs", fix)


def _front(text: str) -> str:
    return text.split("---\n", 2)[1]


def _record(text: str) -> dict[str, object]:
    """The ticket's `groom` record, as its front matter holds it."""
    metadata = yaml.safe_load(_front(text)).get("metadata") or {}
    record: dict[str, object] = metadata.get("orchestrator.groom") or {}
    return record


def _batch(groomed: Groomed) -> str:
    """The batch the first pass published, as its report's directory names it."""
    return _report(groomed).parent.name


def _report(groomed: Groomed) -> Path:
    reported = [
        line for line in groomed.first.stdout.splitlines() if line.startswith("groom report: ")
    ]
    assert len(reported) == 1, groomed.first.stdout + groomed.first.stderr
    return Path(reported[0].removeprefix("groom report: "))


def test_the_first_pass_closes_the_fixed_ticket_and_the_duplicate(groomed: Groomed) -> None:
    assert groomed.first.returncode == 0, groomed.first.stdout + groomed.first.stderr
    fixed, duplicate = groomed.after_first[FIXED], groomed.after_first[DUPLICATE]
    assert "status: done" in _front(fixed), fixed
    assert "status: cancelled" in _front(duplicate), duplicate
    assert 'kind="groom" verdict="fixed"' in fixed, fixed
    assert groomed.fix in fixed.split("## Comments", 1)[-1], fixed
    assert 'kind="groom" verdict="duplicate"' in duplicate, duplicate


def test_the_survivor_stays_open_with_its_consolidation_comment(groomed: Groomed) -> None:
    survivor = groomed.after_first[SURVIVOR]
    assert "status: backlog" in _front(survivor), survivor
    assert 'kind="groom" verdict="survivor"' in survivor, survivor


def test_every_verified_ticket_holds_its_groom_record(groomed: Groomed) -> None:
    batch = _batch(groomed)
    for item, text in groomed.after_first.items():
        record = _record(text)
        assert record.get("schema_version") == 1, (item, text)
        assert record.get("batch") == batch, (item, record)
        assert record.get("repository") == REPOSITORY, (item, record)
        assert record.get("commit") == groomed.fix, (item, record)


def test_a_kept_ticket_gets_no_comment_and_keeps_its_body(groomed: Groomed) -> None:
    kept = groomed.after_first[KEPT]
    assert "status: backlog" in _front(kept), kept
    assert "## Comments" not in kept, kept
    assert kept.split("---\n", 2)[2] == groomed.seeded[KEPT].split("---\n", 2)[2]


def test_the_recipe_names_the_report_publish_wrote(groomed: Groomed) -> None:
    report = _report(groomed)
    assert report.name == "report.md" and report.is_file(), report
    assert report.is_relative_to(groomed.store.work / "groom"), report
    assert TITLES[FIXED] in report.read_text("utf-8")


def test_a_second_pass_writes_nothing_to_the_destination(groomed: Groomed) -> None:
    assert groomed.second.returncode == 0, groomed.second.stdout + groomed.second.stderr
    assert groomed.after_second == groomed.after_first
    assert "no report was written" in groomed.second.stdout, groomed.second.stdout


def _projects(groomed: Groomed) -> dict[str, str]:
    """Every project the recipes created in the journey's `follow-up-runs` source: each
    one's id, and the template it was created from, as the store recorded both."""
    projects = {}
    for path in sorted((groomed.store.plans / "projects").glob("*.md")):
        metadata = yaml.safe_load(_front(path.read_text("utf-8")))["metadata"]
        projects[path.stem] = Path(metadata["onetaskgraph.template"]["template"]).name
    return projects


def _events(groomed: Groomed, run: str) -> list[dict[str, object]]:
    lines = (groomed.runs / run / "events.jsonl").read_text("utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def _at(events: list[dict[str, object]], kind: str, node: str | None = None) -> str:
    """When the first ``kind`` event (of ``node``, when named) was recorded."""
    for event in events:
        labels = event.get("labels")
        assert isinstance(labels, dict)
        if event["kind"] == kind and (node is None or labels.get("node") == node):
            return str(event["ts"])
    raise AssertionError(f"no {kind} event{f' of {node}' if node else ''}: {events}")


def test_each_pass_is_one_groom_plan_in_a_flow_with_one_attached_run_per_shard(
    groomed: Groomed,
) -> None:
    """Read from the recorded projects and run journals, never from what the recipe printed."""
    projects = _projects(groomed)
    plans = sorted(
        name for name, template in projects.items() if template == "follow-up-groom.md.j2"
    )
    shards = sorted(name for name in projects if name not in plans)
    # One groom plan per pass; the first pass's one shard, and none for the second.
    assert len(plans) == 2, projects
    assert [projects[shard] for shard in shards] == ["follow-up-groom-shard.md.j2"], projects
    runs = sorted(path.name for path in groomed.runs.iterdir() if (path / "launch.json").is_file())
    assert runs == sorted(plans + shards), runs
    first, shard = plans[0], shards[0]
    launches = {
        run: json.loads((groomed.runs / run / "launch.json").read_text("utf-8")) for run in runs
    }
    # Each plan runs under `onepipeline flow run`, in a flow of its own name.
    assert [launches[plan]["flow"] for plan in plans] == plans, launches
    # The shard's run is the first plan's verify step's, attached: in the same flow, started
    # after that step's command and settled before it exited.
    assert launches[shard]["flow"] == first, launches[shard]
    assert shard.startswith(_batch(groomed)), (shard, _batch(groomed))
    plan, verified = _events(groomed, first), _events(groomed, shard)
    assert _at(plan, "command-started", "verify") < _at(verified, "run-started")
    assert _at(verified, "driver-exited") < _at(plan, "command-exited", "verify")
    assert _at(verified, "node-dispatched", "verify")
    ran = [
        json.loads(line)
        for line in (groomed.runs.parent / "agent-ran.jsonl").read_text("utf-8").splitlines()
    ]
    assert [(entry["command"][2], entry["status"]) for entry in ran] == [("groom", 0)], ran


def test_no_planning_flow_project_or_run_is_created(groomed: Groomed) -> None:
    projects = _projects(groomed)
    assert set(projects.values()) == {"follow-up-groom.md.j2", "follow-up-groom-shard.md.j2"}
    runs = [path.name for path in groomed.runs.iterdir() if (path / "launch.json").is_file()]
    assert set(runs) == set(projects), (runs, projects)
