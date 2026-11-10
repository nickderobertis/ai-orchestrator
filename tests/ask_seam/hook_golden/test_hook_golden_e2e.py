"""The criteria bar's outcomes over a fixed corpus, held to one golden file in both shapes.

`config/onemessagebus.yaml` declares `orchestrator/envelope_review.py` on the planner
channel's `replies` queue as a **hook**: a pass prints the envelope back byte for byte,
which the bus reads as a keep. It used to be a **validator**, which onemessagebus has
deprecated and will remove. Moving the declaration must not move the bar — what it passes,
what it refuses and the words it refuses in — so `envelope-outcomes.golden.json` beside this
module records each envelope of a fixed corpus with its outcome and, for a refusal, the
reason, and these journeys hold both declarations to it:

* the **validator configuration** is the committed file with its `hooks:` entry spelled as
  the validator it replaced — `validators:`, the same entry and `kind: command` — which is
  exactly what this repository declared before; the **hook configuration** is the committed
  file itself;
* each runs the corpus through the real `onemessagebus validate` and through the real
  `just channel-reply` on a run `just orchestrate` launched under that configuration's
  `--bus-config`, the engine's `onepipeline reply` judging it before anything is appended;
* a passing envelope through the hook is kept rather than changed — the bus's own `keep`,
  its records carrying the envelope as offered, and a receipt with no `hooked` key;
* a pass the validator recorded is honoured by the hook, spending no second judged turn,
  while a moved bar fingerprint spends one again.

The removal round holds the hook alone to the same golden file once validators are gone.
Only the paid model is doubled, at the `oneharness` seam, exactly as
`tests/ask_seam/channel_reply/test_channel_reply_e2e.py` doubles it; the structural
refusal resolves its repositories through a scratch `onevcs` registry, exactly as
`tests/ask_seam/structural_reply/test_structural_reply_e2e.py` does.

llmlint: ignore-file[expensive_tests_stay_behind_their_own_edge] This *is* the edge: the
journeys spend real launches, and this directory is an Nx project of its own keyed on
`askSeamHookGolden`, which `tests/ask_seam/AGENTS.md` states as the settled design.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Literal, NamedTuple, cast

import pytest
import short_state
from project_fixtures import helper, project_from_plan
from scratch_identity import registered
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: The committed hook configuration, which every launch here would record by default.
HOOK_CONFIG = REPO_ROOT / "config" / "onemessagebus.yaml"

#: The golden file: the corpus, and each envelope's outcome and refusal reason.
GOLDEN = REPO_ROOT / "tests" / "ask_seam" / "hook_golden" / "envelope-outcomes.golden.json"

#: The one span of a golden reason that is not fixed: the structural refusal names the
#: releasing dependency's origin, which lives under each journey's own scratch registry.
#: Every other byte of every reason is compared as recorded.
REGISTRY_TOKEN = "{registry}"

#: The bus this checkout pins.
BUS = str(REPO_ROOT / ".venv" / "bin" / "onemessagebus")

#: The stand-ins for the paid model, exactly as the channel-reply journeys use them.
FAKE_BACKEND = helper("fake_backend.py")
FAKE_CODEX = helper("fake_codex.py")
PAID_PROVIDER_GUARD = helper("no-paid-provider")

#: The launcher and dispatch identities the enclosing dispatch would otherwise hand in.
INHERITED_ENVIRONMENT = (
    "ONEPIPELINE_LAUNCHER",
    "ONEPIPELINE_LAUNCHER_SESSION",
    "ONEPIPELINE_RUN_ID",
    "ONEPIPELINE_CHANNEL_ASKER",
    "ONEPIPELINE_REPLY_TIMEOUT_SECONDS",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_SESSION_ID",
    "CODEX_THREAD_ID",
    "CODEX_SESSION_ID",
)


class ReleaseTarget(NamedTuple):
    """One target a scratch producer's `release-targets.toml` declares."""

    name: str
    artifact: str


#: The wheel this host installs, which the scratch producer declares, so the structural
#: rule about adopting `fast` behind a releasing dependency has a release to name.
WHEEL = ReleaseTarget(name="pypi", artifact="pypi:onepipeline-cli")

#: What the doubled reviewer answers a judged turn with, per the corpus's `judge` key.
VERDICTS = {
    "passes": {"passes": True, "findings": []},
    "refuses": {
        "passes": False,
        "findings": [
            {
                "criterion": "- The finished tree carries an assertion whose subject is that "
                "behaviour",
                "why": "names no journey a reader could run to watch that assertion fail",
            }
        ],
    },
}

#: What `just channel-reply` exits with for every refusal.
REPLY_REFUSED = 2

#: The engine's framing of a hook's refusal on the reply's stderr: this prefix, the hook's
#: reason byte for byte, then one newline of the engine's own. onepipeline's
#: `docs/contract-divergences.md` entry 64 states a hook's refusal is "refused at exit 2 in
#: the hook's own words, wrapped exactly as a validator's are"; this is that wrapping.
REFUSAL_PREFIX = "onepipeline: refused: "
REFUSAL_SUFFIX = "\n"

#: A `subscribe --until` predicate no record of any queue carries, so the verb prints the
#: whole log and then exits 1 when its `--timeout` passes with nothing admitted
#: (onemessagebus's `docs/cli.md`): the bus's own way to read a log as it stands.
ADMITS_NOTHING = '{"field": "hook-golden-admits-nothing", "present": true}'

#: The fields a verdict half may carry, which the run's journal records it by.
VERDICT_FIELDS = ("completion", "message", "reason")

#: The two shapes the same check is declared in.
Shape = Literal["validator", "hook"]
SHAPES: tuple[Shape, ...] = ("validator", "hook")


class Entry(NamedTuple):
    """One envelope of the corpus and what the bar answers it."""

    name: str
    envelope: dict[str, Any]
    #: What the doubled reviewer answers, for an envelope that reaches the judged tier.
    judge: str
    #: Which run a channel reply sends it to: a verdict alone needs the settled run, since
    #: on the gated one no question is pending for it to answer.
    run: Literal["gated", "settled"]
    outcome: Literal["pass", "refuse"]
    reason: str | None


def _corpus(registry: Path | None = None) -> list[Entry]:
    """The golden file's entries, in its order, each reason naming `registry` if given."""
    # `cast` rather than a validating read: the golden file is this module's own fixture,
    # and each member is narrowed by the `Entry` it is read into.
    recorded = cast(list[dict[str, Any]], json.loads(GOLDEN.read_text(encoding="utf-8")))
    return [
        Entry(
            name=entry["name"],
            envelope=entry["envelope"],
            judge=entry["judge"],
            run=entry["run"],
            outcome=entry["outcome"],
            reason=(
                None
                if entry.get("reason") is None
                else str(entry["reason"]).replace(REGISTRY_TOKEN, str(registry))
            ),
        )
        for entry in recorded
    ]


#: The committed configuration's pass-cache directory, as the line declaring it reads.
COMMITTED_CACHE = "      dir: .cache/envelope-passes\n"


def _validator_config(hook_config: Path, destination: Path) -> Path:
    """`hook_config` with its hook entry spelled as the validator it replaced.

    `hooks:` becomes `validators:` and the entry gains `kind: command`, which is the whole
    difference between the two declarations; everything else, the argv and the cache
    included, is byte for byte `hook_config`'s.
    """
    text = hook_config.read_text(encoding="utf-8")
    assert text.count("\nhooks:\n") == 1, f"{hook_config} declares no single `hooks:` block"
    entry = "    when: {carries: commands}\n    command: [scripts/envelope-review.sh]\n"
    assert text.count(entry) == 1, f"{hook_config}'s hook entry is not the one this reads"
    validator = entry.replace("    command:", "    kind: command\n    command:")
    text = text.replace("\nhooks:\n", "\nvalidators:\n").replace(entry, validator)
    destination.write_text(text, encoding="utf-8")
    return destination


def _moved_bar(hook_config: Path, destination: Path) -> Path:
    """The committed hook configuration with a bar fingerprint that prints something else."""
    text = hook_config.read_text(encoding="utf-8")
    fingerprint = (
        "      bar_fingerprint: [uv, run, python, -m, orchestrator.envelope_review, "
        "--bar-fingerprint]\n"
    )
    assert text.count(fingerprint) == 1, f"{hook_config}'s bar fingerprint is not the one read"
    destination.write_text(
        text.replace(fingerprint, "      bar_fingerprint: [printf, a-moved-bar]\n"),
        encoding="utf-8",
    )
    return destination


def _uncached(config: Path, cache: Path, destination: Path) -> Path:
    """`config` with its pass cache kept in `cache`, every other byte as it stands.

    The corpus is fixed, so under the committed cache directory its passing envelopes are
    answered from the records an earlier run of this suite left, and the check never runs:
    a hook that printed nothing — a drop — would then still read as a pass. A directory of
    the journey's own makes every pass run the command it is about.
    """
    text = config.read_text(encoding="utf-8")
    assert text.count(COMMITTED_CACHE) == 1, f"{config}'s pass cache is not the one this reads"
    destination.write_text(text.replace(COMMITTED_CACHE, f"      dir: {cache}\n"), encoding="utf-8")
    return destination


@pytest.fixture
def configs(tmp_path: Path) -> dict[Shape, Path]:
    """Each shape's configuration: the committed file, and the validator it replaced.

    Both with a pass cache of this journey's own, which is the one byte span either differs
    from what it stands for in; see `_uncached`.
    """
    hook = _uncached(HOOK_CONFIG, tmp_path / "passes", tmp_path / "hook.onemessagebus.yaml")
    return {
        "validator": _validator_config(hook, tmp_path / "validator.onemessagebus.yaml"),
        "hook": hook,
    }


@pytest.fixture
def registry(tmp_path: Path) -> Path:
    """A scratch `onevcs` registry whose `library` declares a release and `service` does not."""
    if shutil.which("just") is None:
        pytest.skip("just is not installed")
    root = tmp_path / "registry"
    registered(root, ["library", "service"], declarations={"library": _declaring(WHEEL)})
    return root


@pytest.fixture
def environment(tmp_path: Path, registry: Path) -> dict[str, str]:
    """A sandboxed environment reading the scratch registry, its paid model doubled."""
    built = dict(os.environ)
    for name in INHERITED_ENVIRONMENT:
        built.pop(name, None)
    built.pop("VIRTUAL_ENV", None)
    built["CLAUDE_CODE_SESSION_ID"] = "e2e-hook-golden"
    built["ONEPIPELINE_RUNS_DIR"] = str(tmp_path / "runs")
    built["ONEVCS_HOME"] = str(registry / "onevcs")
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    built["ONEAGENTGRAPH_ONEHARNESS_BIN"] = str(FAKE_BACKEND)
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    built["ONEHARNESS_BIN_CODEX"] = str(FAKE_CODEX)
    # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
    built["PATH"] = f"{PAID_PROVIDER_GUARD}{os.pathsep}{built['PATH']}"
    built["FAKE_CODEX_ATTEMPT_LOG"] = str(tmp_path / "review-launches")
    built["XDG_STATE_HOME"] = str(short_state.state_home(tmp_path))
    return built


def _declaring(*declared: ReleaseTarget) -> str:
    """A `release-targets.toml` declaring each target and nothing a probe runs."""
    rows = "".join(
        f'\n[[target]]\nid = "{target.artifact}"\nname = "{target.name}"\n'
        f'what = "The {target.name} target, as a scratch producer declares it."\n'
        f"published_by = \"Nothing: a journey's stand-in for a producer's declaration.\"\n"
        for target in declared
    )
    return f'schema_version = 3\nprobe = "scripts/release-probe.sh"\n{rows}'


def _judging(environment: dict[str, str], judge: str) -> dict[str, str]:
    """`environment`, its doubled reviewer answering every judged turn as `judge` says."""
    return {**environment, "FAKE_CODEX_ANSWERS": json.dumps([json.dumps(VERDICTS[judge])])}


def _judged_turns(environment: dict[str, str]) -> int:
    """How many review turns the doubled provider has been asked for so far."""
    log = Path(environment["FAKE_CODEX_ATTEMPT_LOG"])
    return len(log.read_text(encoding="utf-8").splitlines()) if log.is_file() else 0


def _validate(
    config: Path, channel: Path, envelope: dict[str, Any], environment: dict[str, str]
) -> dict[str, Any]:
    """The bus's dry run of `envelope` on `replies`: its verdict, outcome and records."""
    channel.mkdir(parents=True, exist_ok=True)
    judged = subprocess.run(
        [BUS, "validate", "replies", "--config", str(config), "--transport-dir", str(channel)],
        input=json.dumps(envelope),
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(300),
        check=False,
    )
    assert judged.returncode in (0, 1), judged.stderr
    # `cast` rather than a validating read: the bus owns this shape, and each journey
    # asserts the members it is about.
    return cast(dict[str, Any], json.loads(judged.stdout))


def _just(
    *arguments: str, environment: dict[str, str], stdin: str | None = None, seconds: float = 180
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["just", *arguments],
        cwd=REPO_ROOT,
        env=environment,
        input=stdin,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(seconds),
        check=False,
    )


def _task(what: str) -> str:
    return f"## What\n{what}\n\n## Why\nHold the run.\n\n## Acceptance criteria\n- Done."


def _launch(
    tmp_path: Path,
    environment: dict[str, str],
    run: str,
    config: Path,
    tasks: list[dict[str, object]],
) -> None:
    """Launch `tasks` as `run` through the real `just orchestrate` under `config`."""
    plan = tmp_path / f"{run}.plan.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "goal": {"text": "hold a channel open for the golden corpus"},
                "name": run,
                "tasks": tasks,
            }
        ),
        encoding="utf-8",
    )
    launch = _just(
        "orchestrate",
        project_from_plan(plan),
        "--dag-graph",
        "off",
        "--bus-config",
        str(config),
        "--success-hook=",
        "--failure-hook=",
        environment=environment,
        seconds=600,
    )
    assert launch.returncode == 0, f"the launch failed:\n{launch.stdout}\n{launch.stderr}"


class Runs(NamedTuple):
    """The two runs one shape's channel replies are sent to, by the corpus's `run` key."""

    #: A run whose `work` node waits behind a human gate nobody attests, which nothing drives.
    gated: str
    #: A run that settled complete and whose driver has exited, so a verdict closes it.
    settled: str

    def named(self, which: Literal["gated", "settled"]) -> str:
        """The run an entry whose `run` key is `which` is sent to."""
        return self.gated if which == "gated" else self.settled


@pytest.fixture
def runs(
    tmp_path: Path,
    environment: dict[str, str],
    configs: dict[Shape, Path],
    request: pytest.FixtureRequest,
) -> Iterator[dict[Shape, Runs]]:
    """Per shape, a gated run nothing drives and a settled run whose driver has exited.

    The gated run's `work` node waits behind a human gate nobody attests, so nothing is
    dispatched; the settled run's one node settles without a dispatch, so its channel takes a
    verdict with nothing pending.
    """
    named = re.sub(r"[^A-Za-z0-9]+", "-", request.node.name)[-20:].strip("-")
    launched: dict[Shape, Runs] = {}
    try:
        for shape in SHAPES:
            gated = f"golden-{os.getpid()}-{shape}-gated-{named}"
            settled = f"golden-{os.getpid()}-{shape}-settled-{named}"
            launched[shape] = Runs(gated=gated, settled=settled)
            _launch(
                tmp_path,
                environment,
                gated,
                configs[shape],
                [
                    {"id": "gate", "kind": "human", "task": _task("Approve.")},
                    {
                        "id": "work",
                        "persona": "engineer",
                        "deps": ["gate"],
                        "task": _task("Report."),
                    },
                ],
            )
            _launch(
                tmp_path,
                environment,
                settled,
                configs[shape],
                [
                    {
                        "id": "only",
                        "task": "Report.\n\n## Acceptance criteria\n- Reported.",
                        "expects_no_diff": True,
                    }
                ],
            )
        yield launched
    finally:
        for both in launched.values():
            for run in both:
                _just("stop", run, environment=environment, seconds=60)


def _carries_the_offer(records: list[dict[str, Any]], envelope: dict[str, Any]) -> None:
    """Every record an offer appends carries the envelope exactly as it was offered.

    The planner-channel layout routes a reply's commands to `commands` and keeps a verdict
    half on `replies` with the whole envelope as its `reply`, so each is read for what it
    holds; a hook that changed one byte of the envelope fails one of them.
    """
    assert records, "a passing envelope appended nothing"
    for appended in records:
        record = appended["record"]
        if appended["queue"] == "replies":
            assert record["reply"] == envelope, (record, envelope)
        else:
            assert appended["queue"] == "commands", appended
            assert record["commands"] == envelope["commands"], (record, envelope)
            assert record.get("author", envelope.get("author")) == envelope.get("author"), record


def _log(environment: dict[str, str], run: str, queue: str) -> list[dict[str, Any]]:
    """Every record `run`'s `queue` holds, oldest first, read through `subscribe`."""
    read = subprocess.run(
        [
            BUS,
            "subscribe",
            queue,
            "--until",
            ADMITS_NOTHING,
            "--timeout",
            "1",
            "--config",
            str(HOOK_CONFIG),
            "--transport-dir",
            str(Path(environment["ONEPIPELINE_RUNS_DIR"]) / run / "channel"),
        ],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )
    assert read.returncode == 1 and "no record --until admits" in read.stderr, read
    # `cast` rather than a validating read: each line is the bus's `{position, record}`, and
    # the members read off a record are asserted where they are read.
    return [cast(dict[str, Any], json.loads(line)["record"]) for line in read.stdout.splitlines()]


def _journal(environment: dict[str, str], run: str) -> list[dict[str, Any]]:
    """The run's own journal, oldest first, as the channel-reply journeys read it."""
    written = Path(environment["ONEPIPELINE_RUNS_DIR"]) / run / "events.jsonl"
    # `cast` rather than a validating read: the engine owns its journal, and each member
    # read off it is asserted where it is read.
    return [
        cast(dict[str, Any], json.loads(line))
        for line in written.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


class Persisted(NamedTuple):
    """What a run holds of its channel and journal at one moment, to diff a send against."""

    replies: list[dict[str, Any]]
    commands: list[dict[str, Any]]
    journal: list[dict[str, Any]]


def _persisted(environment: dict[str, str], run: str) -> Persisted:
    return Persisted(
        replies=_log(environment, run, "replies"),
        commands=_log(environment, run, "commands"),
        journal=_journal(environment, run),
    )


def _persisted_the_offer(
    before: Persisted, after: Persisted, envelope: dict[str, Any], name: str
) -> None:
    """What one send left on the run carries the envelope exactly as it was offered.

    Each place a passing envelope is recorded is held to the offer, so a hook that changed
    one byte of the envelope it kept fails at whichever the engine wrote:

    * every record appended to `replies` holds the whole envelope as its `reply`;
    * every record appended to `commands` holds the envelope's commands under the author
      the engine stamps, the planner where the envelope names none;
    * the journal's `edit-committed` records the send added carry the envelope's commands,
      one each, in order — the record a run nothing drives keeps of an edit the reply
      applied itself — and a verdict's `planner-replied` carries each verdict field the
      envelope states.

    Nothing outside that is read: the engine owns its other members, its stamps among them.
    """
    replies = after.replies[len(before.replies) :]
    commands = after.commands[len(before.commands) :]
    journal = after.journal[len(before.journal) :]
    assert after.replies[: len(before.replies)] == before.replies, name
    assert after.commands[: len(before.commands)] == before.commands, name
    for record in replies:
        assert record["reply"] == envelope, (name, record, envelope)
    author = envelope.get("author", "planner")
    for record in commands:
        assert (record["author"], record["commands"]) == (author, envelope["commands"]), (
            name,
            record,
            envelope,
        )
    committed = [
        event["payload"]["command"] for event in journal if event.get("kind") == "edit-committed"
    ]
    assert committed == envelope.get("commands", []), (name, committed, envelope)
    stated = {field: envelope[field] for field in VERDICT_FIELDS if field in envelope}
    replied = [event["payload"] for event in journal if event.get("kind") == "planner-replied"]
    if stated:
        (verdict,) = replied
        assert {field: verdict.get(field) for field in stated} == stated, (name, verdict)
    else:
        assert not replied, (name, replied)


def _refusal_reason(stderr: str, name: str) -> str:
    """The hook's reason out of the reply's stderr, less the engine's framing alone."""
    assert stderr.startswith(REFUSAL_PREFIX) and stderr.endswith(REFUSAL_SUFFIX), (name, stderr)
    return stderr[len(REFUSAL_PREFIX) : -len(REFUSAL_SUFFIX)]


def test_the_corpus_states_every_shape_the_golden_file_is_meant_to_hold() -> None:
    """The six shapes the removal round holds the hook to are all in the file, once each."""
    assert [entry.name for entry in _corpus()] == [
        "add-whole-task-passes",
        "amend-deterministic-refusal",
        "add-judged-refusal",
        "add-structural-refusal",
        "note-carrying-a-criterion",
        "verdict-without-commands",
    ]
    for entry in _corpus():
        assert (entry.outcome == "refuse") == (entry.reason is not None), entry.name
        assert entry.judge in VERDICTS, entry.name


@pytest.mark.parametrize("shape", SHAPES)
def test_onemessagebus_validate_answers_the_golden_outcome_in_either_shape(
    shape: Shape,
    configs: dict[Shape, Path],
    environment: dict[str, str],
    registry: Path,
    tmp_path: Path,
) -> None:
    """The bus's own dry run answers each envelope's golden outcome, in its golden words.

    Through the hook, a pass is the bus's `keep` — the envelope printed back byte for byte —
    never a change or a drop, and the records it would append carry the envelope as offered.
    """
    for entry in _corpus(registry):
        judged = _validate(
            configs[shape], tmp_path / "channel", entry.envelope, _judging(environment, entry.judge)
        )

        assert judged["verdict"] == entry.outcome, (entry.name, judged)
        assert judged.get("reason") == entry.reason, (entry.name, judged)
        if entry.outcome == "pass":
            assert judged["outcome"] == "keep", (entry.name, judged)
            _carries_the_offer(judged["records"], entry.envelope)
        else:
            assert judged["records"] == [], (entry.name, judged)


@pytest.mark.parametrize("shape", SHAPES)
def test_channel_reply_answers_the_golden_outcome_in_either_shape(
    shape: Shape,
    runs: dict[Shape, Runs],
    environment: dict[str, str],
    registry: Path,
) -> None:
    """The manager's reply recipe on the adopted engine answers each golden outcome.

    A refusal exits refused, and the reason inside the engine's framing is the golden
    reason byte for byte, with nothing of it persisted. A pass is taken, with no `hooked`
    key on the receipt, and every record the send left on the run carries the envelope as
    it was offered (`_persisted_the_offer`), because the hook kept it rather than changing
    it.
    """
    for entry in _corpus(registry):
        run = runs[shape].named(entry.run)
        before = _persisted(environment, run)
        sent = _just(
            "channel-reply",
            run,
            environment=_judging(environment, entry.judge),
            stdin=json.dumps(entry.envelope),
            seconds=300,
        )

        if entry.outcome == "refuse":
            assert entry.reason is not None
            assert sent.returncode == REPLY_REFUSED, (entry.name, sent.stdout, sent.stderr)
            assert sent.stdout == "", (entry.name, sent.stdout)
            assert _refusal_reason(sent.stderr, entry.name) == entry.reason, (
                entry.name,
                sent.stderr,
            )
            assert _persisted(environment, run) == before, entry.name
            continue
        assert sent.returncode == 0, (entry.name, sent.stdout, sent.stderr)
        (printed,) = sent.stdout.splitlines()
        receipt = json.loads(printed)
        assert receipt["state"] in ("applied", "delivered"), (entry.name, receipt)
        assert "hooked" not in receipt, (entry.name, receipt)
        _persisted_the_offer(before, _persisted(environment, run), entry.envelope, entry.name)


def test_a_pass_the_validator_recorded_is_honoured_by_the_hook_until_the_bar_moves(
    environment: dict[str, str], tmp_path: Path
) -> None:
    """One judged turn per envelope across the swap; a moved bar fingerprint asks again.

    The passing whole task of the corpus, made new by a token no earlier send carried, so
    nothing holds a pass for it yet. The validator the committed file replaced spends one
    judged turn and records its pass under the committed cache directory; the committed
    file itself — the same argv and cache directory, declared as a hook — reads that record
    as a keep and asks the doubled reviewer nothing. Under a fingerprint that prints
    something else, the same envelope is judged again, and kept by the hook it ran.
    """
    (passing,) = [entry for entry in _corpus() if entry.name == "add-whole-task-passes"]
    envelope = json.loads(json.dumps(passing.envelope))
    envelope["commands"][0]["node"]["task"] = envelope["commands"][0]["node"]["task"].replace(
        "the test that drives it", f"the test that drives it ({uuid.uuid4().hex})"
    )
    judging = _judging(environment, passing.judge)
    channel = tmp_path / "channel"

    validator = _validator_config(HOOK_CONFIG, tmp_path / "validator.onemessagebus.yaml")

    first = _validate(validator, channel, envelope, judging)
    assert (first["verdict"], _judged_turns(judging)) == ("pass", 1), first

    kept = _validate(HOOK_CONFIG, channel, envelope, judging)
    assert (kept["verdict"], kept["outcome"]) == ("pass", "keep"), kept
    assert _judged_turns(judging) == 1, (
        "the hook re-judged an envelope whose pass the validator had already recorded"
    )

    moved = _moved_bar(HOOK_CONFIG, tmp_path / "moved.onemessagebus.yaml")
    again = _validate(moved, channel, envelope, judging)
    assert (again["verdict"], again["outcome"]) == ("pass", "keep"), again
    assert _judged_turns(judging) == 2, "a moved bar fingerprint passed from the old record"
