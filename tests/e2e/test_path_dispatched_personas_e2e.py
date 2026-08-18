"""Every persona a run reads BY PATH still loads, through a real graph.

`graphs/dag-scope.yaml` names `../personas/orchestrator.yaml` and
`../personas/check-in.yaml` by path, and those two files are what the monitor and
the heartbeat pacemaker are actually given on every orchestrated run. Everything
else in `personas/` is a catalog and a validation target — a plan node's bare
`persona: engineer` resolves to a role built into the crate, not to this directory
(`personas/README.md`). So these two are the ones whose shape a release can cost
this host the whole watching tier over.

`just validate-personas` is not enough to hold them, and that is a measurement
rather than a worry. A persona shape is read by **two** binaries: the oneagentgraph
CLI `config/oneagentgraph.version` pins, which is what validation runs, and the
oneagentgraph `onepipeline` **links**, which is what a run resolves a persona with.
Those two were on opposite sides of the 0.2.18/0.3.0 persona-format break — each
refuses the other's spelling outright, with no alias and no deprecation period — so
a bump of one alone would have left `just validate-personas` certifying files that
produce **no member at all**. Validation would have said `OK` the whole time.

The refusal lands in config validation, before any `graph-started` event, so what
tells a loaded persona from a refused one is that a member started at all. These
journeys run each path-named persona through a real `oneagentgraph run` — the same
verb `onepipeline` runs the observer graph with — in the member shape
`graphs/dag-scope.yaml` gives it, and read that. Only the paid provider is
substituted: `tests/e2e/fake_backend.py` at the spawned-CLI seam a two-party member
reaches, and `tests/e2e/fake_codex.py` at the provider binary a single-sided
member's in-library turn spawns.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import NamedTuple, NotRequired, TypedDict, cast

import pytest
from fake_backend import JUDGE_CONFIG_NAME, PROMPT_LOG_ENV
from harness_indirections import established_indirections
from shared_dispatch_bar import shared_agent_preamble
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: The observer graph whose members name a persona by path. The set below is checked
#: against what this document actually names, so a member added here is covered rather
#: than silently outside these journeys.
DAG_SCOPE_GRAPH = REPO_ROOT / "graphs" / "dag-scope.yaml"

#: Where its member block opens, and how a member states its own name inside it: one
#: key at the block's own indentation. Read positionally because there is no YAML
#: parser in this environment; `_observer_members` below is the whole of the parsing.
DAG_SCOPE_MEMBERS = "members:"
DAG_SCOPE_MEMBER = re.compile(r"^  (\S+):\s*$")

#: How that document spells a path-named persona, relative to its own directory.
PATH_NAMED_PERSONA = re.compile(r"^\s*persona:\s*\.\./(personas/\S+\.yaml)\s*$")

#: And the oneharness config a member's turn-taking side reads, wherever inside the
#: member it is spelled. A two-party member nests it under `agent:` and a single-sided
#: one states it at its own level; which of the two it is does not change that this is
#: the file that member's turn is routed by, so the reconciliation reads either.
PATH_NAMED_HARNESS_CONFIG = re.compile(r"^\s*oneharness_config:\s*\.\./(\S+\.toml)\s*$")

#: The stand-in for the paid model at the seam a two-party member reaches it, and the
#: stand-in one layer lower for the provider a single-sided member's in-library turn
#: spawns. Both are this suite's own; see each file's header.
FAKE_BACKEND = Path(__file__).resolve().parent / "fake_backend.py"
FAKE_CODEX = Path(__file__).resolve().parent / "fake_codex.py"

#: The directory put ahead of everything on `PATH`, holding a `claude` that refuses the
#: turn. It covers the identities `ONEHARNESS_BIN_*` cannot, which is every claude-code
#: variant these configs would fall through to.
PAID_PROVIDER_GUARD = Path(__file__).resolve().parent / "no-paid-provider"

#: Who the indirection helpers attribute their diagnostics to when one of them refuses.
INDIRECTION_CALLER = "tests/e2e/test_path_dispatched_personas_e2e.py"


class EnvelopeLabels(TypedDict, total=False):
    """The labels `oneagentgraph` stamps on an envelope, narrowed to what is read here.

    `total=False` because a graph-scope envelope carries only `run_id`: the member and
    the persona it resolved are what a member-scope one adds, and telling those apart is
    the whole of what these journeys read.
    """

    run_id: str
    member: str
    persona: str


class Settlement(TypedDict):
    """A `graph-settled` payload: how the run ended, and how each member did."""

    exit_code: int
    members: dict[str, str]


class Envelope(TypedDict):
    """One NDJSON envelope of a run, narrowed to the fields these journeys read.

    `oneagentgraph` owns the rest of the schema. `payload` is left untyped because the
    two kinds read here carry different ones, and only the settlement's is asserted on
    — through `Settlement`, at the site that knows which kind it is holding.
    """

    kind: str
    labels: EnvelopeLabels
    payload: object


class ProviderTurn(TypedDict):
    """One turn a stand-in recorded, as `fake_backend.py` and `fake_codex.py` write it.

    Both files are this suite's own on both ends, which is why this states their schema
    rather than validating somebody else's: `config` is absent from the codex record,
    because a single-sided member's in-library turn names none.
    """

    config: NotRequired[str | None]
    prompt: str
    system: NotRequired[str]


class ObserverMember(NamedTuple):
    """What `graphs/dag-scope.yaml` itself states about one of its members.

    Three fields rather than `PathDispatched`'s four, and the difference is where each
    is written down: the label a member's events carry is stated by the persona file,
    not by the graph. Keeping the graph's own record separate is what lets the
    reconciliation below compare like with like instead of a subset of one model
    against another.
    """

    #: The member name, as the key opening its block.
    member: str
    #: The persona file it names, repository-relative.
    persona: str
    #: The oneharness config its turn-taking side reads, repository-relative.
    harness_config: str


class PathDispatched(NamedTuple):
    """One persona a run reads by path, in the member shape that reads it.

    `kind` is not a detail: a two-party `kind: onejudge` member merges the persona
    over its base config and the role reaches the model, while a single-sided
    `kind: oneharness` member layers no config at all and takes only the persona's
    label. Both still **parse** the document, which is what a shape break costs — so
    both are journeys here, each asserting what its own shape actually consumes.
    """

    #: The member name in `graphs/dag-scope.yaml`.
    member: str
    #: The persona file, repository-relative, as that document names it.
    persona: str
    #: The label the file's own `name` stamps on this member's events.
    label: str
    #: The oneharness config the member's turn-taking side reads.
    harness_config: str

    @property
    def declared(self) -> ObserverMember:
        """The part of this record `graphs/dag-scope.yaml` is the source of."""
        return ObserverMember(self.member, self.persona, self.harness_config)


#: The two members of `graphs/dag-scope.yaml`, in that document's own shapes. Every
#: field here except `label` is that document's, and none of them is remembered:
#: `test_the_declared_set_is_every_persona_the_observer_graph_names_by_path` reads the
#: member name, the persona ref, and the oneharness config back out of the graph and
#: fails on any of the three moving. `label` is reconciled against the persona file
#: itself, one test below.
PATH_DISPATCHED = (
    PathDispatched(
        member="monitor",
        persona="personas/orchestrator.yaml",
        label="monitor",
        harness_config="oneharness.orchestrator.toml",
    ),
    PathDispatched(
        member="check-in",
        persona="personas/check-in.yaml",
        label="check-in",
        harness_config="oneharness.check-in.toml",
    ),
)

#: The task each probe graph is run with, and the marker a merged system prompt is
#: read against. Arbitrary prose: what is under test is which document reached the
#: turn, never what a model did with it.
PROBE_TASK = "Probe that this member's persona loaded."


def _environment(tmp_path: Path, oneharness_bin: str) -> dict[str, str]:
    """The environment a probe run gets, with the paid provider substituted twice.

    Both seams, because the two member shapes reach a provider by different paths and
    a journey covering one would let the other spend real quota: a two-party member
    spawns an `oneharness` CLI, and a single-sided one runs oneharness in-library and
    spawns only the provider binary. The indirections come from the helpers that own
    them, because a real oneharness refuses to start a variant whose `env_from` is
    unset and nothing on the `just gate` path exports one.
    """
    environment = dict(os.environ)
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEAGENTGRAPH_ONEHARNESS_BIN"] = str(FAKE_BACKEND)
    environment["REAL_ONEHARNESS_BIN"] = oneharness_bin
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["ONEHARNESS_BIN_CODEX"] = str(FAKE_CODEX)
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment["PATH"] = f"{PAID_PROVIDER_GUARD}{os.pathsep}{environment['PATH']}"
    environment.update(established_indirections(INDIRECTION_CALLER))
    # Keeps this run's graph scratch and history out of the host's, so a probe never
    # reads or reclaims a live dispatch's.
    environment["XDG_STATE_HOME"] = str(tmp_path / "state")
    return environment


def _run(graph: Path, environment: dict[str, str]) -> list[Envelope]:
    """Run one probe graph for real, and return the envelopes it streamed.

    `oneagentgraph run` is the verb `onepipeline` starts an observer graph with, and
    its NDJSON stream is where a member starting is observable at all.
    """
    ran = subprocess.run(
        ["oneagentgraph", "run", str(graph), "--task", PROBE_TASK],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(300),
        check=False,
    )
    assert ran.returncode == 0, (
        f"{graph.name} did not run to a settlement; a persona refused for its shape "
        f"fails here, in config validation, before any member starts:\n"
        f"{ran.stdout}\n{ran.stderr}"
    )
    # `cast` rather than a validating read: this is `oneagentgraph`'s own published
    # envelope stream, proven in its own repository, and `Envelope` states the three
    # fields these journeys read out of it.
    return [cast(Envelope, json.loads(line)) for line in ran.stdout.splitlines() if line.strip()]


def _started(envelopes: list[Envelope], member: str) -> Envelope:
    """The `member-started` envelope for one member, which a refused persona has none of."""
    started = [
        envelope
        for envelope in envelopes
        if envelope["kind"] == "member-started" and envelope["labels"].get("member") == member
    ]
    assert started, (
        f"no member started for {member!r}, so its persona produced no member at all:\n"
        f"{json.dumps(envelopes, indent=2)}"
    )
    return started[0]


def _settled(envelopes: list[Envelope]) -> Settlement:
    """The graph's own settlement, as its last envelope states it."""
    settled = [envelope for envelope in envelopes if envelope["kind"] == "graph-settled"]
    assert settled, f"the probe graph never settled:\n{json.dumps(envelopes, indent=2)}"
    return cast(Settlement, settled[0]["payload"])


def _turns(prompt_log: Path) -> list[ProviderTurn]:
    """Every turn a stand-in recorded, as it was actually given it."""
    if not prompt_log.is_file():
        return []
    # Test-owned on both ends, so the cast states that schema rather than skipping a
    # validation of somebody else's.
    return [
        cast(ProviderTurn, json.loads(line))
        for line in prompt_log.read_text(encoding="utf-8").splitlines()
    ]


def _observer_members() -> set[ObserverMember]:
    """Each member of the observer graph that names its persona by path.

    Reads all three fields as that document spells them, so the table above can be
    reconciled against the graph rather than trusted to still describe it. A member is
    one key at the `members:` block's indentation and
    everything indented under it, which is all the structure this needs; there is no
    YAML parser in this environment and the document is this repository's own.
    """
    members: dict[str, dict[str, str]] = {}
    current: dict[str, str] | None = None
    within = False
    for line in DAG_SCOPE_GRAPH.read_text(encoding="utf-8").splitlines():
        if line.rstrip() == DAG_SCOPE_MEMBERS:
            within = True
            continue
        if not within:
            continue
        named = DAG_SCOPE_MEMBER.match(line)
        if named:
            current = members.setdefault(named.group(1), {})
            continue
        if current is None:
            continue
        for key, pattern in (
            ("persona", PATH_NAMED_PERSONA),
            ("harness_config", PATH_NAMED_HARNESS_CONFIG),
        ):
            found = pattern.match(line)
            if found:
                current[key] = found.group(1)
    return {
        ObserverMember(member, read["persona"], read["harness_config"])
        for member, read in members.items()
        if "persona" in read and "harness_config" in read
    }


def test_the_declared_set_is_every_persona_the_observer_graph_names_by_path() -> None:
    """The table above is the graph's own list, not a remembered copy of it.

    A member added to `graphs/dag-scope.yaml` with a persona named by path is a third
    file a run reads and this file would otherwise say nothing about. Read from the
    document rather than restated, so adding one fails here until it has a journey.

    The member's name and its oneharness config are read back with the persona rather
    than only the persona, because the journeys below are written against all three and
    a copy of any one of them can go stale the same way. A member renamed, or repointed
    at another harness config, would otherwise leave those journeys passing against a
    graph that no longer says what they assert about it.
    """
    named = _observer_members()
    declared = {dispatched.declared for dispatched in PATH_DISPATCHED}
    assert named == declared, (
        f"{DAG_SCOPE_GRAPH.name} names {sorted(named)} by path; the journeys here cover "
        f"{sorted(declared)}. Every persona a run reads by path needs one, because "
        "validation alone would not catch it failing to load, and each journey's member "
        "and oneharness config have to be the ones that document still states."
    )


@pytest.mark.parametrize("dispatched", PATH_DISPATCHED, ids=lambda one: one.member)
def test_a_path_named_persona_is_declared_where_the_journey_below_reads_it(
    dispatched: PathDispatched,
) -> None:
    """The file exists and states the label its member's events are stamped with.

    Cheap and separate on purpose: it is what tells a renamed `name:` apart from a
    persona that would not load, which the journey below reports as the same missing
    member.
    """
    persona = (REPO_ROOT / dispatched.persona).read_text(encoding="utf-8")
    assert f"\nname: {dispatched.label}\n" in f"\n{persona}", (
        f"{dispatched.persona} no longer states `name: {dispatched.label}`, so the label "
        f"on the {dispatched.member!r} member's events has moved"
    )


@pytest.mark.xdist_group("path-dispatched-personas")
def test_the_monitors_persona_loads_and_reaches_the_turn(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """`personas/orchestrator.yaml` merges over the base and reaches the model.

    The monitor is `graphs/dag-scope.yaml`'s two-party `kind: onejudge` member, so its
    persona is layered over `config/onejudge.base.yaml` and the composed
    `system_prompt` is the agent side's whole framing: the base's shared preamble
    first, this file's role after it. Reading the prompt the stand-in was handed is
    what separates a persona that merely parsed from one that arrived — a shape break
    costs the member entirely, but a merge that dropped the role would leave a member
    running under instructions nobody wrote.

    The judge side is an `oneharness_config` here where the shipped document names
    `scripts/channel-serve.py`. That command is the **live manager** on a run's own
    channel, which no journey can stand in for; every other party — the real
    `oneagentgraph`, the real base config, the real persona, the real onejudge
    conversation between the two sides — is the shipped one.
    """
    dispatched = PATH_DISPATCHED[0]
    environment = _environment(tmp_path, oneharness_bin)
    prompt_log = tmp_path / "prompts.jsonl"
    environment[PROMPT_LOG_ENV] = str(prompt_log)

    graph = tmp_path / "monitor-probe.yaml"
    graph.write_text(
        "version: 4\n"
        "name: monitor-persona-probe\n"
        "members:\n"
        f"  {dispatched.member}:\n"
        "    kind: onejudge\n"
        f"    base_config: {REPO_ROOT / 'config/onejudge.base.yaml'}\n"
        f"    persona: {REPO_ROOT / dispatched.persona}\n"
        "    agent:\n"
        f"      oneharness_config: {REPO_ROOT / dispatched.harness_config}\n"
        "    judge:\n"
        f"      oneharness_config: {REPO_ROOT / JUDGE_CONFIG_NAME}\n"
        "    mode: bypass\n",
        encoding="utf-8",
    )

    envelopes = _run(graph, environment)
    started = _started(envelopes, dispatched.member)

    assert started["labels"].get("persona") == dispatched.label, (
        f"the member started under persona label {started['labels'].get('persona')!r} "
        f"rather than {dispatched.label!r}, so a different document was resolved:\n"
        f"{json.dumps(started, indent=2)}"
    )
    assert _settled(envelopes)["members"] == {dispatched.member: "settled"}, (
        f"the probe graph did not settle its one member:\n{json.dumps(envelopes, indent=2)}"
    )

    # The agent side, by the one property that separates the two: the judge side is the
    # turn pinned to the judge config.
    # llmlint: ignore[tests_mirror_real_usage] No planner-facing view carries the system prompt.
    working = [
        turn for turn in _turns(prompt_log) if Path(turn["config"] or "").name != JUDGE_CONFIG_NAME
    ]
    assert working, "no agent-side turn was taken, so nothing here reads a merged prompt"
    role = _persona_role(dispatched.persona)
    for turn in working:
        assert shared_agent_preamble() in turn["system"], (
            "the base config's shared preamble is missing from the composed system "
            f"prompt, so the persona replaced it rather than layering over it:\n"
            f"{turn['system']}"
        )
        assert role in turn["system"], (
            f"{dispatched.persona}'s own role is missing from the composed system "
            f"prompt, so the file loaded but did not reach the turn:\n{turn['system']}"
        )


@pytest.mark.xdist_group("path-dispatched-personas")
def test_the_pacemakers_persona_loads_and_labels_its_member(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """`personas/check-in.yaml` loads, and is the label alone — which is the shipped shape.

    The pacemaker is `graphs/dag-scope.yaml`'s single-sided `kind: oneharness` member.
    Such a member has no onejudge base config to layer a persona over, so the document
    is read for its `name` and nothing else: the prose the model is given is the
    member's own `task`. That is what `graphs/pr-author.yaml` records about its own
    drafter, and it is asserted here rather than assumed, because a release that
    started merging the file would silently change what the pacemaker is told.

    It still has to **parse**, which is the whole reason this journey exists beside
    the monitor's: the label-only path runs the same reader, so a shape the pinned
    crate refuses costs this member exactly as completely.
    """
    dispatched = PATH_DISPATCHED[1]
    environment = _environment(tmp_path, oneharness_bin)
    prompt_log = tmp_path / "codex-prompts.jsonl"
    environment["FAKE_CODEX_PROMPT_LOG"] = str(prompt_log)

    graph = tmp_path / "check-in-probe.yaml"
    graph.write_text(
        "version: 4\n"
        "name: check-in-persona-probe\n"
        "members:\n"
        f"  {dispatched.member}:\n"
        "    kind: oneharness\n"
        f"    oneharness_config: {REPO_ROOT / dispatched.harness_config}\n"
        f"    persona: {REPO_ROOT / dispatched.persona}\n"
        "    task: |\n"
        "      {task}\n",
        encoding="utf-8",
    )

    envelopes = _run(graph, environment)
    started = _started(envelopes, dispatched.member)

    assert started["labels"].get("persona") == dispatched.label, (
        f"the member started under persona label {started['labels'].get('persona')!r} "
        f"rather than {dispatched.label!r}, so a different document was resolved:\n"
        f"{json.dumps(started, indent=2)}"
    )
    assert _settled(envelopes)["members"] == {dispatched.member: "settled"}, (
        f"the probe graph did not settle its one member:\n{json.dumps(envelopes, indent=2)}"
    )

    # llmlint: ignore[tests_mirror_real_usage] No view carries a single-sided member's prompt.
    turns = _turns(prompt_log)
    assert turns, "the single-sided member took no provider turn, so nothing here is proven"
    role = _persona_role(dispatched.persona)
    for turn in turns:
        assert PROBE_TASK in turn["prompt"], (
            "the member's own task did not reach the turn, so `{task}` no longer "
            f"expands at this graph schema version:\n{turn['prompt']}"
        )
        assert role not in turn["prompt"], (
            f"{dispatched.persona}'s role now reaches a single-sided member's turn. "
            "That is a change in what the pacemaker is told: reconcile "
            "`graphs/dag-scope.yaml` and `graphs/pr-author.yaml`, which both record "
            f"that such a member layers no persona:\n{turn['prompt']}"
        )


def _persona_role(persona: str) -> str:
    """The first line of a persona's own `system_prompt`, as its reader would see it.

    One line rather than the whole block: it is enough to tell "this document reached
    the turn" from "some other did", and it survives an edit to the paragraphs below
    it, which are prose this file has no business freezing.
    """
    lines = (REPO_ROOT / persona).read_text(encoding="utf-8").splitlines()
    opened = next(
        (index for index, line in enumerate(lines) if line.strip() == "system_prompt: |"),
        None,
    )
    assert opened is not None, (
        f"{persona} no longer opens a block-scalar `system_prompt`, so this reader "
        "cannot state what role it gives"
    )
    role = next((line.strip() for line in lines[opened + 1 :] if line.strip()), "")
    assert role, f"{persona} states an empty role"
    return role
