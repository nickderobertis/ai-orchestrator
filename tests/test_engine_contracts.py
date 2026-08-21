"""Every engine contract this repository's prose states, against the engines' own declarations.

`docs/repo-lifecycle.md` carries a table of every `outcome` a settled node can
report, and the documents around it quote engine enums, constants, and
environment-variable names by name. Every one of those is a copy of somebody else's
contract, and a copy of an engine contract is exactly what goes stale in silence
here: for a whole release cycle this repository documented `gate-failed` and
`checks-failed` at seven sites, a manager read them, and neither word had ever been
written by either engine. The engines were extracted from this repository, the prose
stayed where it was, and nothing noticed. `checks-failed` has since become real —
onevcs 0.10.0 names that failure and onepipeline 0.10.0 settles a node on it — which
is the other direction this gate reads: a denial goes stale exactly as a description
does, and it went stale here first.

So the prose is reconciled rather than trusted, in five shapes:

* **The outcome table**, against every settlement site in `onepipeline` — the
  `Settlement::plain(…, Some(…))` calls, the `failed(node, …)` helper, and
  `vcs::outcome_of`, which is where `onevcs`'s five publication endings become this
  crate's words. A word on either side and not the other fails, in the direction
  that says which way it drifted. The two words the prose declares *absent* are held
  absent by the same read, so the denial fails the moment either becomes real.
* **Every closed vocabulary the prose enumerates exhaustively**, against the engine
  enum it is an enumeration of — `FailureKind`, `Retention`, `GateKind`, the
  telemetry `BucketName`, and `Resume`'s field list.
* **Every engine constant the prose quotes a number for**, rebuilt from the engine's
  own declaration and looked for in the document that quotes it.
* **Every engine name a document asserts or denies**, both ways: a symbol the prose
  says is gone is still gone, and one it says is in force is still declared. The
  assertion is the more dangerous half — a reader who configures an environment
  variable a release stopped reading gets no error at all.
* **The composed mappings**, where the prose states what the engine composes out of
  several declarations rather than any one of them: the outcome table's
  status-to-outcome pairings, and the telemetry phase machine's event-to-bucket
  table and its tie-break order.

These gates are why the surrounding prose may state any of this at all.

The engines' source lives outside the workspace, in checkouts
`config/onevcs.checkouts` registers, so a memoized verdict would describe whatever
they looked like when it was recorded — the one thing worse than no gate. Hence the
module marker: this runs in the uncached tier.

**Two engines are read, at two different refs, because they are adopted
differently.** `onepipeline` is read at `config/onepipeline.version`, the release the
CLI runs. `onevcs` is read at the version `onepipeline`'s own lockfile resolves —
which is what a *dispatch* publishes through, and is not `config/onevcs.version`.
Reading the CLI pin for both is the mistake `tests/test_linked_libraries.py` exists
over, so the linked version comes from that module rather than from a second copy of
the reasoning.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import NamedTuple

import pytest
from test_linked_libraries import _linked_version

from orchestrator.root import REPO_ROOT

pytestmark = pytest.mark.reads_checkouts

#: The tracked list of checkouts this host registers, which is where the engines'
#: own source is found. Both of this host layout's spellings are in it and only the
#: ones that exist are registered, so this reads it the same way `just repos-apply`
#: does rather than hard-coding one machine's paths.
TRACKED_CHECKOUTS = REPO_ROOT / "config" / "onevcs.checkouts"

LIFECYCLE = REPO_ROOT / "docs" / "repo-lifecycle.md"
TELEMETRY = REPO_ROOT / "docs" / "telemetry.md"
ONEJUDGE_INTEGRATION = REPO_ROOT / "docs" / "onejudge-integration.md"

#: The heading the outcome table sits under. A heading rather than a line number, so
#: a reflow above it cannot silently move this gate onto some other table.
TABLE_HEADING = "### The outcome vocabulary is closed, and it is this"

#: A settlement site that names **both** halves itself, as `(status, outcome)`. The
#: table being gated is a table of pairings, so the pairing is what is read: an
#: outcome word alone cannot tell a row that moved from `failed` to `done` from one
#: that did not move at all.
LITERAL_SETTLEMENTS = re.compile(
    r"Settlement::plain\([^)]*?NodeStatus::(\w+),\s*Some\(\"([a-z][a-z-]*)\"\)"
)

#: The `failed(node, "task-failed")` helper, whose status is written once in its own
#: definition rather than at each call. Both halves are read — the calls for the
#: words, the definition for the status they settle under — because a helper that
#: was re-pointed at another status would otherwise drift silently.
FAILED_HELPER = re.compile(r"\bfailed\(\s*&?node(?:\.id)?[^,]*,\s*\"([a-z][a-z-]*)\"\s*\)")
FAILED_HELPER_STATUS = re.compile(
    r"fn failed\(\s*node:\s*&str,\s*outcome:\s*&str\s*\)[^{]*\{\s*"
    r"Settlement::plain\(\s*node,\s*NodeStatus::(\w+),\s*Some\(outcome\)\s*\)"
)

#: The function that turns `onevcs`'s publication endings into this crate's words,
#: and the arms inside it. Matched as its own region because the arms are bare
#: `=> "word"`, which outside this function would match anything.
OUTCOME_OF = re.compile(r"pub fn outcome_of\(.*?\n\}", re.DOTALL)
OUTCOME_OF_ARM = re.compile(r"=>\s*\"([a-z][a-z-]*)\"")

#: The one site that settles a node on a word the *sibling* chose: `outcome_of`'s
#: answer, inside a `Settlement` whose remaining fields come from a `Settlement::plain`
#: naming the status. That status is the pairing for every word this relay can carry.
RELAY_SITE = re.compile(
    r"outcome:\s*Some\(crate::vcs::outcome_of\(.*?\.\.Settlement::plain\([^)]*?NodeStatus::(\w+),",
    re.DOTALL,
)

#: The guard that keeps every failure word away from that relay. `outcome_of`'s
#: `Failed` arm answers whatever `failure_of` decides — the residual, or one of the
#: four preserving words — and each of those settles the node `Failed` somewhere
#: else. Only this early return decides which pairing is real, so it is asserted
#: rather than assumed, and a release that drops it fails here instead of leaving
#: every failure row of the table quietly reading `done`.
RELAY_GUARD = re.compile(
    r"if let onevcs::PublishOutcome::Failed \{[^}]*?\}\s*=\s*&published\.outcome\s*\{\s*"
    r"return failed_publication\("
)

#: What that guard sends a failure to instead, and the two sites there that settle
#: on the word it chose: the failure's own, and the roll-up a spent publication
#: budget writes. Read for the status rather than the word, because the word comes
#: from `Preserving::outcome` below and a settlement that moved off `Failed` would
#: otherwise change nothing this gate can see.
FAILURE_RELAY = re.compile(
    r"Settlement::plain\(\s*node,\s*NodeStatus::(\w+),\s*"
    r"Some\((?:failure|preserved\.outcome)\.outcome\(\)\)"
)

#: Where those words are declared: the closed set of publication failures a further
#: attempt could answer. The residual beside them is a plain literal settlement and
#: is read as one.
PRESERVING_OUTCOME = re.compile(
    r"impl Preserving \{.*?pub fn outcome\(self\).*?\n    \}", re.DOTALL
)

#: Rust's own marker for code that is not the shipped crate. A settlement written in
#: a test fixture is not a settlement the engine makes, and reading one as though it
#: were is how two rows of the table came to be reconciled against a golden-document
#: builder rather than against the settlement path.
TEST_MODULE = re.compile(r"\n#\[cfg\(test\)\]\nmod tests \{.*", re.DOTALL)

#: The word this repository documented for a release cycle and neither engine has
#: ever written as a node outcome. Declared here so the gate fails if it ever becomes
#: real — at which point the prose that says it is absent is the thing to correct,
#: not this list. `checks-failed` was the other one and is no longer denied anywhere:
#: onevcs 0.10.0 gave it a `FailureKind` and onepipeline 0.10.0 a settlement, so it
#: left this list in the same change that gave it a row of the outcome table.
DECLARED_ABSENT = ("gate-failed",)


class Engine(NamedTuple):
    """One engine, and the ref its source is read at.

    The ref is the whole reason this is a type: the two engines here are adopted
    through different mechanisms, and pairing a checkout with the wrong one is the
    failure the module docstring is about.
    """

    #: The crate name, which is also the tail of its checkout directory.
    crate: str
    #: The git ref its adopted source is read at.
    ref: str
    #: Where its library source sits inside the checkout.
    source_root: str


def _pinned_tag(tool: str) -> str:
    """The git tag of the release `config/<tool>.version` pins."""
    version = (REPO_ROOT / "config" / f"{tool}.version").read_text("utf-8").strip()
    return f"v{version}"


#: The engine a dispatch's *pipeline* is, at the release this host runs.
ONEPIPELINE = Engine("onepipeline", _pinned_tag("onepipeline"), "src")
#: The engine a dispatch *publishes through*, at the version onepipeline links —
#: deliberately not `config/onevcs.version`, which is the CLI the manager verbs run.
ONEVCS = Engine("onevcs", f"v{_linked_version('onevcs')}", "crates/onevcs/src")
#: The engine a dispatch's *graph* is, at the version onepipeline links — read for the
#: same reason as onevcs, and never from `config/oneagentgraph.version`.
ONEAGENTGRAPH = Engine("oneagentgraph", f"v{_linked_version('oneagentgraph')}", "src")


class Vocabulary(NamedTuple):
    """One closed engine vocabulary the prose enumerates, and where each side enumerates it.

    Both sides are read the same way — a region, then the words inside it — because
    the comparison is set equality in both directions. Reading the prose side as
    "does this word appear anywhere in the passage" is what a first version of this
    gate did, and it proved nothing: it passed for a passage that had invented a
    variant, and a longer word containing a shorter one satisfied it by accident.
    """

    #: What a failure calls it.
    label: str
    engine: Engine
    #: The file inside the engine declaring it.
    source: str
    #: A pattern capturing the declaration, attributes included so the serde
    #: `rename_all` that decides the wire spelling is read from it rather than
    #: assumed. One `re.DOTALL` match, so the variants below cannot come from
    #: anywhere else in the file.
    region: re.Pattern[str]
    #: A pattern capturing each variant inside that declaration.
    variant: re.Pattern[str]
    #: The document that enumerates it.
    document: Path
    #: A pattern capturing the passage that enumerates it — the sentence or the
    #: table, not the section, so an unrelated mention elsewhere in the document
    #: neither satisfies nor breaks the comparison.
    enumeration: re.Pattern[str]
    #: A pattern capturing each word the enumeration names.
    named: re.Pattern[str]


class Constant(NamedTuple):
    """One engine constant the prose quotes, and the string that quoting must produce."""

    label: str
    engine: Engine
    source: str
    #: A pattern with one group capturing the declared value.
    declaration: re.Pattern[str]
    document: Path
    #: What the document must state, with `{value}` filled from the declaration.
    #: Compared against the document with markdown emphasis and backticks stripped,
    #: so a bolded number still matches.
    template: str


VOCABULARIES = (
    Vocabulary(
        "onevcs FailureKind",
        ONEVCS,
        "publish.rs",
        re.compile(r"(?:#\[[^\]]*\]\s*)*pub enum FailureKind \{.*?\n\}", re.DOTALL),
        re.compile(r"^\s{4}([A-Z][A-Za-z]*),", re.MULTILINE),
        LIFECYCLE,
        re.compile(r"carries a `kind` — (.*?), which the CLI reports", re.DOTALL),
        re.compile(r"`([a-z][a-z-]*)`"),
    ),
    Vocabulary(
        "onevcs Retention",
        ONEVCS,
        "publish.rs",
        re.compile(r"(?:#\[[^\]]*\]\s*)*pub enum Retention \{.*?\n\}", re.DOTALL),
        re.compile(r"^\s{4}([A-Z][A-Za-z]*)\(", re.MULTILINE),
        LIFECYCLE,
        re.compile(r"a `retained` saying whether the branch was\s+(.*?) by it\.", re.DOTALL),
        re.compile(r"`([a-z][a-z-]*)`"),
    ),
    Vocabulary(
        "onevcs GateKind",
        ONEVCS,
        "rules.rs",
        re.compile(r"(?:#\[[^\]]*\]\s*)*pub enum GateKind \{.*?\n\}", re.DOTALL),
        re.compile(r"^\s{4}([A-Z][A-Za-z]*),", re.MULTILINE),
        LIFECYCLE,
        re.compile(r"\| Gate \| Who runs it \|(.*?)\n\n", re.DOTALL),
        re.compile(r"\{kind: ([a-z][a-z-]*)\}"),
    ),
    Vocabulary(
        "onepipeline telemetry BucketName",
        ONEPIPELINE,
        "telemetry.rs",
        re.compile(r"(?:#\[[^\]]*\]\s*)*pub enum BucketName \{.*?\n\}", re.DOTALL),
        re.compile(r"^\s{4}([A-Z][A-Za-z]*),", re.MULTILINE),
        TELEMETRY,
        re.compile(r"\| Bucket \| What it counts \|(.*?)\n\n", re.DOTALL),
        re.compile(r"^\| `([a-z][a-z_]*)` \|", re.MULTILINE),
    ),
    Vocabulary(
        "onepipeline Resume fields",
        ONEPIPELINE,
        "plan.rs",
        re.compile(r"(?:#\[[^\]]*\]\s*)*pub struct Resume \{.*?\n\}", re.DOTALL),
        re.compile(r"^\s{4}pub ([a-z_]+):", re.MULTILINE),
        LIFECYCLE,
        re.compile(r"it has exactly three fields —\s*(.*?) — under", re.DOTALL),
        re.compile(r"`([a-z][a-z_]*)`"),
    ),
    Vocabulary(
        "onepipeline Node fields",
        ONEPIPELINE,
        "plan.rs",
        re.compile(r"(?:#\[[^\]]*\]\s*)*pub struct Node \{.*?\n\}", re.DOTALL),
        re.compile(r"^\s{4}pub ([a-z_]+):", re.MULTILINE),
        LIFECYCLE,
        re.compile(r"so what it may carry is a closed list —\s*(.*?) — and", re.DOTALL),
        re.compile(r"`([a-z][a-z_]*)`"),
    ),
    Vocabulary(
        "onevcs hook-running git commands",
        ONEVCS,
        "git.rs",
        re.compile(r"const HOOK_RUNNING: &\[&\[&str\]\] = &\[.*?\n\];", re.DOTALL),
        # Quoted words only: the declaration's own `&[&str]` type header sits inside
        # the region and would otherwise read as an entry of the list it types.
        re.compile(r"&\[((?:\"[a-z]+\",?\s*)+)\]"),
        LIFECYCLE,
        re.compile(r"Hook-running commands: (.*?)\.\n", re.DOTALL),
        re.compile(r"`git ([a-z ]+)`"),
    ),
    Vocabulary(
        "onepipeline RunTelemetry fields",
        ONEPIPELINE,
        "telemetry.rs",
        re.compile(r"(?:#\[[^\]]*\]\s*)*pub struct RunTelemetry \{.*?\n\}", re.DOTALL),
        re.compile(r"^\s{4}pub ([a-z_]+):", re.MULTILINE),
        TELEMETRY,
        re.compile(r"its top-level fields are\s*(.*?) — a tenth would be", re.DOTALL),
        re.compile(r"`([a-z][a-z_]*)`"),
    ),
    Vocabulary(
        "onepipeline telemetry Usage fields",
        ONEPIPELINE,
        "telemetry.rs",
        re.compile(r"(?:#\[[^\]]*\]\s*)*pub struct Usage \{.*?\n\}", re.DOTALL),
        re.compile(r"^\s{4}pub ([a-z_]+):", re.MULTILINE),
        TELEMETRY,
        re.compile(r"— each carrying\s*(.*?), and each field", re.DOTALL),
        re.compile(r"`([a-z][a-z_]*)`"),
    ),
    Vocabulary(
        "onepipeline telemetry Party",
        ONEPIPELINE,
        "telemetry.rs",
        re.compile(r"(?:#\[[^\]]*\]\s*)*pub enum Party \{.*?\n\}", re.DOTALL),
        re.compile(r"^\s{4}([A-Z][A-Za-z]*),", re.MULTILINE),
        TELEMETRY,
        re.compile(r"`usage` is per party — (.*?) — each carrying", re.DOTALL),
        re.compile(r"`([a-z][a-z_]*)`"),
    ),
    Vocabulary(
        "onepipeline driver liveness verdicts",
        ONEPIPELINE,
        "views.rs",
        re.compile(r"impl DriverLiveness \{.*?pub fn as_str\(self\).*?\n    \}", re.DOTALL),
        re.compile(r'=> "([A-Z][A-Z ]*)"'),
        TELEMETRY,
        re.compile(r"The \*\*driver\*\* verdict is\s+one of (.*?); `just orchestrate", re.DOTALL),
        re.compile(r"`([A-Z][A-Z ]*)`"),
    ),
    Vocabulary(
        "onepipeline observer liveness verdicts",
        ONEPIPELINE,
        "views.rs",
        # The `Watching` arm prints the empty string, which the variant pattern does not
        # match and the prose states as "nothing at all" rather than as a word. That is
        # the one asymmetry here, and it is the right one: a reader cannot look up a
        # verdict that is never printed.
        re.compile(r"impl ObserverLiveness \{.*?fn as_str\(self\).*?\n    \}", re.DOTALL),
        re.compile(r'=> "([A-Z][A-Z ]*)"'),
        TELEMETRY,
        re.compile(r"prints\s+beside it: (.*?)and nothing at all while it is", re.DOTALL),
        re.compile(r"`([A-Z][A-Z ]*)`"),
    ),
    Vocabulary(
        "onepipeline run ledger layout",
        ONEPIPELINE,
        "ledger.rs",
        re.compile(r"impl RunPaths \{.*?\n\}\n", re.DOTALL),
        re.compile(r"self\.dir\.join\(\"([a-z._]+)\"\)"),
        LIFECYCLE,
        re.compile(r"the ledger is flat:\n\n```\n(.*?)```", re.DOTALL),
        re.compile(r"^runs/<run-id>/([a-z._]+)/?", re.MULTILINE),
    ),
)

#: How a serde `rename_all` spells a Rust variant on the wire, and what a passage
#: therefore has to name. Read from the declaration rather than assumed, because the
#: two enums this module compares against tables disagree: `GateKind` is kebab and
#: `BucketName` is snake, and guessing one would make the other's gate vacuous.
RENAME_ALL = re.compile(r'rename_all\s*=\s*"([a-z_-]+)"')


def _wire_spelling(variant: str, rename_all: str | None) -> str:
    """One Rust variant as the declaration says it is written outside Rust."""
    if rename_all is None:
        return variant
    words = re.findall(r"[A-Z]?[a-z0-9]+", variant)
    joined = {"kebab-case": "-", "snake_case": "_", "lowercase": ""}
    assert rename_all in joined, (
        f"the declaration renames variants {rename_all!r}, a spelling this gate cannot "
        "reproduce; teach it that spelling rather than dropping the comparison"
    )
    return joined[rename_all].join(word.lower() for word in words)


CONSTANTS = (
    Constant(
        "boundary attempts",
        ONEPIPELINE,
        "engine.rs",
        re.compile(r"pub const DEFAULT_BOUNDARY_ATTEMPTS: u32 = (\d+);"),
        LIFECYCLE,
        "DEFAULT_BOUNDARY_ATTEMPTS = {value}",
    ),
    Constant(
        "boundary backoff",
        ONEPIPELINE,
        "engine.rs",
        re.compile(r"pub const DEFAULT_BOUNDARY_BACKOFF_SECONDS: u64 = (\d+);"),
        LIFECYCLE,
        "a {value}-second backoff",
    ),
    Constant(
        "boundary backoff ceiling",
        ONEPIPELINE,
        "engine.rs",
        re.compile(r"const BOUNDARY_BACKOFF_CEILING: Duration = Duration::from_secs\((\d+)\);"),
        LIFECYCLE,
        "doubles to a {value}-second ceiling",
    ),
    Constant(
        "boundary attempts env",
        ONEPIPELINE,
        "engine.rs",
        re.compile(r"pub const BOUNDARY_ATTEMPTS_ENV: &str = \"([A-Z_]+)\";"),
        LIFECYCLE,
        "{value}",
    ),
    Constant(
        "boundary backoff env",
        ONEPIPELINE,
        "engine.rs",
        re.compile(r"pub const BOUNDARY_BACKOFF_ENV: &str = \"([A-Z_]+)\";"),
        LIFECYCLE,
        "{value}",
    ),
    Constant(
        "ordinary git bound",
        ONEVCS,
        "git.rs",
        re.compile(r"pub const DEFAULT_TIMEOUT_SECONDS: f64 = (\d+)\.0;"),
        LIFECYCLE,
        "ONEVCS_GIT_TIMEOUT (default {value}s)",
    ),
    Constant(
        "hook-running git bound",
        ONEVCS,
        "git.rs",
        re.compile(r"pub const DEFAULT_HOOK_TIMEOUT_SECONDS: f64 = (\d+)\.0;"),
        LIFECYCLE,
        "ONEVCS_GIT_HOOK_TIMEOUT (default {value}s)",
    ),
    Constant(
        "git bound drain",
        ONEVCS,
        "git.rs",
        re.compile(r"const DRAIN: Duration = Duration::from_secs\((\d+)\);"),
        LIFECYCLE,
        "at most DRAIN ({value}s)",
    ),
    Constant(
        "preserved gate logs retained",
        ONEVCS,
        "gate.rs",
        re.compile(r"pub const PRESERVED_LOG_ATTEMPTS: usize = (\d+);"),
        LIFECYCLE,
        "the newest {value} (gate::PRESERVED_LOG_ATTEMPTS)",
    ),
    Constant(
        "preserved gate log directory",
        ONEVCS,
        "gate.rs",
        re.compile(r"pub const PRESERVED_LOG_DIRNAME: &str = \"([a-z-]+)\";"),
        LIFECYCLE,
        "{value}",
    ),
)


#: The documents that name a symbol as *gone*, and the symbols each names.
#:
#: A denial is a claim about an engine exactly as a description is, and it rots the
#: same way — a reader who acts on "there is no `X`" is as misled as one who acts on
#: an invented `X`, and the engines here are moving. Every one of these was measured
#: absent from both adopted engines when the surrounding prose was written; this is
#: what keeps them measured.
#:
#: The pre-extraction Python names dominate the list because that implementation is
#: what these documents used to describe. Nothing stops an engine adopting one of
#: these names later — and if it does, the paragraph denying it is the stale claim
#: and this is where that is noticed.
#:
#: Keyed by document *and by the engines that document denies against*, because the
#: two are not the same set: `onejudge-integration.md`'s callout names all three, and
#: the other two documents deny against the pair a lifecycle node runs through. A
#: denial read against fewer engines than it claims is a gate that passes while the
#: claim is false; one read against more fails on a name the document never spoke for.
ABSENT_SYMBOLS: dict[tuple[Path, tuple[Engine, ...]], tuple[str, ...]] = {
    (LIFECYCLE, (ONEPIPELINE, ONEVCS)): (
        # The vocabulary that never existed, at seven sites, for a release cycle.
        # `gate-failed` is deliberately *not* here: `onevcs integrate` really does
        # write it, as a per-candidate skip reason, and the prose says so. What was
        # never real is `gate-failed` as a *node outcome*, and that narrower claim is
        # what `DECLARED_ABSENT` above holds — a blanket absence check would fail on
        # the very sentence that draws the distinction.
        # `checks-failed` is not here for a different reason: it stopped being
        # absent. Both engines write it now, so the document describes it instead.
        "not-completed",
        # The continuation machinery the pre-extraction lifecycle had.
        "MAX_AUTOMATIC_STEP_RESUMES",
        "DEFAULT_LIFECYCLE_STEP_MAX_TURNS",
        "run_repo_task",
        "branch-discovered",
        "resume-failed",
        "retry_lineage",
        "resume_declined",
        "stack_bases",
        # Plan keys a reader might still set, and the Node schema now refuses.
        # `recorded_gate` is deliberately not here: `onepipeline` has a test named
        # `a_recorded_gate_is_discarded_and_re_derived`, about a human *gate node*
        # and nothing to do with a plan field, so a substring check would fail on the
        # sentence that correctly says no such field exists. The `Node fields`
        # vocabulary above holds that claim exactly, by set equality.
        "verify_cmd",
        "skip_verify",
        "no_identity_gate",
        # Events and artifacts the gate evidence was said to be in.
        "merge-gate-coverage",
        "verification-finished",
        "gate_log",
        # The pre-extraction teardown, named where the git bounds are described.
        "terminate_process_group",
    ),
    (TELEMETRY, (ONEPIPELINE, ONEVCS)): (
        "merge-gate-coverage",
        "verification-finished",
        "gate_log",
        "ORCHESTRATOR_PROVIDER_HEALTH_PROBE",
        "quota_at_launch",
        "quota_mid_conversation",
        "judge_unrecorded",
        "history-write-failed",
    ),
    (ONEJUDGE_INTEGRATION, (ONEPIPELINE, ONEAGENTGRAPH, ONEVCS)): (
        "worker-died",
        # The two dispatch events the callout denies beside the rest.
        "node-failed",
        "step-settled",
        "AGENT_STATUS_NAMES",
        "outcome_detail",
        "ORCHESTRATOR_WORKER_HEARTBEAT_TIMEOUT",
        "ORCHESTRATOR_DISPATCH_STALL_TIMEOUT",
        "owned_tree",
        "tear_down",
        "terminate_processes",
        "terminate_tree",
        "processes_stamped_for",
        "orphaned_dispatch_processes",
    ),
}


#: The phase machine `telemetry.md` tabulates: which `onevcs` session event kinds put a
#: node in which bucket, and which bucket wins a millisecond two sessions disagree
#: about. Three declarations compose it — the kind-to-phase match, the phase-to-bucket
#: match, and the precedence array — and the prose states the composition, so the gate
#: composes it the same way rather than reading any one of them.
PHASE_OF = re.compile(r"fn of\(kind: &str\) -> Option<Self> \{.*?\n    \}", re.DOTALL)
PHASE_OF_ARM = re.compile(r'((?:\s*\|?\s*"[a-z-]+")+)\s*=>\s*Some\(Self::(\w+)\)')
PHASE_BUCKET = re.compile(r"fn bucket\(self\) -> BucketName \{.*?\n    \}", re.DOTALL)
PHASE_BUCKET_ARM = re.compile(r"Self::(\w+) => BucketName::(\w+),")
PHASE_PRECEDENCE = re.compile(r"const PRECEDENCE: \[Self; \d+\] = \[([^\]]*)\];")
BUCKET_NAME_DECL = re.compile(r"(?:#\[[^\]]*\]\s*)*pub enum BucketName \{.*?\n\}", re.DOTALL)

#: The table that mirrors it, and the sentence that mirrors the order.
PHASE_TABLE = re.compile(
    r"\| Bucket \| The session event kinds that put a node in it \|\n(?:.*\n)*?\n", re.MULTILINE
)
PHASE_ORDER = re.compile(r"blocked before working: (.*?)\.\n", re.DOTALL)


#: The mirror image of `ABSENT_SYMBOLS`: a name a document states is **in force**,
#: and the engine that has to declare it. A denial and an assertion go stale the same
#: way, and the assertion is the more dangerous of the two — a reader who configures
#: `ONEPIPELINE_STALL_AFTER_SECONDS` against a release that stopped reading it gets no
#: error, just a stall proposal that never arrives.
#:
#: A path rather than a symbol where the document names a file, because that is what
#: it points a reader at.
PRESENT_SYMBOLS: dict[Path, tuple[tuple[Engine, str], ...]] = {
    ONEJUDGE_INTEGRATION: (
        # What is measurably in force at the seam whose pre-extraction symbols the
        # paragraph above them denies. The denial is gated; without this the assertion
        # beside it was not, and the two are read as one sentence.
        (ONEPIPELINE, "ONEPIPELINE_STALL_AFTER_SECONDS"),
        (ONEAGENTGRAPH, "ONEAGENTGRAPH_STALL_TIMEOUT"),
        (ONEAGENTGRAPH, "ONEAGENTGRAPH_HEARTBEAT_TIMEOUT"),
        (ONEAGENTGRAPH, "src/scratch.rs"),
    ),
    LIFECYCLE: (
        # Where a merge-path verdict is preserved: the event it hangs off, the call
        # that stores the log as an artifact, and the field naming the durable copy.
        # A reader follows all three to find a gate's own words, and each is `onevcs`'s
        # to rename.
        (ONEVCS, "gate-verdict"),
        (ONEVCS, 'store_artifact("log"'),
        (ONEVCS, "preserved_log"),
    ),
}


def _checkout(engine: Engine) -> Path:
    """A registered checkout of the engine, or a failure naming how to get one.

    **A failure and never a skip.** `config/onevcs.checkouts` tolerates a host that
    does not have every checkout, and it is right to — but a drift gate that answers
    "no checkout, nothing to say" is a gate the whole quality tier can pass while
    reconciling nothing, which is the shape this module exists to remove rather than
    reproduce. The engines are the subject of the documents being gated, so a host
    that maintains them has their source, and one that does not is told where it
    comes from.
    """
    for line in TRACKED_CHECKOUTS.read_text("utf-8").splitlines():
        candidate = line.split("#", 1)[0].strip()
        if not candidate or Path(candidate).name.split("__")[-1] != engine.crate:
            continue
        path = Path(os.path.expanduser(candidate))
        if (path / ".git").exists():
            return path
    raise AssertionError(
        f"no registered checkout of {engine.crate} on this host, so nothing here can "
        f"reconcile what this repository's prose says about it. {TRACKED_CHECKOUTS} "
        "lists every path `just repos-apply` registers, under both of this host "
        f"family's layouts; clone {engine.crate} into one of the ones it names"
    )


def _source(engine: Engine, relative: str) -> str:
    """One file of an engine, at the release this host adopted it through.

    At the ref rather than the working tree: a checkout here is whatever somebody
    last left it at, and reading that would reconcile the prose against a release
    nothing runs. That mistake has already been made against `onevcs`, whose local
    working tree sits several releases behind its origin.
    """
    path = f"{engine.source_root}/{relative}"
    read = subprocess.run(
        ["git", "-C", str(_checkout(engine)), "show", f"{engine.ref}:{path}"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert read.returncode == 0, (
        f"the {engine.crate} checkout cannot show {path} at {engine.ref}: "
        f"{read.stderr.strip()}. Fetch that ref, or correct the pin if this host "
        "adopted a release the engine never published"
    )
    return read.stdout


def _rust_files(engine: Engine) -> list[str]:
    """Every Rust file under the engine's source root, at its adopted ref.

    Enumerated from the ref rather than listed in this module, because what is read
    here backs claims about the *engine*: a symbol is absent from the crate, not from
    a set of files somebody thought to name, and a settlement is composed wherever the
    crate composes one. A hand-written list answers a narrower question than the prose
    asks, and it answers it silently — a file nobody listed goes on being unread while
    the gate stays green.

    Paths are returned relative to the source root, which is what `_source` takes.
    """
    listing = subprocess.run(
        ["git", "-C", str(_checkout(engine)), "ls-tree", "-r", "--name-only", engine.ref],
        capture_output=True,
        text=True,
        check=False,
    )
    assert listing.returncode == 0, (
        f"the {engine.crate} checkout cannot list {engine.ref}: {listing.stderr.strip()}. "
        "Fetch that ref, or correct the pin if this host adopted a release the engine "
        "never published"
    )
    files = [
        line
        for line in listing.stdout.splitlines()
        if line.startswith(f"{engine.source_root}/") and line.endswith(".rs")
    ]
    assert files, (
        f"{engine.crate} {engine.ref} ships no Rust file under {engine.source_root}; "
        "this gate would then prove every denial by reading nothing, so the source root "
        "is what moved and this is where to correct it"
    )
    return [name[len(engine.source_root) + 1 :] for name in sorted(files)]


def _whole_source(engine: Engine) -> str:
    """Everything the engine ships, concatenated, at its adopted ref."""
    return "\n".join(_source(engine, name) for name in _rust_files(engine))


def _region(source: str, pattern: re.Pattern[str], label: str) -> str:
    """One declaration out of an engine source, or a failure naming what moved."""
    found = pattern.search(source)
    assert found, (
        f"onepipeline {ONEPIPELINE.ref} no longer declares {label} where this gate reads "
        "it; the declaration moved, and a gate that cannot find it proves nothing about "
        "what is there now"
    )
    return found.group(0)


def _section(document: Path, heading: str) -> str:
    """The passage under one heading, up to the next heading of the same depth or shallower."""
    text = document.read_text("utf-8")
    start = text.find(heading)
    assert start != -1, f"{document.name} no longer carries the heading {heading!r}"
    body = text[start + len(heading) :]
    depth = len(heading) - len(heading.lstrip("#"))
    for shallower in range(depth, 0, -1):
        body = body.split("\n" + "#" * shallower + " ", 1)[0]
    return body


def _plain(text: str) -> str:
    """A document passage with markdown emphasis and code spans dropped.

    A constant this repository quotes is normally bolded or in a code span, and both
    are presentation. Comparing against the presentation would make a gate that fails
    when somebody un-bolds a number, which teaches a maintainer to delete the gate.
    """
    return re.sub(r"\s+", " ", text.replace("**", "").replace("`", "").replace("*", ""))


@pytest.fixture(scope="module")
def engine_settlements() -> frozenset[tuple[str, str]]:
    """Every `(status, outcome)` the adopted `onepipeline` release can settle a node on.

    Read across the whole crate rather than a named handful of files, because the
    claim is about what the *engine* writes: a settlement composed somewhere nobody
    listed is exactly the drift worth failing on, and a hand-written list absorbs it
    silently. Test modules are stripped first — a fixture that builds a golden
    document is not a settlement path, and reading one as though it were is how
    `merged` and `change-open` came to be reconciled against a test builder.
    """
    shipped = "\n".join(
        TEST_MODULE.sub("", _source(ONEPIPELINE, name)) for name in _rust_files(ONEPIPELINE)
    )

    helper_status = FAILED_HELPER_STATUS.search(shipped)
    assert helper_status, (
        f"onepipeline {ONEPIPELINE.ref} no longer defines the `failed(node, outcome)` "
        "helper this gate reads its status from; every word settled through it is now "
        "paired with nothing"
    )

    relay = RELAY_SITE.search(shipped)
    assert relay, (
        f"onepipeline {ONEPIPELINE.ref} no longer settles a node on `outcome_of`'s "
        "answer where this gate reads it, so the publication words have lost the status "
        "they pair with"
    )
    assert RELAY_GUARD.search(shipped), (
        f"onepipeline {ONEPIPELINE.ref} no longer returns early on "
        f"`PublishOutcome::Failed`, so every word `failure_of` chooses now reaches the "
        f"`{relay.group(1)}` relay as well as the failure sites that settle it. That "
        "guard is the whole reason the table pairs those words with one status; re-read "
        "the path and correct the rows before relaxing this"
    )

    failure_statuses = {status.lower() for status in FAILURE_RELAY.findall(shipped)}
    assert failure_statuses, (
        f"onepipeline {ONEPIPELINE.ref} no longer settles a publication failure on the "
        "word `failure_of` chose where this gate reads it, so the preserving words have "
        "lost the status they pair with"
    )
    preserving = _region(shipped, PRESERVING_OUTCOME, "`Preserving::outcome`")

    found = {(status.lower(), outcome) for status, outcome in LITERAL_SETTLEMENTS.findall(shipped)}
    found.update(
        (helper_status.group(1).lower(), outcome) for outcome in FAILED_HELPER.findall(shipped)
    )
    found.update(
        (relay.group(1).lower(), arm)
        for body in OUTCOME_OF.findall(shipped)
        for arm in OUTCOME_OF_ARM.findall(body)
    )
    found.update(
        (status, word) for status in failure_statuses for word in OUTCOME_OF_ARM.findall(preserving)
    )
    assert found, (
        f"no settlement was found in onepipeline {ONEPIPELINE.ref}; the engine settles a "
        "node somewhere this gate no longer reads, so it is proving nothing"
    )
    return frozenset(found)


@pytest.fixture(scope="module")
def documented_settlements() -> frozenset[tuple[str, str]]:
    """Every `(status, outcome)` the prose's table pairs, read out of the table itself.

    Both columns, because a table of pairings that is gated on one of them is gated on
    neither: `publication-failed` moving to `done`, or `no-changes` to `failed`, would
    leave every word still present and every word still real.

    Rows whose outcome is `*(none)*` are not pairings — they say a node of that shape
    settles with no outcome at all — and a status cell naming two statuses at once is
    read as both.
    """
    paired: set[tuple[str, str]] = set()
    for row in _section(LIFECYCLE, TABLE_HEADING).splitlines():
        columns = [column.strip() for column in row.split("|")]
        if len(columns) < 4 or columns[1].startswith("---"):
            continue
        outcome = re.fullmatch(r"`([a-z][a-z-]*)`", columns[2])
        if not outcome:
            continue
        statuses = re.findall(r"`([a-z][a-z-]*)`", columns[1])
        assert statuses, (
            f"{LIFECYCLE.name}'s outcome table pairs {outcome.group(1)!r} with no status; "
            "the Status column is half of what this gate reconciles"
        )
        paired.update((status, outcome.group(1)) for status in statuses)
    assert paired, f"{LIFECYCLE.name}'s outcome table under {TABLE_HEADING!r} named nothing"
    return frozenset(paired)


def test_the_documented_outcomes_are_the_ones_the_engine_writes(
    engine_settlements: frozenset[tuple[str, str]],
    documented_settlements: frozenset[tuple[str, str]],
) -> None:
    """The table and the engine pair the same statuses with the same words, both ways.

    Pairings rather than words, because a table of pairings is what is being gated: a
    row that moved `publication-failed` from `failed` to `done` invents nothing and
    drops nothing, and a gate that compared only the words would pass it.

    Stated as two comparisons rather than one equality so a failure says which way
    the drift went: a pairing the prose invented sends a reader looking for something
    that will never happen, and one the engine gained without the prose is a
    settlement nobody here can read.
    """
    invented = sorted(documented_settlements - engine_settlements)
    assert not invented, (
        f"{LIFECYCLE.name} pairs statuses with outcomes the adopted onepipeline never "
        f"settles on: {invented}. Re-read the settlement sites and correct the table"
    )
    unreported = sorted(engine_settlements - documented_settlements)
    assert not unreported, (
        f"the adopted onepipeline settles nodes {LIFECYCLE.name} does not name: "
        f"{unreported}. A settled node reporting one leaves a reader with nothing to "
        "look it up under"
    )


def test_the_words_the_prose_declares_absent_are_absent(
    engine_settlements: frozenset[tuple[str, str]],
) -> None:
    """`gate-failed` is still not a node outcome, and the prose still says so.

    The correction this gate was written for. Both halves matter: if the engine ever
    gains one of these, the prose denying it becomes the new stale claim, and this
    fails before a reader acts on it.
    """
    written = {outcome for _, outcome in engine_settlements}
    real = sorted(word for word in DECLARED_ABSENT if word in written)
    assert not real, (
        f"the adopted onepipeline now writes {real} as node outcome(s); {LIFECYCLE.name} "
        "says it never has, so that denial is now the stale claim"
    )
    text = LIFECYCLE.read_text("utf-8")
    for word in DECLARED_ABSENT:
        assert f"`{word}`" in text, (
            f"{LIFECYCLE.name} no longer names `{word}` as absent. A reader arriving with "
            "that word from an older document has nothing to find, which is the state "
            "this correction removed"
        )


@pytest.mark.parametrize(
    ("document", "engine", "symbol"),
    [
        (document, engine, symbol)
        for document, claims in PRESENT_SYMBOLS.items()
        for engine, symbol in claims
    ],
    ids=lambda item: item.name if isinstance(item, Path) else str(item),
)
def test_every_symbol_a_document_calls_current_is_still_there(
    document: Path, engine: Engine, symbol: str
) -> None:
    """An assertion is reconciled the same way a denial is, and matters more.

    A denial that goes stale sends a reader looking for something that turns out to
    exist. An assertion that goes stale sends them to *configure* something that has
    been renamed, and nothing tells them: an environment variable no build reads is
    silently ignored, and a file path no release ships is a dead pointer in the one
    paragraph a reader reaches for when the seam misbehaves.

    Both halves, as before: the document still names it, and the engine still has it.
    """
    text = document.read_text("utf-8")
    assert symbol in text, (
        f"{document.name} no longer names {symbol!r}. It was carried to say the adopted "
        f"{engine.crate} has it; drop this entry in the same change that drops the claim"
    )
    if symbol.endswith(".rs"):
        assert symbol.split("/", 1)[1] in _rust_files(engine), (
            f"{document.name} points a reader at {engine.crate}'s {symbol}, which "
            f"{engine.ref} does not ship. Re-read the crate and correct the pointer"
        )
        return
    assert symbol in _whole_source(engine), (
        f"{document.name} says the adopted {engine.crate} declares {symbol!r}, and "
        f"{engine.ref} does not. That is the claim to correct — a reader configuring "
        "against it gets no error, only a setting nothing reads"
    )


@pytest.fixture(scope="module")
def engine_phases() -> tuple[dict[str, frozenset[str]], tuple[str, ...]]:
    """The phase machine's kind-to-bucket table and its precedence, as wire words.

    Composed from the engine's three declarations exactly as the engine composes it —
    kind to phase, phase to bucket, bucket to its serialized word — because the prose
    states the composition and gating any single declaration would leave the other two
    free to move under it.
    """
    source = _source(ONEPIPELINE, "telemetry.rs")

    renamed = RENAME_ALL.search(_region(source, BUCKET_NAME_DECL, "the BucketName enum"))
    wire = {
        variant: _wire_spelling(variant, renamed.group(1) if renamed else None)
        for _, variant in PHASE_BUCKET_ARM.findall(_region(source, PHASE_BUCKET, "Phase::bucket"))
    }
    bucket_of = dict(PHASE_BUCKET_ARM.findall(_region(source, PHASE_BUCKET, "Phase::bucket")))

    table: dict[str, set[str]] = {}
    for kinds, phase in PHASE_OF_ARM.findall(_region(source, PHASE_OF, "Phase::of")):
        table.setdefault(wire[bucket_of[phase]], set()).update(re.findall(r'"([a-z-]+)"', kinds))
    assert table, (
        f"onepipeline {ONEPIPELINE.ref} maps no session event kind to a phase where this "
        "gate reads it, so the table it reconciles is being compared against nothing"
    )

    order = PHASE_PRECEDENCE.search(source)
    assert order, (
        f"onepipeline {ONEPIPELINE.ref} no longer declares Phase::PRECEDENCE; the order "
        "the prose states is now unreconciled"
    )
    precedence = tuple(
        wire[bucket_of[phase]] for phase in re.findall(r"Self::(\w+)", order.group(1))
    )
    return {name: frozenset(kinds) for name, kinds in table.items()}, precedence


def test_the_documented_phase_machine_is_the_engines_own(
    engine_phases: tuple[dict[str, frozenset[str]], tuple[str, ...]],
) -> None:
    """`telemetry.md`'s bucket table and tie-break order are `telemetry.rs`'s.

    The whole mapping in both directions, not membership: a kind that moved from
    `setup` to `publication_wait` changes which bucket an operator reads a slow run
    out of, while leaving every bucket name and every kind name still present.
    """
    table, precedence = engine_phases
    text = TELEMETRY.read_text("utf-8")

    passage = PHASE_TABLE.search(text)
    assert passage, (
        f"{TELEMETRY.name} no longer carries the phase table where this gate reads it; "
        "a gate that cannot find it proves nothing about the mapping that is there now"
    )
    documented = {
        columns[1].strip("`"): frozenset(re.findall(r"`([a-z-]+)`", columns[2]))
        for row in passage.group(0).splitlines()
        if len(columns := [column.strip() for column in row.split("|")]) >= 4
        and not columns[1].startswith("---")
        and re.fullmatch(r"`[a-z_]+`", columns[1])
    }
    assert documented == table, (
        f"{TELEMETRY.name} tabulates {documented}; onepipeline {ONEPIPELINE.ref} composes "
        f"{table}. Re-read `Phase::of` and `Phase::bucket` and correct the table"
    )

    stated = PHASE_ORDER.search(text)
    assert stated, f"{TELEMETRY.name} no longer states the tie-break order where this gate reads it"
    assert tuple(re.findall(r"`([a-z_]+)`", stated.group(1))) == precedence, (
        f"{TELEMETRY.name} orders the tie-break "
        f"{tuple(re.findall(r'`([a-z_]+)`', stated.group(1)))}; onepipeline "
        f"{ONEPIPELINE.ref} declares {precedence}"
    )


@pytest.mark.parametrize("vocabulary", VOCABULARIES, ids=lambda item: item.label)
def test_every_enumerated_vocabulary_is_the_engines_own(vocabulary: Vocabulary) -> None:
    """A passage that enumerates an engine vocabulary names every member and no others.

    Set equality in both directions, over the enumerating passage alone. A member the
    engine adds is a hole in a list the prose calls closed; a name only the prose has
    is the invention this module exists for. Only the enumerating sentence or table is
    read, because that is the passage making the exhaustiveness claim — and reading
    the whole section instead would let an unrelated mention of one word stand in for
    the enumeration containing it.
    """
    declaration = vocabulary.region.search(_source(vocabulary.engine, vocabulary.source))
    assert declaration is not None, (
        f"{vocabulary.label} is no longer declared where this gate reads it "
        f"({vocabulary.engine.crate} {vocabulary.source} at {vocabulary.engine.ref}); "
        "the prose enumerating it is now unreconciled"
    )
    renamed = RENAME_ALL.search(declaration.group(0))
    declared = {
        _wire_spelling(
            re.sub(r'["\s,]+', " ", variant).strip(), renamed.group(1) if renamed else None
        )
        for variant in vocabulary.variant.findall(declaration.group(0))
    }
    assert declared, f"{vocabulary.label} declares no variant this gate can read"

    passage = vocabulary.enumeration.search(vocabulary.document.read_text("utf-8"))
    assert passage is not None, (
        f"{vocabulary.document.name} no longer enumerates {vocabulary.label} where this "
        "gate reads it; the enumeration moved, and a gate that cannot find it proves "
        "nothing about the words that are there now"
    )
    enumerated = set(vocabulary.named.findall(passage.group(1)))
    assert enumerated == declared, (
        f"{vocabulary.document.name} enumerates {vocabulary.label} as "
        f"{sorted(enumerated)}; {vocabulary.engine.crate} {vocabulary.engine.ref} "
        f"declares {sorted(declared)}. Invented: {sorted(enumerated - declared)}; "
        f"missing: {sorted(declared - enumerated)}"
    )


@pytest.mark.parametrize("constant", CONSTANTS, ids=lambda item: item.label)
def test_every_quoted_engine_constant_is_the_engines_own(constant: Constant) -> None:
    """A number or variable name the prose quotes is the one the engine declares.

    Built from the declaration rather than compared with it, so the failure names the
    sentence the document should now be spelling instead of leaving a maintainer to
    work out which half moved.
    """
    declared = constant.declaration.search(_source(constant.engine, constant.source))
    assert declared is not None, (
        f"{constant.label} is no longer declared where this gate reads it "
        f"({constant.engine.crate} {constant.source} at {constant.engine.ref})"
    )
    expected = _plain(constant.template.format(value=declared.group(1)))
    assert expected in _plain(constant.document.read_text("utf-8")), (
        f"{constant.document.name} does not state {expected!r}. {constant.engine.crate} "
        f"{constant.engine.ref} declares {constant.label} as {declared.group(1)!r}, so "
        "that is the sentence the document should carry"
    )


@pytest.mark.parametrize(
    ("document", "engines"),
    sorted(ABSENT_SYMBOLS, key=lambda key: key[0].name),
    ids=lambda item: item.name if isinstance(item, Path) else "+".join(e.crate for e in item),
)
def test_every_symbol_a_document_calls_gone_is_still_gone(
    document: Path, engines: tuple[Engine, ...]
) -> None:
    """A denial is reconciled the same way a description is.

    Each of these names appears in its document only to say the adopted engines do
    not have it. That is load-bearing prose: it is what stops a reader configuring
    against `ORCHESTRATOR_WORKER_HEARTBEAT_TIMEOUT`, or waiting for an automatic
    continuation, or looking for `gate-failed` in a settled node. If an engine ever
    adopts one of these names, the sentence denying it becomes the new stale claim —
    so both halves are checked: the document still says it, and the engines still do
    not have it.

    The second half reads each engine the document denies against *whole*, at its
    adopted ref, because that is the scope the prose denies at. Reading a chosen
    handful of files would prove a narrower claim than the one being gated, and would
    go on passing after a symbol came back somewhere nobody had thought to list — and
    reading fewer engines than the sentence names does the same thing one crate at a
    time.
    """
    everything = "\n".join(_whole_source(engine) for engine in engines)
    text = document.read_text("utf-8")
    for symbol in ABSENT_SYMBOLS[(document, engines)]:
        assert symbol in text, (
            f"{document.name} no longer names {symbol!r}. It was carried only to say the "
            "adopted engines do not have it; a reader arriving with that name from an "
            "older document now finds nothing, which is the state this correction removed"
        )
        assert symbol not in everything, (
            f"{document.name} says {symbol!r} is in none of "
            f"{', '.join(f'{engine.crate} {engine.ref}' for engine in engines)}, and one of "
            "them now has it. That denial is the stale claim — re-read the engine and "
            "correct the paragraph carrying it"
        )
