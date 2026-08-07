"""What one launched orchestrator recorded so a fresh driver can take its place.

An orphaned run — its driver dead, its ledger intact — used to have no way forward:
`just orchestrate` refuses a run directory that already exists, so the only move left
was a *new* run id, which strands the journal, the round ledger, and every publication
anchor the old id owned. Adoption is the other answer. A fresh orchestrator process
attaches to the same run directory, re-registers itself as the owner, and drives the
next round from the journal-folded plan of record.

Nothing here decides *whether* a run may be adopted — the ownership and liveness gate
is `dispatch`'s, beside the launch it mirrors. This module owns the one thing an
adopting process genuinely cannot reconstruct: the parameters the dead driver was
launched with. They are written once at launch and re-read at adoption, so the second
driver runs the same plan, against the same runs root, with the same per-side routing —
which harness each conversation side runs on, and which model it runs there — rather
than whatever the adopting command line happened to say.

The record is a file in a run directory, which makes it a trust boundary like any
other: every field is revalidated on the way back in, and a record that fails is a
refusal to adopt rather than a launch on half-understood parameters.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from pathlib import Path
from typing import Any, NotRequired, TypedDict

from .cli_contract import ONEHARNESS_MODES
from .config import ConfigError
from .coordination import atomic_json
from .runs import load_mapping

#: Where a launch leaves the parameters an adoption replays, beside the orchestrator's
#: own status and effective config rather than in the planner-facing `launch.json`:
#: this is harness-internal plumbing, and `launch.json` is the record a planner reads.
RELAUNCH_RECORD_NAME = "relaunch.json"
#: Version 2 added each side's ``model`` beside the harness identity it must be paired
#: with. Every version this build can replay in full is listed below and normalized to
#: the current one on the way back out.
RELAUNCH_SCHEMA_VERSION = 2
#: Version 1 is still adoptable: the fields v2 added are optional, and a v1 record's
#: silence about them is the same answer a v2 record that names no model gives. Reading
#: it is what keeps a run launched by the previous build out of the one state adoption
#: exists to rescue it from. The refusal is kept in the other direction — a v1 build
#: handed a v2 record stops rather than driving a run onto a model it cannot see, which
#: is why this is a bump rather than two more optional fields at version 1.
SUPPORTED_RELAUNCH_SCHEMA_VERSIONS = frozenset({1, RELAUNCH_SCHEMA_VERSION})


class RelaunchRecord(TypedDict):
    """The launch parameters one run can be driven again from.

    ``adoptions`` counts how many fresh drivers this run has had. It is what names
    each adoption's own conversation and preserved artifacts, so a second adoption
    cannot overwrite the first one's evidence — and it is why the record is rewritten
    on every adoption rather than only at launch.
    """

    schema_version: int
    plan: str
    base_path: str
    onejudge_bin: str
    cwd: str
    max_turns: int
    turn_timeout: int
    heartbeat_interval: float
    acknowledge_concurrent: bool
    oneharness_mode: str
    adoptions: int
    round_budget: NotRequired[float]
    worker_harness: NotRequired[str]
    judge_harness: NotRequired[str]
    #: The model half of each side's routing, recorded because a driver that replayed
    #: only the identity would put every judge turn back on the tier that side's config
    #: pins — silently, which is the substitution `--judge-model` exists to prevent.
    worker_model: NotRequired[str]
    judge_model: NotRequired[str]
    #: The orchestrator's own provider block, replayed verbatim. `Any` because this
    #: is onejudge's schema rather than one this repository owns — a `command` list,
    #: an `oneharness` bin, and whatever a future provider kind carries — and the
    #: launch path validates the shape it actually needs before spawning anything.
    skill_provider: NotRequired[dict[str, Any]]


def relaunch_path(run_dir: Path) -> Path:
    return run_dir / "orchestrator" / RELAUNCH_RECORD_NAME


def write_relaunch_record(run_dir: Path, record: RelaunchRecord) -> Path:
    """Persist the parameters an adoption of this run replays."""
    path = relaunch_path(run_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(path, record)
    return path


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ConfigError(f"relaunch record field {field!r} must be a non-empty, non-NUL string")
    return value


def _count(value: object, field: str) -> int:
    """One recorded non-negative count, for a value zero is a real answer to."""
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ConfigError(f"relaunch record field {field!r} must be a non-negative integer")
    return value


def _limit(value: object, field: str) -> int:
    """One recorded execution limit, which zero is never a usable value of.

    A zero turn cap or a zero timeout does not start a driver that does less work —
    it starts one that cannot run a turn at all, which is a run adopted into a state
    it can never leave.
    """
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ConfigError(f"relaunch record field {field!r} must be a positive integer")
    return value


def _flag(value: object, field: str) -> bool:
    """One recorded boolean, refusing anything merely truthy.

    Coercing would accept `"false"`, `0.0`, and `[]` as answers to a question that
    decides whether an adoption proceeds past the concurrency guard. A record that
    does not say `true` or `false` has not answered it.
    """
    if not isinstance(value, bool):
        raise ConfigError(f"relaunch record field {field!r} must be a JSON boolean")
    return value


def _mode(value: object) -> str:
    """One recorded approval mode, checked against the modes this harness has.

    A mode nothing configures does not degrade — oneharness refuses it — so an
    adoption started on one is a driver that dies on its first turn, which is the
    state adoption exists to get a run out of.
    """
    if not isinstance(value, str) or value not in ONEHARNESS_MODES:
        raise ConfigError(
            "relaunch record field 'oneharness_mode' must be one of "
            + ", ".join(sorted(ONEHARNESS_MODES))
        )
    return value


def _seconds(value: object, field: str) -> float:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(value)
        or value <= 0
    ):
        raise ConfigError(f"relaunch record field {field!r} must be a positive finite number")
    return float(value)


# llmlint: ignore[changed_behavior_has_e2e] The observable refusal — a run with no
# relaunch record to replay — runs through the real `just orchestrate --adopt` in
# tests/e2e/test_run_adoption_e2e.py. What stays unit-proven is the *shape* of a
# record a launch cannot write: every branch below needs the file hand-edited into a
# state no launch produces, so a journey per branch would drive one `if` through a
# real run and prove nothing about adoption.
def read_relaunch_record(run_dir: Path) -> RelaunchRecord:
    """Re-read one run's launch parameters, refusing anything unusable.

    Every field is checked rather than trusted: this decides how a *second* live
    orchestrator process is started against work the first one left behind, so a
    record that has been truncated, hand-edited, or written by a build that spelled a
    field differently must stop the adoption rather than start a driver on values half
    of which came from a default.
    """
    path = relaunch_path(run_dir)
    if not path.is_file():
        raise ConfigError(
            f"no relaunch record at {path}; this run was launched before adoption existed "
            "and cannot be adopted"
        )
    raw = load_mapping(path)
    if raw.get("schema_version") not in SUPPORTED_RELAUNCH_SCHEMA_VERSIONS:
        replayable = ", ".join(
            str(version) for version in sorted(SUPPORTED_RELAUNCH_SCHEMA_VERSIONS)
        )
        raise ConfigError(
            f"relaunch record at {path} is schema version {raw.get('schema_version')!r}, "
            f"not one this build replays ({replayable})"
        )
    record: RelaunchRecord = {
        "schema_version": RELAUNCH_SCHEMA_VERSION,
        "plan": _text(raw.get("plan"), "plan"),
        "base_path": _text(raw.get("base_path"), "base_path"),
        "onejudge_bin": _text(raw.get("onejudge_bin"), "onejudge_bin"),
        "cwd": _text(raw.get("cwd"), "cwd"),
        "max_turns": _limit(raw.get("max_turns"), "max_turns"),
        "turn_timeout": _limit(raw.get("turn_timeout"), "turn_timeout"),
        "heartbeat_interval": _seconds(raw.get("heartbeat_interval"), "heartbeat_interval"),
        "acknowledge_concurrent": _flag(
            raw.get("acknowledge_concurrent"), "acknowledge_concurrent"
        ),
        "oneharness_mode": _mode(raw.get("oneharness_mode")),
        "adoptions": _count(raw.get("adoptions"), "adoptions"),
    }
    if (budget := raw.get("round_budget")) is not None:
        record["round_budget"] = _seconds(budget, "round_budget")
    if (worker := raw.get("worker_harness")) is not None:
        record["worker_harness"] = _text(worker, "worker_harness")
    if (judge := raw.get("judge_harness")) is not None:
        record["judge_harness"] = _text(judge, "judge_harness")
    # Only the shape is settled here; the *pairing* rule — a model is refused unless
    # its side's identity names one harness family — is `harnesses.side_override_env`'s,
    # which the adoption runs over these values exactly as the launch ran it over the
    # command line. A second copy of that judgement here would be one that drifts.
    if (worker_model := raw.get("worker_model")) is not None:
        record["worker_model"] = _text(worker_model, "worker_model")
    if (judge_model := raw.get("judge_model")) is not None:
        record["judge_model"] = _text(judge_model, "judge_model")
    skill = raw.get("skill_provider")
    if skill is not None:
        # Only the shape this record is responsible for is settled here; what makes a
        # provider *runnable* is the launch path's own validation, which every adoption
        # goes through unchanged. Re-deriving that judgement here would be a second
        # copy of it, and the two would drift.
        if not isinstance(skill, Mapping) or not all(isinstance(key, str) for key in skill):
            raise ConfigError("relaunch record field 'skill_provider' must be a string-keyed map")
        record["skill_provider"] = dict(skill)
    return record
