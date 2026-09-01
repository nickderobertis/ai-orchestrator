"""The `design-doc` graph loads, and each side resolves the identity order it was given.

`graphs/design-doc.yaml` exists for one reason: this role's pairing is the reverse of
every other pairing on this host — Codex leads the side that writes the design document,
the Claude subscriptions lead the side that reviews it. That reversal lives entirely in
which oneharness config each side names, and nothing about a config file says which side
loaded it. So reading the two orders off the files would prove only that somebody wrote
them down; what has to hold is that the graph still routes the writing side to the file
carrying the writer's order and the reviewing side to the file carrying the reviewer's.

Both halves are therefore taken through the real tooling. `oneagentgraph` loads and runs
the shipped document, which is what says the members are wired at all — a member whose
config or base config cannot be read produces no `member-started` event, and a graph that
would not load is refused before one. `oneharness` resolves each named config, which is
what says the chain the loaded member would select from is the intended one; the run
itself cannot say that, because the paid provider is stood in for by pinning one identity.

The role's own prose is here for the same reason. `personas/design-doc.yaml` is what a
plan node hands this graph, and a persona is prose two other files decide the meaning of
— `config/onejudge.base.yaml` merges it and `oneagentgraph` resolves it — so the only way
to read what it gives a member is to run one. What that proves is that the writing side
is given the role and the reviewing side is given the bar, each carrying the pointer to
the template both are held to; what no journey can prove is what a model then writes,
which is why the document's shape is gated deterministically in
`tests/test_design_doc_template.py` instead.

Only the paid provider is substituted, at both seams `tests/e2e/persona_probe.py` owns.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from textwrap import dedent
from typing import TypedDict, cast

import pytest
from fake_backend import JUDGE_CONFIG_NAME, PROMPT_LOG_ENV
from persona_probe import (
    Envelope,
    probe_environment,
    recorded_turns,
    run_graph,
    settlement,
    started_member,
)
from shared_dispatch_bar import shared_agent_preamble
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: The shipped graph under test, and the member a dispatched node runs as. The member
#: name is the shipped node-scope graph's, because this document differs from it in the
#: two harness configs and in nothing else.
DESIGN_DOC_GRAPH = REPO_ROOT / "graphs" / "design-doc.yaml"
WORKER_MEMBER = "worker"

#: The identity order each side is supposed to resolve, stated here rather than read from
#: the file under test — a gate that read its expectation out of its subject would pass
#: whatever that subject said. Both name all five identities this host uses, because a
#: role that omitted one would lose that quota entirely once everything ahead of it was
#: exhausted, and both end on the primary Claude identity, which is last everywhere.
WRITER_CHAIN = (
    "codex",
    "codex:alternate",
    "claude-code:alternate",
    "claude-code:alternate2",
    "claude-code:primary",
)
REVIEWER_CHAIN = (
    "claude-code:alternate",
    "claude-code:alternate2",
    "codex",
    "codex:alternate",
    "claude-code:primary",
)

#: The role a plan node dispatching this graph names, as a path — the bare catalog name
#: would resolve against the roles built into oneagentgraph and read no file here.
DESIGN_DOC_PERSONA = REPO_ROOT / "personas" / "design-doc.yaml"

#: The label that file stamps on this member's events, which is what says the document
#: resolved rather than some other one.
DESIGN_DOC_LABEL = "design-doc"

#: How `oneagentgraph` names each side of a two-party turn in its published stream: the
#: side that does the work speaks as the assistant, and the simulated user supervising it
#: speaks as the user. Reading the sides from there rather than from a stand-in's private
#: record is what makes this journey an assertion about what an operator can see.
WRITING_TURN = "assistant"
REVIEWING_TURN = "user"

#: Who the indirection helpers attribute their diagnostics to when one of them refuses.
INDIRECTION_CALLER = "tests/e2e/test_design_doc_graph_e2e.py"


class TurnPayload(TypedDict):
    """A `turn-completed` payload, narrowed to the field that names the side."""

    role: str


class SettledPayload(TypedDict):
    """A `member-settled` payload, narrowed to what the reviewing side ruled.

    `cast` rather than a validating read at the site: this is `oneagentgraph`'s own
    published envelope, held to its schema in its own repository, so this states the two
    fields read here instead of restating somebody else's validation.
    """

    verdict: list[object]
    completion_reason: str


#: The task the probe run is given. Arbitrary prose: what is under test is which
#: documents reached the run, never what a model did with them.
PROBE_TASK = "Probe that this graph's two sides are wired."


def _side_configs() -> dict[str, Path]:
    """Each side of the graph's worker member, and the config it names.

    Read out of the document rather than restated, because the wiring IS the subject: a
    file repointed at another side's config, or at a config both sides share, is exactly
    what this journey exists to fail on, and a test naming the paths itself would go on
    passing through it. An indentation scan rather than a YAML dependency, as the sibling
    journeys over these two-level member documents use.
    """
    sides: dict[str, Path] = {}
    side: str | None = None
    for raw in DESIGN_DOC_GRAPH.read_text(encoding="utf-8").splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        key, _, value = stripped.partition(":")
        if indent == 4:
            side = key if not value.strip() else None
        elif indent == 6 and side is not None and key == "oneharness_config":
            sides[side] = (DESIGN_DOC_GRAPH.parent / value.strip()).resolve()
    return sides


def _member_settlement(envelopes: list[Envelope]) -> Envelope:
    """The `member-settled` envelope for the graph's one member.

    Its payload is where the reviewing side's own ruling is published — the verdict per
    criterion and the reason it completed on — so a member that settled with nothing
    having supervised it is visible from the stream an operator watches.
    """
    settled = [
        envelope
        for envelope in envelopes
        if envelope["kind"] == "member-settled"
        and envelope["labels"].get("member") == WORKER_MEMBER
    ]
    assert settled, (
        f"{DESIGN_DOC_GRAPH.name}'s `{WORKER_MEMBER}` member never settled:\n"
        f"{json.dumps(envelopes, indent=2)}"
    )
    return settled[0]


def _effective_chain(oneharness_bin: str, config: Path) -> tuple[str, ...]:
    """The identity chain a run loading exactly `config` would select from.

    `ONEHARNESS_*` is dropped for the reason the sibling deadline journey drops it: a
    worker verifies itself by running this suite from inside a dispatch, and an inherited
    `ONEHARNESS_HARNESSES` beats every file, so the suite would otherwise be reading the
    enclosing dispatch's chain instead of the one under test.
    """
    env = {key: value for key, value in os.environ.items() if not key.startswith("ONEHARNESS_")}
    resolved = subprocess.run(
        [oneharness_bin, "config", "--config", str(config), "--compact"],
        env=env,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(30),
    )
    assert resolved.returncode == 0, resolved.stderr
    chain: list[str] = json.loads(resolved.stdout)["harnesses"]["value"]
    return tuple(chain)


def test_the_graph_gives_each_side_its_own_config() -> None:
    """The two sides name two files, and neither is one an existing member already has.

    Cheap and separate on purpose: it is what tells a graph repointed at a shared config
    apart from one whose chains merely drifted, which the assertions below would report
    as the same wrong order.
    """
    sides = _side_configs()
    assert set(sides) == {"agent", "judge"}, (
        f"{DESIGN_DOC_GRAPH.name}'s `{WORKER_MEMBER}` member no longer names an "
        f"`oneharness_config` on each side; it names {sorted(sides)}"
    )
    assert sides["agent"] != sides["judge"], (
        f"both sides of {DESIGN_DOC_GRAPH.name} name {sides['agent']}, so the reversal "
        "this graph exists for has collapsed onto one file"
    )
    shipped = {
        (REPO_ROOT / name).resolve()
        for name in (
            "oneharness.toml",
            "oneharness.judge.toml",
            "oneharness.orchestrator.toml",
            "oneharness.check-in.toml",
            "oneharness.pr-author.toml",
            "oneharness.plan-review.toml",
            "oneharness.llmlint.toml",
        )
    }
    reused = shipped & set(sides.values())
    assert not reused, (
        f"{DESIGN_DOC_GRAPH.name} points a side at {sorted(reused)}, which another member "
        "already reads; sharing one config is what silently takes a member's own deadline "
        "and model tier away"
    )


@pytest.mark.parametrize(
    ("side", "chain"), (("agent", WRITER_CHAIN), ("judge", REVIEWER_CHAIN)), ids=("agent", "judge")
)
def test_each_side_resolves_its_intended_identity_order(
    side: str, chain: tuple[str, ...], oneharness_bin: str
) -> None:
    """The reversal holds, side by side, as the real CLI resolves it.

    Asserted per side and by whole order rather than by "Codex is first": the property
    this role was given two files for is that the writing side leads with Codex *and* the
    reviewing side leads with Claude, and either half alone is satisfied by both files
    carrying one chain.
    """
    resolved = _effective_chain(oneharness_bin, _side_configs()[side])
    assert resolved == chain, (
        f"the {side} side of {DESIGN_DOC_GRAPH.name} resolves {resolved}, not {chain}; this "
        "role's pairing is deliberately the reverse of this host's ordinary one, and every "
        "chain names all five identities with the primary Claude subscription last"
    )


@pytest.mark.xdist_group("design-doc-graph")
def test_the_shipped_graph_runs_both_of_its_sides(tmp_path: Path, oneharness_bin: str) -> None:
    """A real `oneagentgraph run` of the shipped document takes a turn on each side.

    The shipped file itself, not a probe copy of it: this graph's judge side is an
    oneharness config rather than the live manager `graphs/dag-scope.yaml`'s monitor
    names, so nothing has to be substituted for the document to run as written. What that
    reads is everything a config-validation failure costs — a base config, a member's two
    harness configs, and the onejudge conversation composed from them — none of which
    `oneagentgraph validate` alone reaches.

    Read entirely from the published envelope stream, which is where an operator watches
    a run: `oneagentgraph` stamps each turn with the conversation role that took it, so
    the writing side is the `assistant` turn and the reviewing side is the `user` one,
    and the member's settlement carries the verdict that side ruled with. A misconfigured
    judge shows up there as a member that settled with nothing having supervised it.
    """
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment = probe_environment(tmp_path, oneharness_bin, INDIRECTION_CALLER)

    envelopes = run_graph(DESIGN_DOC_GRAPH, environment, PROBE_TASK)

    started_member(envelopes, WORKER_MEMBER)
    assert settlement(envelopes)["members"] == {WORKER_MEMBER: "settled"}, (
        f"{DESIGN_DOC_GRAPH.name} did not settle its one member:\n{json.dumps(envelopes, indent=2)}"
    )

    completed = [
        cast(TurnPayload, envelope["payload"])
        for envelope in envelopes
        if envelope["kind"] == "turn-completed"
        and envelope["labels"].get("member") == WORKER_MEMBER
    ]
    took = {payload["role"] for payload in completed}
    assert took == {WRITING_TURN, REVIEWING_TURN}, (
        f"{DESIGN_DOC_GRAPH.name} recorded {sorted(took)} turn(s) where both sides of the "
        f"conversation must take one; a member that never reaches its {REVIEWING_TURN} "
        f"turn settled unsupervised:\n{json.dumps(envelopes, indent=2)}"
    )

    ruled = cast(SettledPayload, _member_settlement(envelopes)["payload"])
    assert ruled["verdict"] and ruled["completion_reason"], (
        f"{DESIGN_DOC_GRAPH.name}'s member settled carrying no verdict from its reviewing "
        f"side, so nothing judged the work:\n{json.dumps(ruled, indent=2)}"
    )


def _persona_halves() -> tuple[str, str]:
    """The role and the review bar `personas/design-doc.yaml` declares, as prose.

    Read out of the file rather than restated, because what is under test is that each
    half arrives at the side it was written for. A copy here would go on passing after
    the file said something else.
    """
    persona = DESIGN_DOC_PERSONA.read_text(encoding="utf-8")
    _, _, declared = persona.partition("\nsystem_prompt: |\n")
    role, _, rest = declared.partition("\nuser:\n")
    _, _, bar = rest.partition("persona: |\n")
    # Dedented, because a YAML block scalar's common indentation is not part of its
    # value: what a turn is handed is this prose flush left, and comparing the indented
    # source against it would fail on every persona whatever it said.
    halves = (dedent(role).strip(), dedent(bar).strip())
    assert all(halves), (
        f"{DESIGN_DOC_PERSONA.name} no longer declares both a `system_prompt` and a "
        "`user.persona`, so this journey has nothing to follow to a turn"
    )
    return halves


@pytest.mark.xdist_group("design-doc-graph")
def test_the_role_reaches_the_writer_and_its_bar_reaches_the_reviewer(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """`personas/design-doc.yaml` merges over the base and both halves land where written.

    A plan node supplies this graph's persona, so the member shape here is the shipped
    one with that override added — which is what a dispatch of this role actually is.
    Reading the prompts each side was handed is what separates a persona that merely
    parsed from one that arrived: a shape break costs the member entirely, while a merge
    that dropped one half leaves a writer composing without the template's pointer, or a
    supervisor scoring against a bar nobody wrote.

    The bar is asserted on the **judge** side and the role on the **agent** side rather
    than on either turn, because swapping them is the failure that would still leave both
    strings somewhere in the run: a worker that can read its own review bar argues with
    it, and a supervisor handed only the role has no bar to score against.
    """
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    environment = probe_environment(tmp_path, oneharness_bin, INDIRECTION_CALLER)
    prompt_log = tmp_path / "persona-prompts.jsonl"
    environment[PROMPT_LOG_ENV] = str(prompt_log)

    sides = _side_configs()
    graph = tmp_path / "design-doc-persona-probe.yaml"
    graph.write_text(
        "version: 1\n"
        "name: design-doc-persona-probe\n"
        "members:\n"
        f"  {WORKER_MEMBER}:\n"
        "    kind: onejudge\n"
        f"    base_config: {REPO_ROOT / 'config/onejudge.base.yaml'}\n"
        f"    persona: {DESIGN_DOC_PERSONA}\n"
        "    agent:\n"
        f"      oneharness_config: {sides['agent']}\n"
        "    judge:\n"
        f"      oneharness_config: {sides['judge']}\n"
        "    mode: bypass\n",
        encoding="utf-8",
    )

    envelopes = run_graph(graph, environment, PROBE_TASK)
    started = started_member(envelopes, WORKER_MEMBER)
    assert started["labels"].get("persona") == DESIGN_DOC_LABEL, (
        f"the member started under persona label {started['labels'].get('persona')!r} "
        f"rather than {DESIGN_DOC_LABEL!r}, so a different document was resolved:\n"
        f"{json.dumps(started, indent=2)}"
    )

    role, bar = _persona_halves()
    # What each side was *given* is the subject here, and no planner-facing or operator-
    # facing view carries it: the published stream reports that a turn happened and what
    # it answered, never the composed system prompt or the bar its supervisor was handed
    # — which is exactly why a merge that dropped one half is invisible until something
    # reads the turn itself. `tests/e2e/test_path_dispatched_personas_e2e.py` and
    # `tests/e2e/test_persona_review_bar_e2e.py` read the same two records for the same
    # reason; the sibling journey above asserts the observable half from the stream.
    turns = recorded_turns(prompt_log)
    # llmlint: ignore[tests_mirror_real_usage] No view carries a composed system prompt.
    writing = [turn for turn in turns if Path(turn["config"] or "").name != JUDGE_CONFIG_NAME]
    # llmlint: ignore[tests_mirror_real_usage] No view carries a supervisor's delivered bar.
    reviewing = [turn for turn in turns if Path(turn["config"] or "").name == JUDGE_CONFIG_NAME]
    assert writing and reviewing, (
        f"a side of the conversation took no turn, so nothing here reads what it was "
        f"given:\n{json.dumps(turns, indent=2)}"
    )

    for turn in writing:
        assert shared_agent_preamble() in turn["system"], (
            "the base config's shared preamble is missing from the composed system "
            f"prompt, so the persona replaced it rather than layering over it:\n"
            f"{turn['system']}"
        )
        assert role in turn["system"], (
            f"{DESIGN_DOC_PERSONA.name}'s role is missing from the writing side's "
            f"composed system prompt, so the file loaded but did not reach the turn:\n"
            f"{turn['system']}"
        )
        assert bar not in turn["system"], (
            "the review bar reached the writing side, where a worker reads the standard "
            f"it is scored against and argues with it rather than meeting it:\n"
            f"{turn['system']}"
        )

    # llmlint: ignore[tests_mirror_real_usage] No view carries a supervisor's delivered bar.
    delivered = "\n".join(turn["prompt"] for turn in reviewing)
    assert bar in delivered, (
        f"{DESIGN_DOC_PERSONA.name}'s review bar never reached the supervisor, which is "
        f"then scoring this document against whatever it supplies itself:\n{delivered}"
    )
