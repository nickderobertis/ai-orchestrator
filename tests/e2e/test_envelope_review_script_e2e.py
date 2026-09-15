"""`scripts/envelope-review.sh` refuses to judge without its helpers, and the bus sends nothing.

The script is the command `config/onemessagebus.yaml` names as the planner channel's
envelope validator. Before its judged turn can run it sources the two helpers that resolve
this host's alternate harness identities, and a checkout missing either one cannot judge
anything. So it exits unjudged, naming the helper — never refused, which would read as a
verdict about the envelope, and never passed.

What this host owes is that the bus reads that exit the way its configuration intends. So
the journey runs the real script from a checkout laid out as this one is, with one helper
absent, and puts the same envelope through the real `onemessagebus validate` over the
copied configuration: the verdict is `unjudged`, its reason is the script's own sentence,
and nothing is appended.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

pytestmark = pytest.mark.reads_recipes

#: The validator, the configuration naming it, and the helpers it sources.
VALIDATOR = Path("scripts") / "envelope-review.sh"
CONFIG = Path("config") / "onemessagebus.yaml"
HELPERS = ("codex-alt-home.sh", "claude-alt-config-dir.sh")

#: An envelope carrying commands, which is what the configuration's validator judges.
ENVELOPE = json.dumps(
    {"version": 2, "commands": [{"op": "amend", "id": "work", "text": "Report."}]}
)

#: The script's exit for "could not judge", which the bus reads as unjudged.
UNJUDGED = 2


def _checkout_missing(tmp_path: Path, missing: str) -> Path:
    """A checkout holding the validator, its configuration, and every helper but `missing`."""
    checkout = tmp_path / "checkout"
    (checkout / "scripts").mkdir(parents=True)
    (checkout / "config").mkdir()
    shutil.copy2(REPO_ROOT / VALIDATOR, checkout / VALIDATOR)
    shutil.copy2(REPO_ROOT / CONFIG, checkout / CONFIG)
    for helper in HELPERS:
        if helper != missing:
            shutil.copy2(REPO_ROOT / "scripts" / helper, checkout / "scripts" / helper)
    return checkout


def _environment() -> dict[str, str]:
    environment = dict(os.environ)
    environment.pop("VIRTUAL_ENV", None)
    return environment


@pytest.mark.parametrize("missing", HELPERS)
def test_the_validator_is_unjudged_rather_than_refused_without_a_helper(
    tmp_path: Path, missing: str
) -> None:
    """Run as the bus runs it — the envelope on stdin — the script names the absent helper."""
    checkout = _checkout_missing(tmp_path, missing)

    judged = subprocess.run(
        [str(checkout / VALIDATOR)],
        cwd=checkout,
        env=_environment(),
        input=ENVELOPE,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )

    assert judged.returncode == UNJUDGED, f"{judged.stdout}{judged.stderr}"
    assert "required helper is not a readable regular file" in judged.stderr, judged.stderr
    assert f"scripts/{missing}" in judged.stderr, judged.stderr


@pytest.mark.parametrize("broken", HELPERS)
def test_the_validator_is_unjudged_naming_a_helper_that_fails_to_load(
    tmp_path: Path, broken: str
) -> None:
    """A helper present and readable but failing as it is sourced is named, not a shell error."""
    checkout = _checkout_missing(tmp_path, broken)
    (checkout / "scripts" / broken).write_text("return 1\n", encoding="utf-8")

    judged = subprocess.run(
        [str(checkout / VALIDATOR)],
        cwd=checkout,
        env=_environment(),
        input=ENVELOPE,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )

    assert judged.returncode == UNJUDGED, f"{judged.stdout}{judged.stderr}"
    assert f"scripts/{broken} failed to load" in judged.stderr, judged.stderr
    assert "just bootstrap" in judged.stderr, judged.stderr


@pytest.mark.parametrize("missing", HELPERS)
def test_the_bus_reads_a_validator_without_its_helper_as_unjudged_and_appends_nothing(
    tmp_path: Path, missing: str
) -> None:
    """Through the configuration: `unjudged` with the script's reason, and an empty channel."""
    checkout = _checkout_missing(tmp_path, missing)
    channel = tmp_path / "runs" / "run-1" / "channel"
    channel.mkdir(parents=True)

    validated = subprocess.run(
        [
            "onemessagebus",
            "validate",
            "replies",
            "--config",
            str(checkout / CONFIG),
            "--transport-dir",
            str(channel),
        ],
        cwd=checkout,
        env=_environment(),
        input=ENVELOPE,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(120),
        check=False,
    )

    assert validated.returncode == 1, f"{validated.stdout}{validated.stderr}"
    verdict = json.loads(validated.stdout)
    assert verdict["verdict"] == "unjudged", verdict
    assert f"scripts/{missing}" in verdict["reason"], verdict
    status = subprocess.run(
        [
            "onemessagebus",
            "status",
            "--config",
            str(checkout / CONFIG),
            "--transport-dir",
            str(channel),
        ],
        cwd=checkout,
        env=_environment(),
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )
    assert status.returncode == 0, status.stderr
    # `status` is the bus's own answer, one object per declared queue; only `records` is read.
    held = {queue["queue"]: queue["records"] for queue in json.loads(status.stdout)}
    assert held and not any(held.values()), f"the unjudged envelope was appended: {held}"
