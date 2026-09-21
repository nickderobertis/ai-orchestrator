"""Real-CLI coverage for what the adopted `oneharness` does with a variant identity.

Every chain this host runs is spelled as variants (`claude-code:alternate`, never a
bare `claude-code`), so a binary override that reaches only the bare id leaves the
variant resolving to the real `claude` — a paid turn where a stand-in was meant.
The adopted release falls a base-id `--bin` back onto every variant of that harness
(oneharness#1329); these journeys drive the pinned CLI over this repository's own
`oneharness.toml` and read the answer off the stand-in that ran, never off the report
alone. Beside it they hold the release's one-candidate exit invariant (#1324) for the
shapes this host selects one identity in, and a verb's own usage on a
`--format text --compact` conflict (#1333).

`claude` is kept off every launch's `PATH` and asserted absent before the launch, so a
release that stopped honouring the override skips the candidate rather than billing
the host's own subscription.
"""

# llmlint: ignore-file[test_tiers_split_by_project_not_by_marker] Nothing here is expensive:
# the paid provider is doubled and every journey finishes in well under a second, and the
# real `oneharness` CLI it drives is the one tests/e2e/test_oneharness_timeout_e2e.py
# already drives over the same configs in this tier, so a project of its own would isolate
# no cost.

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

STAND_IN_CLAUDE = REPO_ROOT / "tests" / "e2e" / "stand_in_claude.py"
AGENT_CONFIG = REPO_ROOT / "oneharness.toml"
#: A variant `oneharness.toml` declares, and the identity a dispatch pinned to the
#: first alternate subscription selects.
VARIANT = "claude-code:alternate"


def _launch_env(tmp_path: Path, **extra: str) -> dict[str, str]:
    """An environment built from nothing, holding what the variant's rules read.

    Nothing inherited reaches the launch, so a dispatch's `ONEHARNESS_*` pins cannot
    reselect the chain. `PATH` carries the interpreter the stand-in runs under and the
    system directories, and never a `claude`.
    """
    alt_config_dir = tmp_path / "claude-alt"
    scratch = tmp_path / "scratch"
    alt_config_dir.mkdir(exist_ok=True)
    scratch.mkdir(exist_ok=True)
    path = f"{Path(sys.executable).parent}:/usr/bin:/bin"
    assert shutil.which("claude", path=path) is None, (
        f"a real `claude` is reachable on {path}; this journey would bill it"
    )
    return {
        "HOME": str(tmp_path),
        "PATH": path,
        # `oneharness.toml`'s `env_from` for the alternate variant names both.
        "ORCHESTRATOR_CLAUDE_ALT_CONFIG_DIR": str(alt_config_dir),
        "ONEPIPELINE_NODE_SCRATCH_DIR": str(scratch),
        "STAND_IN_CLAUDE_RECORD": str(tmp_path / "stand-in.jsonl"),
        **extra,
    }


def _run(
    oneharness_bin: str, tmp_path: Path, args: list[str], env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [oneharness_bin, "run", "--config", str(AGENT_CONFIG), *args],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(30),
    )


def test_a_base_id_bin_override_covers_a_variant_qualified_identity(
    tmp_path: Path, oneharness_bin: str
) -> None:
    """Pin the identity the way a dispatch does and override only the base id."""
    env = _launch_env(tmp_path, ONEHARNESS_HARNESSES=VARIANT)

    proc = _run(
        oneharness_bin,
        tmp_path,
        [
            "--bin",
            # The paid provider is the one sanctioned double (AGENTS.md, "Tests are
            # realistic, not mocked"), and here it is the subject: whether the real CLI's
            # base-id override reaches a variant is read off which binary ran, which only
            # a recording stand-in in the provider's place can show.
            # llmlint: ignore[e2e_not_mocked] the sanctioned paid-provider double; see above
            f"claude-code={STAND_IN_CLAUDE}",
            "--prompt",
            "answer from the stand-in",
            "--format",
            "json",
            "--compact",
        ],
        env,
    )

    assert proc.returncode == 0, proc.stderr
    record_file = Path(env["STAND_IN_CLAUDE_RECORD"])
    lines = record_file.read_text(encoding="utf-8").splitlines() if record_file.exists() else []
    records = [json.loads(line) for line in lines]
    assert len(records) == 1, (
        f"the stand-in did not run for {VARIANT}; the base-id override did not reach "
        f"the variant: {proc.stdout}"
    )
    assert records[0]["argv"][0] == str(STAND_IN_CLAUDE)
    # The variant's own `env_from` reached the stand-in, so what ran was the
    # `alternate` candidate under its routing, not the bare harness.
    assert records[0]["claude_config_dir"] == env["ORCHESTRATOR_CLAUDE_ALT_CONFIG_DIR"]
    report = json.loads(proc.stdout)
    assert report["fallback"]["ran"] == VARIANT
    (result,) = report["results"]
    assert result["harness_id"] == VARIANT
    assert result["bin"] == str(STAND_IN_CLAUDE)
    assert result["status"] == "ok"
    assert result["text"] == "stand-in answered"


@pytest.mark.parametrize(
    ("selection", "extra_args"),
    [
        pytest.param({"ONEHARNESS_HARNESSES": VARIANT}, [], id="pinned-identity-chain"),
        pytest.param(
            {"ONEHARNESS_HARNESSES": VARIANT},
            ["--resume", "stand-in-session"],
            id="pinned-identity-continuation",
        ),
        pytest.param({}, ["--harness", VARIANT], id="explicit-harness"),
    ],
)
def test_a_one_candidate_selection_that_cannot_run_exits_1(
    tmp_path: Path, oneharness_bin: str, selection: dict[str, str], extra_args: list[str]
) -> None:
    """One selected identity whose binary is absent: `skipped` data, exit `1`.

    The absent path is given as the base-id override, so the refusal naming it is
    also the override reaching the variant on the failure path.
    """
    absent = tmp_path / "absent-claude"
    env = _launch_env(tmp_path, **selection)

    proc = _run(
        oneharness_bin,
        tmp_path,
        [
            *extra_args,
            "--bin",
            f"claude-code={absent}",
            "--prompt",
            "nothing can run this",
            "--format",
            "json",
            "--compact",
        ],
        env,
    )

    assert proc.returncode == 1, (proc.stdout, proc.stderr)
    assert "no selected harness could be run" in proc.stderr
    # The report keeps its shape: a continuation over a chain of one carries no
    # `fallback` block (it runs as the single-harness run), the others one naming
    # nothing that ran. The result is the same `skipped` data under every shape.
    report = json.loads(proc.stdout)
    assert report["fallback"] is None or report["fallback"]["ran"] is None
    (result,) = report["results"]
    assert result["harness_id"] == VARIANT
    assert result["status"] == "skipped"
    assert result["available"] is False
    assert result["bin"] == str(absent)
    assert not Path(env["STAND_IN_CLAUDE_RECORD"]).exists()


@pytest.mark.parametrize("verb", [["run", "--prompt", "hi"], ["list"], ["config"]])
def test_a_verb_refusing_text_with_compact_prints_its_own_usage(
    tmp_path: Path, oneharness_bin: str, verb: list[str]
) -> None:
    """The refusal points at the options of the command that was invoked."""
    proc = subprocess.run(
        [oneharness_bin, *verb, "--format", "text", "--compact"],
        cwd=tmp_path,
        env=_launch_env(tmp_path),
        text=True,
        capture_output=True,
        timeout=e2e_timeout(30),
    )

    assert proc.returncode == 2, proc.stderr
    assert "--format text cannot be combined with --compact" in proc.stderr
    assert f"Usage: oneharness {verb[0]} [OPTIONS]" in proc.stderr
    assert "Usage: oneharness <COMMAND>" not in proc.stderr
