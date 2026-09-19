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
and that is structural rather than a rule anybody is asked to follow. The store this
gate writes into is the gitignored `.plans/` of *this* checkout: a dispatched worker
runs in a worktree of its own, nothing it writes below that directory is tracked, and so
no branch it produces can carry a record back here. There is no recipe, flag, or
documented step by which a dispatch writes one either.

**Three questions are asked here rather than deterministically, and which tier asks a
question is decided by whether it needs judgment.** Whether a number is the right
number, and whether the criteria answer a demand their own bar makes, both do — and
`orchestrator/criteria_guard.py` held proxies for them until three judged rounds on real
plans were spent with the two tiers refusing each other's required wording: one review
prescribed pinning an immutable version and the deterministic rule refused it, and
another refused a criterion for pinning a spelling while the deterministic rule refused
the same task for lacking a literal phrase its criteria stated in three sentences of
their own. One verdict can hold both considerations at once; two tiers compound rather
than add. The third is whether a node whose criteria describe work that changes no file
declares `expects_no_diff`, which is a reading of prose and so belongs here for the same
reason.

**One question spans nodes, so it is asked once per plan and recorded on the project.**
Whether a plan puts the change its goal needs *in force on this host* — a producer's fix
landed upstream and adopted here through the `config/<pin>.version` the installed wheel
governs — is not a property of any task in it: a consumer missing from a plan is absent
from every task, and the per-task turn reviews one task. So :func:`review` spends one
further turn under :data:`PLAN_REVIEW_PROMPT` once every task carries a record, over the
goal and every node whole, and records the pass on the **project** record under the same
:data:`RECORD_KEY`, keyed by :func:`plan_key` under :func:`plan_bar_fingerprint`. The
three properties hold for it exactly as for a task's record, and `just check-plan` and
`just copy-plan` both refuse a plan whose project carries none. **A mid-run live edit is
held to the per-task tiers only.** An `add`, a `retry` or a `requeue` with an amended
task reaches :mod:`orchestrator.envelope_review`, which spends the per-task turn and
never this one, because a model call over the whole plan there would stall every
mid-run correction; the manager's own review is what covers adoption on a mid-run add,
and the plan-level record says nothing about an edit it never saw.

**A record is read from any source and written into a local Markdown one**, which is the
asymmetry to know before reaching for :func:`review`. A record travels in the open
metadata map a task already carries, so :func:`recorded` answers for a board task exactly
as it does for a file; writing one edits the task's own Markdown document, which only a
`local-md` source has. So a plan held on the GitHub Projects board this repository plans
against can be *checked* — and is refused, because it carries no record — while nothing
here can record one for it. :func:`unwritable` is what says that up front, so the refusal
arrives before a judged turn is spent rather than after.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import NamedTuple, NewType, TypedDict

from orchestrator import host_installs, plan_store
from orchestrator.plan_store import QualifiedTaskId, StoreTask
from orchestrator.project_store import hosted_origin
from orchestrator.publication_guard import RESERVED_REPO_KEY
from orchestrator.root import REPO_ROOT

#: A digest of the review bar in force. Distinct from :data:`ReviewKey`, which is a
#: digest of one task's content *under* a bar: both are hex strings of the same length
#: and each is meaningless in the other's place, so a record compared against a bar
#: fingerprint would refuse every plan and never say why.
BarFingerprint = NewType("BarFingerprint", str)

#: The digest one task's authored content hashes to under one bar — what a record holds
#: and what the deterministic check compares against.
ReviewKey = NewType("ReviewKey", str)

#: Where one task's review record lives — and, on the project record, where the plan's
#: own lives. A namespaced entry of the open metadata map a task already carries, so it
#: travels with the record it describes and stays visible to anybody reading the plan —
#: rather than in a sidecar this repository would then have to keep in step with a store
#: it does not own. One key at both levels, because a project and a task are different
#: records and the entry never has to say which it is on.
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
#: The judged turn's argv up to the prompt, which :func:`verdict` reads as one JSON
#: document. ``--format json`` is what asks for that document: the CLI's stdout is a
#: human-readable view unless a reader names the JSON contract, so a spawn without it
#: reads prose and records nothing.
HARNESS_COMMAND: tuple[str, ...] = (
    "oneharness",
    "run",
    "--config",
    str(REPO_ROOT / HARNESS_CONFIG),
    "--format",
    "json",
)

#: The plan stores a planning closeout looks at: every source it may record a pass into.
#: `authoring` is the gitignored root `just plan` writes a manager's brief into, and it
#: is the only one — the other configured local sources are this repository's shipped
#: examples and the suite's fixtures, and the `plans` board a plan of this repository
#: lives on is a source no record can be written into at all (see :func:`unwritable`).
PLAN_SOURCES = ("authoring",)


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
a perishable fact in place of the property it stands in for.

A fact is perishable when it can move on its own between this task being written and
its node being dispatched, so that finished, correct work fails against it: "the newest
release at the time of writing", a floor a later publication overtakes, a line count.
Two things are not perishable, and refusing either makes the criteria vaguer than the
work rather than more precise. An **immutable anchor** names a fixed point that can
never move — a commit sha, a tag, a release already published — and is exactly what a
criterion should pin to. And a **release or version that is the subject of the task** —
a node whose whole job is adopting a named release — is the property itself rather than
a stand-in for one, so that number belongs in its criteria and there is nothing behind
it to ask for instead. Refuse a version literal only where the task's subject is
something else and the number is standing in for a property that outlives it.

This turn is the only thing that asks about a version literal. No deterministic check
refuses one any more, so a number you ask for here is a number the plan may carry: the
two tiers used to refuse each other's required wording, and an author sent back to pin
an immutable version was then refused for pinning it.

Two demands are made of every implementation dispatch on this host: that the behaviour
the node adds is proven end to end by a test or journey driving the real interface, and
that every claim the dispatch makes about the finished work is true of the tree as it
finally stands. A task's own `## Additional info` and the review bar its persona
resolves to are where those are made, and a judge that finds the criteria silent about
one of them imports it and applies its own reading — which has already failed finished,
green work. So refuse criteria that leave a demand their own task or their own bar makes
unanswered. Judge that by **meaning rather than by wording**: criteria stating the
demand in their own words answer it in full, and no criterion is ever refused for
failing to use a particular phrase.

Ask one further thing of a task whose criteria describe work that changes no file in the
repository — an external side effect, a read, a measurement reported back and nothing
else. Such a node produces an empty branch, and the engine settles an empty branch
`failed` as `empty-branch` unless the node declares `expects_no_diff`, which settles it
`done` as `no-changes` without a dispatch. So where the criteria describe work with no
repository change in it and that field is not declared above, refuse the criterion that
describes the no-change work and say which field is missing; where it *is* declared,
that is the right shape for such a node and is not a defect. Read this off the criteria
rather than the prose around them, and leave alone a task whose criteria do require a
file to change.

For each acceptance criterion, name to yourself the fixture, input, or repository state
that would make it fail. A criterion with no such state is decorative: it reads as
satisfied whatever the dispatch does, so it can never be what stops bad work, and it
hides the absence of a criterion that would. Refuse a criterion you cannot falsify that
way, saying what you looked for and did not find.

A task whose `kind` is "human" is the one exception, and it is a different question
rather than a softer one. Nothing is dispatched from it: it names an action an external
person or outside system performs, so it states that action rather than acceptance
criteria and must never grow any. Hold that one to whether the action is genuinely
external — a merge, a deploy, an outside sign-off, a release somebody performs — rather
than a review or an acceptance the plan's own supervisor would make; and to whether one
person could read it and know exactly what to do. Refusing it for having no acceptance
criteria refuses the shape itself, which is the one refusal that can never be corrected.

One question is asked of a node that adopts a dependency's release — `adoption` of
`published` in its header, or `consumes` naming one — and it has two halves. **It
names the pin it moves.** Below the bar is the table of what this host installs from
each producer it depends on and the `config/<pin>.version` file each wheel governs,
and then this host's own repository, stated as the origin the header's `repo` is
compared with. A node of this host's own repository adopting a wheel that table names
must name, in its `## Acceptance criteria`, the `config/<pin>.version` the table's row
gives for that wheel: a task that adopts one and names none adopts nothing a dispatch
runs, so refuse it naming the criterion that should carry the path. Decide whether the
node is one of this host's own repository **directly**, by comparing the header's
`repo` with the origin stated below — never by inferring it from the prose — and read
which wheel it adopts from the task's own content and the header's `adoption` and
`consumes`. A node whose `repo` is another repository, consuming a producer's crate or
package for its own manifest, is not asked this. Judge it by meaning: a criterion
stating the pin's path in its own words answers it, and no particular phrase is
required. Whether the named pin is the right one for the *fix* — a crate fix reaches a
dispatch only through `config/onepipeline.version` — is asked of the plan as a whole by
a later turn, so do not refuse a task here for naming the wrong one. **It names no
version of its own.** The engine renders the released version into the task's
`## Cross-repository references` block when the hold releases, so a criterion or
instruction naming which version, commit or branch of the dependency to pin — or
telling the worker to go and find one — is a second answer the worker follows over the
engine's, and is refused. The immutable-anchor exemption above applies only to a
release already published; a release the node waits for is not yet an anchor.

Do not rewrite the task and do not judge it on style. Answer with the JSON object the
response schema declares: whether it passes, and one finding for **every** criterion
you would refuse — not the first one, and not the worst one. Each finding names the
criterion it is about and why that criterion is refused. A verdict that passes carries
no findings at all, and a verdict that refuses carries one for each criterion it
refuses; the schema admits no other pair, because a refusal naming nothing to correct
and a pass reporting criteria it refuses are both answers its reader cannot act on.
Report every criterion you would refuse in this one answer: this task's author corrects
what you name and comes back, so a criterion you saw and left out costs another whole
round. A refusal is what stops this content reaching a dispatch, so refuse only what
you can name.
"""


class Finding(TypedDict):
    """One criterion a verdict refuses, and why.

    A finding *is* a refused criterion rather than a note beside one, which is what
    lets the schema hold a verdict's outcome and its findings to each other: a
    refusal carries at least one and a pass carries none.
    """

    criterion: str
    why: str


class Verdict(TypedDict):
    """One review turn's answer, in the two fields the response schema declares.

    Stated here as well as in `config/plan-review-verdict.schema.json` because the
    schema is what oneharness validates and this is what decides whether a record is
    written: the two have to agree, and `tests/plan_tooling/test_plan_review_e2e.py` drives a
    real turn through both.

    `findings` is a list because a reviewer that can see three defects and report one
    tells its reader that one thing is wrong; `docs/plan-review-refusals.md` is what the
    single-finding contract this replaced cost.
    """

    passes: bool
    findings: list[Finding]


class Refusal(NamedTuple):
    """One task this run refused, carrying every finding its verdict named.

    The findings are kept as the verdict answered them rather than collapsed into a
    line here, because the count of *tasks* and the count of *findings* are different
    numbers an operator reads differently — one says how much of the plan is unreviewed
    and the other says how much there is to correct.
    """

    node_id: str
    findings: list[Finding]

    def lines(self) -> list[str]:
        """This refusal as the operator reads it: one line per finding, never per task."""
        return [
            f"{self.node_id}: {finding['criterion']} — {finding['why']}"
            for finding in self.findings
        ]


class PlanReview(StrEnum):
    """What the plan-level turn did, as a closed vocabulary rather than three flags.

    `UNASKED` is the deliberate one: the plan-level turn is spent only once every task
    carries a record, so a plan with a refused or unreviewed task spends nothing on it
    and reports that rather than a pass or a refusal about a plan it never read whole.
    """

    RECORDED = "recorded"
    HELD = "held"
    REFUSED = "refused"
    UNASKED = "unasked"


class Reviewed(NamedTuple):
    """What one `just review-plan` did, named rather than positional."""

    #: Tasks this run reviewed and recorded a pass for.
    recorded: int
    #: Tasks that already carried a record for their current content, so cost nothing.
    held: int
    #: One entry per refused task, each carrying every criterion its verdict refused.
    #: Nothing was recorded for any of them, which is why this is a list rather than a
    #: count.
    refused: list[Refusal]
    #: Why the run stopped early, when it did. A review is per task and each pass is
    #: recorded as it is granted, so a plan whose fourth task cannot be reviewed keeps
    #: the three records already written — and a diagnostic claiming nothing was
    #: recorded would send its reader looking for state that is there.
    stopped: str | None = None
    #: What the one plan-level turn did, and — when it refused — every finding it named,
    #: each `criterion` being a node id or `the plan`. Nothing was recorded for a
    #: refusal, and the task records above stand whatever this says.
    plan: PlanReview = PlanReview.UNASKED
    plan_findings: Sequence[Finding] = ()


#: How this checkout's own `origin` remote is asked for, and the one shape it answers
#: in. `git remote get-url` reads the configured URL and nothing else — no fetch, no
#: network — so a checkout with the remote configured answers in milliseconds and one
#: without answers non-zero.
ORIGIN_COMMAND = ("git", "remote", "get-url", "origin")

#: The scp-like spelling of a clone URL, `[user@]host:owner/name`, which
#: :func:`orchestrator.project_store.hosted_origin` deliberately answers `None` for
#: because a task record never holds one. A configured remote may be spelled that way,
#: so it is rewritten to the `host/owner/name` shape that function reads before it is
#: asked; an `ssh://` scheme is dropped for the same reason `https://` is.
_SCP_LIKE = re.compile(r"^(?:ssh://)?(?:[\w.-]+@)?(?P<host>[A-Za-z0-9.-]+)[:/](?P<path>.+)$")


def host_repository() -> str:
    """This host's own repository: the normalized origin of this checkout's `origin` remote.

    The pin-path question the reviewer is asked turns on whether a node is one of *this*
    repository — the one whose `config/<pin>.version` files a dispatch runs under — and
    that is decided by comparing the node's `repo` with this value, never by inferring it
    from the prose. It is read from git rather than from any tracked file because the
    tracked files name this repository nowhere a program reads, and because a copy of
    this checkout is a different repository only if its remote says so.

    Raises :class:`OSError` when the remote cannot be read or is not a hosted origin,
    naming the repair, because every caller reads that as the review configuration this
    checkout cannot answer: a review granted against an unknown origin would be a pass
    over a question the reviewer could not decide.
    """
    try:
        asked = subprocess.run(
            ORIGIN_COMMAND, cwd=REPO_ROOT, text=True, capture_output=True, check=False
        )
    except OSError as exc:
        raise OSError(f"git could not be run to read this checkout's origin: {exc}") from exc
    if asked.returncode != 0:
        raise OSError(
            f"this checkout's `origin` remote could not be read from {REPO_ROOT} "
            f"({asked.stderr.strip() or 'git reported no reason'}); the review compares "
            f"each node's repository with it, so run this from a git checkout whose "
            f"`origin` names this repository"
        )
    url = asked.stdout.strip()
    origin = hosted_origin(url)
    if origin is None:
        rewritten = _SCP_LIKE.match(url)
        if rewritten is not None:
            origin = hosted_origin(f"{rewritten['host']}/{rewritten['path']}")
    if origin is None:
        raise OSError(
            f"this checkout's `origin` remote is {url!r}, which is not a hosted "
            f"`host/owner/name` origin a task record could name, so no node's repository "
            f"can be compared with it"
        )
    return origin


def bar_fingerprint(root: Path = REPO_ROOT) -> BarFingerprint:
    """A digest of the review bar in force, over ``root``'s copy of the files it is.

    The bar is the question, the files, and **what the reviewer was shown beside them**:
    the table of what this host installs and the pin each wheel governs, and this host's
    own repository. Both are rendered into every prompt and both decide the pin-path
    answer, so a pass granted while either read differently is a pass over a question
    the reviewer was not asked.
    """
    digest = hashlib.sha256()
    digest.update(REVIEW_PROMPT.encode("utf-8"))
    for relative in BAR_FILES:
        digest.update(relative.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update((root / relative).read_bytes())
        digest.update(b"\0")
    digest.update(host_installs.rendered().encode("utf-8"))
    digest.update(b"\0")
    digest.update(host_repository().encode("utf-8"))
    return BarFingerprint(digest.hexdigest())


#: Where a task states the persona whose bar it will be judged under — an authored field
#: that lives in the open metadata map rather than beside it. A lifecycle node states one
#: per step instead; see :data:`STEPS`.
PERSONA = "onepipeline.persona"

#: Where a task states which shape of node it is — the only `kind` a plan states, and a
#: field the reviewer is shown because it decides which question the bar asks. A
#: `kind: human` node carries the action a person performs rather than criteria a judge
#: reads, so a reviewer shown only its prose refuses it for the one property its shape
#: forbids it from having, and `check-plan` then refuses the plan for want of the record
#: that refusal could not write. `tests/test_plan_review.py` holds this value to
#: `orchestrator.criteria_guard.HUMAN`, which is the same vocabulary read from the other
#: side; it is restated rather than imported because that module reads this one.
KIND = "onepipeline.kind"

#: The one value :data:`KIND` takes. An agent node says it is one by carrying no `kind`.
HUMAN = "human"

#: Where a node declares that it expects to change no file of the repository. Authored,
#: and keyed for the reason :data:`KIND` is: it decides which question the reviewer was
#: asked. A node whose work is an external side effect produces an empty branch, and the
#: engine settles an empty branch `failed` as `empty-branch` unless this says not to
#: (https://github.com/nickderobertis/onepipeline/pull/229) — so criteria describing
#: no-change work are sound beside this field and a defect without it, and a record
#: granted while it was absent says nothing about the node once it is there.
EXPECTS_NO_DIFF = "onepipeline.expects_no_diff"

#: Where a lifecycle node states its steps. A node that runs several agent steps on one
#: branch states its prose and its persona once per step rather than in `task` and
#: `persona`, so for that node this — and not `content` — is where its authored content
#: lives, and a key blind to it would leave a standing record over criteria nobody read.
STEPS = "onepipeline.steps"

#: The three fields that say how a node adopts another repository's release, and under
#: which publication policy — authored, and keyed because each decides which question
#: the reviewer asks of the node. `adoption` says whether it waits on the release or the
#: branch, `consumes` names the target it waits for, and `merge_policy` decides whether
#: a `consumes` is one its publication can ever satisfy; a record granted while any of
#: them read differently is a pass over a node that now adopts something else. Spelled
#: here the way :data:`PERSONA` is, as the engine's `onepipeline.<field>` metadata keys,
#: and held to the engine rather than trusted: `tests/e2e/test_release_adoption_in_force_e2e.py`
#: drives the installed loader refusing a bad `adoption` and a mis-keyed `consumes`
#: written under exactly these keys, `tests/plan_tooling/test_check_plan_recipe_e2e.py`
#: drives it reading a node's stated `merge_policy`, and the store-versus-loaded journey
#: in `tests/plan_tooling/test_plan_review_e2e.py` holds the record written from the
#: store's spelling to the document the loader hands the check.
ADOPTION = "onepipeline.adoption"
CONSUMES = "onepipeline.consumes"
MERGE_POLICY = "onepipeline.merge_policy"


def repository_of(task: StoreTask) -> str | None:
    """``task``'s repository as its record names it, or ``None`` when it names none.

    The one entry of the record's own `repositories` list for a hosted identity, else
    what the reserved `onepipeline.repo` key holds for a local checkout — the same two
    spellings the engine reads a node's `repo` from, in the same order. Keyed and shown
    because the pin-path question is answered differently for a node of this host's own
    repository and for one outside it, and the reviewer decides which by this value.
    """
    if task.repositories:
        return str(task.repositories[0])
    held = task.metadata.get(RESERVED_REPO_KEY)
    return held if isinstance(held, str) else None


class AuthoredStep(TypedDict):
    """One lifecycle step, narrowed to the three fields its author writes.

    Narrowed rather than carried whole so that a field the engine adds to a step later
    does not invalidate a review of content nobody moved — the same reason the key over
    a task covers its authored fields rather than its whole record. Each of the three is
    read for its meaning, exactly as the fields beside them are; see
    :func:`meaning_bearing`.
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
    return _narrowed_steps(task.metadata.get(STEPS))


def _narrowed_steps(held: object) -> list[AuthoredStep] | None:
    """``held`` as a node's authored steps, or ``None`` when it is not a list of them.

    Shared by the task key, which reads the steps off a store record's metadata, and the
    plan key, which reads them off a node of the plan in the engine's loaded shape: one
    narrowing, so the two keys cover a step's authored fields identically.
    """
    if not isinstance(held, list) or not all(isinstance(step, Mapping) for step in held):
        return None
    return [
        AuthoredStep(
            id=meaning_bearing(step.get("id")),
            persona=meaning_bearing(step.get("persona")),
            task=meaning_bearing(step.get("task")),
        )
        for step in held
    ]


#: An inline-code span: a run of backticks, whatever it holds, and the same run closing
#: it. Whitespace *inside* one is part of the demand rather than the cosmetics around it
#: — a criterion about a parser that accepts `a b` as one field is a different criterion
#: from one about `a  b` — so this is the boundary :func:`meaning_bearing` collapses up
#: to and never across. A backtick run that never closes on its line matches nothing and
#: leaves that line prose, which is what it already was; criteria carrying one are
#: refused by `orchestrator.criteria_guard.check_backticks_pair` before they reach here.
INLINE_CODE = re.compile(r"(`+).*?\1")

#: Where a fenced block opens or closes. The same rule as above over a whole run of
#: lines: indentation inside a fence is the demand — re-indent a YAML sample or a Python
#: body and it means something else — where indentation of prose is the cosmetic edit
#: this key exists to absorb. So the lines a fence encloses are carried through byte for
#: byte, the fence lines with them.
FENCE = re.compile(r"^\s*(?P<fence>`{3,}|~{3,})")


def _collapsed_outside_code(line: str) -> str:
    """``line`` with its runs of whitespace collapsed, except inside an inline-code span.

    Whitespace at a span's edge is collapsed rather than dropped, because whether there
    was any is itself part of the demand: ``a `b``` and ``a`b``` are two different
    strings, and a normalization that closed the gap would hash them alike.
    """
    collapsed: list[str] = []
    at = 0
    for span in INLINE_CODE.finditer(line):
        collapsed.append(re.sub(r"\s+", " ", line[at : span.start()]))
        collapsed.append(span.group(0))
        at = span.end()
    collapsed.append(re.sub(r"\s+", " ", line[at:]))
    return "".join(collapsed).strip()


def _meaning_bearing_lines(text: str) -> list[str]:
    """``text``'s lines, each collapsed, and each line a fence encloses left untouched."""
    lines: list[str] = []
    fence: str | None = None
    for line in text.splitlines():
        opened = FENCE.match(line)
        if fence is None:
            if opened is None:
                lines.append(_collapsed_outside_code(line))
                continue
            fence = opened.group("fence")
        elif opened is not None and opened.group("fence")[0] == fence[0]:
            if len(opened.group("fence")) >= len(fence):
                fence = None
        lines.append(line)
    return lines


def meaning_bearing(value: object) -> object:
    """``value`` with the whitespace a reviewer could not have ruled on taken out of it.

    The key is over what a task **demands**, not over its bytes. Re-indenting a block,
    trimming trailing whitespace, or closing up a run of blank lines alters no demand a
    reviewer read, and charging a judged turn for one is how this gate comes to cost
    something for nothing — the two tiers that used to refuse each other's wording made
    exactly that edit necessary.

    The normalization stops where it stops on purpose, because a key that survives a
    changed demand is worse than the cost it saves. Each line is collapsed **within
    itself** and lines are never joined: bullets are how criteria are separated, so
    merging them would let one criterion and two hash alike. So re-wrapping a paragraph
    at a different width still invalidates the record, and every change to a word still
    does. Anything that is not a string is passed through untouched, which is what keeps
    `kind`, `deps` and an unreadable `steps` answering exactly as they did.

    **It stops at code, too, and that boundary is the one worth stating.** Whitespace is
    cosmetic in prose and load-bearing in a literal: a criterion naming `a  b` demands a
    different string from one naming `a b`, and a fenced sample re-indented is a
    different sample. Collapsing those hashed a changed demand to its old key, which is
    a review record standing over content nobody read — the one failure this whole gate
    exists to prevent, arriving through the machinery meant to make it cheap. So an
    inline-code span and every line a fence encloses are carried through byte for byte.
    """
    if not isinstance(value, str):
        return value
    lines = _meaning_bearing_lines(value)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip("\n")


def review_key(task: StoreTask, bar: BarFingerprint) -> ReviewKey:
    """The digest ``task``'s current authored content, reviewed under ``bar``, hashes to.

    **Exactly the authored fields, and the bar.** The title, the body prose, the
    persona, every dependency the node states — in the one representation
    :func:`orchestrator.plan_store.authored_deps` composes, which is what keeps this key
    and the one `just check-plan` reconstructs from the loaded plan the same digest —
    whether the node declares it expects no diff, and — for a lifecycle node, which
    states its prose and its persona once per `steps` entry rather than in `task` and
    `persona` — the authored half of each of those steps. Nothing a
    settlement write-back owns is here. `status` in particular is not: the engine
    projects each settlement back onto the plan it was launched from, so a whole-record
    key would go stale the first time a node ran and this gate would refuse every plan
    that had ever been launched. Covering exactly the authored content buys the other
    half of that too — a write-back that overwrote authored prose invalidates the record
    rather than leaving a pass standing over content nobody read.

    Each of those fields is read for its **meaning** rather than for its bytes;
    :func:`meaning_bearing` says exactly how far that reaches and why it stops there.

    The repository the node names is in it too, and that **reverses** the decision this
    docstring used to record — a task retargeted at another repository kept its record.
    It no longer does: the pin-path question is answered differently for a node of this
    host's own repository and for one outside it, and the reviewer decides which by
    comparing the node's `repo` with this host's own, so a retargeted task is a
    different question and is re-reviewed. So are its `adoption`, `consumes` and
    `merge_policy`, for the reason their constants give.
    """
    authored = {
        "adoption": task.metadata.get(ADOPTION),
        "bar": bar,
        "consumes": task.metadata.get(CONSUMES),
        "deps": plan_store.authored_deps(task),
        "expects_no_diff": task.metadata.get(EXPECTS_NO_DIFF),
        "kind": task.metadata.get(KIND),
        "merge_policy": task.metadata.get(MERGE_POLICY),
        "persona": task.metadata.get(PERSONA),
        "repo": repository_of(task),
        "steps": authored_steps(task),
        "task": task.content,
        "title": task.title,
    }
    return content_key(authored, bar)


def content_key(authored: Mapping[str, object], bar: BarFingerprint) -> ReviewKey:
    """The digest ``authored``, read for its meaning and reviewed under ``bar``, hashes to.

    The one place a review key is composed, so every carrier of reviewable content is
    keyed the same way: :func:`review_key` hands it a plan task's authored fields, and
    :func:`plan_key` a whole plan's. Each value goes through :func:`meaning_bearing`
    first, which is what keeps a re-indented block from costing a second review of a
    demand nobody moved. A live edit is keyed by no function here: the bus's own pass
    cache keys it on the envelope's bytes and :func:`edit_bar_fingerprint`.
    """
    read = {field: meaning_bearing(value) for field, value in {**authored, "bar": bar}.items()}
    rendered = json.dumps(read, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return ReviewKey(hashlib.sha256(rendered.encode("utf-8")).hexdigest())


#: What the reviewer is told about a whole task a **live edit** states, between the
#: question above and the bar. A plan task reaches the reviewer through `just
#: review-plan` with its title and dependencies beside it; an `add`, a `retry`'s
#: replacement node and a `requeue`'s amended task reach it through
#: :mod:`orchestrator.envelope_review` with neither, written by a manager in the minute
#: after reading a failure. The bar is the same one, because the judge it reaches is.
LIVE_EDIT_FRAME = """\
This task was not read out of a plan. It was stated by a live edit on a running run's
channel — an added node, a retry's replacement node, or a requeued node's amended task
— written by a manager in the minute after reading a failure, and it reaches its
worker's judge exactly as a plan's task would. Hold it to the same bar. It carries no
title, no dependencies, no repository and no adoption fields to show you — so the
pin-path question above is asked of it only as far as its own prose says which
repository it is of and what it adopts; the persona beside it is the role whose review
bar its judge is given, and `null` means the base config's generic contract.
"""


def edit_bar_fingerprint(root: Path = REPO_ROOT) -> BarFingerprint:
    """A digest of the judged bar a live edit is held to, over ``root``'s copy of it.

    The plan bar — :func:`bar_fingerprint`, with the prompt and the files it hashes —
    and the frame above, which is as much a part of what a live edit is asked as the
    prompt is of what a plan task is asked. Distinct from :func:`bar_fingerprint` so that
    rewording the frame moves every live-edit record and no plan record: a plan task was
    never shown it, so a review granted to one says exactly what it said before.
    """
    digest = hashlib.sha256()
    digest.update(bar_fingerprint(root).encode("utf-8"))
    digest.update(b"\0")
    digest.update(LIVE_EDIT_FRAME.encode("utf-8"))
    return BarFingerprint(digest.hexdigest())


def edit_prompt(text: str, persona: object, where: str) -> str:
    """One live edit's resulting whole task, rendered for review under a plan's bar.

    ``where`` is how the refusal names the text — *the replacement task for node
    'x'* — and it is shown to the reviewer as well, so that a finding's wording and the
    refusal that carries it are about the same thing. The persona shown is the one whose
    bar the task's judge is given; `null` is the base config's generic contract.
    """
    bar = (REPO_ROOT / BAR_FILES[0]).read_text(encoding="utf-8")
    stated = json.dumps({"stated_as": where, "persona": persona}, indent=2, ensure_ascii=False)
    return (
        f"{REVIEW_PROMPT}\n{LIVE_EDIT_FRAME}\n"
        f"## The review bar\n\n{bar}\n\n"
        f"{_host_sections()}"
        f"## The task, as the live edit states it\n\n{stated}\n\n{text}\n"
    )


def recorded(task: StoreTask) -> ReviewKey | None:
    """The digest ``task``'s record names, or ``None`` when it carries no readable one.

    A record this cannot read is answered as no record rather than as an error, which is
    the safe direction: the task is then refused for want of a review, which is what an
    unreadable record means anyway, and one malformed entry cannot refuse a whole plan.
    """
    return _recorded_key(task.metadata)


def _recorded_key(metadata: Mapping[str, object]) -> ReviewKey | None:
    """The digest the record in ``metadata`` names, or ``None`` when it holds none."""
    held = metadata.get(RECORD_KEY)
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
    value = {"key": key, "reviewed_at": datetime.now(UTC).isoformat(), "by": by.value}
    written = plan_store.sdk(
        plan_store.client().task_metadata_set(str(task.qualified_id), RECORD_KEY, json.dumps(value))
    )
    return Path(
        plan_store.located(
            written.location.model_dump(mode="python") if written.location else None,
            written.id.model_dump(),
        )
    )


#: Every field the verdict schema declares, at each of its two levels. The schema sets
#: `additionalProperties: false` at both, so a verdict carrying anything else is one its
#: reviewer wrote to a contract this does not have — and reading it as a verdict anyway
#: would record a pass from an answer nobody agreed the shape of. `_answered` is the
#: fallback validator for a harness whose own was skipped, misconfigured, or stood in
#: for, so it refuses the same two shapes rather than a subset of them.
VERDICT_FIELDS = frozenset({"passes", "findings"})
FINDING_FIELDS = frozenset({"criterion", "why"})


def _declared(mapping: object, fields: frozenset[str]) -> bool:
    """Whether ``mapping`` is a mapping carrying exactly ``fields`` and nothing else."""
    return isinstance(mapping, dict) and mapping.keys() == fields


def _answered(structured: object) -> Verdict | None:
    """``structured`` as a verdict, or ``None`` when it is not one this may act on.

    The schema is enforced by oneharness, and this narrowing is enforced again here for
    the two properties a record is written from. **The outcome and the findings have to
    agree**: a refusal naming no criterion says nothing an author can correct, and a
    pass carrying findings would clear a task whose own reviewer refused criteria of it
    — so neither is a verdict, and a report carrying only those leaves the task
    unreviewed rather than recorded either way. And **the shape has to be the declared
    one**, at both its levels, because the schema sets `additionalProperties: false` and
    an answer carrying more than that was written to a contract this does not have.
    Reading both here as well as in the schema is what makes them true of a harness
    whose validator was skipped, misconfigured, or stood in for.
    """
    match structured:
        case {"passes": bool(passes), "findings": list(raw)} if _declared(
            structured, VERDICT_FIELDS
        ):
            findings = [
                Finding(criterion=criterion, why=why)
                for finding in raw
                if _declared(finding, FINDING_FIELDS)
                for criterion in [finding["criterion"]]
                for why in [finding["why"]]
                if isinstance(criterion, str) and isinstance(why, str)
                if criterion.strip() and why.strip()
            ]
            # A finding this could not read is not a finding dropped: it would make a
            # two-finding refusal report one, which is the whole defect this contract
            # was widened away from. A blank `criterion` or `why` is unreadable in the
            # sense that matters — it leaves its reader where a refusal carrying no
            # finding leaves them — so the schema and this narrowing both refuse it.
            if len(findings) != len(raw) or passes is bool(findings):
                return None
            return Verdict(passes=passes, findings=findings)
        case _:
            return None


def verdict(prompt: str) -> Verdict:
    """Spend one judged turn on ``prompt`` and return the structured verdict it answered.

    The turn goes through the same seam every other side of this repository reaches its
    model at — `oneharness run` under a config that names the identity chain, the
    deadline, and the response schema — so the paid provider is the only thing a journey
    has to stand in for, and the schema is enforced by oneharness rather than here.

    Public because it is the one judged turn this repository spends on task prose, and
    :mod:`orchestrator.envelope_review` spends it on a live edit's resulting task under
    :func:`edit_prompt`; a second spawn there would be a second seam to stand in for.
    Raises :class:`OSError` when no verdict came back, naming why and the repair, so a
    caller never records a pass from a turn that answered nothing.
    """
    completed = subprocess.run(
        [*HARNESS_COMMAND, "--prompt-file", "-"],
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
        answered = _answered(result.get("structured"))
        if answered is not None:
            return answered
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
        "repo": repository_of(task),
        "depends_on": plan_store.authored_deps(task),
        "adoption": task.metadata.get(ADOPTION),
        "consumes": task.metadata.get(CONSUMES),
        "merge_policy": task.metadata.get(MERGE_POLICY),
        "expects_no_diff": task.metadata.get(EXPECTS_NO_DIFF),
        "kind": task.metadata.get(KIND),
        "persona": task.metadata.get(PERSONA),
    }
    return (
        f"{REVIEW_PROMPT}\n"
        f"## The review bar\n\n{bar}\n\n"
        f"{_host_sections()}"
        f"## The plan\n\n{plan_name}\n\n"
        f"## The task, as its author wrote it\n\n"
        f"{json.dumps(authored, indent=2, ensure_ascii=False)}\n\n"
        f"{task.content or '(this task states no body prose)'}"
        f"{_steps(task)}\n"
    )


def _host_sections() -> str:
    """What this host installs and which repository it is, rendered for a reviewer.

    Both sit in every prompt beside the bar, because both are facts the pin-path
    question turns on and neither is knowable from the task: the table says which
    `config/<pin>.version` an adopted wheel governs, and the origin is what the task's
    `repo` is compared with to decide whether the question is asked of it at all.
    """
    return (
        f"## What this host installs, and the pin each wheel governs\n\n"
        f"{host_installs.rendered()}\n"
        f"## This host's own repository\n\n"
        f"`{host_repository()}` — the origin the task's `repo` is compared with to decide "
        f"whether it is a node of this host's own repository.\n\n"
    )


#: What the reviewer is asked of a plan whole, once every task in it carries a record.
#: Hashed into every plan-level key with the task bar and the table, and into no task
#: key: rewording this moves every plan record and no task record.
PLAN_REVIEW_PROMPT = """\
You are reviewing ONE plan whole, after every task in it has passed its own review,
for the one property no single task can show: whether the plan puts the change its
goal needs in force on this host. Below is the review bar, then the plan's goal, then
the table of what this host installs from each producer it depends on — the wheel,
and the `config/<pin>.version` file that wheel governs — with the two rungs a node
waits on a release under, and then every node of the plan with its authored fields
and its whole task.

Ask two questions.

First: does the goal need a change in one of the producers the table names to be **in
force on this host** — installed here, run by this host's own commands or by the nodes
it dispatches — for the goal to be met? A plan naming no producer passes. A plan that
touches a producer for a reason this host does not consume — that repository's own
documentation, its tests, an artifact this host does not install — passes too, and
your verdict says why you read it that way. This tier errs toward missing: refuse only
a plan whose goal, read plainly, is not met while this host goes on running the
release it has.

Second, if it does, for each such producer: is there a node of this host's own
repository that adopts that producer's release? Such a node is reachable from the
producer's node through `deps`; waits `published` — stated on the node, or resolved by
this repository's own rung, stated under the table — rather than `fast` against the
branch; waits on the wheel this host installs, which is the table's row for that
producer, and so names no `consumes` of its own; names that row's
`config/<pin>.version` in its acceptance criteria; and names no version, commit or
branch of its own, because the engine renders the released version into the task when
the hold releases. One rule decides which pin is the right one for a fix: a fix in a
crate the engine links — the version-control, agent-graph, judging and harness
libraries — reaches a dispatch only through `config/onepipeline.version`, via an
`onepipeline` node that links the crate, and never through that library's own CLI
pin, which governs the host's own commands and nothing a dispatch runs; a fix in a
tool this host runs itself moves that tool's own pin.

A plan that omits the adoption its goal needs is refused with a finding whose
`criterion` is `the plan`. A plan that declares it wrongly — the wrong pin for the fix,
`fast` where the release is needed, a `consumes` on a node of this repository, a
version literal — is refused with a finding whose `criterion` names the node's id. A
verdict that passes carries no findings; one that refuses carries one per thing to
correct, and says in each `why` what the plan would have to state instead.
"""

#: The two rungs a node of a plan waits on a release under, stated beside the table in
#: the plan-level prompt: this repository's own, from `config/onevcs.releases.yml`, and
#: the global one every other repository resolves to. Stated as prose the reviewer
#: reads, and hashed into the plan bar through the prompt rather than restated in it.
#: A restatement of that tracked file, held to it by `tests/test_plan_review.py`, which
#: reads the override's own rule for this repository and its `default:` and fails the
#: moment either rung moves without this sentence.
RUNGS = (
    "A node of this host's own repository waits `published` by default — the release "
    "carrying the work, never the branch; every other repository's rung is `fast`."
)

#: What a finding of the plan-level turn names when it is about the plan rather than
#: about one node: the word the prompt tells the reviewer to use, and what the operator
#: reads it back as.
THE_PLAN = "the plan"


def plan_bar_fingerprint(root: Path = REPO_ROOT) -> BarFingerprint:
    """A digest of the bar a plan is held to whole, over ``root``'s copy of the files.

    The task bar, the plan-level question, and the table the question is asked over —
    distinct from :func:`bar_fingerprint` so that rewording the plan-level prompt
    moves every plan record and no task record, while a change to the table moves
    both, since both prompts render it.
    """
    digest = hashlib.sha256()
    digest.update(bar_fingerprint(root).encode("utf-8"))
    digest.update(b"\0")
    digest.update(PLAN_REVIEW_PROMPT.encode("utf-8"))
    digest.update(b"\0")
    digest.update(host_installs.rendered().encode("utf-8"))
    return BarFingerprint(digest.hexdigest())


class KeyedNode(TypedDict):
    """One node of a plan, narrowed to the authored fields the plan-level key covers.

    Read off the plan in the **engine's loaded shape** — `plan_store.read_plan`'s answer
    for the store path and the document `scripts/plan-check.sh` receives for the check
    path, which carry these fields under these names, so both readers hash one thing.
    Every field is authored; nothing a settlement write-back owns is here. Each is
    ``object`` because it is read for its meaning rather than narrowed to a type: a
    value of the wrong shape is keyed as what it is, and the engine's loader is what
    refuses it, long before this is asked.
    """

    id: object
    title: object
    repo: object
    deps: list[str]
    adoption: object
    consumes: object
    merge_policy: object
    kind: object
    expects_no_diff: object
    persona: object
    task: object
    steps: list[AuthoredStep] | None


def plan_nodes(plan: object) -> list[KeyedNode]:
    """Every node of ``plan``, narrowed to a :class:`KeyedNode`, sorted by id.

    Read for its meaning field by field, exactly as a task's record is, and sorted so
    that the order a store lists its tasks in — which is the store's to decide — is
    not part of what a plan's key says. A plan carrying no readable `tasks` answers no
    nodes rather than raising: the engine's loader has refused that shape long before
    this is asked, and a key over an empty list refuses the plan for want of a record
    exactly as an unreadable one would.
    """
    tasks = plan.get("tasks") if isinstance(plan, Mapping) else None
    nodes: list[KeyedNode] = []
    for task in tasks if isinstance(tasks, list) else []:
        if not isinstance(task, Mapping):
            continue
        deps = task.get("deps")
        nodes.append(
            KeyedNode(
                id=meaning_bearing(task.get("id")),
                title=meaning_bearing(task.get("title")),
                repo=meaning_bearing(task.get("repo")),
                deps=sorted(str(dependency) for dependency in deps)
                if isinstance(deps, list)
                else [],
                adoption=meaning_bearing(task.get("adoption")),
                consumes=meaning_bearing(task.get("consumes")),
                merge_policy=meaning_bearing(task.get("merge_policy")),
                kind=meaning_bearing(task.get("kind")),
                expects_no_diff=meaning_bearing(task.get("expects_no_diff")),
                persona=meaning_bearing(task.get("persona")),
                task=meaning_bearing(task.get("task")),
                steps=_narrowed_steps(task.get("steps")),
            )
        )
    return sorted(nodes, key=lambda node: json.dumps(node["id"], sort_keys=True))


def plan_goal(plan: object) -> object:
    """The goal text ``plan`` states, or whatever it states in the goal's place."""
    goal = plan.get("goal") if isinstance(plan, Mapping) else None
    if isinstance(goal, Mapping) and isinstance(goal.get("text"), str):
        return goal["text"]
    return goal


def plan_key(plan: object, bar: BarFingerprint) -> ReviewKey:
    """The digest ``plan``'s goal and every node's authored content hash to under ``bar``.

    Over the plan in the engine's loaded shape, so the store path and the check path
    compute one key. The `shape` field keeps it from ever equalling a task's key or a
    live edit's.
    """
    return content_key({"goal": plan_goal(plan), "nodes": plan_nodes(plan), "shape": "plan"}, bar)


def plan_recorded(record: Mapping[str, object]) -> ReviewKey | None:
    """The digest the project ``record``'s review entry names, or ``None`` for none."""
    metadata = record.get("metadata")
    return _recorded_key(metadata) if isinstance(metadata, Mapping) else None


def plan_unreviewed(
    record: Mapping[str, object], plan: object, bar: BarFingerprint | None = None
) -> bool:
    """Whether ``plan`` as it stands carries no plan-level record on its project ``record``."""
    resolved = plan_bar_fingerprint() if bar is None else bar
    return plan_recorded(record) != plan_key(plan, resolved)


def write_plan_record(project: str, key: ReviewKey, by: By) -> Path:
    """Record ``key`` as ``project``'s reviewed plan, and answer where it was written."""
    plan_store.qualified(project)
    value = {"key": key, "reviewed_at": datetime.now(UTC).isoformat(), "by": by.value}
    written = plan_store.sdk(
        plan_store.client().project_metadata_set(project, RECORD_KEY, json.dumps(value))
    )
    return Path(
        plan_store.located(
            written.location.model_dump(mode="python") if written.location else None,
            written.id.model_dump(),
        )
    )


def _plan_prompt(plan: object) -> str:
    """One plan whole, rendered for the plan-level review.

    Every field :func:`plan_key` hashes is rendered here — the goal, and each node's
    fields and whole task — for the reason `_prompt` gives about the task key: a field
    whose change invalidates the record but which the reviewer never saw is one nobody
    reviewed. `tests/test_plan_review.py` holds the two together.
    """
    bar = (REPO_ROOT / BAR_FILES[0]).read_text(encoding="utf-8")
    goal = plan_goal(plan)
    sections = []
    for node in plan_nodes(plan):
        fields = {field: value for field, value in node.items() if field not in {"task", "steps"}}
        steps = node["steps"]
        body = node["task"] if isinstance(node["task"], str) else "(this node states no body prose)"
        rendered = (
            f"### Node `{node['id']}`\n\n"
            f"{json.dumps(fields, indent=2, ensure_ascii=False)}\n\n{body}"
        )
        if isinstance(steps, list):
            rendered += "\n\n" + "\n\n".join(
                f"#### Step {position}: {step['id']} (persona: {step['persona']})\n\n"
                f"{step['task'] or '(this step states no body prose)'}"
                for position, step in enumerate(steps, start=1)
            )
        sections.append(rendered)
    return (
        f"{PLAN_REVIEW_PROMPT}\n"
        f"## The review bar\n\n{bar}\n\n"
        f"## The plan's goal\n\n{goal if isinstance(goal, str) else json.dumps(goal)}\n\n"
        f"## What this host installs, and the pin each wheel governs\n\n"
        f"{host_installs.rendered()}\n{RUNGS}\n\n"
        f"## Every node of the plan, as its author wrote it\n\n" + "\n\n".join(sections) + "\n"
    )


def unwritable(project: str) -> str | None:
    """Why ``project``'s source could never carry a review record, or ``None``.

    A record is an entry of the task's own Markdown document, so only a `local-md`
    source has anywhere to put one — and that is the whole of what this decides: the
    plugin, and that it names a root at all. Whether that root then accepts the write,
    being absent or read-only, is the write's own answer and is reported there. This is
    the half that can be known before a provider turn is spent, which is why it is asked
    separately rather than left to the write alone.

    Answered as a reason rather than as a flag because both callers print it: this is
    the whole of what an operator can do about a plan on a store nothing here writes
    into, and "not writable" without the plugin's name sends them looking for a
    permission problem.
    """
    source, _ = plan_store.qualified(project)
    try:
        plan_store.source_root(source)
    except OSError as exc:
        return str(exc)
    return None


def review(project: str) -> Reviewed:
    """Review every unreviewed task of ``project`` and record each pass.

    A source no record can be written into is refused before the first turn is spent.
    Reviewing it would otherwise pay a provider for a verdict, discover at the write
    that there is nowhere to put it, and report a plan no further run of this command
    can advance — which reads as a transient failure and is not one.
    """
    reason = unwritable(project)
    if reason is not None:
        raise OSError(
            f"{reason}, so no run of this command can record one for {project!r} and none "
            f"was attempted; a plan held there is checked but never cleared"
        )
    records = plan_store.read_tasks(project)
    bar = bar_fingerprint()
    pending = unreviewed(records, bar)
    plan = plan_store.read_plan(project, records)
    plan_name = plan.get("name", project)
    refused: list[Refusal] = []
    recorded = 0
    for index, task in enumerate(pending):
        try:
            answered = verdict(_prompt(str(plan_name), task))
            if answered["passes"]:
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
        if not answered["passes"]:
            refused.append(Refusal(task.node_id, answered["findings"]))
    held = len(records) - len(pending)
    if refused:
        return Reviewed(recorded, held, refused)
    # The plan-level turn, spent only now: every task carries a record, so what is left
    # is the one question no task can answer for itself. A plan with a refused task
    # spends nothing here, because the plan it would be reading whole is one its author
    # is about to change.
    try:
        plan_bar = plan_bar_fingerprint()
        if not plan_unreviewed(plan_store.project_record(project), plan, plan_bar):
            return Reviewed(recorded, held, refused, plan=PlanReview.HELD)
        answered = verdict(_plan_prompt(plan))
        if answered["passes"]:
            write_plan_record(project, plan_key(plan, plan_bar), BY_REVIEW)
            return Reviewed(recorded, held, refused, plan=PlanReview.RECORDED)
    except OSError as exc:
        return Reviewed(
            recorded, held, refused, f"{exc}. The plan-level review was left unrecorded"
        )
    return Reviewed(
        recorded, held, refused, plan=PlanReview.REFUSED, plan_findings=answered["findings"]
    )


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
        # One line per finding rather than per task: a task whose reviewer refused three
        # criteria has three things to correct, and printing one of them is the defect
        # the verdict contract was widened to end.
        findings = 0
        for refusal in answered.refused:
            for line in refusal.lines():
                findings += 1
                print(f"review-plan: {line}", file=sys.stderr)
        print(
            f"review-plan: {findings} criterion(s) across {len(answered.refused)} task(s) "
            f"were refused and nothing was recorded for them; correct every criterion named "
            f"above in the plan's own task record, then run this command again — the "
            f"plan-level review is spent only once every task carries a record",
            file=sys.stderr,
        )
        return 1
    if answered.plan is PlanReview.REFUSED:
        for finding in answered.plan_findings:
            print(f"review-plan: {finding['criterion']} — {finding['why']}", file=sys.stderr)
        print(
            f"review-plan: the plan as a whole was refused on {len(answered.plan_findings)} "
            f"finding(s) and no plan-level record was written; every task's own record "
            f"stands. A finding naming `{THE_PLAN}` is an adoption the plan omits, and one "
            f"naming a node is that node declaring it wrongly — correct the plan's own "
            f"records, then run this command again",
            file=sys.stderr,
        )
        return 1
    print(
        f"review-plan: recorded a review of {answered.recorded} task(s); {answered.held} "
        f"already carried one for their current authored content; the plan as a whole "
        + (
            "was reviewed and recorded on the project"
            if answered.plan is PlanReview.RECORDED
            else "already carried a record for its current content"
        )
    )
    return 0


class Recorded(NamedTuple):
    """What a planning closeout could speak for, and what it had to leave alone."""

    #: Every task this closeout recorded a planner pass for.
    written: list[QualifiedTaskId]
    #: Every plan project this closeout recorded a plan-level planner pass for — the
    #: planner's own judge reviewed the plan whole, so the plan is recorded beside its
    #: tasks rather than left for `just review-plan` to spend the plan-level turn on.
    plans: list[str]
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
    settlement write-back has re-rendered carries metadata the SDK may refuse to update —
    so a launch overlapping another's
    would otherwise exit non-zero for a reason having nothing to do with it. Leaving the
    project alone fails in the safe direction: unrecorded is what `just check-plan`
    refuses, and the caller names each one.
    """
    bar = bar_fingerprint()
    plan_bar = plan_bar_fingerprint()
    known = set(before)
    written: list[QualifiedTaskId] = []
    plans: list[str] = []
    passed_over: list[str] = []
    for source in PLAN_SOURCES:
        for project in plan_store.local_projects(source):
            if project in known:
                continue
            try:
                tasks = plan_store.read_tasks(project)
                for task in tasks:
                    write_record(project, task, review_key(task, bar), BY_PLANNING)
                    written.append(task.qualified_id)
                plan = plan_store.read_plan(project, tasks)
                write_plan_record(project, plan_key(plan, plan_bar), BY_PLANNING)
                plans.append(project)
            except OSError as exc:
                passed_over.append(f"{project}: {exc}")
    return Recorded(written, plans, passed_over)


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
        f"plan-review: recorded a planner pass for {len(recorded.written)} task(s) and "
        f"for {len(recorded.plans)} plan(s) whole",
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
