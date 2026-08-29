"""Refuse plan content nothing has reviewed, and record the review that clears it.

`personas/planner.yaml` carries a judge whose whole job is to find the ways a plan does
not deliver the request it came from. Every plan a *planner* writes is read by it. A
plan an operator writes by hand, and a planner's plan an operator then tweaks, are read
by nobody — and both of those have shipped here:

* one required each requirement to change *"only where the requirement, rather than the
  resolution, was what excluded the release"*, which is the opposite of what the target
  repository's own manifest says twelve lines above its pins. It was caught by reading
  the file before launching, which is not a control;
* one required a lockfile to resolve a sibling to an exact version. The sibling
  published a newer one between the task being written and the node being dispatched,
  the worker resolved the newest as that repository's convention demands, and the judge
  failed finished, gate-green work for doing the right thing.

Detecting *who typed* a node is the wrong question — it answers the first case and
misses the second. What this module detects is **content nothing has reviewed**, which
covers both with one rule: a task carries a record naming a digest of its own authored
content, and a task whose content does not hash to its record has not been reviewed.

Three properties are deliberate, and each is one this repository already defends for its
judged lint tier. Only a **pass** is recorded, so a review that found something leaves
nothing behind to replay. A record **is authoritative**: the deterministic check accepts
it and spends no judged turn, because a second opinion on identical content is how one
branch comes to hold two opposite verdicts. And there is **no escape hatch** — no flag,
no option, no environment variable — because an escape here is reached under exactly the
time pressure that produced both errors above.

**A record is written by this repository's own code and never by a dispatched agent**,
and that is structural rather than a rule anybody is asked to follow. The plan stores
this gate covers are the gitignored `.plans-local/` and `.plans/` of *this* checkout: a
dispatched worker runs in a worktree of its own, nothing it writes below either
directory is tracked, and so no branch it produces can carry a record back here. There
is no recipe, flag, or documented step by which a dispatch writes one either.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import NamedTuple, NewType, TypedDict

from orchestrator import plan_store
from orchestrator.plan_store import QualifiedTaskId, StoreTask
from orchestrator.root import REPO_ROOT

#: A digest of the review bar in force. Distinct from :data:`ReviewKey`, which is a
#: digest of one task's content *under* a bar: both are hex strings of the same length
#: and each is meaningless in the other's place, so a record compared against a bar
#: fingerprint would refuse every plan and never say why.
BarFingerprint = NewType("BarFingerprint", str)

#: The digest one task's authored content hashes to under one bar — what a record holds
#: and what the deterministic check compares against.
ReviewKey = NewType("ReviewKey", str)

#: Where one task's review record lives. A namespaced entry of the open metadata map a
#: task already carries, so it travels with the record it describes and stays visible to
#: anybody reading the plan — rather than in a sidecar this repository would then have to
#: keep in step with a store it does not own.
RECORD_KEY = "orchestrator.plan-review"

#: The bar this review is held to, and the schema its verdict is validated against. Both
#: are hashed into every key, so editing either invalidates every record made under the
#: previous one — a content-only key would leave a stale pass standing after the bar it
#: was granted under had moved.
BAR_FILES = (
    Path("personas") / "planner.yaml",
    Path("config") / "plan-review-verdict.schema.json",
)

#: The harness side that spends the judged turn: the supervisory routing, a finite
#: deadline, and the verdict schema, exactly as the change-request drafter is configured.
HARNESS_CONFIG = Path("oneharness.plan-review.toml")

#: The plan stores this repository plans against, and the only ones a planning closeout
#: looks at. `plans-local` is where a plan of this repository lives and `authoring` is
#: what `just plan` writes; the other configured sources are this repository's shipped
#: examples, the suite's fixtures, and a GitHub Projects board nothing plans against.
PLAN_SOURCES = ("plans-local", "authoring")


class By(StrEnum):
    """What wrote a record, as a closed vocabulary rather than two loose strings.

    Two writers exist and no third may appear without being named here, because the
    distinction is what an operator reads off a record: one spent a judged turn on this
    exact content, the other trusts the planner's own judge to have covered it. A
    provenance nothing declared would read as either.

    A `StrEnum`, because the value is written into the record as JSON and read back as
    a plain string by anybody looking at the plan.
    """

    REVIEW = "review-plan"
    PLANNING = "planning-closeout"


#: The two writers, named for the commands they are. Kept as module constants because
#: that is how every caller and every journey names them.
BY_REVIEW = By.REVIEW
BY_PLANNING = By.PLANNING

#: What the reviewer is asked, above the bar itself. Hashed into every key with the bar,
#: because a rewritten question is as much a change of review as a rewritten bar.
REVIEW_PROMPT = """\
You are reviewing ONE task of a plan before anything is dispatched from it. Below is
the review bar you hold it to, and then the task itself as its author wrote it.

Hold this one task to the parts of that bar which apply to a single node: that its
acceptance criteria genuinely prove the work is functional, that they could not all be
satisfied while the goal the task states is missed, that each one is satisfiable by the
worker inside its own dispatch, and that none of them names a procedure, a spelling, or
a perishable fact — a release number, a dependency version, a line count — in place of
the property it stands in for.

Do not rewrite the task and do not judge it on style. Answer with the JSON object the
response schema declares: whether it passes, and one sentence saying why. A refusal is
what stops this content reaching a dispatch, so refuse only what you can name.
"""


class Verdict(TypedDict):
    """One review turn's answer, in the two fields the response schema declares.

    Stated here as well as in `config/plan-review-verdict.schema.json` because the
    schema is what oneharness validates and this is what decides whether a record is
    written: the two have to agree, and `tests/e2e/test_plan_review_e2e.py` drives a
    real turn through both.
    """

    passes: bool
    reason: str


class Reviewed(NamedTuple):
    """What one `just review-plan` did, named rather than positional."""

    #: Tasks this run reviewed and recorded a pass for.
    recorded: int
    #: Tasks that already carried a record for their current content, so cost nothing.
    held: int
    #: One line per refusal, naming the task and the reason. Nothing was recorded for
    #: any of them, which is why this is a list rather than a count.
    refused: list[str]
    #: Why the run stopped early, when it did. A review is per task and each pass is
    #: recorded as it is granted, so a plan whose fourth task cannot be reviewed keeps
    #: the three records already written — and a diagnostic claiming nothing was
    #: recorded would send its reader looking for state that is there.
    stopped: str | None = None


def bar_fingerprint(root: Path = REPO_ROOT) -> BarFingerprint:
    """A digest of the review bar in force, over ``root``'s copy of the files it is."""
    digest = hashlib.sha256()
    digest.update(REVIEW_PROMPT.encode("utf-8"))
    for relative in BAR_FILES:
        digest.update(relative.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update((root / relative).read_bytes())
        digest.update(b"\0")
    return BarFingerprint(digest.hexdigest())


#: Where a task states the persona whose bar it will be judged under — an authored field
#: that lives in the open metadata map rather than beside it. A lifecycle node states one
#: per step instead; see :data:`STEPS`.
PERSONA = "onepipeline.persona"

#: Where a lifecycle node states its steps. A node that runs several agent steps on one
#: branch states its prose and its persona once per step rather than in `task` and
#: `persona`, so for that node this — and not `content` — is where its authored content
#: lives, and a key blind to it would leave a standing record over criteria nobody read.
STEPS = "onepipeline.steps"


class AuthoredStep(TypedDict):
    """One lifecycle step, narrowed to the three fields its author writes.

    Narrowed rather than carried whole so that a field the engine adds to a step later
    does not invalidate a review of content nobody moved — the same reason the key over
    a task covers its authored fields rather than its whole record.
    """

    id: object
    persona: object
    task: object


def authored_steps(task: StoreTask) -> list[AuthoredStep] | None:
    """``task``'s lifecycle steps narrowed to their authored fields, in order.

    ``None`` both for a node that states no steps and for one whose `steps` are shaped
    unlike steps, because `just review-plan` reads a plan `just check-plan` may not have
    passed and so meets whatever the store holds. Answering the two alike costs nothing
    that is not already lost: `check_plan` refuses `steps` that are not a list of
    mappings by name, and it runs before the record is ever consulted, so a plan this
    cannot narrow is one no launch reaches whatever its record says.
    """
    held = task.metadata.get(STEPS)
    if not isinstance(held, list) or not all(isinstance(step, Mapping) for step in held):
        return None
    return [
        AuthoredStep(id=step.get("id"), persona=step.get("persona"), task=step.get("task"))
        for step in held
    ]


def review_key(task: StoreTask, bar: BarFingerprint) -> ReviewKey:
    """The digest ``task``'s current authored content, reviewed under ``bar``, hashes to.

    **Exactly the authored fields, and the bar.** The title, the body prose, the
    persona, the dependencies, and — for a lifecycle node, which states its prose and
    its persona once per `steps` entry rather than in `task` and `persona` — the
    authored half of each of those steps. Nothing a settlement write-back owns is here.
    `status` in particular is not: the engine projects each settlement back onto the
    plan it was launched from, so a whole-record key would go stale the first time a
    node ran and this gate would refuse every plan that had ever been launched. Covering
    exactly the authored content buys the other half of that too — a write-back that
    overwrote authored prose invalidates the record rather than leaving a pass standing
    over content nobody read.

    One field a plan also carries is deliberately outside it, and what that costs is
    worth knowing rather than discovering: a task **retargeted at another repository**
    after its review keeps its record.
    """
    authored = {
        "bar": bar,
        "deps": sorted(task.deps),
        "persona": task.metadata.get(PERSONA),
        "steps": authored_steps(task),
        "task": task.content,
        "title": task.title,
    }
    rendered = json.dumps(authored, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return ReviewKey(hashlib.sha256(rendered.encode("utf-8")).hexdigest())


def recorded(task: StoreTask) -> ReviewKey | None:
    """The digest ``task``'s record names, or ``None`` when it carries no readable one.

    A record this cannot read is answered as no record rather than as an error, which is
    the safe direction: the task is then refused for want of a review, which is what an
    unreadable record means anyway, and one malformed entry cannot refuse a whole plan.
    """
    held = task.metadata.get(RECORD_KEY)
    if not isinstance(held, dict):
        return None
    key = held.get("key")
    return ReviewKey(key) if isinstance(key, str) else None


def unreviewed(records: Sequence[StoreTask], bar: BarFingerprint | None = None) -> list[StoreTask]:
    """Every task of ``records`` whose current authored content carries no review record."""
    resolved = bar_fingerprint() if bar is None else bar
    return [task for task in records if recorded(task) != review_key(task, resolved)]


def write_record(project: str, task: StoreTask, key: ReviewKey, by: By) -> Path:
    """Record ``key`` as ``task``'s reviewed content, and answer where it was written.

    ``task`` is required to be one of ``project``'s own. Both are the caller's to pass
    and the pair decides a file that is then edited, so a task from one project handed
    in beside another project's id would write this review into a record it does not
    describe — and the record would read as sound, because nothing downstream can tell
    a key computed elsewhere from a stale one.
    """
    source, native = plan_store.qualified(project)
    held, _, task_native = task.qualified_id.partition(":")
    if held != source or not task_native.startswith(f"{native}/"):
        raise OSError(
            f"task {task.qualified_id!r} is not one of {project!r}'s, so recording its "
            f"review against that project would write into a record it does not describe"
        )
    document = plan_store.task_document(source, task_native)
    plan_store.write_metadata(
        document,
        RECORD_KEY,
        {"key": key, "reviewed_at": datetime.now(UTC).isoformat(), "by": by.value},
    )
    return document


def _verdict(prompt: str) -> Verdict:
    """Spend one judged turn on ``prompt`` and return the structured verdict it answered.

    The turn goes through the same seam every other side of this repository reaches its
    model at — `oneharness run` under a config that names the identity chain, the
    deadline, and the response schema — so the paid provider is the only thing a journey
    has to stand in for, and the schema is enforced by oneharness rather than here.
    """
    completed = subprocess.run(
        [
            "oneharness",
            "run",
            "--config",
            str(REPO_ROOT / HARNESS_CONFIG),
            "--prompt-file",
            "-",
        ],
        cwd=REPO_ROOT,
        input=prompt,
        text=True,
        capture_output=True,
        check=False,
    )
    try:
        # llmlint: ignore[boundary_inputs_validated] The harness report is oneharness's
        # own open contract; the two fields this reads out of it are narrowed below,
        # and nothing is recorded from a report that does not carry them.
        report = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise OSError(
            f"the review turn returned no readable report ({exc}); "
            f"{completed.stderr.strip() or 'the harness printed nothing'}. Run "
            f"`oneharness doctor`, then run this command again — nothing was recorded"
        ) from exc
    results = report.get("results") if isinstance(report, dict) else None
    for result in results if isinstance(results, list) else []:
        if not isinstance(result, dict) or result.get("schema_valid") is not True:
            continue
        structured = result.get("structured")
        if (
            isinstance(structured, dict)
            and isinstance(structured.get("passes"), bool)
            and isinstance(structured.get("reason"), str)
        ):
            return Verdict(passes=structured["passes"], reason=structured["reason"])
    raise OSError(
        f"no candidate answered the review with a verdict matching {BAR_FILES[1]}; "
        f"{completed.stderr.strip() or 'the harness reported no reason'}. Check the "
        f"identity chain in {HARNESS_CONFIG} with `oneharness detect`, then run this "
        f"command again — nothing was recorded"
    )


def _steps(task: StoreTask) -> str:
    """A lifecycle node's steps, rendered for a reviewer to read rather than to parse.

    A stepped node's acceptance criteria *are* its steps' prose, which is the content
    this review exists to look at — and prose inside a JSON string is prose read through
    escapes. So each step is rendered as its own section, in the order it runs, naming
    the persona whose bar that step is judged under. A node with no steps renders
    nothing at all rather than an empty heading, because a heading over nothing reads
    like a node whose steps went missing.
    """
    steps = authored_steps(task)
    if not steps:
        return ""
    sections = "\n\n".join(
        f"### Step {position}: {step['id']} (persona: {step['persona']})\n\n"
        f"{step['task'] or '(this step states no body prose)'}"
        for position, step in enumerate(steps, start=1)
    )
    return f"\n\n## Its steps, in the order they run, as their author wrote them\n\n{sections}"


def _prompt(plan_name: str, task: StoreTask) -> str:
    """One task, rendered for review under the bar this repository holds a plan to."""
    bar = (REPO_ROOT / BAR_FILES[0]).read_text(encoding="utf-8")
    # Every field :func:`review_key` hashes is rendered here, and that is a property
    # rather than a coincidence: a field whose change invalidates the record but which
    # the reviewer was never shown is one nobody ever reviewed.
    # `tests/test_plan_review.py` holds the two together.
    authored = {
        "title": task.title,
        "depends_on": sorted(task.deps),
        "persona": task.metadata.get(PERSONA),
    }
    return (
        f"{REVIEW_PROMPT}\n"
        f"## The review bar\n\n{bar}\n\n"
        f"## The plan\n\n{plan_name}\n\n"
        f"## The task, as its author wrote it\n\n"
        f"{json.dumps(authored, indent=2, ensure_ascii=False)}\n\n"
        f"{task.content or '(this task states no body prose)'}"
        f"{_steps(task)}\n"
    )


def review(project: str) -> Reviewed:
    """Review every unreviewed task of ``project`` and record each pass."""
    records = plan_store.read_tasks(project)
    bar = bar_fingerprint()
    pending = unreviewed(records, bar)
    plan_name = plan_store.read_plan(project, records).get("name", project)
    refused: list[str] = []
    recorded = 0
    for index, task in enumerate(pending):
        try:
            verdict = _verdict(_prompt(str(plan_name), task))
            if verdict["passes"]:
                write_record(project, task, review_key(task, bar), BY_REVIEW)
                recorded += 1
        # Reviewing a task and recording its pass are caught together, because they fail
        # the same way from the operator's side: this plan is not fully reviewed, some of
        # it may be, and the same command run again picks up where this one stopped. What
        # must never happen is a pass reported and not written, or written and not
        # reported, which is why the counter moves with the record and not before it.
        except OSError as exc:
            remaining = len(pending) - index
            return Reviewed(
                recorded,
                len(records) - len(pending),
                refused,
                f"{exc}. {remaining} task(s) were left unreviewed, beginning at {task.node_id}",
            )
        if not verdict["passes"]:
            refused.append(f"{task.node_id}: {verdict['reason'] or 'refused'}")
    return Reviewed(recorded, len(records) - len(pending), refused)


def main(argv: Sequence[str] | None = None) -> int:
    """Review a qualified plan project's unreviewed tasks, from `just review-plan`."""
    parser = argparse.ArgumentParser(
        description="Review a plan's unreviewed task content and record each pass."
    )
    parser.add_argument("project", metavar="SOURCE:PROJECT")
    args = parser.parse_args(argv)
    try:
        answered = review(args.project)
    except (OSError, ValueError) as exc:
        print(
            f"review-plan: {exc}; nothing was recorded, so `just check-plan "
            f"{args.project}` still refuses this plan",
            file=sys.stderr,
        )
        return 2
    if answered.stopped is not None:
        print(
            f"review-plan: {answered.stopped}. {answered.recorded} task(s) were reviewed "
            f"and recorded before that, and those records stand; run this command again "
            f"to review the rest — it re-reviews only what carries no record",
            file=sys.stderr,
        )
        return 2
    if answered.refused:
        for refusal in answered.refused:
            print(f"review-plan: {refusal}", file=sys.stderr)
        print(
            f"review-plan: {len(answered.refused)} task(s) were refused and nothing was "
            f"recorded for them; correct the criteria each refusal names in the plan's own "
            f"task record, then run this command again",
            file=sys.stderr,
        )
        return 1
    print(
        f"review-plan: recorded a review of {answered.recorded} task(s); {answered.held} "
        f"already carried one for their current authored content"
    )
    return 0


class Recorded(NamedTuple):
    """What a planning closeout could speak for, and what it had to leave alone."""

    #: Every task this closeout recorded a planner pass for.
    written: list[QualifiedTaskId]
    #: One line per plan project the closeout could not record, naming it and why.
    #: Kept rather than raised, because a project it cannot read or write is by
    #: construction one it cannot speak for — see :func:`record_projects_new_since`.
    passed_over: list[str]


def plan_projects() -> list[str]:
    """Every plan project of this host's plan sources, by qualified id.

    Taken before a planning run launches so its closeout can tell the plan that run
    produced from the plans that were already here. A source with no root yet is simply
    empty: `just plan` creates one on its first launch, and a host that has never
    planned is not an error.
    """
    return [project for source in PLAN_SOURCES for project in plan_store.local_projects(source)]


# llmlint: ignore[changed_behavior_has_e2e] The one uncovered path is the concurrent
# window this docstring's last paragraph names. Driving it needs two real planning runs
# whose windows overlap, which the e2e closeout journeys give each launch a plan store
# of its own to prevent; it is held instead by
# `tests/test_plan_review.py::test_a_second_planning_runs_project_is_recorded_too`.
def record_projects_new_since(before: Sequence[str]) -> Recorded:
    """Record a planner pass for every task of a plan project absent from ``before``.

    Named for what it does rather than for what it is for, because those are not quite
    the same thing and the gap is the point. A planning run's output is a plan, and that
    plan's own judge is `personas/planner.yaml`'s — the same bar `just review-plan`
    spends a turn on. So a task this run wrote is recorded here rather than
    re-reviewed, and that is the operator's decision rather than an inference: it
    trusts the planner's judge to have covered node-level criteria, and a
    planner-authored node that later fails a review is evidence to stop and look
    rather than a reason to add a second turn per node.

    **This cannot identify the calling run's own output, and does not claim to.** What
    it records is every task of a plan project that *appeared* while the run was in
    flight, which is narrower than "every task that changed" and deliberately so: a plan
    already on disk when the run launched is left alone even if it moved, so the
    hand-written plan this gate exists to catch is not blessed by a planning run
    happening beside it, and neither is an operator's own edit to an existing plan while
    their planner works. A planner *revising* a project it created in an earlier run is
    left alone too, and that costs a `just review-plan` rather than opening a hole, which
    is the direction this has to fail in.

    What is left is a **second planning run** creating its own new project in the same
    window, whose plan this one's closeout records as though its own planner had written
    it. Nothing tells this host which project a dispatched planner authored — the plan is
    its deliverable, not its argument — so closing that would mean the planner declaring
    its output, which nothing in the plan model lets it do.

    A project in that window which cannot be read or recorded is **passed over rather
    than raised**, and that follows from the same fact: this cannot tell its own run's
    output from a neighbour's, so failing the launch would let any unrelated plan kill a
    planning run. One such plan already exists here — a project `onepipeline`'s
    settlement write-back has re-rendered carries a `metadata` block
    `plan_store.write_metadata` will not edit around — so a launch overlapping another's
    would otherwise exit non-zero for a reason having nothing to do with it. Leaving the
    project alone fails in the safe direction: unrecorded is what `just check-plan`
    refuses, and the caller names each one.
    """
    bar = bar_fingerprint()
    known = set(before)
    written: list[QualifiedTaskId] = []
    passed_over: list[str] = []
    for source in PLAN_SOURCES:
        for project in plan_store.local_projects(source):
            if project in known:
                continue
            try:
                for task in plan_store.read_tasks(project):
                    write_record(project, task, review_key(task, bar), BY_PLANNING)
                    written.append(task.qualified_id)
            except OSError as exc:
                passed_over.append(f"{project}: {exc}")
    return Recorded(written, passed_over)


# llmlint: ignore[changed_behavior_has_e2e] Reaching a refusal here means handing the
# verb a destination `scripts/plan.sh` cannot produce — it passes its own `mktemp`
# file and nothing else — so a journey would have to drive the module rather than the
# recipe, which is what `tests/test_plan_review.py` already does for these three.
def snapshot_file(path: Path) -> Path:
    """``path``, once it is a file a snapshot may be written through, or ``OSError``.

    The verbs below take their destination from the command line, and `scripts/plan.sh`
    hands over its own `mktemp` file — so the shape to hold the argument to is that one:
    a real path, reached without following a symlink, naming either nothing yet or a
    regular file that is empty or already holds a snapshot. Everything else names
    something a snapshot must not be written through, and the two that matter are the
    ones an argument slip actually produces: a symlink, whose target is a file somebody
    else owns and this would truncate, and a path already holding content of its own.
    A snapshot is worth nothing beside either.
    """
    if path.is_symlink():
        raise OSError(f"{path} is a symlink, which a review snapshot is never written through")
    if path.exists():
        if not path.is_file():
            raise OSError(f"{path} is not a regular file, so it holds no review snapshot")
        if path.read_text(encoding="utf-8").strip():
            read_snapshot(path)
    return path


def read_snapshot(path: Path) -> list[str]:
    """The qualified project ids ``path`` holds, or ``OSError`` when it holds no snapshot.

    The one place a snapshot's content is narrowed, which is why the verb that writes
    one reads through it too: a destination is refused for holding something this cannot
    read, and a closeout refuses the same content for the same reason, so the two can
    never come to disagree about what a snapshot is.
    """
    try:
        decoded = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise OSError(f"{path} does not hold a review snapshot: {exc}") from exc
    if not isinstance(decoded, list) or not all(isinstance(project, str) for project in decoded):
        raise OSError(f"{path} does not hold a review snapshot")
    return decoded


def planning_main(argv: Sequence[str]) -> int:
    """`snapshot <path>` before a planning launch, `closeout <path>` after it settles."""
    parser = argparse.ArgumentParser(
        description="Record a planner pass for the tasks a planning run authored."
    )
    parser.add_argument("verb", choices=("snapshot", "closeout"))
    parser.add_argument("path", type=Path)
    args = parser.parse_args(argv)
    try:
        destination = snapshot_file(args.path)
        if args.verb == "snapshot":
            destination.write_text(json.dumps(plan_projects()), encoding="utf-8")
            return 0
        recorded = record_projects_new_since(read_snapshot(destination))
    except (OSError, ValueError) as exc:
        print(
            f"plan-review: {exc}; the plan this run authored carries no review record, so "
            f"`just review-plan <source>:<project>` is what records one",
            file=sys.stderr,
        )
        return 2
    for passed_over in recorded.passed_over:
        print(f"plan-review: {passed_over}", file=sys.stderr)
    print(
        f"plan-review: recorded a planner pass for {len(recorded.written)} task(s)",
        file=sys.stderr,
    )
    if recorded.passed_over:
        print(
            f"plan-review: {len(recorded.passed_over)} plan project(s) named above were left "
            f"alone, so each carries no review record and `just review-plan "
            f"<source>:<project>` is what records one",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(planning_main(sys.argv[1:]))
