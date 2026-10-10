"""What a dispatched turn's provider is really handed, read out of the provider.

A dispatch inherits the launching session's process environment whole, and two of the
names in it were doing harm.

The **plan store's credentials** `scripts/credentials-env.sh` exports — the board token,
and the production Linear key beside it — reached every provider a run started, whatever
that role's job was. A worker running a sibling repository's own
crate suite inherited it and opened a real credentialed GitHub session it never asked
for — six reconciliations against the API, on a run whose whole subject was what that
source spends on GitHub's rate limit. Only the roles that read the plan store have any
use for it.

The **runtime directory** is worse in a quieter way: `XDG_RUNTIME_DIR` on this host names
a path mounted `noexec`, and the recipe runner writes a shebang recipe's body under it
and execs it — so every recipe carrying a shebang failed inside every dispatch with a
bare permission error.

Both are fixed where a per-identity environment rule can be stated at all, which is the
`oneharness` config, and both are read back here out of a **real** `oneharness run`: the
provider binary is a stand-in that records its own environment, and nothing above it is
substituted. Reading the config's declarations instead would prove what this repository
wrote down rather than what a turn receives, and the whole defect was that those two
were not the same thing.

llmlint: ignore-file[expensive_tests_stay_behind_their_own_edge] What each journey here
spends is one `oneharness run` whose provider is the recorder below — about 40 ms, no
paid turn, no launch — and what it reads is this repository's own `oneharness*.toml`,
which the root project's `codeWorkspace` key already covers. A project of this module's
own would be keyed on less than the module reads, or on the same workspace twice.

llmlint: ignore-file[shell_test_tiers_stay_split] These are pytest journeys over the
real `oneharness` CLI rather than a shell test suite, and their cost is the line above.

llmlint: ignore-file[test_tiers_split_by_project_not_by_marker] No marker selects a tier
here: the module carries none, and the split it declines is the project one, for the
reason above.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import NamedTuple

import pytest
import short_state
from command_ceiling import (
    CEILING_MS,
    CLAUDE_CODE_CEILING,
    CLAUDE_CODE_DEFAULT,
    CODEX_CEILING_ARGS,
    CODEX_CEILING_KEY,
)
from harness_configs import harness_routing
from harness_indirections import established_indirections
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: Who the indirection helpers attribute their diagnostics to when one of them refuses.
INDIRECTION_CALLER = "tests/e2e/test_dispatch_environment_e2e.py"

#: Every ROLE config this repository ships, read off the tree so a role added here is
#: covered rather than forgotten. Only files declaring a `harnesses` chain: the shared
#: parents name no candidates, so including them would add no coverage silently.
ROLE_CONFIGS = tuple(
    sorted(
        path.name
        for path in REPO_ROOT.glob("oneharness*.toml")
        if "harnesses" in harness_routing(path)
    )
)

#: How a graph document names the oneharness config a member's side reads, relative to
#: its own directory — the spelling `tests/e2e/test_path_dispatched_personas_e2e.py`
#: reconciles the observer graph by.
NAMED_HARNESS_CONFIG = re.compile(r"^\s*oneharness_config:\s*\.\./(\S+\.toml)\s*$", re.MULTILINE)


def _configs_named_by(graph: str) -> tuple[str, ...]:
    """Every oneharness config `graph` routes one of its members' sides through.

    Read off the graph rather than listed here, because the graph is what decides which
    role a config serves: a config this module classified by hand would go on being
    tested as that role after the graph stopped routing it there.
    """
    named = NAMED_HARNESS_CONFIG.findall((REPO_ROOT / graph).read_text(encoding="utf-8"))
    assert named, f"{graph} names no oneharness config, so nothing can be derived from it"
    return tuple(dict.fromkeys(named))


#: How a two-sided member names the config its AGENT side reads, as distinct from its judge's.
NAMED_AGENT_SIDE_CONFIG = re.compile(
    r"^\s*agent:\s*\n\s*oneharness_config:\s*\.\./(\S+\.toml)\s*$", re.MULTILINE
)

#: The configs a node dispatch's own conversation runs under — the two sides
#: `graphs/node-scope.yaml` names. These are the sides that run a target repository's
#: recipes, so these are the ones that repoint the runtime directory.
DISPATCH_CONFIGS = (
    *_configs_named_by("graphs/node-scope.yaml"),
    # The worker's agent side a plan node names per node in place of the graph's, to prove
    # behaviour against the real board. No graph names it, which is the point of it, so it
    # is named here; `tests/e2e/test_oneharness_timeout_e2e.py` holds that nothing else does.
    "oneharness.board-live.toml",
)

#: That config, and the one credential of the two it lets travel.
BOARD_LIVE_CONFIG = "oneharness.board-live.toml"
BOARD_TOKEN = "GH_PROJECTS_TOKEN"

#: The configs of the members whose dispatch reads the plan store, so these keep its
#: credentials: the two sides `graphs/design-doc.yaml` names, because
#: `scripts/finish-plan.sh` composes a task telling that member to read the whole plan out
#: of the store through `onetaskgraph`; and the one `graphs/follow-up.yaml` names, because
#: `scripts/follow-ups.sh` composes a task whose whole deliverable is reading and writing
#: the `followups` board. A credential is what both are for.
PLAN_STORE_CONFIGS = (
    *_configs_named_by("graphs/design-doc.yaml"),
    *_configs_named_by("graphs/follow-up.yaml"),
)

#: The credentials a dispatch must not carry unless its role reads the plan store — the
#: board token, and the production Linear workspace's key, which a sibling's live Linear
#: lane must never see in place of its own test-scoped key — and the board
#: nomination that travels either way. Keeping the second is deliberate: a
#: sibling's live lane handed the board's owner, number and repository but no token
#: skips and prints its reason, where one handed nothing at all cannot tell a
#: misconfigured lane from a deliberate one.
CREDENTIALS = ("GH_PROJECTS_TOKEN", "HELLOPATIENT_LINEAR_API_KEY")
NOMINATION = ("GH_PROJECTS_OWNER", "GH_PROJECTS_NUMBER", "GH_PROJECTS_REPOSITORY")

#: The configs whose role reaches no plan store, so the credentials stop at the config. The
#: board-live worker is neither: it carries the board token alone, journeyed below.
MASKED_CONFIGS = tuple(
    name for name in ROLE_CONFIGS if name not in (*PLAN_STORE_CONFIGS, BOARD_LIVE_CONFIG)
)

#: A value per credential the stand-in would report if it reached it. Not a real token,
#: and never asserted as one — what is asserted is that the name is absent.
PLANTED_CREDENTIALS = {
    name: f"not-a-real-{name.lower()}-planted-by-this-journey" for name in CREDENTIALS
}
PLANTED_NOMINATION = {
    "GH_PROJECTS_OWNER": "nickderobertis",
    "GH_PROJECTS_NUMBER": "1",
    "GH_PROJECTS_REPOSITORY": "nickderobertis/onetaskgraph",
}

#: The provider binary each harness family resolves to, which is the name `PATH`
#: answers — the one seam every variant of every family shares.
PROVIDER_BINARIES = ("claude", "codex")


class Candidate(NamedTuple):
    """One identity of one role config's chain: what a turn is driven as."""

    config: str
    identity: str

    @property
    def identifier(self) -> str:
        return f"{self.config}-{self.identity}"


def _candidates(configs: tuple[str, ...]) -> tuple[Candidate, ...]:
    """Every candidate each config's chain names, paired with the config.

    Read off the config so that a candidate added to a chain is driven rather than
    forgotten: the one reached once the others are spent is exactly the one nobody is
    watching. The config is parsed here only to enumerate; what each candidate asserts
    is read out of a real turn.
    """
    return tuple(
        Candidate(config, identity)
        for config in configs
        for identity in harness_routing(REPO_ROOT / config).get("harnesses") or ()
    )


def _identifiers(candidates: tuple[Candidate, ...]) -> list[str]:
    return [candidate.identifier for candidate in candidates]


MASKED_CANDIDATES = _candidates(MASKED_CONFIGS)
BOARD_LIVE_CANDIDATES = _candidates((BOARD_LIVE_CONFIG,))
PLAN_STORE_CANDIDATES = _candidates(PLAN_STORE_CONFIGS)
DISPATCH_CANDIDATES = _candidates(DISPATCH_CONFIGS)

#: Every candidate of every role, which is the model journey's subject.
ALL_CANDIDATES = _candidates(ROLE_CONFIGS)

#: The follow-up agent's candidates, whose turn works in a directory that is no repository
#: and so must be handed each identity's own bypass argument.
FOLLOW_UP_CANDIDATES = _candidates(_configs_named_by("graphs/follow-up.yaml"))


#: The dispatched worker roles, whose turns run a target repository's checks and so may run
#: one shell command for an hour: the node-scope worker's agent side — never its judge's,
#: which runs no shell command — and the follow-up agent. Read off the graphs, so a graph
#: pointing a worker at another config moves the ceiling's subject with it.
def _agent_side_of(graph: str) -> tuple[str, ...]:
    """The config `graph`'s two-sided member runs its agent side under, and not its judge's."""
    named = NAMED_AGENT_SIDE_CONFIG.findall((REPO_ROOT / graph).read_text(encoding="utf-8"))
    assert named, f"{graph} names no agent-side oneharness config"
    return tuple(named)


WORKER_CONFIGS = (
    *_agent_side_of("graphs/node-scope.yaml"),
    BOARD_LIVE_CONFIG,
    *_configs_named_by("graphs/follow-up.yaml"),
)
WORKER_CANDIDATES = _candidates(WORKER_CONFIGS)

#: Every other role — the judge, monitor, pacemaker, drafter, plan reviewer, design-doc
#: pair and lint tier — which keeps its provider's own command limits.
UNRAISED_CANDIDATES = _candidates(
    tuple(name for name in ROLE_CONFIGS if name not in WORKER_CONFIGS)
)

#: What `bypass` is spelled as on each provider's command line, as `oneharness` builds it:
#: codex's one flag, and Claude Code's permission mode. Each is the whole of that
#: provider's answer to an untrusted directory or an unapproved tool.
BYPASS_ARGUMENTS = {
    "codex": ("--dangerously-bypass-approvals-and-sandbox",),
    "claude": ("--permission-mode", "bypassPermissions"),
}

#: The stand-in: a provider binary that records its environment and its argv, and answers.
#: Written per invocation so concurrent tests never share one recording.
RECORDING_PROVIDER = """#!/usr/bin/env python3
import json, os, pathlib, sys

if {"--version", "-v"}.intersection(sys.argv[1:]):
    print("0.0.0 (environment recorder)")
    raise SystemExit(0)
pathlib.Path(os.environ["RECORD_ENVIRONMENT_TO"]).write_text(
    json.dumps(dict(os.environ)), encoding="utf-8"
)
pathlib.Path(os.environ["RECORD_ARGV_TO"]).write_text(json.dumps(sys.argv), encoding="utf-8")
print("recorded")
"""


def _provider(directory: Path, record: Path) -> dict[str, str]:
    """A `PATH` front holding both families' provider names, each the recorder."""
    directory.mkdir(parents=True, exist_ok=True)
    for name in PROVIDER_BINARIES:
        binary = directory / name
        binary.write_text(RECORDING_PROVIDER, encoding="utf-8")
        binary.chmod(0o755)
    return {
        "RECORD_ENVIRONMENT_TO": str(record),
        "RECORD_ARGV_TO": str(record.with_name("argv.json")),
    }


def _indirections(tmp_path: Path) -> dict[str, str]:
    """Every alternate identity's credential directory, as a directory that exists.

    The *names* are the helpers' own — `established_indirections` reads them out of the
    one source every wrapper sources — and the values are deliberately not: an alternate
    candidate maps its credential directory in through one of these, and `oneharness`
    falls the candidate through as `auth` when that directory is absent, which on a host
    that never authenticated an alternate identity is every alternate. A directory that
    exists is all the recorder needs, so each name gets one here and every candidate of
    every chain can be driven rather than the one per family that needs none.
    """
    indirections = {}
    for name, _ in established_indirections(INDIRECTION_CALLER):
        directory = tmp_path / "indirections" / name
        directory.mkdir(parents=True)
        indirections[name] = str(directory)
    return indirections


def _turn(
    tmp_path: Path,
    oneharness_bin: str,
    candidate: Candidate,
    planted: dict[str, str] | None = None,
) -> dict[str, str]:
    """Spend one real turn as `candidate`; answer what its provider got.

    `planted` adds further names to the environment the turn arrives with, for a journey
    whose subject is a credential beyond the plan store's.

    Only the provider process is a stand-in. The config is the committed one, the chain
    resolution is `oneharness`'s own, and the environment below is the one a dispatch
    arrives with: the launching session's names, the plan store's credentials among them.
    """
    record = tmp_path / "environment.json"
    scratch = tmp_path / "node-scratch"
    scratch.mkdir()
    front = tmp_path / "bin"
    environment = {
        **{
            name: value
            for name, value in os.environ.items()
            # The ceiling's own names are dropped too, so what a turn is handed of them is
            # the config's alone rather than whatever the shell running this suite exports.
            if not name.startswith("ONEHARNESS_")
            and name not in (CLAUDE_CODE_CEILING, CLAUDE_CODE_DEFAULT)
        },
        **_indirections(tmp_path),
        # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
        **_provider(front, record),
        **PLANTED_NOMINATION,
        **PLANTED_CREDENTIALS,
        **(planted or {}),
        # What the engine hands a dispatch, and what the repoint reads.
        "ONEPIPELINE_NODE_SCRATCH_DIR": str(scratch),
        # Somewhere that is emphatically not this dispatch's own scratch, standing in
        # for the session runtime directory a dispatch would otherwise inherit.
        "XDG_RUNTIME_DIR": str(tmp_path / "ambient-runtime"),
        "ONEHARNESS_HARNESSES": candidate.identity,
        # Keeps this journey's harness history out of the host's.
        "XDG_STATE_HOME": str(short_state.state_home(tmp_path)),
        "PATH": f"{front}{os.pathsep}{os.environ['PATH']}",
    }
    ran = subprocess.run(
        [
            oneharness_bin,
            "run",
            "--config",
            str(REPO_ROOT / candidate.config),
            "--prompt",
            "record",
        ],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(120),
        check=False,
    )
    assert record.exists(), (
        f"{candidate.identity} never reached the recording provider under "
        f"{candidate.config}, so this turn says nothing about what a dispatch is handed:\n"
        f"{ran.stdout}\n{ran.stderr}"
    )
    # llmlint: ignore[boundary_inputs_validated] The recorder's own JSON, written by this
    # module a dozen lines above; every name read out of it is narrowed at its assertion.
    recorded: dict[str, str] = json.loads(record.read_text(encoding="utf-8"))
    return recorded


@pytest.mark.parametrize("candidate", MASKED_CANDIDATES, ids=_identifiers(MASKED_CANDIDATES))
def test_a_role_that_does_not_read_the_plan_store_is_handed_no_plan_store_credential(
    tmp_path: Path, oneharness_bin: str, candidate: Candidate
) -> None:
    """Every candidate of every chain, because a rule stated for one leaves the rest carrying it.

    `unset_env` is declarable on a variant only, so a bare harness id in a chain resolves
    no mask at all — and the candidate that does not carry the rule is reached exactly
    when the ones ahead of it are spent, which is the case nobody is watching. So each
    one is driven for real rather than the first per family.
    """
    recorded = _turn(tmp_path, oneharness_bin, candidate)

    handed = [name for name in CREDENTIALS if name in recorded]
    assert not handed, (
        f"{candidate.config} handed {candidate.identity} {handed}, which nothing this "
        f"role does reaches: a turn holding one can open a real credentialed session on "
        f"somebody's account"
    )


def _worker_masks(identity: str) -> list[str]:
    """What `oneharness.toml`'s committed record masks on `identity`."""
    # llmlint: ignore[boundary_inputs_validated] The committed record beside this module,
    # captured from `oneharness config`; the one list read out of it is narrowed here.
    record = json.loads((RESOLVED_RECORDS / "oneharness.toml.json").read_text("utf-8"))
    family, _, variant = identity.partition(":")
    masked = record["harness"][family]["variant"][variant]["unset_env"]["value"]
    assert isinstance(masked, list) and masked, (
        f"oneharness.toml's committed record masks nothing on {identity}"
    )
    return [str(name) for name in masked]


@pytest.mark.parametrize(
    "candidate", BOARD_LIVE_CANDIDATES, ids=_identifiers(BOARD_LIVE_CANDIDATES)
)
def test_the_board_live_worker_is_handed_the_board_token_and_nothing_else_the_worker_masks(
    tmp_path: Path, oneharness_bin: str, candidate: Candidate
) -> None:
    """Every identity of the board-live chain holds the board token and only that.

    A plan node that must prove behaviour against the real board names this config, so its
    turn must arrive holding `GH_PROJECTS_TOKEN` on whichever identity is reached — and
    lacking the production Linear key and every other credential `oneharness.toml` masks on
    that identity, because unmasking one token is all that config was written to do. Each
    of those is planted in the environment the turn arrives with, so its absence is the mask.
    """
    expected_masked = [name for name in _worker_masks(candidate.identity) if name != BOARD_TOKEN]
    assert "HELLOPATIENT_LINEAR_API_KEY" in expected_masked
    planted = {
        name: f"not-a-real-{name.lower()}-planted-by-this-journey" for name in expected_masked
    }
    recorded = _turn(tmp_path, oneharness_bin, candidate, planted)

    assert recorded.get(BOARD_TOKEN) == PLANTED_CREDENTIALS[BOARD_TOKEN], (
        f"{candidate.config} did not hand {candidate.identity} {BOARD_TOKEN}, the one "
        "credential a node naming it is meant to hold"
    )
    handed = [name for name in expected_masked if name in recorded]
    assert not handed, (
        f"{candidate.config} handed {candidate.identity} {handed}, which oneharness.toml "
        "masks there; only the board token was to travel"
    )
    missing = [name for name in NOMINATION if recorded.get(name) != PLANTED_NOMINATION[name]]
    assert not missing, f"{candidate.config} stopped handing {candidate.identity} {missing}"


@pytest.mark.parametrize("candidate", MASKED_CANDIDATES, ids=_identifiers(MASKED_CANDIDATES))
def test_the_board_nomination_still_reaches_a_role_that_carries_no_credential(
    tmp_path: Path, oneharness_bin: str, candidate: Candidate
) -> None:
    """The half a blanket mask would take away, and the reason the mask is not blanket.

    A live lane nominated a board but handed no token skips and prints why. One handed
    nothing at all reads as a lane nobody configured — which is how a credentialed write
    lane came to retarget itself and leave an item on the plans board.
    """
    recorded = _turn(tmp_path, oneharness_bin, candidate)

    missing = [name for name in NOMINATION if recorded.get(name) != PLANTED_NOMINATION[name]]
    assert not missing, (
        f"{candidate.config} stopped handing {candidate.identity} {missing}; the "
        f"nomination is what lets a lane with no credential skip with its reason rather "
        f"than look unconfigured"
    )


@pytest.mark.parametrize(
    "candidate", PLAN_STORE_CANDIDATES, ids=_identifiers(PLAN_STORE_CANDIDATES)
)
def test_a_role_that_reads_the_plan_store_keeps_its_credentials(
    tmp_path: Path, oneharness_bin: str, candidate: Candidate
) -> None:
    """The other direction: the mask must not have been written everywhere.

    `scripts/finish-plan.sh` tells the design-document role to read the whole plan out of
    the store through `onetaskgraph`, and `scripts/follow-ups.sh` tells the follow-up agent
    to copy its tickets onto the `followups` board and comment there — a board is what that
    store may be, so a masked credential here would fail the dispatch rather than protect
    anything. Every identity of each chain, because the one reached once the others are
    spent is the one nobody watches.
    """
    recorded = _turn(tmp_path, oneharness_bin, candidate)

    withheld = [name for name in CREDENTIALS if recorded.get(name) != PLANTED_CREDENTIALS[name]]
    assert not withheld, (
        f"{candidate.config} no longer hands {candidate.identity} {withheld}, and this "
        f"role's task is to read the plan out of the store"
    )


@pytest.mark.parametrize("candidate", FOLLOW_UP_CANDIDATES, ids=_identifiers(FOLLOW_UP_CANDIDATES))
def test_the_follow_up_agent_hands_every_identity_its_own_bypass_argument(
    tmp_path: Path, oneharness_bin: str, candidate: Candidate
) -> None:
    """Read off the command line the provider was really given, not the config's `mode`.

    The follow-up member's turn works in its agent graph's scratch directory, which is no
    repository: without its bypass argument codex refuses the turn as an untrusted directory
    and Claude Code is denied every tool outside that directory. Every identity, because the
    one reached once the others are spent is the one nobody watches.
    """
    _turn(tmp_path, oneharness_bin, candidate)
    # llmlint: ignore[boundary_inputs_validated] The recorder's own JSON, written by this
    # module's provider; it is compared whole against a literal below.
    argv: list[str] = json.loads((tmp_path / "argv.json").read_text(encoding="utf-8"))
    provider = Path(argv[0]).name
    expected = BYPASS_ARGUMENTS[provider]
    windows = [tuple(argv[at : at + len(expected)]) for at in range(len(argv))]

    assert expected in windows, (
        f"{candidate.config} handed {candidate.identity} {argv[1:-1]} with no {expected}; "
        f"its turn runs in a scratch directory that is no repository, where {provider} "
        "without bypass refuses the turn or denies it its tools"
    )


def _recorded_argv(tmp_path: Path) -> list[str]:
    # llmlint: ignore[boundary_inputs_validated] The recorder's own JSON, written by this
    # module's provider; each element read out of it is compared against a literal.
    argv: list[str] = json.loads((tmp_path / "argv.json").read_text(encoding="utf-8"))
    return argv


def _windows(argv: list[str], width: int) -> list[tuple[str, ...]]:
    return [tuple(argv[at : at + width]) for at in range(len(argv))]


@pytest.mark.parametrize("candidate", WORKER_CANDIDATES, ids=_identifiers(WORKER_CANDIDATES))
def test_a_dispatched_worker_may_run_one_shell_command_for_an_hour(
    tmp_path: Path, oneharness_bin: str, candidate: Candidate
) -> None:
    """Read off what the provider was handed, on every identity of each worker role.

    The settings sit at the harness level, so one stated on a variant instead would reach
    one identity and leave the rest at ten minutes — and the identity reached once the
    others are spent is the one nobody watches. Claude Code reads its ceiling from the
    environment, with the default per-call timeout left alone; Codex reads its wait bound
    from its command line.
    """
    recorded = _turn(tmp_path, oneharness_bin, candidate)
    argv = _recorded_argv(tmp_path)
    provider = Path(argv[0]).name

    if provider == "claude":
        assert recorded.get(CLAUDE_CODE_CEILING) == CEILING_MS, (
            f"{candidate.config} handed {candidate.identity} {CLAUDE_CODE_CEILING}="
            f"{recorded.get(CLAUDE_CODE_CEILING)!r}, not {CEILING_MS}; one Bash call there "
            "is capped at ten minutes and a long check has to be split to fit"
        )
        assert CLAUDE_CODE_DEFAULT not in recorded, (
            f"{candidate.config} handed {candidate.identity} {CLAUDE_CODE_DEFAULT}; the "
            "default per-call timeout stays the provider's, so a hung command does not "
            "block for an hour unless the model asked for that"
        )
    else:
        assert CODEX_CEILING_ARGS in _windows(argv, len(CODEX_CEILING_ARGS)), (
            f"{candidate.config} handed {candidate.identity} {argv[1:]} with no "
            f"{CODEX_CEILING_ARGS}; each `write_stdin` wait there stops at five minutes"
        )


@pytest.mark.parametrize("candidate", UNRAISED_CANDIDATES, ids=_identifiers(UNRAISED_CANDIDATES))
def test_a_role_that_is_no_dispatched_worker_keeps_its_providers_command_limits(
    tmp_path: Path, oneharness_bin: str, candidate: Candidate
) -> None:
    """The other direction: the ceiling was raised for the workers and nobody else.

    A shared parent is the tidy-looking place to state it, and it would reach the judge,
    the monitor, the drafter and the lint tier along with the two workers.
    """
    recorded = _turn(tmp_path, oneharness_bin, candidate)
    argv = _recorded_argv(tmp_path)

    carried = [name for name in (CLAUDE_CODE_CEILING, CLAUDE_CODE_DEFAULT) if name in recorded]
    carried.extend(word for word in argv if CODEX_CEILING_KEY in word)
    assert not carried, (
        f"{candidate.config} handed {candidate.identity} {carried}; only the dispatched "
        "worker roles raise a shell command's limit"
    )


#: The committed effective configurations
#: `tests/e2e/test_oneharness_config_equivalence_e2e.py` holds to the real CLI. The
#: journey below reads its expectation out of these rather than out of the same resolver
#: it is checking: the record is the artifact a reviewer reads a routing change in, so a
#: turn matching it is the wire agreeing with the review.
RESOLVED_RECORDS = Path(__file__).parent / "oneharness_resolved"

MODEL_ARGUMENT = "--model"


def _recorded_model(candidate: Candidate) -> str:
    """The model `candidate`'s committed record resolves, nearest layer first.

    A variant states its own `model` throughout this host's configs, and the fallbacks
    are the layering's own: a variant that stated none would take the harness table's,
    and a harness that stated none the run-level value.
    """
    # llmlint: ignore[boundary_inputs_validated] The committed record beside this module,
    # captured from `oneharness config`; every value read out of it is narrowed here.
    record = json.loads((RESOLVED_RECORDS / f"{candidate.config}.json").read_text("utf-8"))
    family, _, variant = candidate.identity.partition(":")
    harness = record.get("harness", {}).get(family, {})
    for layer in (harness.get("variant", {}).get(variant, {}), harness, record):
        named = layer.get("model", {}).get("value")
        if isinstance(named, str):
            return named
    raise AssertionError(
        f"{candidate.config}'s committed record names no model for {candidate.identity}, "
        f"so nothing below says which tier that identity spends"
    )


@pytest.mark.parametrize("candidate", ALL_CANDIDATES, ids=_identifiers(ALL_CANDIDATES))
def test_every_identity_spends_its_turn_on_the_model_its_role_resolves(
    tmp_path: Path, oneharness_bin: str, candidate: Candidate
) -> None:
    """Read off the command line the provider was given, not off the config that names it.

    A tier is a decision made once — `oneharness.identities.toml` states one model for
    the Claude identities and one for the Codex identities, and a role restates one only
    where it deliberately differs — and it is spent one identity at a time. So a move
    that reached the resolver and not the wire is invisible until the quota bill arrives,
    which is exactly how a codex-first side here came to spend a tier neither its config
    nor its record named (`tests/e2e/test_controlled_turn_model_e2e.py` re-takes that on
    the controlled path). Every candidate of every chain, because the identity that keeps
    an old tier is the one reached after the ones ahead of it are spent.
    """
    expected = _recorded_model(candidate)
    _turn(tmp_path, oneharness_bin, candidate)
    # llmlint: ignore[boundary_inputs_validated] The recorder's own JSON, written by this
    # module's provider; the one element read out of it is compared against a string.
    argv: list[str] = json.loads((tmp_path / "argv.json").read_text(encoding="utf-8"))
    named = [
        argv[at + 1]
        for at, word in enumerate(argv)
        if word == MODEL_ARGUMENT and at + 1 < len(argv)
    ]

    assert named == [expected], (
        f"{candidate.config} handed {candidate.identity} {named} on its provider's "
        f"command line, not the {expected!r} its committed record resolves; this turn "
        f"is billed against whatever that name selects"
    )


#: A recipe of the shape that failed: `just` writes a shebang recipe's body under
#: `XDG_RUNTIME_DIR` and execs it, so where that directory points decides where every
#: shebang recipe of a dispatch runs from. The body reports its own path and that the file
#: is there while it runs, so where the runner wrote it is read off the recipe itself.
#: This repository's own `justfile` carries none, which is why the defect was invisible
#: here and cost a dispatch in every other repository.
SHEBANG_RECIPE = """demo:
    #!/usr/bin/env bash
    test -f "$0" && echo "shebang-body-at=$0"
"""


def _shebang_recipe(
    directory: Path, environment: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    """Run a real shebang recipe under `environment`, the one a dispatched turn was handed.

    `JUST_TEMPDIR` is dropped because `just` prefers it to `XDG_RUNTIME_DIR`, and a
    session's shell may export one (Claude Code's exports `/tmp`): inherited, it decides
    where the body is written and the runtime directory decides nothing.
    """
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "justfile").write_text(SHEBANG_RECIPE, encoding="utf-8")
    return subprocess.run(
        ["just", "demo"],
        cwd=directory,
        env={name: value for name, value in environment.items() if name != "JUST_TEMPDIR"},
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )


@pytest.mark.parametrize("candidate", DISPATCH_CANDIDATES, ids=_identifiers(DISPATCH_CANDIDATES))
def test_a_dispatched_turn_runs_its_shebang_recipes_out_of_its_own_scratch(
    tmp_path: Path, oneharness_bin: str, candidate: Candidate
) -> None:
    """Not out of the launching session's runtime directory, which is mounted `noexec` here.

    Every candidate, for the reason the credential journey gives: `env_from` is
    declarable on a variant only, so a candidate resolving no variant runs with the
    session's runtime directory, and it is reached once the ones ahead of it are spent.
    The recipe then runs under exactly what that turn's provider recorded, so the second
    half reads where the runner really puts a dispatch's runtime files rather than
    inferring it from a runtime directory the runner refuses.
    """
    scratch = tmp_path / "node-scratch"
    recorded = _turn(tmp_path, oneharness_bin, candidate)

    assert recorded.get("XDG_RUNTIME_DIR") == str(scratch), (
        f"{candidate.config} handed {candidate.identity} XDG_RUNTIME_DIR "
        f"{recorded.get('XDG_RUNTIME_DIR')!r} rather than this dispatch's own scratch; "
        f"the ambient one is where a shebang recipe's body cannot be executed"
    )

    ran = _shebang_recipe(tmp_path / "workspace", recorded)

    assert ran.returncode == 0, (
        f"a shebang recipe did not run under the environment {candidate.identity} was "
        f"handed:\n{ran.stdout}\n{ran.stderr}"
    )
    written = re.search(r"^shebang-body-at=(.+)$", ran.stdout, re.MULTILINE)
    assert written is not None, (
        f"the shebang recipe never reported where its body was written:\n{ran.stdout}"
    )
    assert Path(written.group(1)).is_relative_to(scratch), (
        f"just wrote the shebang recipe's body to {written.group(1)!r}, outside this "
        f"dispatch's own scratch {scratch}, so the repoint does not decide where a "
        f"dispatch's recipes run from"
    )
