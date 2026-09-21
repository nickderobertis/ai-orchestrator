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
  enum it is an enumeration of — `FailureKind`, `Retention`, `Coverage`, the
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

import ast
import json
import os
import re
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

import probe_run_root
import pytest
from published_surface import surface_of
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
ORCHESTRATION = REPO_ROOT / "docs" / "orchestration.md"
#: The manager's own document, which quotes one engine constant: how much of a tool
#: output `just transcript` prints. It is here rather than in an e2e journey because
#: no run this host has recorded produced an output past that ceiling — driving it
#: would mean writing synthetic records into a recorded journal, which is a producer
#: this repository does not have standing in for one it does.
MANAGER = REPO_ROOT / "AGENTS.md"

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
CONSTANT_SETTLEMENTS = re.compile(
    r"Settlement::plain\([^)]*?NodeStatus::(\w+),\s*Some\((?:[a-z:]+::)?([A-Z][A-Z_]*)\)"
)

#: The `failed(node, "task-failed")` helper, whose status is written once in its own
#: definition rather than at each call. Both halves are read — the calls for the
#: words, the definition for the status they settle under — because a helper that
#: was re-pointed at another status would otherwise drift silently.
#:
#: **The first argument is read as a binding rather than as the word `node`**, and the
#: difference has already cost a reading. These patterns required it to be spelled
#: `node`, and the engine now passes `id` at three of its settlement sites — so
#: `failed(id, NO_AGENT_PROGRESS)` went unseen and the gate reported a pairing the
#: prose had *invented*, on a settlement the engine writes to this day. A gate that
#: names a true row as a fiction is worse than one that misses it: the repair it asks
#: for is deleting the row. What identifies the site is the helper's own two-argument
#: shape — this crate's other `failed` takes one argument and a qualified path — so
#: that is what is anchored on, and the set it reads at the release before this change
#: is unchanged by the widening.
#: The node argument, however this crate happens to bind it at the call.
FAILED_HELPER_NODE = r"\bfailed\(\s*&?[a-z_][a-z_0-9]*(?:\.id)?\s*,\s*"
FAILED_HELPER = re.compile(FAILED_HELPER_NODE + r"\"([a-z][a-z-]*)\"\s*\)")
FAILED_HELPER_CONSTANT = re.compile(FAILED_HELPER_NODE + r"([A-Z][A-Z_]*)\s*\)")
OUTCOME_CONSTANT = re.compile(r"pub const ([A-Z][A-Z_]*): &(?:'static )?str = \"([a-z][a-z-]*)\";")
FAILED_HELPER_STATUS = re.compile(
    r"fn failed\(\s*node:\s*&str,\s*outcome:\s*&str\s*\)[^{]*\{\s*"
    r"Settlement::plain\(\s*node,\s*NodeStatus::(\w+),\s*Some\(outcome\)\s*\)"
)

#: The function that turns `onevcs`'s publication endings into this crate's words,
#: and the arms inside it. Matched as its own region because the arms are bare
#: `=> "word"`, which outside this function would match anything.
OUTCOME_OF = re.compile(r"pub fn outcome_of\(.*?\n\}", re.DOTALL)
OUTCOME_OF_ARM = re.compile(r"=>\s*\"([a-z][a-z-]*)\"")

#: The same arms read *with* the publication ending each answers for, and accepting a
#: named constant on the right-hand side as well as a literal. `change-draft` arrived
#: as `=> DRAFTED`, which the bare-literal pattern above cannot see at all — so the
#: word the engine had just started settling on read as a word it had stopped writing.
OUTCOME_OF_PAIR = re.compile(
    r"PublishOutcome::(\w+)[^=]*?=>\s*(?:\"([a-z][a-z-]*)\"|([A-Z][A-Z_]*))"
)

#: The one settlement whose word is chosen by a **binding** rather than named at the
#: call: the dispatch-death classifier picks between its constants and hands the
#: result to the `failed` helper. Read as its own site because a `failed(node, word)`
#: says nothing about which words `word` can be, and the two it can be — a dispatch
#: that died and a provider that went — are the pair a reader of a failed node most
#: needs told apart.
DEATH_WORD_BINDING = re.compile(
    r"let \([a-z_]+, ([a-z_]+)\) = match [a-z_]+ \{(.*?)\n    \};", re.DOTALL
)

#: The one site that settles a node on a word the *sibling* chose: `outcome_of`'s
#: answer, inside a `Settlement` whose remaining fields come from a `Settlement::plain`
#: whose status is **computed from the same publication**.
#:
#: It used to be a literal `NodeStatus::` at that call, and reading it as one is what
#: this pattern now refuses to do. onepipeline 0.18.x made the status a function of
#: the publication — a change held open as a draft settles somewhere a merged one does
#: not — and the old pattern's `.*?` ran past the call it was anchored on and captured
#: the next literal status anywhere in the crate, which is `Failed`. Nothing failed:
#: the gate went on reconciling, against three pairings the engine has never written.
#: So what is captured here is the **function's name**, and its arms are read below.
#: The function's argument list is not anchored past the publication it reads:
#: onepipeline 0.28.0 hands it the closeout's draft reason as a second argument, and a
#: pattern demanding the call close on the first would report the relay gone.
RELAY_SITE = re.compile(
    r"outcome:\s*Some\(crate::vcs::outcome_of\(.*?"
    r"\.\.Settlement::plain\(\s*&node\.id,\s*([a-z_]+)\(\s*&published\.outcome\b.*?\),"
    r"\s*None,?\s*\)",
    re.DOTALL,
)


#: That function, and the arms inside it. Both halves are joined on the *sibling's*
#: `PublishOutcome` variant rather than crossed, because crossing them would invent
#: pairings the engine cannot write — every status against every word, when in truth
#: one publication ending answers both questions at once.
def _definition_of(name: str) -> re.Pattern[str]:
    """The pattern matching the named function's definition, its whole body included."""
    return re.compile(rf"fn {re.escape(name)}\(.*?\n\}}", re.DOTALL)


PUBLICATION_STATUS_ARM = re.compile(
    r"(?:onevcs::)?PublishOutcome::(\w+)[^=]*?=>\s*NodeStatus::(\w+)"
)
PUBLICATION_STATUS_DEFAULT = re.compile(r"\n\s*_\s*=>\s*NodeStatus::(\w+)")

#: How the engine spells each status where a reader meets one. Read from the engine
#: rather than lower-cased from the Rust identifier, which is what this gate did while
#: every status was one word: `CompleteDraft` is written `complete-but-draft`, and a
#: gate deriving the word from the variant would look for a status no document has and
#: report the engine as having stopped settling on it.
NODE_STATUS_WORD = re.compile(r"Self::(\w+)\s*=>\s*\"([a-z][a-z-]*)\",")

#: The guard that keeps every failure word away from that relay. `outcome_of`'s
#: `Failed` arm answers whatever `failure_of` decides — the residual, the unread
#: merge path, or one of the preserving words — and each settles before the `Done`
#: relay. Only these early returns decide which pairing is real, so the path through
#: the preserving return is asserted rather than assumed.
RELAY_GUARD = re.compile(
    r"if let onevcs::PublishOutcome::Failed \{[^}]*?\}\s*=\s*&published\.outcome\s*\{\s*"
    r".*?return failed_publication\(",
    re.DOTALL,
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
#: The same release's published contract document, where the declarations a caller of the
#: engine — rather than a reader of its source — is held to are stated.
ONEPIPELINE_DOCS = Engine("onepipeline", _pinned_tag("onepipeline"), "docs")
#: The engine a dispatch *publishes through*, at the version onepipeline links —
#: deliberately not `config/onevcs.version`, which is the CLI the manager verbs run.
ONEVCS = Engine("onevcs", f"v{_linked_version('onevcs')}", "crates/onevcs/src")
#: The engine a dispatch's *graph* is, at the version onepipeline links — read for the
#: same reason as onevcs, and never from `config/oneagentgraph.version`.
ONEAGENTGRAPH = Engine("oneagentgraph", f"v{_linked_version('oneagentgraph')}", "src")
#: The engine that runs a **turn**, read at the tag of the `oneharness` CLI this host
#: pins — the one ref here that is not the crate's own, because `oneharness` tags only
#: the CLI. That is the right ref rather than a fallback: measured 2026-08-25, the
#: pinned CLI wheel is compiled against exactly the `oneharness-core` the engine wheel
#: links, so the tree at `v<pin>` is the core a dispatched turn runs through. It does
#: not make the pin the core's version — `tests/test_linked_libraries.py` gates that
#: distinction.
ONEHARNESS = Engine("oneharness", _pinned_tag("oneharness"), "crates/oneharness-core/src")
#: The message bus a journal envelope is, at the `onemessagebus-agent` onepipeline links.
#: Since onepipeline 0.31.0 the envelope, its `Source`, and the envelope versions a
#: build reads are that crate's — over the `onemessagebus` core, released from the same
#: repository at the same commit — and `src/event.rs` re-exports them. Read at the agent
#: crate's own tag, with the source root at `crates/` so both crates are reachable.
ONEMESSAGEBUS = Engine(
    "onemessagebus", f"onemessagebus-agent-v{_linked_version('onemessagebus-agent')}", "crates"
)


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
    # `GateKind` was this row until onevcs 0.11.0 removed the gate concept along with
    # the enum. What replaced it is the enum that answers the question the gate used
    # to: which verifier an identity's merge path actually has. Reconciled the same
    # way, so the table in the prose cannot outlive the variants it names.
    Vocabulary(
        "onevcs Coverage",
        ONEVCS,
        "store.rs",
        re.compile(r"(?:#\[[^\]]*\]\s*)*pub enum Coverage \{.*?\n\}", re.DOTALL),
        re.compile(r"^\s{4}([A-Z][A-Za-z]*)[,(]", re.MULTILINE),
        LIFECYCLE,
        re.compile(r"\| Coverage \| What verifies a change \|(.*?)\n\n", re.DOTALL),
        re.compile(r"\| `([A-Z][A-Za-z]*)"),
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
        # The live-edit protocol's own table, which is what a manager picks a lever
        # from. Set equality is what makes `AGENTS.md`'s pairing readable: the levers
        # it names are ops this table has, and the day the engine gains one that
        # amends a node in place, the table — and the paragraph resting on it — come
        # due here rather than the first time somebody types the op.
        "onepipeline live-edit commands",
        ONEPIPELINE,
        "channel.rs",
        re.compile(r"(?:#\[[^\]]*\]\s*)*pub enum Command \{.*?\n\}", re.DOTALL),
        re.compile(r"^\s{4}([A-Z][A-Za-z]*) \{", re.MULTILINE),
        ORCHESTRATION,
        re.compile(
            r"The accepted commands are:\n\n\|[^\n]*\n\|[^\n]*\n((?:\|[^\n]*\n)+)",
            re.DOTALL,
        ),
        re.compile(r"^\| `([a-z]+)` \|", re.MULTILINE),
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
#: two enums this module compares against tables disagree in spelling, and guessing
#: one would make the other's gate vacuous.
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
    # The reply envelope's own version, which is a serialized contract rather than a
    # number in prose: the engine bumped it to 2 when it removed `context` and left one
    # manager-note op, and a document still telling a manager to send version 1 is
    # describing the envelope before that break. Held against the crate constant so the
    # next bump comes due here rather than after somebody sends the old shape. Since
    # onepipeline 0.33.0 the channel runs on `onemessagebus-agent`, whose `channel.rs`
    # declares the number and whose re-export in onepipeline's own `channel.rs` names no
    # literal, so it is read where it is declared.
    Constant(
        "reply envelope version",
        ONEMESSAGEBUS,
        "onemessagebus-agent/src/channel.rs",
        re.compile(r"pub const REPLY_ENVELOPE_VERSION: u32 = (\d+);"),
        ORCHESTRATION,
        '"version":{value}',
    ),
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
        "git exit polling ceiling",
        ONEVCS,
        "git.rs",
        re.compile(r"const EXIT_POLL: Duration = Duration::from_millis\((\d+)\);"),
        LIFECYCLE,
        "at most EXIT_POLL ({value}ms)",
    ),
    Constant(
        "preserved merge-path logs retained",
        ONEVCS,
        "merge_path.rs",
        re.compile(r"pub const PRESERVED_LOG_ATTEMPTS: usize = (\d+);"),
        LIFECYCLE,
        "the newest {value} (merge_path::PRESERVED_LOG_ATTEMPTS)",
    ),
    # Still `gate-logs`, and deliberately: it is an on-disk layout every run root an
    # earlier build left behind already carries, and `sweep` reads it to decide
    # whether a root may be reclaimed. The prose says why, and this holds it to the
    # value rather than to the reason.
    Constant(
        "preserved merge-path log directory",
        ONEVCS,
        "merge_path.rs",
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
        # Where a merge-path verdict is preserved: the module that owns the durable
        # copy, the call that stores the log as an artifact, and the field naming it.
        # A reader follows all three to find the merge path's own words, and each is
        # `onevcs`'s to rename. `gate-verdict` was the first of these until onevcs
        # 0.11.0 deleted the tier that emitted it.
        (ONEVCS, "merge_path::preserve_log"),
        (ONEVCS, 'store_artifact("log"'),
        (ONEVCS, "preserved_log"),
        # The one spelling of the line a merge-path hook says a host prerequisite is
        # missing with, exported so a hook's author, the engine's router and this
        # document cannot disagree about it. The routing the prose describes — settled
        # once as `infrastructure-failure`, never re-dispatched — rests on the engine
        # reading exactly this marker, so a rename would leave a documented refusal
        # nothing produces and every such push back on the retried path.
        (ONEVCS, "HOST_PREREQUISITE_MARKER"),
    ),
    MANAGER: (
        # How a carried `context` note reaches a worker, quoted where a manager is told
        # which lever binds its node's judge and which only steers the worker. The
        # heading and the sentence under it are what make a note non-binding, and both
        # are the engine's own words: a release that reworded either would leave the
        # manager's document describing a note that no longer says what it claims, and
        # the whole pairing rests on that.
        (ONEPIPELINE, "## Planner context"),
        (ONEPIPELINE, "This reports observed state and adds no acceptance criteria."),
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


def _death_word_constants(shipped: str, constants: dict[str, str]) -> set[str]:
    """The outcome constants the dispatch-death classifier can hand the `failed` helper.

    Every other settlement names its word at the call, so reading the call is the whole
    of it. This one does not: the classifier binds a word and settles on the binding,
    which is why `dispatch-died` — a row this table has carried since it was written —
    silently stopped being found the release the classifier gained a second word to
    choose between. A gate that could not see either would report the engine as having
    dropped a settlement it makes on every dispatch that dies.
    """
    words: set[str] = set()
    for binding, body in DEATH_WORD_BINDING.findall(shipped):
        if f"failed(node, {binding})" not in shipped:
            continue
        words.update(name for name in re.findall(r"\b([A-Z][A-Z_]+)\b", body) if name in constants)
    assert words, (
        f"onepipeline {ONEPIPELINE.ref} no longer settles a dead dispatch on a word its "
        "classifier chose, where this gate reads it; a dispatch that dies is settled "
        "somewhere else now, and the words it settles under are paired with nothing"
    )
    return words


def _publication_pairings(
    shipped: str,
    status_fn: str,
    constants: dict[str, str],
    word_for: Callable[[str], str],
) -> set[tuple[str, str]]:
    """What a publication settles as, joined on the ending that answers both halves.

    `outcome_of` turns the sibling's `PublishOutcome` into this crate's word and the
    status function turns the same value into a status, so the pairing is per ending
    rather than the cross product: only a change held open as a draft settles
    `complete-but-draft`, and pairing every status with every word would put that
    status beside `merged`.

    The `Failed` ending is deliberately not here. `RELAY_GUARD` asserts it returns
    before this settlement, and its words are paired with the failure status by the
    preserving read in the caller.
    """
    body = _region(shipped, _definition_of(status_fn), f"`{status_fn}`")
    per_ending = dict(PUBLICATION_STATUS_ARM.findall(body))
    default = PUBLICATION_STATUS_DEFAULT.search(body)
    assert per_ending or default, (
        f"onepipeline {ONEPIPELINE.ref}'s `{status_fn}` names no status this gate can "
        "read, so every publication word has lost the status it pairs with"
    )

    pairings: set[tuple[str, str]] = set()
    for region in OUTCOME_OF.findall(shipped):
        for ending, literal, constant in OUTCOME_OF_PAIR.findall(region):
            word = literal or constants.get(constant)
            if word is None:
                # The `Failed` ending, whose word comes from `failure_of`. Its status
                # is read from the failure relay instead; see the docstring.
                continue
            status = per_ending.get(ending)
            if status is None:
                assert default, (
                    f"onepipeline {ONEPIPELINE.ref} settles `{word}` on a publication "
                    f"ending `{ending}` that `{status_fn}` neither names nor defaults"
                )
                status = default.group(1)
            pairings.add((word_for(status), word))
    assert pairings, (
        f"onepipeline {ONEPIPELINE.ref} no longer turns a publication ending into one of "
        "this crate's words where this gate reads it"
    )
    return pairings


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

    status_words: dict[str, str] = dict(NODE_STATUS_WORD.findall(shipped))
    assert status_words, (
        f"onepipeline {ONEPIPELINE.ref} no longer spells its statuses where this gate "
        "reads them, so every pairing below would be named with a word no reader meets"
    )

    def word_for(variant: str) -> str:
        """The word a settled status is written as, or a failure naming the variant."""
        assert variant in status_words, (
            f"onepipeline {ONEPIPELINE.ref} settles a node at `NodeStatus::{variant}` and "
            "declares no word for it, so the table cannot name the status this pairs with"
        )
        return status_words[variant]

    failure_statuses = {word_for(status) for status in FAILURE_RELAY.findall(shipped)}
    assert failure_statuses, (
        f"onepipeline {ONEPIPELINE.ref} no longer settles a publication failure on the "
        "word `failure_of` chose where this gate reads it, so the preserving words have "
        "lost the status they pair with"
    )
    preserving = _region(shipped, PRESERVING_OUTCOME, "`Preserving::outcome`")

    found = {
        (word_for(status), outcome) for status, outcome in LITERAL_SETTLEMENTS.findall(shipped)
    }
    constants = dict(OUTCOME_CONSTANT.findall(shipped))
    found.update(
        (word_for(helper_status.group(1)), outcome) for outcome in FAILED_HELPER.findall(shipped)
    )
    found.update(
        (word_for(helper_status.group(1)), constants[name])
        for name in FAILED_HELPER_CONSTANT.findall(shipped)
    )
    found.update(
        (word_for(status), constants[name])
        for status, name in CONSTANT_SETTLEMENTS.findall(shipped)
    )
    found.update(
        (word_for(helper_status.group(1)), constants[name])
        for name in _death_word_constants(shipped, constants)
    )
    found.update(_publication_pairings(shipped, relay.group(1), constants, word_for))
    found.update(
        (status, word) for status in failure_statuses for word in OUTCOME_OF_ARM.findall(preserving)
    )
    found.update(
        (status, constants[name]) for status in failure_statuses for name in ("RESIDUAL", "UNREAD")
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


#: Where the turn engine declares the provider health block this repository's views
#: forward. `just status` and `just runs` print `oneagentgraph health`'s JSON verbatim
#: and that call is `oneharness_core::io::usage::report`, so every name below is
#: oneharness's to rename and nothing on this side re-assembles it.
HEALTH_REPORT_SOURCE = "domain/usage.rs"

#: A `pub` field declaration, and the tuple-struct/enum-variant fields beside it, as
#: the names a serialized report carries. Read as declarations rather than as bare
#: words because a bare `plan` matches most of a crate: the whole value of this gate is
#: that a *renamed field* fails it, and a substring search over the source would go on
#: passing after the rename.
DECLARED_FIELD = re.compile(r"^\s*(?:pub\s+)?([a-z_][a-z0-9_]*)\s*:\s*[A-Za-z_&<]", re.MULTILINE)

#: The one numbered step of `docs/telemetry.md` that lists those names, which is the
#: region the field claims below are read from. Scoped rather than searched over the
#: whole document: several of these words appear elsewhere in it for unrelated reasons,
#: so a document-wide search would go on passing after the block that names them lost
#: one.
HEALTH_BLOCK = re.compile(
    r"^1\. Read the \*\*provider health block\*\*.*?(?=^\d+\. )", re.MULTILINE | re.DOTALL
)

#: Every field name that block tells an operator to read out of the report. Each is
#: quoted there in backticks and each has to be a field the crate declares — a reader
#: who greps a report for a name the crate renamed finds nothing and concludes the
#: identity was not probed, which is the failure this gate is about.
HEALTH_REPORT_FIELDS = (
    "harness",
    "selector",
    "auth_mode",
    "plan",
    "availability",
    "used_percent",
    "resets_at",
    "is_binding",
)


@pytest.mark.parametrize("field", HEALTH_REPORT_FIELDS)
def test_the_health_block_fields_are_the_turn_engines_own(field: str) -> None:
    """Both halves, as every claim here is: the document names it, the crate declares it.

    The direction that bites is a *rename*. Nothing on this side of the boundary would
    notice one: the views forward the report verbatim, so a renamed field arrives under
    its new name and the operator following this paragraph greps for the old one, finds
    nothing, and reads a healthy identity as an unprobed one. That is indistinguishable
    from the probe failing, which is the reading the paragraph exists to prevent.
    """
    block = _region(TELEMETRY.read_text("utf-8"), HEALTH_BLOCK, "the provider health block")
    assert f"`{field}`" in block, (
        f"{TELEMETRY.name}'s provider health block no longer names `{field}`; drop "
        "this entry in the same change that drops the claim"
    )
    declared = set(DECLARED_FIELD.findall(_source(ONEHARNESS, HEALTH_REPORT_SOURCE)))
    assert field in declared, (
        f"{TELEMETRY.name} tells an operator to read `{field}` out of the provider "
        f"health block, and oneharness {ONEHARNESS.ref} declares no such field in "
        f"{HEALTH_REPORT_SOURCE}. Re-read the crate and correct the paragraph — a "
        "renamed field reads to that operator as an identity that was never probed"
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


#: The script that holds a dispatch's run-root occupancy lease, and the journey that
#: proves it. Both restate `onevcs`'s own answer to "is this session's owner still
#: running", because they run where no `onevcs` is installed yet.
RUN_LEASE_SCRIPT = REPO_ROOT / "scripts" / "hold-run-lease.sh"
RUN_LEASE_JOURNEY = REPO_ROOT / "tests" / "e2e" / "test_run_root_lease_e2e.py"

#: `/proc/<pid>/stat`, split at the last `)`, and the index into what follows.
#: `onevcs` reads `fields.get(19)`; each copy here indexes the same list its own way,
#: so the gate reads the index rather than the expression around it.
ONEVCS_STAT_FIELD = re.compile(r"fields\s*\.\s*get\((\d+)\)")
SHELL_STAT_FIELD = re.compile(r"printf[^\n]*fields\[(\d+)\]")
PYTHON_STAT_FIELD = re.compile(r"rsplit\(\"\)\", 1\)\[1\]\.split\(\)\[(\d+)\]")

#: How `onevcs` spells a run root's occupancy-lease identity, which decides which
#: lock file guards which directory.
ONEVCS_LEASE_IDENTITY = re.compile(r'format!\("run:\{\}", run_root\.display\(\)\)')


#: How `onevcs` states the bound a queued lock wait gives up at when nothing names one.
ONEVCS_LOCK_DEFAULT = re.compile(r"pub const DEFAULT_TIMEOUT_SECONDS: f64 = ([0-9.]+);")
#: How `scripts/lock-timeout.sh` restates it, as the floor its derived bound never goes
#: below.
HELPER_LOCK_DEFAULT = re.compile(r"^ONEVCS_DEFAULT_LOCK_TIMEOUT_SECONDS=([0-9]+)$", re.MULTILINE)


def test_the_merge_queue_bound_is_floored_at_the_default_onevcs_declares() -> None:
    """`scripts/lock-timeout.sh`'s floor is `onevcs`'s own default, in every copy a waiter runs.

    The helper can only ever lengthen a wait because it never derives a bound below the
    one `onevcs` would use unasked (ai-orchestrator#1164). Both copies are read: the one a
    dispatch publishes through, which the engine links, and the CLI `config/onevcs.version`
    installs for the manager's own landings, each of which the helper exports a bound to.
    """
    helper = (REPO_ROOT / "scripts" / "lock-timeout.sh").read_text(encoding="utf-8")
    floor = HELPER_LOCK_DEFAULT.search(helper)
    assert floor is not None, (
        "scripts/lock-timeout.sh states no ONEVCS_DEFAULT_LOCK_TIMEOUT_SECONDS"
    )
    for engine in (ONEVCS, Engine("onevcs", _pinned_tag("onevcs"), ONEVCS.source_root)):
        declared = ONEVCS_LOCK_DEFAULT.search(_source(engine, "lock.rs"))
        assert declared is not None, f"onevcs {engine.ref} declares no lock DEFAULT_TIMEOUT_SECONDS"
        assert float(declared[1]) == float(floor[1]), (
            f"onevcs {engine.ref} gives up a queued wait at {declared[1]}s by default and "
            f"scripts/lock-timeout.sh floors its bound at {floor[1]}s; move the helper's "
            "floor to the default, or it can shorten a wait it exists to lengthen"
        )


def test_the_run_root_lease_reads_the_process_identity_onevcs_records() -> None:
    """The lease holder's liveness test is `onevcs`'s, and this is what says so.

    `scripts/hold-run-lease.sh` holds a run root's lease only while the session that
    cut it is live, and it decides that the way `Record::liveness` does: the owner's
    pid *and* that process's creation identity, which on Linux is field 22 of
    `/proc/<pid>/stat`. It reads that field itself rather than asking `onevcs`,
    because it runs before a dispatched worktree has an `onevcs` to ask — so the copy
    is deliberate and this is the gate that keeps it honest.

    The journey is read too. It writes the value the script compares against, so two
    matching local copies would agree with each other while both disagreed with the
    engine — which is the exact shape of a green test proving nothing.
    """
    engine = _source(ONEVCS, "workspace.rs")
    declared = ONEVCS_STAT_FIELD.search(engine)
    assert declared is not None, (
        f"{ONEVCS.crate} {ONEVCS.ref} no longer reads a numbered field out of "
        "/proc/<pid>/stat in workspace.rs, so what `process_started` means by a "
        "process's creation identity has moved; re-read it and correct both copies"
    )
    for path, pattern in (
        (RUN_LEASE_SCRIPT, SHELL_STAT_FIELD),
        (RUN_LEASE_JOURNEY, PYTHON_STAT_FIELD),
    ):
        copied = pattern.search(path.read_text("utf-8"))
        assert copied is not None, (
            f"{path.name} no longer indexes /proc/<pid>/stat in the shape this gate "
            "reconciles; it holds a run root's lease on that answer, so update the "
            "gate and the copy together"
        )
        assert copied.group(1) == declared.group(1), (
            f"{path.name} reads field {copied.group(1)} of /proc/<pid>/stat while "
            f"{ONEVCS.crate} {ONEVCS.ref} reads field {declared.group(1)}. A lease "
            "keyed on the wrong field either protects a dead run root forever or lets "
            "go of a live one"
        )


def test_the_run_root_lease_takes_the_lock_onevcs_guards_that_run_root_with() -> None:
    """The lease identity is `onevcs`'s spelling, and a lock nobody else takes is no lease.

    `reclaim` skips a run root whose occupancy lease is held, and the lease it asks
    about is `run:<run root>`. The holder derives its lock path from that same string;
    if the two ever spell it differently the holder takes a lock of its own and the
    reclaimer deletes the directory anyway, with every log line here still saying the
    lease is held.
    """
    assert ONEVCS_LEASE_IDENTITY.search(_source(ONEVCS, "workspace.rs")) is not None, (
        f"{ONEVCS.crate} {ONEVCS.ref} no longer spells a run root's occupancy-lease "
        'identity "run:<path>"; scripts/hold-run-lease.sh builds its lock path from '
        "that string, so re-read `occupancy_identity` and correct it"
    )
    assert 'f"run:{run_root}"' in RUN_LEASE_SCRIPT.read_text("utf-8"), (
        f"{RUN_LEASE_SCRIPT.name} no longer builds its lock path from the lease "
        f"identity {ONEVCS.crate} uses, so the lock it holds is not the one `reclaim` "
        "asks about"
    )


#: Every field of `onevcs`'s session record `scripts/hold-run-lease.sh` reads. It reads
#: the record directly because it runs before a dispatched worktree has an `onevcs` to
#: ask, so the field names are a copy of somebody else's schema.
RUN_LEASE_RECORD_FIELDS = ("token", "worktree", "run_root", "state", "owner_pid", "owner_started")

#: The `Lifecycle` variant the script requires before it will hold a lease, and the
#: rename that decides how `serde` writes it into the record.
RUN_LEASE_OPEN_STATE = "open"
LIFECYCLE_RENAME = re.compile(r'#\[serde\(rename_all = "([a-z-]+)"\)\]\s*pub enum Lifecycle')


def test_the_run_root_lease_reads_the_session_record_fields_onevcs_writes() -> None:
    """The lease holder's view of a session record is `onevcs`'s, and this says so.

    A renamed field would not fail the script: it would decline, saying the record
    names no run root, and a dispatch would go on working in a directory nothing was
    holding — with the log line for it reading like an ordinary "nothing to hold here".
    That is a silence, so it is gated rather than left to be noticed.
    """
    declared = _source(ONEVCS, "workspace.rs")
    read = RUN_LEASE_SCRIPT.read_text("utf-8")
    for field in RUN_LEASE_RECORD_FIELDS:
        assert f"pub {field}:" in declared, (
            f"{ONEVCS.crate} {ONEVCS.ref} no longer declares `{field}` on its session "
            f"record, and {RUN_LEASE_SCRIPT.name} reads it to decide which run root to "
            "hold a lease on; re-read the record and correct the reader"
        )
        assert f'"{field}"' in read, (
            f"{RUN_LEASE_SCRIPT.name} no longer reads `{field}` from the session record, "
            "so this gate is reconciling a field nothing uses; update it with the reader"
        )


def test_the_run_root_lease_requires_the_state_onevcs_writes_for_an_open_session() -> None:
    """`open` is `Lifecycle::Open` as `serde` spells it, and the rename decides that.

    The script holds a lease only for a session whose recorded state is this word. A
    rename here would make every session look finished to it, which is the failure that
    silently returns every live run root to the reclaimer.
    """
    rename = LIFECYCLE_RENAME.search(_source(ONEVCS, "session.rs"))
    assert rename is not None, (
        f"{ONEVCS.crate} {ONEVCS.ref} no longer renames its `Lifecycle` variants for "
        f"serialization, so what {RUN_LEASE_SCRIPT.name} should compare a recorded "
        "state against is no longer decided where this gate reads it"
    )
    # `Open` is one word, so lowercase and kebab-case spell it identically; either is
    # this word, and a rename to anything else is not.
    assert rename.group(1) in ("lowercase", "kebab-case"), (
        f"{ONEVCS.crate} {ONEVCS.ref} renames `Lifecycle` as {rename.group(1)!r}, which "
        f"no longer writes `Open` as {RUN_LEASE_OPEN_STATE!r}"
    )
    assert f'!= "{RUN_LEASE_OPEN_STATE}"' in RUN_LEASE_SCRIPT.read_text("utf-8"), (
        f"{RUN_LEASE_SCRIPT.name} no longer requires a recorded state of "
        f"{RUN_LEASE_OPEN_STATE!r}, so this gate reconciles a comparison it does not make"
    )


#: The view a manager's watch greps, as `onepipeline` composes it. `just status` is a
#: thin wrapper over `onepipeline status`, so the boundary `AGENTS.md` tells a watch to
#: cut at is this function's own formatting and nothing on this side of the seam.
#:
#: Read from `status_of`, which composes **one run's** block — its run lines, its node
#: lines, and the health report under them — rather than from `status`, which since
#: https://github.com/nickderobertis/onepipeline/pull/402 only concatenates that block per
#: surveyed run and adds the
#: skipped-runs trailer. Anchoring on the outer function is how these gates came to
#: report the health opener as gone from a view that still prints it: the composition
#: moved down one function while the entry point kept its name.
STATUS_VIEW = re.compile(r"pub\(crate\) fn status_of\(.*?\n\}\n", re.DOTALL)

#: The helper that view writes a run's own block through, and the call by which it does.
#:
#: Two functions rather than one because the engine split them, and reading only the
#: first is how this gate came to report the unread-surface line as *gone* from a view
#: that still prints it: the composition moved into the helper while the call stayed
#: where it was. So the line is looked for where it is written, and the *ordering* the
#: watch rule depends on is read from where that helper is called — which is the fact
#: that decides it either way, since the helper's own text says nothing about what is
#: rendered after it.
STATUS_RUN_LINES = re.compile(r"fn status_run_lines\(.*?\n\}\n", re.DOTALL)
STATUS_RUN_LINES_CALL = "status_run_lines("

#: The line the embedded provider health report opens with, exactly as the view writes
#: it — two leading spaces included, because the cut is anchored (`/^  providers:/`) and
#: a re-indent would leave it matching nothing while reading like it still works.
HEALTH_BLOCK_OPENER = '"  providers: '

#: The line rule 5 makes a HARD REQUIREMENT, which has to survive that cut. It survives
#: only by being printed *above* the health block, so this is an ordering claim rather
#: than a presence one.
UNREAD_SURFACE_LINE = '"  {} planner update(s) waiting'

#: The cut itself, as the watch item spells it. Read back out of the manager's document
#: so a reworded instruction and this gate cannot come to be about different sed
#: programs.
HEALTH_BLOCK_CUT = "sed '/^  providers:/,$d'"


def _status_run_lines() -> str:
    """The helper the status view writes a run's own block through, as source.

    Its own function rather than a second search at each site, because three gates read
    it and a view split across two functions is exactly the shape that drifts one site at
    a time.
    """
    found = STATUS_RUN_LINES.search(_source(ONEPIPELINE, "views.rs"))
    assert found is not None, (
        f"onepipeline {ONEPIPELINE.ref} no longer composes a run's own status block in "
        "`status_run_lines`, so nothing here can read the lines that block prints"
    )
    return found.group(0)


def test_the_watch_cuts_the_status_view_where_the_health_report_really_starts() -> None:
    """The anchor a watch cuts at is `onepipeline`'s own, and the cut keeps rule 5.

    Both halves fail silently. A re-indented or renamed opener leaves the cut matching
    nothing, so the watch goes back to greping the host's health report for the run's
    words — which is the eleven-seconds-into-a-healthy-dispatch quota death the item
    was written from. And a health block that moved *above* the unread-surface line
    would make the cut swallow the one line rule 5 forbids filtering, turning a
    documented fix into the exact failure the HARD REQUIREMENT exists to prevent.
    """
    written = MANAGER.read_text("utf-8")
    assert HEALTH_BLOCK_CUT in written, (
        f"{MANAGER.name} no longer tells a watch to cut the status view at "
        f"{HEALTH_BLOCK_CUT!r}, so this gate reconciles an instruction nobody is given"
    )
    view = STATUS_VIEW.search(_source(ONEPIPELINE, "views.rs"))
    assert view is not None, (
        f"onepipeline {ONEPIPELINE.ref} no longer composes its status view where this "
        "gate reads it, so nothing here can say where that view's two documents meet"
    )
    composed = view.group(0)
    opener = composed.find(HEALTH_BLOCK_OPENER)
    assert opener != -1, (
        f"onepipeline {ONEPIPELINE.ref} no longer opens the embedded health report with "
        f"{HEALTH_BLOCK_OPENER!r}, so {MANAGER.name}'s {HEALTH_BLOCK_CUT!r} cuts nothing "
        "and a watch over that view is grepping the host's report for the run's words"
    )
    assert UNREAD_SURFACE_LINE in _status_run_lines(), (
        f"onepipeline {ONEPIPELINE.ref}'s status view no longer prints the unread-surface "
        f"line, which {MANAGER.name} makes a HARD REQUIREMENT of every watch; the rule now "
        "rests on `runs` alone and this gate can no longer say the cut keeps it"
    )
    unread = composed.find(STATUS_RUN_LINES_CALL)
    assert unread != -1, (
        f"onepipeline {ONEPIPELINE.ref}'s status view no longer writes a run's own block "
        f"through {STATUS_RUN_LINES_CALL!r}, so nothing here can say whether the line that "
        "helper prints lands above the health report or below it"
    )
    assert unread < opener, (
        f"onepipeline {ONEPIPELINE.ref} now prints the health report above the "
        f"unread-surface line, so {MANAGER.name}'s cut removes the one line rule 5 forbids "
        "filtering. Move the anchor, or the documented watch drops the question channel"
    )


# What each restatement below is for, and what its drift costs, is stated where it is
# made; each gate names the consequence of the one it reconciles.

#: The reading whose restatements these gates reconcile.
SUPERVISION_READINGS = REPO_ROOT / "scripts" / "supervision-readings.py"

#: The builder two of its journeys compose a run root with. It writes the engine's own
#: records so the installed engine will read them, which makes every field name in it a
#: copy of the same contracts — reconciled below by building one and reading it back.
PROBE_RUN_ROOT = REPO_ROOT / "tests" / "e2e" / "probe_run_root.py"

#: How `RunPaths` names one file under a run root. Anchored on the accessor and held
#: inside its own body — no closing brace between the two — for both directions of the
#: same mistake: searching for the file name would pass because *some* accessor joins it,
#: and searching forward from the accessor unbounded would pass on the next accessor's
#: join if this one stopped making its own.
RUN_PATHS_JOIN = r'pub fn {accessor}\(&self\)[^{{}}]*\{{[^}}]*?self\.dir\.join\("([^"]+)"\)'

#: The runs root when the environment names none, and the variable that moves it.
DEFAULT_RUNS_DIR = re.compile(r'pub const DEFAULT_RUNS_DIR: &(?:\'static )?str = "([^"]+)";')
RUNS_DIR_ENV = re.compile(r'pub const RUNS_DIR_ENV: &(?:\'static )?str = "([^"]+)";')

#: How `sys::process_start_token` encodes what it read out of `/proc/<pid>/stat`. The
#: prefix is captured rather than written here, because this gate exists to say the
#: reading strips the prefix the engine actually stamps.
START_TOKEN_ENCODING = re.compile(r'\.map\(\|ticks\| format!\("([^"{]*)\{ticks\}"\)\)')

#: `onevcs`'s state root: the variable that moves it, where it sits when nothing does,
#: and the directory under it every per-run clone and isolated worktree is cut in.
ONEVCS_HOME_ENV = re.compile(r'pub const HOME_ENV: &(?:\'static )?str = "([^"]+)";')
ONEVCS_DEFAULT_HOME = re.compile(r'home\.join\("([^"]+)"\)')
ONEVCS_WORKSPACES_DIR = re.compile(
    r'pub fn workspaces_dir\(\)[^{]*\{\s*Ok\(root\(\)\?\.join\("([^"]+)"\)\)'
)

#: One field of a serde-derived record, with the attributes above it — which is where a
#: `rename` moves its wire name and a `default` says the engine can read a record that
#: omits it. Both are what part a field this repository must write from one it may.
SERDE_FIELD = re.compile(r"((?:[ \t]*#\[[^\]]*\]\n)*)[ \t]*pub (\w+):")
SERDE_RENAME = re.compile(r'rename\s*=\s*"([^"]+)"')
#: The struct a field's type names, by the last segment of its path (`V::Dimensions`).
SERDE_FIELD_TYPE = re.compile(r"\s*(?:\w+::)*(\w+)")

#: The wire word for each of this crate's own event kinds, which is the closed
#: vocabulary a built journal's `kind` has to be drawn from.
PIPELINE_KIND_WORD = re.compile(r'Self::\w+ => "([a-z][a-z-]*)",')

#: How `Source` — which library produced an envelope — is spelled on the wire.
SOURCE_RENAME = re.compile(r'#\[serde\(rename_all = "([a-z-]+)"\)\]\s*pub enum Source')
SOURCE_VARIANT = re.compile(r"^\s{4}([A-Z][A-Za-z]*),", re.MULTILINE)
#: onepipeline's re-export of the bus's envelope types, which is what makes the bus the
#: place a journal envelope is declared.
BUS_REEXPORT = re.compile(r"pub use onemessagebus_agent::event::\{([^}]*)\};")
#: How onepipeline takes the version it stamps: the newest its bus profile reads.
ENVELOPE_VERSION_FROM_THE_BUS = "pub const ENVELOPE_VERSION: u32 = EVENT_ENVELOPE_READS[0];"

#: `/proc/<pid>/stat` split at the last `)`, as the two Python copies added with these
#: readings spell it. The existing shell and journey copies spell the same split with
#: `rsplit`; both are reconciled against the engine's own index by the lease gate above.
RINDEX_STAT_FIELD = re.compile(r'rindex\("\)"\)\s*\+\s*2\s*:\]\.split\(\)\[(\d+)\]')


class Field(NamedTuple):
    """One field of an engine record, as that record is written and read on the wire."""

    #: The name it is serialized under — its `rename`, where it carries one.
    name: str
    #: Whether the engine can read a record that omits it. A `default` says yes; every
    #: other field is one this repository's builder has to write or the engine refuses.
    optional: bool


class Record(NamedTuple):
    """One engine-owned record `tests/e2e/probe_run_root.py` composes.

    The reconciliation is two-directional and each direction catches a different
    drift. A key the builder writes that the engine no longer declares is a value the
    engine either refuses outright or — for the records that tolerate unknown fields —
    reads past in silence, leaving a run root that looks right and answers wrong. A
    required field the builder stopped writing is a record the engine cannot read at
    all, which is the loud half and is gated anyway: a builder that quietly dropped one
    while the views still rendered would mean the views had stopped reading it.
    """

    label: str
    engine: Engine
    source: str
    struct: str
    #: Where in a built run root that record's JSON objects are. Every one found is
    #: checked, so a journal's second event is as reconciled as its first.
    found: Callable[[Path], list[dict[str, object]]]
    #: Where a `#[serde(flatten)]` field's struct is declared, when the record carries
    #: one: its fields travel as the record's own on the wire, so they are read there.
    flattened_from: str | None = None


def _module_source(path: Path) -> ast.Module:
    """One of this repository's Python files, parsed rather than pattern-matched.

    Parsed because the values below are what the file *assigns*, and a regex over its
    text answers about how it is written instead — which is the same distance from the
    contract that made these copies worth gating.
    """
    return ast.parse(path.read_text("utf-8"), filename=str(path))


def _assigned(path: Path, name: str) -> ast.expr:
    """The expression one module-level constant of `path` is assigned."""
    for statement in _module_source(path).body:
        match statement:
            case ast.Assign(targets=[ast.Name(id=assigned)], value=value) if assigned == name:
                return value
    raise AssertionError(
        f"{path.name} no longer assigns a module-level `{name}`, so this gate is "
        "reconciling a restatement nothing makes; update the gate with the reading"
    )


def _constant(path: Path, name: str) -> object:
    """The literal value one module-level constant of `path` holds."""
    return ast.literal_eval(_assigned(path, name))


def _pattern(path: Path, name: str) -> re.Pattern[str]:
    """The regular expression one module-level `re.compile(...)` constant holds."""
    match _assigned(path, name):
        case ast.Call(
            func=ast.Attribute(value=ast.Name(id="re"), attr="compile"),
            args=[ast.Constant(value=str() as source), *_],
        ):
            return re.compile(source)
    raise AssertionError(
        f"{path.name}'s `{name}` is no longer a literal `re.compile(...)`, so this gate "
        "cannot read the pattern it reconciles; update the gate with the reading"
    )


def _joined_under_a_run_root(accessor: str) -> str:
    """The file or directory name one `RunPaths` accessor joins onto a run's directory."""
    joined = re.search(
        RUN_PATHS_JOIN.format(accessor=re.escape(accessor)),
        _source(ONEPIPELINE, "ledger.rs"),
        re.DOTALL,
    )
    assert joined is not None, (
        f"onepipeline {ONEPIPELINE.ref}'s `RunPaths::{accessor}` no longer joins a "
        f"literal name onto the run's directory where this gate reads it, so "
        f"{SUPERVISION_READINGS.name}'s copy of that name cannot be reconciled; re-read "
        "`ledger.rs` and correct both"
    )
    return joined.group(1)


def _declared_fields(
    engine: Engine, source: str, struct: str, flattened_from: str | None = None
) -> tuple[Field, ...]:
    """Every field one engine record declares on the wire, in declaration order.

    A generic record (`Envelope<V: Vocabulary>`) is read like any other, and a
    `#[serde(flatten)]` field is replaced by the fields of the struct it names, read
    out of `flattened_from` — refused when the record names none, because dropping
    the field would reconcile the record against fewer keys than it carries.
    """
    body = re.search(
        rf"pub struct {re.escape(struct)}(?:<[^>{{]*>)? \{{(.*?)\n\}}",
        _source(engine, source),
        re.DOTALL,
    )
    assert body is not None, (
        f"{engine.crate} {engine.ref} no longer declares `{struct}` in {source}, so the "
        "record this repository writes cannot be reconciled against it; re-read that "
        "file and correct the builder"
    )
    fields: list[Field] = []
    for found in SERDE_FIELD.finditer(body.group(1)):
        if "flatten" in found.group(1):
            flattened = SERDE_FIELD_TYPE.match(body.group(1), found.end())
            assert flattened is not None and flattened_from is not None, (
                f"{engine.crate} {engine.ref}'s `{struct}` flattens `{found.group(2)}` into "
                "its wire record, and nothing names where that struct is declared, so its "
                "keys cannot be reconciled; name the source it is read from"
            )
            fields.extend(_declared_fields(engine, flattened_from, flattened.group(1)))
            continue
        fields.append(
            Field(
                name=(
                    rename.group(1)
                    if (rename := SERDE_RENAME.search(found.group(1)))
                    else found.group(2)
                ),
                optional="default" in found.group(1),
            )
        )
    assert fields, (
        f"{engine.crate} {engine.ref}'s `{struct}` declares no public fields where this "
        "gate reads them, so it would reconcile every record against an empty set"
    )
    return tuple(fields)


def test_the_readings_look_under_the_runs_root_the_engine_writes() -> None:
    """The store both readings answer about is `onepipeline`'s, resolved its way.

    A runs root read some other way is not a narrower answer, it is an answer about a
    different store: no run root is found, every run reads as absent, and every live
    rendezvous is reported as bound to no run this host supervises. That is the same
    sentence a rendezvous belonging to somebody else's test gets, so the drift would
    undo exactly what this reading is for while the view still renders.
    """
    ledger = _source(ONEPIPELINE, "ledger.rs")
    for pattern, constant, what in (
        (RUNS_DIR_ENV, "RUNS_ROOT_ENV", "the variable that moves the runs root"),
        (DEFAULT_RUNS_DIR, "DEFAULT_RUNS_ROOT", "the runs root when nothing names one"),
    ):
        declared = pattern.search(ledger)
        assert declared is not None, (
            f"onepipeline {ONEPIPELINE.ref} no longer declares {what} where this gate "
            f"reads it, so {SUPERVISION_READINGS.name}'s copy of it cannot be "
            "reconciled; re-read `ledger.rs` and correct both"
        )
        assert _constant(SUPERVISION_READINGS, constant) == declared.group(1), (
            f"{SUPERVISION_READINGS.name} resolves {what} as "
            f"{_constant(SUPERVISION_READINGS, constant)!r} while onepipeline "
            f"{ONEPIPELINE.ref} declares {declared.group(1)!r}. Both readings would "
            "answer about a store nothing is writing"
        )


def test_the_readings_read_the_run_marker_and_registry_the_engine_writes() -> None:
    """A run root is what the engine marks one, and the dispatches are where it puts them.

    The marker is what parts a run root from any other directory under the runs root,
    and the registry is the only thing that turns a rendezvous's process ancestry into
    the dispatch it sits under. A rename of either leaves the reading answering that no
    run and no dispatch is there — a whole host of live work reported as belonging to
    nobody.
    """
    for accessor, constant in (("launch", "LAUNCH_RECORD"), ("dispatches", "DISPATCH_REGISTRY")):
        declared = _joined_under_a_run_root(accessor)
        assert _constant(SUPERVISION_READINGS, constant) == declared, (
            f"{SUPERVISION_READINGS.name} looks for "
            f"{_constant(SUPERVISION_READINGS, constant)!r} under a run root while "
            f"onepipeline {ONEPIPELINE.ref} writes `RunPaths::{accessor}` as "
            f"{declared!r}, so every run reads as a directory that is not one"
        )


def test_the_readings_read_the_dispatch_record_the_engine_writes() -> None:
    """Every field the rendezvous attribution reads is one `DispatchRecord` declares.

    A renamed field fails nothing: the entry stops naming a dispatch and every
    rendezvous under a live one is reported as under no dispatch this runs root
    records, which is the sentence reserved for a rendezvous that is somebody else's.
    """
    declared = {
        field.name for field in _declared_fields(ONEPIPELINE, "ledger.rs", "DispatchRecord")
    }
    for constant in ("DISPATCH_NODE", "DISPATCH_PID", "DISPATCH_STARTED"):
        read = _constant(SUPERVISION_READINGS, constant)
        assert read in declared, (
            f"{SUPERVISION_READINGS.name} reads {read!r} off a dispatch registry entry "
            f"while onepipeline {ONEPIPELINE.ref}'s `DispatchRecord` declares "
            f"{sorted(declared)}. Every rendezvous under a live dispatch would report "
            "itself as under none"
        )


def test_the_readings_strip_the_start_token_the_engine_stamps() -> None:
    """The stamp that tells a live dispatch from a reused pid is `onepipeline`'s spelling.

    The reading takes an entry's start time only when it carries this prefix and treats
    a record spelling it any other way as unverifiable, which leaves the entry usable
    but unchecked. So a re-spelled prefix does not fail either: it silently returns
    every attribution to the unverified pid match this exists to improve on, on a host
    that holds dozens of stale entries.
    """
    declared = START_TOKEN_ENCODING.search(_source(ONEPIPELINE, "sys.rs"))
    assert declared is not None, (
        f"onepipeline {ONEPIPELINE.ref} no longer encodes a process's start token from "
        f"`/proc/<pid>/stat` where this gate reads it, so {SUPERVISION_READINGS.name}'s "
        "copy of that prefix cannot be reconciled; re-read `sys.rs` and correct both"
    )
    assert _constant(SUPERVISION_READINGS, "PROC_STAT_START") == declared.group(1), (
        f"{SUPERVISION_READINGS.name} strips "
        f"{_constant(SUPERVISION_READINGS, 'PROC_STAT_START')!r} from a recorded start "
        f"time while onepipeline {ONEPIPELINE.ref} stamps {declared.group(1)!r}, so "
        "every attribution falls back to an unverified pid match"
    )


def test_the_readings_measure_the_worktree_root_onevcs_cuts_under() -> None:
    """The second filesystem these readings measure is the one `onevcs` writes into.

    It is the one the incident this reading was written from filled, and it is not
    always the one the runs root is on. Resolved any other way, the free-space line
    reports a filesystem nothing is filling — which reads exactly like a healthy host.
    """
    home = _source(ONEVCS, "home.rs")
    for pattern, constant, what in (
        (ONEVCS_HOME_ENV, "ONEVCS_HOME_ENV", "the variable that moves its state root"),
        (ONEVCS_DEFAULT_HOME, "DEFAULT_ONEVCS_HOME", "that root under a home directory"),
        (ONEVCS_WORKSPACES_DIR, "ONEVCS_WORKSPACES", "the directory worktrees are cut in"),
    ):
        declared = pattern.search(home)
        assert declared is not None, (
            f"{ONEVCS.crate} {ONEVCS.ref} no longer declares {what} where this gate "
            f"reads it, so {SUPERVISION_READINGS.name}'s copy cannot be reconciled; "
            "re-read `home.rs` and correct both"
        )
        assert _constant(SUPERVISION_READINGS, constant) == declared.group(1), (
            f"{SUPERVISION_READINGS.name} resolves {what} as "
            f"{_constant(SUPERVISION_READINGS, constant)!r} while {ONEVCS.crate} "
            f"{ONEVCS.ref} declares {declared.group(1)!r}, so the free-space line "
            "measures a filesystem no dispatch is writing to"
        )


def test_the_readings_watch_for_the_rendezvous_the_installed_bus_publishes() -> None:
    """The words that make a process a rendezvous are the bus command line's own.

    Read from the installed binary rather than from the crate, because what a live
    process carries on its argv is what that binary accepts. Each name matters: the
    program and its two verbs are what the reading matches, and the flag is where it
    reads the channel — and so the run — from. A bus that renamed a verb would leave
    every rendezvous of that shape unreported; one that renamed the flag, or stopped
    taking one directory with it, would report every one as unattributable, which is the
    sentence reserved for a process whose command line names no run.
    """
    bus = _constant(SUPERVISION_READINGS, "BUS")
    verbs = _constant(SUPERVISION_READINGS, "RENDEZVOUS_VERBS")
    flag = _constant(SUPERVISION_READINGS, "TRANSPORT_DIR_FLAG")
    assert isinstance(bus, str) and isinstance(flag, str) and isinstance(verbs, tuple), (
        f"{SUPERVISION_READINGS.name}'s BUS, RENDEZVOUS_VERBS and TRANSPORT_DIR_FLAG are no "
        "longer a program name, a tuple of verbs and a flag"
    )
    binary = REPO_ROOT / ".venv" / "bin" / bus
    assert binary.is_file(), (
        f"no {bus} is installed at {binary}, so {SUPERVISION_READINGS.name} watches for a "
        "program this host does not run; run 'just bootstrap'"
    )
    version = subprocess.run(
        [str(binary), "--version"], capture_output=True, text=True, check=False
    )
    assert version.returncode == 0 and version.stdout.split()[:1] == [bus], (
        f"the installed bus names itself {version.stdout.strip()!r} rather than {bus!r}, so "
        f"{SUPERVISION_READINGS.name} matches a program name nothing carries"
    )
    surface = surface_of(bus)
    for verb in verbs:
        assert (verb,) in surface.paths, (
            f"the installed {bus} publishes no {verb!r} verb, so "
            f"{SUPERVISION_READINGS.name} matches a command nothing runs"
        )
        reported = subprocess.run(
            [str(binary), verb, "--help"], capture_output=True, text=True, check=False
        )
        assert re.search(OPTION_WITH_A_VALUE.format(flag=re.escape(flag)), reported.stdout), (
            f"`{bus} {verb}` no longer takes one value with {flag!r}, so "
            f"{SUPERVISION_READINGS.name} reads every live {verb} as naming no channel:\n"
            f"{reported.stdout}"
        )


#: How clap's help states an option taking one value: `      --transport-dir <DIR>  …`.
OPTION_WITH_A_VALUE = r"(?m)^\s+{flag} <[A-Z_]+>\s"

#: This host's own two rendezvous: the ask shim a dispatched agent asks through, and the
#: observer graph whose monitor's judge side is a bus `serve`. The reading binds either to
#: its run only if each names the run's channel the way the engine keeps it.
RENDEZVOUS_PRODUCERS = (
    REPO_ROOT / "scripts" / "ask-manager.sh",
    REPO_ROOT / "graphs" / "dag-scope.yaml",
)


def test_the_readings_bind_a_rendezvous_by_the_channel_directory_the_engine_keeps() -> None:
    """A run's channel is the directory the engine keeps it in, and this host names it so.

    The reading takes a rendezvous's run to be the directory holding the channel it names,
    so the channel directory's name is the engine's to decide — `RunPaths::channel_dir` —
    and both of this host's producers have to compose it under the flag the reading reads.
    A renamed directory would report every live rendezvous as unattributable; a producer
    that named its channel some other way would leave its own rendezvous unattributed.
    """
    declared = _joined_under_a_run_root("channel_dir")
    kept = _constant(SUPERVISION_READINGS, "CHANNEL_DIRECTORY")
    assert kept == declared, (
        f"{SUPERVISION_READINGS.name} reads a run's channel as {kept!r} while onepipeline "
        f"{ONEPIPELINE.ref}'s `RunPaths::channel_dir` joins {declared!r}, so every live "
        "rendezvous reads as naming no run"
    )
    flag = _constant(SUPERVISION_READINGS, "TRANSPORT_DIR_FLAG")
    assert isinstance(flag, str) and isinstance(kept, str)
    composed = re.compile(rf'{re.escape(flag)}\s+"[^"\n]*/{re.escape(kept)}"')
    for producer in RENDEZVOUS_PRODUCERS:
        assert composed.search(producer.read_text("utf-8")) is not None, (
            f"{producer.relative_to(REPO_ROOT)} no longer names its channel as "
            f'`{flag} "…/{kept}"`, so {SUPERVISION_READINGS.name} cannot bind the '
            "rendezvous it starts to its run"
        )


def test_the_readings_cut_the_status_view_where_the_engine_opens_its_health_report() -> None:
    """The reading is inserted above the line a supervisor's watch is told to cut at.

    Two copies of that anchor now exist — `AGENTS.md`'s `sed` program, gated above, and
    this reading's own pattern — and this one decides where the free-space line lands.
    Below the opener it would be invisible to every watch that follows this
    repository's own guidance, which is a reading nobody ever sees rather than a
    missing one.
    """
    view = STATUS_VIEW.search(_source(ONEPIPELINE, "views.rs"))
    assert view is not None, (
        f"onepipeline {ONEPIPELINE.ref} no longer composes its status view where this "
        "gate reads it, so nothing here can say where that view's two documents meet"
    )
    opener = view.group(0).find(HEALTH_BLOCK_OPENER)
    assert opener != -1, (
        f"onepipeline {ONEPIPELINE.ref} no longer opens the embedded health report with "
        f"{HEALTH_BLOCK_OPENER!r}, so {SUPERVISION_READINGS.name} has no boundary to "
        "put its reading above"
    )
    # The engine writes it as a Rust string literal, opening quote included, and the
    # reading matches the rendered line — so the literal's own quote is dropped and
    # what is matched is the text a supervisor sees.
    written = HEALTH_BLOCK_OPENER.lstrip('"').rstrip()
    assert _pattern(SUPERVISION_READINGS, "PROVIDERS").match(written) is not None, (
        f"{SUPERVISION_READINGS.name}'s boundary pattern does not match {written!r}, "
        f"which is how onepipeline {ONEPIPELINE.ref} opens the health report, so the "
        "free-space reading lands below the cut and no watch following AGENTS.md sees it"
    )


#: Every record `probe_run_root.run_root` composes, and where to find each one in a
#: root it has built. Built and read back rather than parsed out of the builder's
#: source: what has to agree with the engine is the JSON that reaches disk, and a
#: reading of the code that writes it answers a question one step away from that.
BUILT_RECORDS = (
    Record(
        "the launch record",
        ONEPIPELINE,
        "ledger.rs",
        "LaunchRecord",
        lambda root: [json.loads((root / "launch.json").read_text("utf-8"))],
    ),
    Record(
        "the plan",
        ONEPIPELINE,
        "plan.rs",
        "Plan",
        lambda root: [json.loads((root / "plan.json").read_text("utf-8"))],
    ),
    Record(
        "each plan node",
        ONEPIPELINE,
        "plan.rs",
        "Node",
        lambda root: json.loads((root / "plan.json").read_text("utf-8"))["tasks"],
    ),
    Record(
        "the plan's goal",
        ONEPIPELINE,
        "plan.rs",
        "Goal",
        lambda root: [json.loads((root / "plan.json").read_text("utf-8"))["goal"]],
    ),
    Record(
        "each journal envelope",
        ONEMESSAGEBUS,
        "onemessagebus/src/envelope.rs",
        "Envelope",
        lambda root: [
            json.loads(line)
            for line in (root / "events.jsonl").read_text("utf-8").splitlines()
            if line
        ],
        flattened_from="onemessagebus-agent/src/event.rs",
    ),
    Record(
        "each dispatch registry entry",
        ONEPIPELINE,
        "ledger.rs",
        "DispatchRecord",
        lambda root: [
            json.loads(entry.read_text("utf-8"))
            for entry in sorted((root / "dispatches").iterdir())
        ],
    ),
)


@pytest.fixture(name="built_run_root")
def _built_run_root(tmp_path: Path) -> Path:
    """A run root composed exactly as the two journeys that use it compose one.

    With a dispatch recorded against this process, which is the pairing those journeys
    exist for — a launch nothing is driving beside a dispatch that is still alive — and
    the only shape in which every record this builder writes is on disk at once.
    """
    return probe_run_root.run_root(tmp_path, probe_run_root.run_name(), dispatch_pid=os.getpid())


@pytest.mark.parametrize("record", BUILT_RECORDS, ids=lambda record: record.struct)
def test_the_built_run_root_writes_the_records_the_engine_declares(
    record: Record, built_run_root: Path
) -> None:
    """Every field of a built run root is one the installed engine's own record declares.

    Both directions, because they fail differently. A key the engine no longer declares
    is refused outright by the records that deny unknown fields and read past in
    silence by the ones that do not, so half of that drift produces a run root that
    looks right and answers wrong. A required field the builder stopped writing is a
    record the engine cannot read at all — loud where it is read, and gated here so a
    builder that dropped one cannot pass by nothing having read that record.
    """
    declared = _declared_fields(record.engine, record.source, record.struct, record.flattened_from)
    names = {field.name for field in declared}
    required = {field.name for field in declared if not field.optional}
    written = record.found(built_run_root)
    assert written, (
        f"{PROBE_RUN_ROOT.name} no longer writes {record.label}, so this gate "
        "reconciles a record nothing composes; update it with the builder"
    )
    for composed in written:
        assert set(composed) <= names, (
            f"{PROBE_RUN_ROOT.name} writes {sorted(set(composed) - names)} into "
            f"{record.label}, which {record.engine.crate} {record.engine.ref}'s "
            f"`{record.struct}` does not declare. The engine either refuses the record "
            "or reads past those keys, and a built run root the views answer wrongly "
            "about proves nothing about the views"
        )
        assert required <= set(composed), (
            f"{PROBE_RUN_ROOT.name}'s {record.label} omits "
            f"{sorted(required - set(composed))}, which {record.engine.crate} "
            f"{record.engine.ref}'s `{record.struct}` requires, so the engine cannot "
            "read the run root these journeys drive the views over"
        )


def test_onepipeline_stamps_the_newest_envelope_version_its_bus_reads() -> None:
    """The version a built journal is held to is the one onepipeline writes.

    Read off the bus's registry since the envelope moved there, which is right only
    while onepipeline still takes its own stamp from that read set rather than
    declaring a number of its own again.
    """
    assert ENVELOPE_VERSION_FROM_THE_BUS in _source(ONEPIPELINE, "event.rs"), (
        f"onepipeline {ONEPIPELINE.ref} no longer takes `ENVELOPE_VERSION` from "
        f"{ONEMESSAGEBUS.crate}'s `EVENT_ENVELOPE_READS[0]`, so the version a built "
        "journal is reconciled against may not be the one the engine writes"
    )


def test_the_built_journal_names_events_the_engine_produces(built_run_root: Path) -> None:
    """A built journal's kinds and producer are drawn from the engine's own vocabularies.

    `EventKind` is an open wire string because this crate relays two siblings' kinds,
    so an invented one is accepted rather than refused — which is precisely why it is
    gated: a journal of events the engine never writes is a fixture agreeing with
    itself, and the views built on it would go on rendering.
    """
    event = _source(ONEPIPELINE, "event.rs")
    kinds = set(PIPELINE_KIND_WORD.findall(event))
    assert kinds, (
        f"onepipeline {ONEPIPELINE.ref} no longer spells its own event kinds where this "
        "gate reads them, so a built journal cannot be reconciled against them"
    )
    reexport = BUS_REEXPORT.search(event)
    assert reexport is not None and {"Envelope", "Source"} <= set(
        re.findall(r"\w+", reexport.group(1))
    ), (
        f"onepipeline {ONEPIPELINE.ref} no longer re-exports `Envelope` and `Source` from "
        f"`onemessagebus_agent::event`, so {ONEMESSAGEBUS.crate} {ONEMESSAGEBUS.ref} is no "
        "longer where a journal envelope is declared; re-read `event.rs`"
    )
    bus_event = _source(ONEMESSAGEBUS, "onemessagebus-agent/src/event.rs")
    rename = SOURCE_RENAME.search(bus_event)
    assert rename is not None and rename.group(1) == "lowercase", (
        f"{ONEMESSAGEBUS.crate} {ONEMESSAGEBUS.ref} no longer writes `Source` in lowercase, "
        f"so how {PROBE_RUN_ROOT.name} spells the producer of a built envelope has moved"
    )
    declaration = re.search(r"pub enum Source \{.*?\n\}", bus_event, re.DOTALL)
    assert declaration is not None, (
        f"{ONEMESSAGEBUS.crate} {ONEMESSAGEBUS.ref} no longer declares `Source` where this "
        "gate reads it"
    )
    sources = {variant.lower() for variant in SOURCE_VARIANT.findall(declaration.group(0))}
    for envelope in (
        json.loads(line)
        for line in (built_run_root / "events.jsonl").read_text("utf-8").splitlines()
        if line
    ):
        assert envelope["kind"] in kinds, (
            f"{PROBE_RUN_ROOT.name} writes a {envelope['kind']!r} event, which "
            f"onepipeline {ONEPIPELINE.ref} does not produce; the wire kind is an open "
            "string, so nothing else would refuse it"
        )
        assert envelope["source"] in sources, (
            f"{PROBE_RUN_ROOT.name} attributes a built event to {envelope['source']!r}, "
            f"which is not one of onepipeline {ONEPIPELINE.ref}'s {sorted(sources)}"
        )


def test_the_built_run_root_reads_the_start_time_field_onevcs_records() -> None:
    """The two Python copies added with these readings index the field `onevcs` does.

    The lease gate above reconciles the shell holder and its journey against the same
    declaration; these two spell the split with `rindex` rather than `rsplit`, so they
    are read with their own pattern and compared against that one source. A copy
    reading the wrong field of `/proc/<pid>/stat` verifies nothing while looking like
    it does: every live dispatch reads as a reused pid, and every rendezvous under one
    is reported as under no dispatch at all.
    """
    declared = ONEVCS_STAT_FIELD.search(_source(ONEVCS, "workspace.rs"))
    assert declared is not None, (
        f"{ONEVCS.crate} {ONEVCS.ref} no longer reads a numbered field out of "
        "/proc/<pid>/stat in workspace.rs, so what a process's creation identity means "
        "has moved; re-read it and correct every copy"
    )
    for path in (SUPERVISION_READINGS, PROBE_RUN_ROOT):
        copied = RINDEX_STAT_FIELD.search(path.read_text("utf-8"))
        assert copied is not None, (
            f"{path.name} no longer indexes /proc/<pid>/stat in the shape this gate "
            "reconciles; it tells a live dispatch from a reused pid on that answer, so "
            "update the gate and the copy together"
        )
        assert copied.group(1) == declared.group(1), (
            f"{path.name} reads field {copied.group(1)} of /proc/<pid>/stat while "
            f"{ONEVCS.crate} {ONEVCS.ref} reads field {declared.group(1)}, so no "
            "dispatch this host runs can be verified as the one its record names"
        )


def test_the_readings_indent_as_the_engine_indents_a_runs_block() -> None:
    """A reading added beside the engine's lines is indented the way the engine indents.

    The one value in this filter that is presentation rather than fact, and it is
    reconciled for the same reason as the rest: it is the engine's choice, not this
    repository's. A drift here is the mildest failure in this module — a line that reads
    as a second document rather than as part of the run's own block — but it is also the
    one a reader would put down to taste rather than to drift, so it is gated instead of
    noticed.

    Read from the two lines this module already reads for other reasons, and required to
    agree with each other: one literal could be indented by accident, two that disagree
    would mean the view has no single block depth for this to match at all.
    """
    view = STATUS_VIEW.search(_source(ONEPIPELINE, "views.rs"))
    assert view is not None, (
        f"onepipeline {ONEPIPELINE.ref} no longer composes its status view where this "
        "gate reads it, so nothing here can say how deep a run's block is indented"
    )
    composed = f"{view.group(0)}{_status_run_lines()}"
    depths = set()
    for literal in (HEALTH_BLOCK_OPENER, UNREAD_SURFACE_LINE):
        assert literal in composed, (
            f"onepipeline {ONEPIPELINE.ref}'s status view no longer writes {literal!r}, "
            "so this gate is reading a line the view does not print"
        )
        written = literal.lstrip('"')
        depths.add(written[: len(written) - len(written.lstrip())])
    assert len(depths) == 1, (
        f"onepipeline {ONEPIPELINE.ref}'s status view indents the lines inside a run's "
        f"block by {sorted(depths)}, so there is no single depth for "
        f"{SUPERVISION_READINGS.name} to match; re-read the view and correct the reading"
    )
    indent = depths.pop()
    assert _constant(SUPERVISION_READINGS, "INDENT") == indent, (
        f"{SUPERVISION_READINGS.name} indents its readings by "
        f"{_constant(SUPERVISION_READINGS, 'INDENT')!r} while onepipeline "
        f"{ONEPIPELINE.ref} indents a run's own block by {indent!r}, so every line this "
        "host adds reads as a second document beside the view rather than as part of it"
    )


class Value(NamedTuple):
    """One value a built run root states that the engine owns outright.

    A different question from the field it sits under, and one the record gate above
    cannot ask: that gate reconciles the *names* a record carries, and a version the
    engine has moved past sits under a name that never moved. Both halves have to hold
    for a built root to be one the installed engine would read as it reads a real one.
    """

    label: str
    engine: Engine
    source: str
    #: A pattern with one group capturing what the engine declares.
    declaration: re.Pattern[str]
    #: Every occurrence of that value in a built run root, as text.
    found: Callable[[Path], list[str]]


def _built_envelopes(root: Path) -> list[dict[str, object]]:
    """Every envelope a built run root's journal carries."""
    return [
        json.loads(line) for line in (root / "events.jsonl").read_text("utf-8").splitlines() if line
    ]


BUILT_VALUES = (
    Value(
        "the plan's schema version",
        ONEPIPELINE,
        "plan.rs",
        re.compile(r"pub const PLAN_SCHEMA_VERSION: u32 = (\d+);"),
        lambda root: [str(json.loads((root / "plan.json").read_text("utf-8"))["schema_version"])],
    ),
    # The newest version the bus profile reads, which onepipeline stamps as its own —
    # `test_onepipeline_stamps_the_newest_envelope_version_its_bus_reads` holds that.
    Value(
        "each journal envelope's version",
        ONEMESSAGEBUS,
        "onemessagebus-agent/src/registry.rs",
        re.compile(r"pub const EVENT_ENVELOPE_READS: &\[u32\] = &\[(\d+)"),
        lambda root: [str(envelope["v"]) for envelope in _built_envelopes(root)],
    ),
    Value(
        "the launch record's no-observer sentinel",
        ONEPIPELINE,
        "cli.rs",
        re.compile(r"pub const DAG_GRAPH_OFF: &(?:'static )?str = \"([^\"]+)\";"),
        lambda root: [str(json.loads((root / "launch.json").read_text("utf-8"))["graph"])],
    ),
)


@pytest.mark.parametrize("value", BUILT_VALUES, ids=lambda value: value.label)
def test_the_built_run_root_states_the_values_the_engine_owns(
    value: Value, built_run_root: Path
) -> None:
    """Every engine-owned value in a built run root is the one the engine declares.

    A stale one is the quiet half of this drift: the field is still named what the
    engine names it, so the record gate above passes and the record still loads, while
    the engine reads it as a plan of a schema it has moved past, an envelope of a
    version it no longer writes, or a launch whose observer sentinel means nothing. The
    views go on rendering over it either way, which is what makes a journey built on one
    a fixture agreeing with itself.
    """
    declared = value.declaration.search(_source(value.engine, value.source))
    assert declared is not None, (
        f"{value.engine.crate} {value.engine.ref} no longer declares {value.label} where "
        f"this gate reads it, so {PROBE_RUN_ROOT.name}'s copy cannot be reconciled; "
        f"re-read `{value.source}` and correct both"
    )
    written = value.found(built_run_root)
    assert written, (
        f"{PROBE_RUN_ROOT.name} no longer states {value.label}, so this gate reconciles "
        "a value nothing composes; update it with the builder"
    )
    for stated in written:
        assert stated == declared.group(1), (
            f"{PROBE_RUN_ROOT.name} states {value.label} as {stated!r} while "
            f"{value.engine.crate} {value.engine.ref} declares {declared.group(1)!r}. "
            "The field is still named what the engine names it, so nothing else here "
            "would notice"
        )


#: The derived record the engine writes when a **reader** folds a run's state, declared
#: on `RunPaths` beside the run's own records. Its name is restated in two places here
#: that no other gate reads — the ignore rule that keeps it out of this repository's own
#: tree, and the journey that holds the reading views to leaving a run's record alone —
#: so the engine's declaration is what both are compared against.
CHECKPOINT_DECLARATION = re.compile(
    r"fn checkpoint\(&self\) -> PathBuf \{\s*self\.dir\.join\(\"([a-z._]+)\"\)"
)


class Restatement(NamedTuple):
    """One place this repository writes down a filename the engine owns."""

    #: Where it is written, relative to the repository root.
    path: Path
    #: What reads it back out of that file.
    reads: re.Pattern[str]
    #: What it is doing there, for a failure that has to say what stopped working.
    what: str


CHECKPOINT_RESTATEMENTS = (
    Restatement(
        path=Path(".gitignore"),
        reads=re.compile(r"^/tests/fixtures/\*\*/([a-z._]+)$", re.MULTILINE),
        what="the rule that keeps a reader's checkpoint out of this repository's own tree",
    ),
    Restatement(
        path=Path("tests/e2e/test_supervision_readings_e2e.py"),
        reads=re.compile(r"DERIVED_BY_A_READER = frozenset\(\{\"([a-z._]+)\"\}\)"),
        what="the record the reading views are allowed to leave behind",
    ),
)


# llmlint: ignore-block[test_tiers_split_by_project_not_by_marker] `reads_checkouts` is
# not a narrower key inside a memoized tier: it moves this test out of every memoized
# tier into the uncached `orchestrator:test-checkouts`, because its subject — the
# adopted engine's own source, in a checkout `config/onevcs.checkouts` registers — is
# outside this workspace and no `nx.json` glob hashes it. That target declares
# `cache: false` and no `inputs`, so it runs over every project every time; a project
# of its own is what would give it an affected edge to be skipped by and a key to be
# replayed from, across the very engine upgrade this module exists to catch.
# `tests/conftest.py`'s checkout guard states that reasoning where it enforces the
# marker, and every `reads_checkouts` test in this repository is tiered this way for it.
@pytest.mark.parametrize("restatement", CHECKPOINT_RESTATEMENTS, ids=lambda row: row.path.name)
def test_the_checkpoint_a_reader_writes_is_named_what_the_engine_names_it(
    restatement: Restatement,
) -> None:
    """A filename this repository keeps out of its tree is the one the engine writes.

    The adopted engine folds a run's state from a checkpoint rather than replaying its
    journal, and a *reader* writes it — so `just status` over the recorded runs this
    suite drives leaves one behind, and without the ignore rule every such read dirties
    the working tree and moves the Nx key those fixtures are part of. Both restatements
    are inert if the engine renames the file: the ignore stops matching and the journey
    starts excluding a name nothing writes, and neither says so. The engine's own
    declaration is what closes that.

    `docs/repo-lifecycle.md`'s ledger listing carries the third restatement and is
    reconciled by the run-ledger vocabulary above, which reads the same `RunPaths`.
    """
    declared = CHECKPOINT_DECLARATION.search(_source(ONEPIPELINE, "ledger.rs"))
    assert declared is not None, (
        f"onepipeline {ONEPIPELINE.ref} no longer declares a fold checkpoint on "
        "`RunPaths` where this gate reads it, so the two restatements below are "
        "reconciled against nothing; re-read `ledger.rs` and correct all three"
    )

    path = REPO_ROOT / restatement.path
    stated = restatement.reads.search(path.read_text(encoding="utf-8"))
    assert stated is not None, (
        f"{restatement.path} no longer states {restatement.what} where this gate reads "
        "it, so the name is restated somewhere this does not check — or not at all, "
        "which for the ignore rule means every read of a recorded run dirties this tree"
    )
    assert stated.group(1) == declared.group(1), (
        f"{restatement.path} names {restatement.what} {stated.group(1)!r} while "
        f"onepipeline {ONEPIPELINE.ref} writes {declared.group(1)!r}"
    )


# llmlint: ignore-end[test_tiers_split_by_project_not_by_marker]


#: The two staging names the engine's atomic writers create beside a record and never
#: leave behind, each read as the format string `src/ledger.rs` composes it from:
#: `write_atomic` swaps the record's extension for `tmp.<pid>` and renames the result
#: over the record, and `create_exclusively_filled` appends `.tmp.<pid>.<nonce>` to the
#: record's name and links the result to it. The captured group is the format string,
#: whose `{}` placeholders are the integers the engine fills in.
STAGING_DECLARATIONS = {
    "write_atomic": re.compile(r'path\.with_extension\(format!\("(tmp\.\{\})", sys::pid\(\)\)\)'),
    "create_exclusively_filled": re.compile(
        r'name\.push\(format!\(\s*"(\.tmp\.\{\}\.\{\})",\s*sys::pid\(\),\s*NONCE\.fetch_add',
        re.MULTILINE,
    ),
}

#: Where `tests/e2e/test_run_snapshot.py` performs each shape itself, to race the
#: snapshot against a real writer: the suffix its `rename_into_place` swaps in with
#: `with_suffix`, which takes the dot `with_extension` adds, and the tail its
#: `link_into_place` appends to the record's name. Each is read back to the engine's
#: format string by replacing the Python expressions it fills in with `{}`.
STAGING_WRITERS = {
    "write_atomic": re.compile(r'record\.with_suffix\(f"(\.tmp\.\{os\.getpid\(\)\})"\)'),
    "create_exclusively_filled": re.compile(
        r'record\.with_name\(f"\{record\.name\}(\.tmp\.\{os\.getpid\(\)\}\.\{nonce\})"\)'
    ),
}
RUN_SNAPSHOT_TEST = REPO_ROOT / "tests" / "e2e" / "test_run_snapshot.py"


def test_the_staging_names_a_snapshot_leaves_out_are_the_ones_the_engine_writes() -> None:
    """`tests/e2e/run_snapshot.py` copies a live run without its atomic writers' staging
    files, recognising them by a name grammar restated from the engine, and
    `tests/e2e/test_run_snapshot.py` races it against a writer performing the same two
    shapes. Both restatements are inert if the engine changes the name: the snapshot
    starts copying — and racing — a staging file it no longer recognises, and the test's
    writer goes on proving the copy against a shape nothing writes. So each is held to
    the format string the engine composes the name from, at the pinned release: the
    grammar must match every name the engine's format produces and no stable record's,
    and the test's writer must fill in the same format.
    """
    import run_snapshot

    ledger = _source(ONEPIPELINE, "ledger.rs")
    writers = RUN_SNAPSHOT_TEST.read_text(encoding="utf-8")
    for writer, declaration in STAGING_DECLARATIONS.items():
        declared = declaration.search(ledger)
        assert declared is not None, (
            f"onepipeline {ONEPIPELINE.ref} no longer composes `{writer}`'s staging name "
            "where this gate reads it, so the snapshot's grammar is reconciled against "
            "nothing; re-read `ledger.rs` and correct `run_snapshot.STAGING_NAME`"
        )
        format_string = declared.group(1)
        # `with_extension` supplies the dot the pushed tail already carries.
        tail = format_string if format_string.startswith(".") else f".{format_string}"
        for record in ("summary.json", "events.jsonl", "owner.lock", "launch.json"):
            stem = record.rsplit(".", 1)[0] if writer == "write_atomic" else record
            staged = stem + tail.replace("{}", "4242")
            assert run_snapshot.is_staging_name(staged), (
                f"onepipeline {ONEPIPELINE.ref}'s `{writer}` stages {record} at {staged!r}, "
                "which `run_snapshot.STAGING_NAME` does not recognise, so a snapshot of a "
                "live run would race it exactly as `copytree` did"
            )
            assert not run_snapshot.is_staging_name(record), (
                f"`run_snapshot.STAGING_NAME` reads the stable record {record!r} as a "
                "staging file, so a snapshot would leave a real record out"
            )

        performed = STAGING_WRITERS[writer].search(writers)
        assert performed is not None, (
            f"{RUN_SNAPSHOT_TEST.name} no longer performs `{writer}`'s shape where this "
            "gate reads it, so the race it proves is against a writer this cannot vouch for"
        )
        filled = re.sub(r"\{[^}]+\}", "{}", performed.group(1))
        assert filled == tail, (
            f"{RUN_SNAPSHOT_TEST.name}'s writer stages `{writer}`'s shape as {filled!r} "
            f"while onepipeline {ONEPIPELINE.ref} composes {tail!r}"
        )


#: The three strings the engine composes an amendment into a task with, and where each
#: is declared in `src/plan.rs`: the heading it renders under, the sentence stating its
#: authority, and the heading it is placed immediately above. Read as declarations of
#: string constants rather than as prose, so a reworded sentence is a moved value here
#: rather than a paragraph quietly drifting.
AMENDMENT_CONSTANTS = {
    "AMENDMENT_HEADING": re.compile(r'pub const AMENDMENT_HEADING: &str = "([^"]+)";'),
    "AMENDMENT_PRECEDENCE": re.compile(
        r'const AMENDMENT_PRECEDENCE: &str =\s*"([^"]+)";', re.MULTILINE
    ),
    "ADDITIONAL_INFO_HEADING": re.compile(r'const ADDITIONAL_INFO_HEADING: &str = "([^"]+)";'),
}

#: The engine's `amended`, whose two arms are the whole of the placement rule: the block
#: is one heading, one precedence sentence, a blank line and the amendment; a task that
#: states the operational-notes heading gets the block immediately before that line,
#: and one that states none gets it at the end.
AMENDED = re.compile(
    r"fn amended\(task: &str, amendment: &str\) -> String \{\s*"
    r'let block = format!\("\{AMENDMENT_HEADING\}\\n\{AMENDMENT_PRECEDENCE\}'
    r'\\n\\n\{amendment\}\\n"\);\s*'
    r"match additional_info_at\(task\) \{\s*"
    r'Some\(at\) => format!\("\{\}\\n\\n\{block\}\\n\{\}", '
    r"task\[\.\.at\]\.trim_end\(\), &task\[at\.\.\]\),\s*"
    r'None => format!\("\{\}\\n\\n\{block\}", task\.trim_end\(\)\),',
    re.DOTALL,
)

#: A `Command` variant with its fields, doc comments and attributes included, so a field a
#: variant gained or lost is read from the declaration rather than assumed.
OPERATION_VARIANT = re.compile(r"\n    (?P<name>[A-Z]\w*) \{(?P<body>.*?)\n    \},", re.DOTALL)
#: A top-level struct's fields, at the indentation a struct body declares them.
STRUCT_FIELD = re.compile(r"^\s{4}pub (?P<field>[a-z_]\w*): ", re.MULTILINE)


# llmlint: ignore-block[test_tiers_split_by_project_not_by_marker] `reads_checkouts` moves
# this into the uncached `orchestrator:test-checkouts` for the reason the checkpoint gate
# above states: its subject is the adopted engine's own source, outside this workspace.
def test_the_node_a_live_edit_states_is_read_with_the_fields_the_engine_declares() -> None:
    """`orchestrator/envelope_review.py` reads a node an `add` or a `retry` states only when
    every field it carries is one the engine's `Node` accepts, renders the node's amendment
    from the field the engine renders one from, and passes over a node declaring that it
    dispatches nobody by the field the engine settles that on. Each is a restatement of
    `Node` at the pinned release: a field the engine gained would have the preflight pass
    over a node the engine commits, and a renamed amendment or no-dispatch field would have
    it judge text no dispatch reads, or refuse a bookmark nobody works.
    """
    from orchestrator import envelope_review

    node_struct = _source(ONEPIPELINE, "plan.rs").split("pub struct Node {", 1)[1]
    node_fields = set(STRUCT_FIELD.findall(node_struct.split("\n}\n", 1)[0]))
    assert node_fields == envelope_review.NODE_FIELDS, (
        f"onepipeline {ONEPIPELINE.ref}'s Node fields changed, so the live structural "
        f"preflight's command validation drifted: engine={sorted(node_fields)}, "
        f"check={sorted(envelope_review.NODE_FIELDS)}"
    )
    for name in ("AMENDMENT", "NO_DISPATCH"):
        assert getattr(envelope_review, name) in node_fields, (
            f"onepipeline {ONEPIPELINE.ref}'s `Node` no longer carries the field "
            f"`orchestrator.envelope_review.{name}` names; it declares {sorted(node_fields)}"
        )


#: A struct's fields including a raw identifier — `pub r#match: RuleMatch` — spelled
#: as the document key serde writes it, which is the identifier without the `r#`.
RAW_STRUCT_FIELD = re.compile(r"^\s{4}pub (?:r#)?(?P<field>[a-z_]\w*): ", re.MULTILINE)


def _struct_fields(source: str, struct: str) -> set[str]:
    """The document keys one `pub struct` declares, read off its body."""
    body = source.split(f"pub struct {struct} {{", 1)
    assert len(body) == 2, f"onevcs {ONEVCS.ref} declares no `pub struct {struct}`"
    return set(RAW_STRUCT_FIELD.findall(body[1].split("\n}\n", 1)[0]))


def test_the_workspaces_overlay_composes_the_keys_the_linked_onevcs_declares() -> None:
    """`orchestrator/workspaces_overlay.py` refuses a host file by the keys `onevcs` reads.

    The composer restates the workspaces document's shape — the top-level keys and the
    keys a rule may carry — so a host file off the schema is refused by name before the
    composed document reaches `onevcs pool status`. Each restatement is held to the
    `WorkspacesFile` and `WorkspaceRule` structs at the release a dispatch places its
    session through, which is also the CLI pin's at this adoption: a key the release
    gained would otherwise be refused here as unknown, and one it dropped composed into a
    document `onevcs` refuses whole.
    """
    from orchestrator import workspaces_overlay

    source = _source(ONEVCS, "workspaces.rs")
    assert _struct_fields(source, "WorkspacesFile") == workspaces_overlay.TOP_LEVEL_KEYS, (
        f"onevcs {ONEVCS.ref}'s WorkspacesFile declares "
        f"{sorted(_struct_fields(source, 'WorkspacesFile'))}, and the composer takes "
        f"{sorted(workspaces_overlay.TOP_LEVEL_KEYS)}"
    )
    assert _struct_fields(source, "WorkspaceRule") == workspaces_overlay.RULE_KEYS, (
        f"onevcs {ONEVCS.ref}'s WorkspaceRule declares "
        f"{sorted(_struct_fields(source, 'WorkspaceRule'))}, and the composer takes "
        f"{sorted(workspaces_overlay.RULE_KEYS)}"
    )
    assert _struct_fields(source, "WorkspaceDefault") == workspaces_overlay.RULE_KEYS - {"match"}


def test_the_task_a_live_edit_is_judged_as_is_composed_as_the_engine_composes_it() -> None:
    """A node an envelope states is judged on the task its dispatch will read, so how the
    engine renders an amendment into a task is restated in `orchestrator/envelope_review.py`,
    and this holds the restatement to the engine's source at the pinned release: the three
    strings and the placement rule. A release that moved either would have every stated
    node judged on text no dispatch reads, silently.
    """
    from orchestrator import envelope_review

    plan_rs = _source(ONEPIPELINE, "plan.rs")
    for name, declaration in AMENDMENT_CONSTANTS.items():
        declared = declaration.search(plan_rs)
        assert declared is not None, (
            f"onepipeline {ONEPIPELINE.ref} no longer declares {name} in plan.rs where this "
            "gate reads it; re-read how it renders an amendment and correct the restatement"
        )
        assert getattr(envelope_review, name) == declared.group(1), (
            f"orchestrator/envelope_review.py restates {name} as "
            f"{getattr(envelope_review, name)!r} while onepipeline {ONEPIPELINE.ref} "
            f"declares {declared.group(1)!r}"
        )
    assert AMENDED.search(plan_rs) is not None, (
        f"onepipeline {ONEPIPELINE.ref}'s `amended` no longer places the block the way "
        "`orchestrator.envelope_review.amended` restates; re-read it and correct the "
        "restatement"
    )


#: Which `add` and `retry` the engine refuses for their target, and how it moves a node's
#: `deps`, `consumes` and `delivers` under the three edits that move edges — each at the
#: declaration in `src/edits.rs` that `orchestrator.envelope_review.stated_graph` restates.
EDGE_FOLDS = {
    "an add of an id the graph already holds is refused": re.compile(
        r"fn compile_add\(graph: &mut Graph, node: &Node\) -> Result<Vec<Operation>> \{\s*"
        r"if graph\.contains\(&node\.id\) \{\s*"
        r"return Err\(refuse\(format!\(\"add: node '\{\}' already exists\""
    ),
    "a retry of a node the graph does not hold is refused": re.compile(
        r"let Some\(target\) = graph\.get\(id\)\.cloned\(\) else \{\s*"
        r"return Err\(refuse\(format!\(\"retry: no node '\{id\}'\"\)\)\);"
    ),
    "a retry onto a replacement id the graph already holds is refused": re.compile(
        r"if graph\.contains\(&replacement\.id\) \{\s*"
        r"return Err\(refuse\(format!\(\s*\"retry: replacement id '\{\}' must be new\""
    ),
    "a retry's replacement stating no deps inherits the superseded node's deps and consumes": (
        re.compile(
            r"if replacement\.deps\.is_empty\(\) \{\s*"
            r"replacement\.deps = target\.deps\.clone\(\);.*?"
            r"replacement\.consumes\.clone_from\(&target\.consumes\);",
            re.DOTALL,
        )
    ),
    "a retry's replacement stating no delivers inherits the superseded node's delivers": (
        re.compile(
            r"if replacement\.delivers\.is_empty\(\) \{\s*"
            r"replacement\.delivers\.clone_from\(&target\.delivers\);\s*\}",
        )
    ),
    "a retry redirects each dependent onto the replacement, its consumes entry rekeyed": (
        re.compile(
            r"for dep in &mut node\.deps \{\s*if dep == id \{\s*"
            r"dep\.clone_from\(&replacement\.id\);\s*\}\s*\}\s*"
            r"carried = node\.consumes\.remove\(id\);\s*"
            r"if let Some\(carried\) = carried\.clone\(\) \{\s*"
            r"node\.consumes\.insert\(replacement\.id\.clone\(\), carried\);",
            re.DOTALL,
        )
    ),
    "a retry removes the superseded node": re.compile(
        r"graph\.remove\(id\);\s*operations\.push\(Operation::NodeDropped \{\s*"
        r"node: id\.to_string\(\),\s*dependents: Dependents::Detach,",
        re.DOTALL,
    ),
    "a cascading drop removes every dependent, recursively": re.compile(
        r"Dependents::Drop => \{\s*let mut pending = direct;\s*"
        r"while let Some\(candidate\) = pending\.pop\(\) \{.*?"
        r"pending\.extend\(graph\.dependents_of\(&candidate\)\);",
        re.DOTALL,
    ),
    "a detaching drop removes each dependent's edge and consumes entry": re.compile(
        r"Dependents::Detach => \{.*?node\.deps\.retain\(\|dep\| dep != id\);.*?"
        r"node\.consumes\.remove\(id\);",
        re.DOTALL,
    ),
    "a dropped node is consumed by no remaining node": re.compile(
        r"Operation::NodeDropped \{ node, \.\. \} => \{\s*graph\.remove\(node\);.*?"
        r"other\.consumes\.remove\(node\);",
        re.DOTALL,
    ),
    "a reparent replaces deps and keeps only the consumes keyed on one of them": re.compile(
        r"Operation::Reparent \{ node, to, \.\. \} => \{\s*"
        r"if let Some\(node\) = graph\.get_mut\(node\) \{\s*"
        r"node\.deps\.clone_from\(to\);\s*"
        r"node\.consumes\.retain\(\|dep, _\| to\.iter\(\)\.any\(\|d\| d == dep\)\);",
        re.DOTALL,
    ),
}


@pytest.mark.parametrize("fold", EDGE_FOLDS, ids=lambda fold: fold)
def test_the_graph_a_live_edit_is_checked_over_is_folded_as_the_engine_folds_it(
    fold: str,
) -> None:
    """The structural rules a reply's resulting nodes are held to read `deps` and
    `consumes`, so how a `retry`, a `drop` and a `reparent` move them is restated in
    `orchestrator/envelope_review.py` and held here to the engine's source at the pinned
    release. A release that moved one would have the check ask the rules about a graph the
    engine never commits — accepting a reply that lands a refusable node, or refusing one
    that does not.
    """
    assert EDGE_FOLDS[fold].search(_source(ONEPIPELINE, "edits.rs")) is not None, (
        f"onepipeline {ONEPIPELINE.ref}'s `edits.rs` no longer declares that {fold}; re-read "
        "`compile_add`, `compile_retry`, `compile_drop` and `apply`, and correct "
        "`orchestrator.envelope_review.stated_graph`"
    )


#: The engine's live-edit command vocabulary: `Command` in `channel.rs`, serialized by
#: `op` in lower case with unknown fields refused, which is what makes each variant's
#: field set the whole of what an envelope may carry for that op.
COMMAND_TAGGING = re.compile(
    r'#\[serde\(tag = "op", rename_all = "lowercase", deny_unknown_fields\)\]\s*'
    r"pub enum Command \{"
)


def _command_fields() -> dict[str, dict[str, str]]:
    """Every `Command` variant at the pinned release, by wire op, with its fields' types."""
    channel_rs = _source(ONEPIPELINE, "channel.rs")
    tagged = COMMAND_TAGGING.search(channel_rs)
    assert tagged is not None, (
        f"onepipeline {ONEPIPELINE.ref} no longer serializes `Command` by `op` in lower "
        "case with unknown fields refused, so every op `orchestrator.envelope_review` "
        "names and every field it reads for one is unreconciled"
    )
    body = channel_rs[tagged.end() :].split("\n}\n", 1)[0]
    return {
        variant["name"].lower(): dict(
            re.findall(r"^\s{8}(?:pub )?([a-z_]\w*): (.+?),?$", variant["body"], re.MULTILINE)
        )
        for variant in OPERATION_VARIANT.finditer(body)
    }


def test_the_ops_a_live_edit_reads_task_prose_from_are_the_commands_the_engine_declares() -> None:
    """Which ops carry a node mapping, in which field, and which carry a text or a
    criterion, is restated in `orchestrator/envelope_review.py` as data, and the
    live-edit table in `docs/orchestration.md` is held to that restatement elsewhere. This
    holds it to the declaration both derive from: the engine's own `Command` enum at the
    pinned release. An op that stopped carrying its node, or moved it to another field,
    would otherwise leave the table and the module agreeing with each other about a wire
    the engine no longer speaks — and task prose reaching a dispatch through the field
    nothing reads.
    """
    from orchestrator import envelope_review

    declared = _command_fields()
    assert set(envelope_review.WHOLE_TASK_OPS) == set(envelope_review.STATED_IN)
    for op, field in envelope_review.STATED_IN.items():
        assert op in declared, (
            f"orchestrator/envelope_review.py reads a whole task off {op!r}, which onepipeline "
            f"{ONEPIPELINE.ref} does not declare among {sorted(declared)}"
        )
        assert field in declared[op], (
            f"orchestrator/envelope_review.py reads {op!r}'s task from {field!r}, while "
            f"onepipeline {ONEPIPELINE.ref} declares it with {sorted(declared[op])}"
        )
    # `add` and `retry` state a full `Node`; `requeue` states partial overrides of one,
    # which the module reads as the overrides they are.
    assert declared["add"]["node"] == declared["retry"]["node"] == "Node", declared
    assert declared["requeue"]["amend"].startswith("Option<Map<"), declared["requeue"]
    # An amendment is the `text` of `amend`, a note's criteria half is its `criterion`,
    # and both name their node by `id` — the three fields the module matches on.
    assert "text" in declared[envelope_review.AMEND_OP], declared[envelope_review.AMEND_OP]
    assert {"id", "criterion"} <= set(declared[envelope_review.NOTE_OP]), declared[
        envelope_review.NOTE_OP
    ]
    assert all("id" in declared[op] for op in ("retry", "requeue", envelope_review.AMEND_OP)), (
        declared
    )


# llmlint: ignore-end[test_tiers_split_by_project_not_by_marker]


#: The run-end hook this host wires, which restates the engine's failure document.
RUN_ENDED = REPO_ROOT / "scripts" / "run-ended.sh"
#: The fenced block of `docs/contract.md` that states the run-end hooks.
RUN_END_HOOKS_BLOCK = re.compile(r"```json\n(\{\s*\"run_end_hooks\".*?)\n```", re.DOTALL)
#: The reason kinds the hook's own reader accepts.
HOOK_REASON_KINDS = re.compile(r"^KINDS = \(([^)]*)\)$", re.MULTILINE)
#: The engine variables the hook reads.
HOOK_READS = ("ONEPIPELINE_HOOK", "ONEPIPELINE_RUN_ID", "ONEPIPELINE_RUN_ROOT")


def _run_end_hooks_contract() -> dict[str, object]:
    block = RUN_END_HOOKS_BLOCK.search(_source(ONEPIPELINE_DOCS, "contract.md"))
    assert block is not None, (
        f"onepipeline {ONEPIPELINE_DOCS.ref} no longer states its run-end hooks in a JSON block "
        "of docs/contract.md, so scripts/run-ended.sh's reading of the hook document cannot be "
        "reconciled; re-read the contract and correct both"
    )
    contract: object = json.loads(block.group(1))["run_end_hooks"]
    assert isinstance(contract, dict), contract
    return contract


def test_the_run_end_hook_reads_the_failure_document_the_engine_declares() -> None:
    """`scripts/run-ended.sh` restates the engine's failure document, so it is held to it.

    Three halves: the reason kinds it accepts are the ones the engine names, the variables
    it reads are ones the engine exports to a hook, and the contract's own failure example,
    handed to the real script, comes back as the line naming its nodes rather than as a
    document the script could not read.
    """
    contract = _run_end_hooks_contract()
    declared = HOOK_REASON_KINDS.search(RUN_ENDED.read_text(encoding="utf-8"))
    assert declared is not None, "scripts/run-ended.sh no longer declares the reason kinds it reads"
    assert re.findall(r'"([a-z-]+)"', declared.group(1)) == contract["reason_kinds"], (
        f"scripts/run-ended.sh accepts {declared.group(1)} while onepipeline "
        f"{ONEPIPELINE_DOCS.ref} declares {contract['reason_kinds']}"
    )
    exported = contract["environment"]
    assert isinstance(exported, list) and set(HOOK_READS) <= set(exported), (
        f"scripts/run-ended.sh reads {HOOK_READS}, and onepipeline {ONEPIPELINE_DOCS.ref} "
        f"exports {exported} to a hook"
    )

    stdin = contract["stdin"]
    assert isinstance(stdin, dict), stdin
    example = stdin["failure"]
    assert isinstance(example, dict), example
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("ONEPIPELINE_")
    }
    ran = subprocess.run(  # noqa: S603 - this repository's own hook, spawned as the engine spawns it
        [str(RUN_ENDED)],
        cwd=REPO_ROOT,
        env=environment | {"ONEPIPELINE_HOOK": "failure", "ONEPIPELINE_RUN_ID": example["run_id"]},
        input=json.dumps(example),
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )
    reason = example["reason"]
    assert isinstance(reason, dict), reason
    listed = ", ".join(f"{node['id']} {node['status']}" for node in reason["nodes"])
    assert ran.returncode == 0, ran.stdout + ran.stderr
    assert f"(reason {reason['kind']}: {listed})" in ran.stdout, (
        f"scripts/run-ended.sh could not read onepipeline {ONEPIPELINE_DOCS.ref}'s own failure "
        f"example:\n{ran.stdout}{ran.stderr}"
    )


#: The paragraph of `docs/orchestration.md` that restates the lineage item's stored shape
#: and reuse rule, opened by its bolded lead and closed by the next blank line.
LINEAGE_PARAGRAPH = re.compile(
    r"\*\*One board item per lineage, reused for the life of the work\.\*\*.*?\n\n", re.DOTALL
)
#: The sentence in the record paragraph that lists what `actions` carries.
RECORD_ACTIONS_SENTENCE = re.compile(r"`actions` \((.*?)\), and `spent`", re.DOTALL)
#: The sentence of AGENTS.md that says the item is reused, and where the rule is stated —
#: matched over `_plain` text, so the code span and emphasis around the two names are gone.
MANAGER_REUSE_SENTENCE = re.compile(
    r"A node's board item is reused across its retries.*?under the rule "
    r"docs/orchestration\.md's One board item per lineage states\."
)
#: Entry 80's fenced block, where the engine states the lineage item as data.
LINEAGE_CONTRACT_BLOCK = re.compile(r"```json\n(\{\s*\"lineage\".*?)\n```", re.DOTALL)
#: Entry 73's fenced block, where the projection record's fields are stated.
PROJECTION_RECORD_BLOCK = re.compile(r"```json\n(\{\s*\"projection\".*?)\n```", re.DOTALL)
#: The reserved keys the write-back writes on an item, declared in `src/taskgraph.rs`.
LINEAGE_KEY_DECLARATION = re.compile(
    r"const (ID_KEY|NODE_KEY|SUPERSEDES_KEY): &str = \"(onepipeline\.[a-z]+)\";"
)
#: The action counts a projection line carries, as the record's own struct declares them.
PROJECTION_ACTIONS_STRUCT = re.compile(r"pub struct ProjectionActions \{(.*?)\n\}", re.DOTALL)
PROJECTION_ACTIONS_FIELD = re.compile(r"^\s{4}pub ([a-z_]+): u64,", re.MULTILINE)
#: The stored shape this repository's readers rely on — `orchestrator/plan_store.py` reads
#: items by `onepipeline.id`, and `orchestrator/follow_up_tickets.py` owns the board a
#: delivered ticket sits on — as key → what the prose must say it holds.
LINEAGE_KEYS = {
    "onepipeline.id": "root",
    "onepipeline.node": "head",
    "onepipeline.supersedes": "the superseded ids in lineage order, root first",
}


def _lineage_contract() -> dict[str, object]:
    block = LINEAGE_CONTRACT_BLOCK.search(_source(ONEPIPELINE_DOCS, "contract-divergences.md"))
    assert block is not None, (
        f"onepipeline {ONEPIPELINE_DOCS.ref} no longer states the lineage item in a JSON block "
        "of docs/contract-divergences.md (entry 80), so the shape docs/orchestration.md "
        "restates cannot be reconciled; re-read the entry and correct the restatement"
    )
    contract: object = json.loads(block.group(1))
    assert isinstance(contract, dict), contract
    return contract


def _lineage_paragraph() -> str:
    paragraph = LINEAGE_PARAGRAPH.search(ORCHESTRATION.read_text("utf-8"))
    assert paragraph is not None, (
        "docs/orchestration.md no longer opens a paragraph with the lineage item's lead "
        "sentence; the stored shape has to be stated there once"
    )
    return _plain(paragraph.group(0))


def test_the_lineage_item_keys_the_prose_states_are_the_ones_the_engine_writes() -> None:
    """Three sources, one set of keys: the engine's constants, its contract, and the prose.

    Read off the crate's declarations rather than the contract alone, because the contract
    is prose about the crate and this gate exists for the day the crate moves under it.
    Each key is then held to what the prose says it carries — the root, the head, the
    superseded ids root first — since a reader that agreed on the key names and disagreed
    on which node each names would read every retried item as the wrong node.
    """
    declared = dict(
        (key, name)
        for name, key in LINEAGE_KEY_DECLARATION.findall(_source(ONEPIPELINE, "taskgraph.rs"))
    )
    assert set(declared) == set(LINEAGE_KEYS), (
        f"onepipeline {ONEPIPELINE.ref} declares the reserved item keys {sorted(declared)}, "
        f"and this repository reads {sorted(LINEAGE_KEYS)}; re-read src/taskgraph.rs and "
        "correct orchestrator/plan_store.py's readers and docs/orchestration.md together"
    )
    contract = _lineage_contract()
    lineage = contract["lineage"]
    assert isinstance(lineage, dict), lineage
    keys = lineage["keys"]
    assert isinstance(keys, dict) and set(keys) == set(LINEAGE_KEYS), keys
    assert keys["onepipeline.id"] == "root" and keys["onepipeline.node"] == "head", keys
    supersedes = keys["onepipeline.supersedes"]
    assert isinstance(supersedes, dict), supersedes
    assert supersedes["is"] == LINEAGE_KEYS["onepipeline.supersedes"], supersedes
    assert lineage["shadow_tasks_per_lineage"] == 1 and lineage["file_and_member"] == "root", (
        lineage
    )

    paragraph = _lineage_paragraph()
    for key, holds in LINEAGE_KEYS.items():
        assert key in paragraph, f"docs/orchestration.md's lineage paragraph never names {key}"
        assert holds in paragraph, (
            f"docs/orchestration.md's lineage paragraph does not say {key} carries {holds!r}, "
            f"which onepipeline {ONEPIPELINE_DOCS.ref}'s entry 80 states"
        )
    written_when = supersedes["written_when"]
    assert isinstance(written_when, str) and written_when in paragraph, (
        f"the paragraph does not say onepipeline.supersedes is written only where {written_when!r}"
    )
    assert MANAGER_REUSE_SENTENCE.search(_plain(MANAGER.read_text("utf-8"))) is not None, (
        "AGENTS.md's board paragraph no longer says the item is reused across retries and "
        "where the rule is stated"
    )


def test_the_projection_records_actions_and_items_are_the_engines_own() -> None:
    """`actions` gains `reopened` and `items` names roots, as the struct and both entries say.

    The struct is the source for the member names — a serialized record's fields are what
    a reader keys on — and the two entries are held to it as well, because entry 73 states
    the record and entry 80 states the derived count, and a reader of either that disagreed
    with the struct would be reading a schema no attempt writes.
    """
    struct = PROJECTION_ACTIONS_STRUCT.search(_source(ONEPIPELINE, "writeback.rs"))
    assert struct is not None, (
        f"onepipeline {ONEPIPELINE.ref} no longer declares ProjectionActions in src/writeback.rs"
    )
    members = PROJECTION_ACTIONS_FIELD.findall(struct.group(1))
    assert "reopened" in members and "created" in members, members

    record = PROJECTION_RECORD_BLOCK.search(_source(ONEPIPELINE_DOCS, "contract-divergences.md"))
    assert record is not None, "entry 73 no longer states the projection record as a JSON block"
    projection = json.loads(record.group(1))["projection"]
    fields = projection["fields"]
    assert fields["actions"]["members"] == members, (
        f"entry 73 lists actions {fields['actions']['members']} while the struct declares {members}"
    )
    assert "actions.reopened" in projection["schema"]["added_at_3"], projection["schema"]
    reopened = _lineage_contract()["reopened"]
    assert isinstance(reopened, dict), reopened
    assert reopened["record"] == {
        "key": "actions.reopened",
        "from_schema_version": 3,
        "stated_in": "entry 73",
    }, reopened

    sentence = RECORD_ACTIONS_SENTENCE.search(ORCHESTRATION.read_text("utf-8"))
    assert sentence is not None, (
        "docs/orchestration.md's record paragraph no longer lists what `actions` carries"
    )
    quoted = re.findall(r"`([a-z]+)`", sentence.group(1))
    assert quoted[: len(members)] == members and {"done", "cancelled"} <= set(quoted), (
        f"the record paragraph lists {sentence.group(1)!r}; the struct declares {members}, and "
        "the sentence names the two closed categories a reopen is counted from after them"
    )
    entry = _plain(_source(ONEPIPELINE_DOCS, "contract-divergences.md"))
    assert "items names lineage roots" in entry, "entry 80 no longer says what `items` names"
    assert "items (the lineage roots the copy carried" in _plain(
        ORCHESTRATION.read_text("utf-8")
    ), "docs/orchestration.md's record paragraph no longer says `items` names lineage roots"


def test_the_reuse_rule_the_prose_states_is_the_engines_own() -> None:
    """Retry edits, a closed item retried reopens, cancel parks, drop closes, siblings stay.

    Each word the prose uses for what a ruling projects is read off entry 80's `unchanged`
    and `destination` blocks — `parked` for a cancel, `cancelled` for a drop, the
    furthest-along item over an older board — so the day the engine changes what a cancel
    projects, the sentence telling a manager what to expect on the board fails here rather
    than being believed.
    """
    contract = _lineage_contract()
    unchanged = contract["unchanged"]
    assert isinstance(unchanged, dict), unchanged
    destination = contract["destination"]
    assert isinstance(destination, dict), destination
    retry = contract["retry"]
    assert isinstance(retry, dict), retry
    reopened = contract["reopened"]
    assert isinstance(reopened, dict), reopened

    paragraph = _lineage_paragraph()
    assert unchanged["cancelled_running_node"] == "parked" and unchanged["cancel"] == "parked", (
        unchanged
    )
    assert "its item reads parked" in paragraph, (
        f"the paragraph does not say a cancelled running node's item reads {unchanged['cancel']!r}"
    )
    assert unchanged["drop"] == "cancelled", unchanged
    assert "a drop projects cancelled and keeps its paired close" in paragraph, (
        f"the paragraph does not say a drop projects {unchanged['drop']!r}"
    )
    several = destination["several_under_one_root"]
    assert isinstance(several, str) and "furthest-along" in several, several
    assert "the item at the furthest-along position" in paragraph, (
        "the paragraph does not say an older board's siblings resolve to the furthest-along item"
    )
    counted = reopened["counted_when"]
    assert isinstance(counted, list) and any("done or cancelled" in when for when in counted), (
        counted
    )
    assert "reads done or cancelled writes an open word onto it" in paragraph, (
        "the paragraph does not say which items a retry or requeue reopens"
    )
    assert "inherits the superseded node's" in str(retry["delivers"]), retry
    assert "reopened: 0" in paragraph, (
        "the paragraph does not say a retry after a plain cancel reports reopened: 0"
    )
