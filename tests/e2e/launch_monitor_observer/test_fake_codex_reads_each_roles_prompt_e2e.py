"""The codex stand-in's reading of its argv, reconciled against the `oneharness` that builds it.

`tests/e2e/fake_codex.py` records the prompt a turn was given, and every journey that reads
a prompt back reads it through that record. Where the prompt sits in the argv is not the
stand-in's to decide: oneharness builds `exec … --json <prompt>` and then appends the role's
own `[harness.codex] args`, so a role that gains arguments moves the prompt off the end. So
each role this host ships is run here through the real `oneharness`, over the stand-in, on
every Codex identity in its chain, and the stand-in must record exactly the prompt it was
sent. A role or a oneharness release that changes the argv fails here, rather than as a
journey elsewhere reading a flag's value as its prompt.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from project_fixtures import helper
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

STAND_IN = helper("fake_codex.py")

#: The prompt each turn is sent: spaces and a quote, so a split or a mis-quoted word shows.
PROMPT = "Read the prompt back, word for word: it's the whole of this."

#: Every role a member, a recipe or a dispatch runs against; the two shared parents run nothing.
ROLES = sorted(
    path.name
    for path in REPO_ROOT.glob("oneharness*.toml")
    if path.name not in ("oneharness.identities.toml", "oneharness.dispatch.toml")
)


# llmlint: ignore-block[shell_test_tiers_stay_split] Not a shell suite, and the only tool
# driven is the worktree's own pinned `oneharness`, over this repository's codex stand-in, so
# no harness is spawned, nothing is installed and no network is reached — the same tier and
# the same CLI as `test_supervisory_codex_posture_e2e.py` and `test_oneharness_timeout_e2e.py`
# beside it, whose role files this module reads the argv of.
def _codex_identities(oneharness_bin: str, config: str) -> list[str]:
    """The Codex identities `config`'s chain resolves to, in order."""
    resolved = subprocess.run(  # noqa: S603 - the pinned harness CLI
        [oneharness_bin, "config", "--config", config, "--compact"],
        cwd=REPO_ROOT,
        env=_environment(),
        text=True,
        capture_output=True,
        timeout=e2e_timeout(30),
        check=False,
    )
    assert resolved.returncode == 0, f"{config} did not resolve: {resolved.stderr}"
    chain: list[str] = json.loads(resolved.stdout)["harnesses"]["value"]
    return [identity for identity in chain if identity.partition(":")[0] == "codex"]


def _environment() -> dict[str, str]:
    """The suite's environment less every override a dispatch or another journey left set."""
    return {
        name: value
        for name, value in os.environ.items()
        if not name.startswith(("ONEHARNESS_", "FAKE_CODEX_", "ONEPIPELINE_NODE_SCRATCH_DIR"))
    }


@pytest.mark.parametrize("config", ROLES)
def test_the_stand_in_records_the_prompt_each_role_sends(
    oneharness_bin: str, config: str, tmp_path: Path
) -> None:
    """One real turn per Codex identity of `config`, over the stand-in, recording its prompt."""
    identities = _codex_identities(oneharness_bin, config)
    assert identities, f"{config} names no Codex identity, so its argv is not read here"
    for identity in identities:
        log = tmp_path / f"{identity.replace(':', '-')}.jsonl"
        environment = _environment()
        # A dispatched role's Codex variants take `XDG_RUNTIME_DIR` from the node's scratch
        # directory, which the engine always hands a dispatch and a pre-push hook never does,
        # so the turn is given one as its dispatch would be rather than whatever the caller had.
        scratch = tmp_path / "scratch"
        scratch.mkdir(exist_ok=True)
        environment["ONEPIPELINE_NODE_SCRATCH_DIR"] = str(scratch)
        # llmlint: ignore[e2e_not_mocked] Only the paid provider process is substituted.
        environment["ONEHARNESS_BIN_CODEX"] = str(STAND_IN)
        environment["FAKE_CODEX_PROMPT_LOG"] = str(log)
        subprocess.run(  # noqa: S603 - the pinned harness CLI
            [
                oneharness_bin,
                "run",
                "--config",
                config,
                "--harness",
                identity,
                "--prompt",
                PROMPT,
                "--compact",
            ],
            cwd=REPO_ROOT,
            env=environment,
            text=True,
            capture_output=True,
            timeout=e2e_timeout(60),
            check=False,
        )
        assert log.is_file(), f"{identity} under {config} never reached the stand-in"
        # A structured role re-prompts on the stand-in's answer, so only the first launch's
        # prompt is the one this turn was sent; oneharness wraps it in a plan-mode template or
        # a schema where the role asks for one, so the sent text is read inside the record.
        first = json.loads(log.read_text(encoding="utf-8").splitlines()[0])["prompt"]
        assert PROMPT in first, (
            f"the stand-in recorded {first[:200]!r} as the prompt {identity} was sent under "
            f"{config}, so it read some other word of the argv oneharness built"
        )


# llmlint: ignore-end[shell_test_tiers_stay_split]
