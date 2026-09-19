"""Real-CLI proof that streaming and out-of-band turn control are independent here.

`AGENTS.md` and docs/onejudge-integration.md both record that `--stream` (when a
turn's transcript arrives) and `--control` (a socket a separate process can abort
the live turn over) are unrelated, that the adopted oneharness supports both at once
on a multi-identity `run_mode = "fallback"` chain, and that turning streaming off is
never the answer to a control refusal. That is a per-release claim, and the release
it was measured against moves. So it is measured here instead, against the real CLI
and this repository's own committed chain.

Every run is `--print-command`: the CLI applies the same up-front validation a real
run applies, renders the argv it would spawn, and spawns nothing — so nothing here
bills a provider turn. Each names `--format json`, because the plan is read as the
CLI's JSON report and the adopted release renders a human-readable view without it.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

WORKER_CONFIG = REPO_ROOT / "oneharness.toml"
GRAPHS = (REPO_ROOT / "graphs" / "dag-scope.yaml", REPO_ROOT / "graphs" / "node-scope.yaml")
#: What claude-code's turn-control mechanism puts on the child's argv: control
#: requests arrive on stdin, so the prompt leaves argv and an input format appears.
CONTROL_ARGV = ("--input-format", "stream-json")
#: What a streamed turn puts there: normalized events on stdout as they occur.
STREAM_ARGV = ("--output-format", "stream-json")
#: codex's mechanism is a whole different process, not a flag on a one-shot turn:
#: `codex-app-server` speaks its protocol over stdio, so the argv IS the server.
CODEX_CONTROL_ARGV = ("codex", "app-server")
#: The prompt every plan below is rendered for. A controlled turn moves it off argv.
PROMPT = "hi"


@dataclass(frozen=True)
class Candidate:
    """One chain candidate as oneharness rendered it: who it is, and what it would spawn."""

    identity: str
    argv: tuple[str, ...]

    def flag_value(self, flag: str) -> str | None:
        """The value oneharness placed after `flag`, or None when it placed no `flag`."""
        return self.argv[self.argv.index(flag) + 1] if flag in self.argv else None


@dataclass(frozen=True)
class Plan:
    """A refused plan (`refusal` set) or an accepted one (`candidates` in chain order)."""

    refusal: str | None
    candidates: tuple[Candidate, ...]


def _plan(oneharness_bin: str, config: Path, *arguments: str) -> Plan:
    """Ask the real CLI what a run against `config` would spawn, and spawn nothing.

    `ONEHARNESS_*` is dropped because every worker verifies itself by running this
    suite from inside a dispatch, and both the harness and model variables beat every
    file — so the suite would be planning the enclosing dispatch's chain rather than
    the committed one under test.
    """
    env = {key: value for key, value in os.environ.items() if not key.startswith("ONEHARNESS_")}
    rendered = subprocess.run(
        [oneharness_bin, "run", "--config", str(config), "--print-command", "--format", "json"]
        + ["--prompt", PROMPT, *arguments],
        env=env,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(30),
    )
    if rendered.returncode != 0:
        assert rendered.stderr.strip(), (
            f"oneharness refused with exit {rendered.returncode} and said nothing"
        )
        return Plan(refusal=rendered.stderr, candidates=())
    document = json.loads(rendered.stdout)
    return Plan(
        refusal=None,
        candidates=tuple(
            Candidate(identity=result["harness_id"], argv=tuple(result["command"]))
            for result in document["results"]
        ),
    )


def _single_family_chain(config: Path, destination: Path) -> str:
    """Copy the committed worker config, keeping only its first harness family.

    Derived from the real file rather than written out, so this proves something
    about the chain this repository actually dispatches on: drop the identities whose
    turn-control mechanism differs and what is left is still several identities, which
    is the whole point of the claim being measured.
    """
    kept: list[str] = []
    family: str | None = None
    lines: list[str] = []
    in_chain = False
    for raw in config.read_text(encoding="utf-8").splitlines():
        stripped = raw.strip()
        if stripped.startswith("harnesses = ["):
            in_chain = True
            lines.append(raw)
            continue
        if not in_chain:
            lines.append(raw)
            continue
        if stripped == "]":
            in_chain = False
            lines.append(raw)
            continue
        identity = stripped.strip(",").strip('"')
        if family is None:
            family = identity.split(":")[0]
        if identity.split(":")[0] == family:
            kept.append(identity)
            lines.append(raw)
    assert len(kept) > 1, (
        f"{config} no longer chains several identities of one harness family; this test "
        "measures a MULTI-identity chain, so re-derive it rather than weakening the claim"
    )
    destination.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return family or ""


def test_the_committed_chain_takes_control_on_each_candidates_own_mechanism(
    oneharness_bin: str,
) -> None:
    """A controlled turn's mechanism binds to the candidate serving it, not to the chain.

    Through oneharness 0.7.1 the validator counted the selected spec list and refused
    anything longer than one entry; through 0.7.2 it required one mechanism across
    every candidate, which the chains here can never satisfy because they genuinely mix
    claude-code (`claude-control-request`) with codex (`codex-app-server`). 0.8.0 binds
    late instead, so the committed chain is planned rather than refused and each
    candidate carries the mechanism its own family declares. Asserting per family is
    what would catch a regression to one mechanism imposed on all of them.
    """
    planned = _plan(
        oneharness_bin, WORKER_CONFIG, "--session", "e2e-control", "--control", "--stream"
    )

    assert planned.refusal is None, (
        "the committed chain mixes control mechanisms, which no longer refuses it: "
        f"{planned.refusal}"
    )
    families = {candidate.identity.split(":")[0] for candidate in planned.candidates}
    assert families == {"claude-code", "codex"}, (
        f"this test measures a chain that MIXES mechanisms; it planned {sorted(families)}, "
        "so re-derive it rather than weakening the claim"
    )
    for candidate in planned.candidates:
        if candidate.identity.startswith("claude-code"):
            assert candidate.flag_value(CONTROL_ARGV[0]) == CONTROL_ARGV[1], (
                f"{candidate.identity} declares claude-control-request and must be planned "
                f"with the stdin channel that mechanism uses: {candidate.argv}"
            )
        else:
            assert candidate.argv == CODEX_CONTROL_ARGV, (
                f"{candidate.identity} declares codex-app-server and must be planned as "
                f"that server rather than as a one-shot turn: {candidate.argv}"
            )
        assert PROMPT not in candidate.argv, (
            f"a controlled turn delivers its prompt over the channel, not on argv: {candidate.argv}"
        )


def test_a_multi_identity_chain_takes_control_and_streaming_together(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """Both concerns on one chain of several identities — the thing 0.7.2 unblocked."""
    config = tmp_path / "single-family-chain.toml"
    family = _single_family_chain(WORKER_CONFIG, config)

    planned = _plan(oneharness_bin, config, "--session", "e2e-control", "--control", "--stream")

    assert planned.refusal is None, planned.refusal
    identities = [candidate.identity for candidate in planned.candidates]
    assert len(identities) > 1, (
        f"the chain collapsed to {identities}; control must not narrow a fallback chain"
    )
    assert all(identity.startswith(family) for identity in identities), identities

    # Streaming is a property of the whole chain; the control channel is rendered onto
    # the candidate that would hold it, which is the head of a fallback chain. Assert
    # each where it belongs rather than folding them together — the point being proven
    # is that one turn carries both, not that they travel as a pair.
    for candidate in planned.candidates:
        assert candidate.flag_value(STREAM_ARGV[0]) == STREAM_ARGV[1], (
            f"{candidate.identity} was planned unstreamed: {candidate.argv}"
        )
    holder = planned.candidates[0]
    assert holder.flag_value(CONTROL_ARGV[0]) == CONTROL_ARGV[1], (
        f"the chain's first candidate was planned with no control channel: {holder.argv}"
    )
    assert PROMPT not in holder.argv, (
        "a controlled turn delivers its prompt over the channel, not on argv; a prompt "
        f"back on argv means the control request never bound: {holder.argv}"
    )


def test_streaming_alone_asks_for_no_control_channel(tmp_path: Path, oneharness_bin: str) -> None:
    """The two are separable in the other direction too, which is why one is not a lever
    for the other: a streamed turn opens no control channel and carries the prompt on argv."""
    config = tmp_path / "single-family-chain.toml"
    _single_family_chain(WORKER_CONFIG, config)

    planned = _plan(oneharness_bin, config, "--stream")

    assert planned.refusal is None, planned.refusal
    for candidate in planned.candidates:
        assert candidate.flag_value(CONTROL_ARGV[0]) is None, (
            f"streaming alone requested a control channel: {candidate.argv}"
        )
        assert candidate.flag_value(STREAM_ARGV[0]) == STREAM_ARGV[1], candidate.argv
        assert PROMPT in candidate.argv, (
            f"a streamed turn without control keeps its prompt on argv: {candidate.argv}"
        )


def test_no_graph_member_disables_streaming(oneagentgraph_bin: str) -> None:
    """`stream: false` was the rejected workaround for the control refusal above.

    It traded the per-turn visibility a planner supervises with for a problem it had
    nothing to do with. Every member takes oneagentgraph's default of `true`, and a
    member that opts out has to justify itself here rather than appear quietly.

    `validate` is the public entry point for loading these graphs and is driven first,
    but it reports only that the members are well formed. No shipped verb renders a
    member's *effective* settings, so the absent key is read from the declaration —
    which is also the only thing a reviewer of a re-added `stream: false` would see.
    """
    for graph in GRAPHS:
        loaded = subprocess.run(
            [oneagentgraph_bin, "validate", str(graph)],
            text=True,
            capture_output=True,
            timeout=e2e_timeout(30),
        )
        assert loaded.returncode == 0, f"{graph} no longer loads: {loaded.stderr}"

        for number, raw in enumerate(graph.read_text(encoding="utf-8").splitlines(), start=1):
            key, _, _ = raw.strip().partition(":")
            assert key != "stream", (
                f"{graph}:{number} declares a `stream` key; disabling streaming is not an "
                "acceptable response to a turn-control failure — fix the chain's composition"
            )
