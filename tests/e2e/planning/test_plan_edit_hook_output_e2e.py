"""The plan-edit hook's refusal reads in one line, and carries no judge run's event rendering.

`scripts/review-plan-item.sh`, run with no arguments on a hook document exactly as the engine
runs it as `plan_edit_hook`, in the throwaway checkout `delegation_checkout` builds: the
script and every wrapper it sources are real, the engine it reads the run-written keys from is
the installed one, and the published `oneagentgraph` it judges through is doubled at its
boundary, its
`.venv/bin` path. The double states its verdict the way the pinned `oneagentgraph run
--judge-only` does — the event rendering on stdout, a fail's findings on stderr under its
`<member> failed its bar (<bar>):` line — with a prompt line in that rendering, so a refusal
leaking the rendering is visible on either stream.

The engine reads a refusal's first non-empty stdout line as its one-line summary and stderr as
its full reason; a pass exits 0, and a judge that could not judge exits 2 with its cause.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import NamedTuple

import pytest
from delegation_checkout import ENGINE, at, delegation_checkout

from orchestrator import npm_installs

TITLE = "feat: probe the route"
FIRST_FINDING = "reviewer (judge) failed: the criterion names a procedure, not a property"
FIRST_FINDING_MORE = "    it says to run just gate rather than what must be true."
SECOND_FINDING = "plan_criteria_state_properties: a criterion names a command"
PROMPT_LINE = "prompt: You are a principal engineer reviewing a plan before anyone builds from it."
EVENT_LINE = "[worker] judge turn 1 started"
CANNOT_JUDGE = "the judge side died (provider-unavailable): the provider did not answer"
COULD_NOT_JUDGE = f"oneagentgraph: worker could not judge the input: {CANNOT_JUDGE}"
#: A fail stated in no shape the hook recognizes as findings.
UNSHAPED = "the reviewer refused in words of its own"

#: The doubled `oneagentgraph`: its event rendering on stdout whatever it decides, then the
#: verdict `FAKE_JUDGE_VERDICT` names, stated as the pinned release states it.
ONEAGENTGRAPH = f"""#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >>"$FAKE_JUDGE_ARGV"
printf '%s\\n' {json.dumps(EVENT_LINE)} {json.dumps(PROMPT_LINE)}
case "${{FAKE_JUDGE_VERDICT:?}}" in
  pass) exit 0 ;;
  refuse)
    printf '%s\\n' 'oneagentgraph: worker failed its bar (sha256:0123abcd):' \\
      {json.dumps("  - " + FIRST_FINDING)} {json.dumps(FIRST_FINDING_MORE)} \\
      {json.dumps("  - " + SECOND_FINDING)} >&2
    exit 1 ;;
  refuse-unshaped)
    printf '%s\\n' {json.dumps(UNSHAPED)} >&2
    exit 1 ;;
  down)
    printf '%s\\n' {json.dumps(COULD_NOT_JUDGE)} >&2
    exit 2 ;;
esac
"""


class Hooked(NamedTuple):
    done: subprocess.CompletedProcess[str]
    #: Every argv the doubled judge was run with.
    argv: list[str]


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    root, _ = delegation_checkout(tmp_path)
    # The installed engine rather than the checkout's tracing double, which reads a piped
    # stdin as a reply envelope: the hook's `plan writeback-keys` read is the engine's own,
    # and it leaves the hook document on stdin for the hook.
    engine = Path(at(root, ENGINE))
    engine.unlink()
    engine.symlink_to(npm_installs.ENGINE)
    # llmlint: ignore-block[e2e_not_mocked] The published oneagentgraph is the CLI this
    # script delegates its judgment to, and AGENTS.md's realism invariant doubles exactly
    # that, at its `.venv/bin` boundary and nothing above it: the hook script, the wrappers
    # it sources and the engine it reads are real, and this task's criteria name this double.
    # tests/e2e/planning/test_live_correction_e2e.py drives the real judge end to end.
    judge = root / ".venv" / "bin" / "oneagentgraph"
    judge.write_text(ONEAGENTGRAPH, encoding="utf-8")
    judge.chmod(0o755)
    # llmlint: ignore-end[e2e_not_mocked]
    return root


def _hook(checkout: Path, verdict: str, title: str = TITLE) -> Hooked:
    """Run the hook as the engine does: the document on stdin, no arguments."""
    argv = checkout / f"judge-argv.{verdict}"
    argv.write_text("", encoding="utf-8")
    scratch = checkout / "scratch-root"
    scratch.mkdir(exist_ok=True)
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("ONEPIPELINE_", "ONEHARNESS_", "ORCHESTRATOR_"))
    }
    environment.update(
        TRACE_FILE=str(checkout / "trace"),
        TMPDIR=str(scratch),
        FAKE_JUDGE_ARGV=str(argv),
        FAKE_JUDGE_VERDICT=verdict,
    )
    document = {
        "node": {"id": "probe-a", "deps": ["route"]},
        "task": {"title": title, "body": "## What\n\nProbe the route.\n"},
    }
    done = subprocess.run(
        [str(checkout / "scripts" / "review-plan-item.sh")],
        cwd=checkout,
        env=environment,
        input=json.dumps(document),
        text=True,
        capture_output=True,
        check=False,
        timeout=120,
    )
    return Hooked(done, argv.read_text(encoding="utf-8").splitlines())


def test_a_refusal_is_one_summary_line_on_stdout_and_the_findings_alone_on_stderr(
    checkout: Path,
) -> None:
    hooked = _hook(checkout, "refuse")
    done = hooked.done
    assert done.returncode == 1, done.stdout + done.stderr
    # The judge really ran over the hook's document, rendering events as text.
    assert len(hooked.argv) == 1 and "--judge-only" in hooked.argv[0], hooked.argv
    assert "--output text" in hooked.argv[0], hooked.argv
    lines = done.stdout.splitlines()
    assert lines, done.stdout
    # The first finding whole, its further line joined onto it, and not the second.
    first = f"{FIRST_FINDING} {FIRST_FINDING_MORE.strip()}"
    assert lines[0] == f"the plan reviewer refused '{TITLE}': {first}", done.stdout
    for stream in (done.stdout, done.stderr):
        assert PROMPT_LINE not in stream and EVENT_LINE not in stream, stream
    assert FIRST_FINDING in done.stderr and SECOND_FINDING in done.stderr, done.stderr
    assert FIRST_FINDING_MORE in done.stderr, done.stderr


def test_a_refused_node_with_no_title_is_named_by_its_id(checkout: Path) -> None:
    done = _hook(checkout, "refuse", title="").done
    assert done.returncode == 1, done.stdout + done.stderr
    assert done.stdout.startswith(f"the plan reviewer refused 'probe-a': {FIRST_FINDING}"), (
        done.stdout
    )


def test_a_title_over_several_lines_is_named_on_one(checkout: Path) -> None:
    done = _hook(checkout, "refuse", title="  feat: probe\n  the route  ").done
    assert done.returncode == 1, done.stdout + done.stderr
    assert done.stdout.startswith("the plan reviewer refused 'feat: probe the route': "), (
        done.stdout
    )


def test_a_refusal_in_no_recognized_shape_still_refuses_with_all_it_said(checkout: Path) -> None:
    done = _hook(checkout, "refuse-unshaped").done
    assert done.returncode == 1, done.stdout + done.stderr
    assert done.stdout.splitlines()[0] == (
        f"the plan reviewer refused '{TITLE}': its findings are on stderr"
    ), done.stdout
    assert UNSHAPED in done.stderr, done.stderr
    assert PROMPT_LINE not in done.stdout + done.stderr


def test_a_pass_exits_zero(checkout: Path) -> None:
    done = _hook(checkout, "pass").done
    assert done.returncode == 0, done.stdout + done.stderr
    assert PROMPT_LINE not in done.stdout + done.stderr


def test_a_judge_that_cannot_judge_exits_two_with_its_cause_on_stderr(checkout: Path) -> None:
    done = _hook(checkout, "down").done
    assert done.returncode == 2, done.stdout + done.stderr
    assert CANNOT_JUDGE in done.stderr, done.stderr
    assert f"could not judge '{TITLE}'" in done.stderr and "send the edit again" in done.stderr
    assert PROMPT_LINE not in done.stdout + done.stderr
