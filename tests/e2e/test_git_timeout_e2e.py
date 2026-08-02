"""Real git journeys for the bound every lifecycle git operation now carries.

Nothing here is faked: a real bare origin, a real `pre-push` hook, a real remote
transport, and the real `git push` / `git fetch` the merge path performs. What is
driven past each bound is the thing that actually wedges in production — a gate
that never finishes, and a remote that never answers.

The bounded call runs in a process of its own rather than in the test session. The
suite's own resource guard replaces `subprocess.Popen` and reaps whole trees behind
every test, so a gate left running by an in-session call would be cleaned up by the
harness and the assertion that it *was not* left running would prove nothing.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

import pytest
from conftest import install_pre_push_hook
from waits import timeout as e2e_timeout

from orchestrator import REPO_ROOT, gitops
from orchestrator.gitops import (
    DEFAULT_HOOK_TIMEOUT_SECONDS,
    DEFAULT_TIMEOUT_SECONDS,
    GIT_HOOK_TIMEOUT_ENV,
    GIT_TIMEOUT_ENV,
    GitError,
)

#: A stand-in that records its own pid and then never finishes on its own — the shape
#: of a gate wedged on a lock, or a transport waiting on a host that will not answer.
#: Either used to be indistinguishable from a run still doing work.
_WEDGED = """
import os
import sys
import time
from pathlib import Path

Path(sys.argv[1]).write_text(str(os.getpid()), encoding="utf-8")
time.sleep(3600)
"""

#: Drives one bounded git operation and reports what the bound said. Run out of
#: process so the git subprocess it starts is the real one, with nothing between the
#: bound and the tree it has to stop waiting for.
_DRIVER = """
import json
import sys
import time

from orchestrator import gitops
from orchestrator.gitops import GitError

operation, target, branch = sys.argv[1:4]
started = time.monotonic()
try:
    if operation == "push":
        gitops.push(target, branch)
    else:
        gitops.fetch(target)
except GitError as error:
    print(json.dumps({"message": str(error), "elapsed": time.monotonic() - started}))
    raise SystemExit(0)
raise SystemExit("the bound never fired")
"""


def _drive(operation: str, target: Path, branch: str, **environment: str) -> dict[str, object]:
    """Run one bounded git operation to completion in its own process."""
    result = subprocess.run(
        [sys.executable, "-c", _DRIVER, operation, str(target), branch],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(120),
        env={**os.environ, **environment},
    )
    assert result.returncode == 0, result.stderr
    reported = json.loads(result.stdout)
    assert isinstance(reported, dict)
    return reported


def _await_gone(pid: int, what: str) -> None:
    """Block until a process is no longer running, whatever became of its parent."""
    guard = time.monotonic() + e2e_timeout(30)
    while True:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        assert time.monotonic() < guard, f"{what} (pid {pid}) outlived the bound that fired"
        time.sleep(0.01)


def test_push_past_its_bound_reports_the_command_and_leaves_no_gate_running(
    bare_origin: Callable[..., Path], tmp_path: Path
) -> None:
    """A publication whose gate wedges now fails with a diagnostic instead of hanging."""
    origin = bare_origin()
    checkout = tmp_path / "checkout"
    gitops.clone(str(origin), checkout)
    marker = tmp_path / "gate.pid"
    install_pre_push_hook(
        checkout,
        " ".join(shlex.quote(part) for part in (sys.executable, "-c", _WEDGED, str(marker))),
    )
    branch = "publish-me"
    gitops._git(["checkout", "-b", branch], cwd=checkout)
    gitops.commit_empty(checkout, "chore: something to publish")

    reported = _drive("push", checkout, branch, **{GIT_HOOK_TIMEOUT_ENV: "3"})

    message = reported["message"]
    assert isinstance(message, str)
    assert f"git push --set-upstream origin {branch} timed out after" in message
    assert f"bound 3s; raise it with {GIT_HOOK_TIMEOUT_ENV}" in message
    # The bound itself is the behavior under test, so this deadline stays explicit: it
    # must have waited the whole bound, and must not have waited unbounded.
    elapsed = reported["elapsed"]
    assert isinstance(elapsed, float) and 3 <= elapsed < 3 + e2e_timeout(20)
    # The wedged gate is not merely disowned. A fired bound that left it running would
    # manufacture exactly the reparented leaving the scratch sweep then has to
    # recognise days later — and its inherited pipes would hang the timeout path itself.
    _await_gone(int(marker.read_text(encoding="utf-8")), "the wedged pre-push gate")
    # Nothing reached the remote: an aborted push publishes no ref.
    assert branch not in gitops.branches(origin)


def test_a_fetch_from_a_remote_that_never_answers_is_bounded_separately(tmp_path: Path) -> None:
    """The hookless bound is its own knob, and it reaches the transport git spawns."""
    checkout = tmp_path / "waiting"
    gitops._git(["init", "-q", "-b", "main", str(checkout)])
    gitops._git(["remote", "add", "origin", "ssh://nowhere.invalid/repo.git"], cwd=checkout)
    marker = tmp_path / "transport.pid"
    transport = tmp_path / "stalling-ssh.py"
    transport.write_text(f"#!{sys.executable}\n{_WEDGED}", encoding="utf-8")
    transport.chmod(0o755)

    reported = _drive(
        "fetch",
        checkout,
        "main",
        GIT_SSH_COMMAND=f"{shlex.quote(str(transport))} {shlex.quote(str(marker))}",
        **{GIT_TIMEOUT_ENV: "3", GIT_HOOK_TIMEOUT_ENV: str(DEFAULT_HOOK_TIMEOUT_SECONDS)},
    )

    message = reported["message"]
    assert isinstance(message, str)
    assert "git fetch origin --prune timed out after" in message
    assert f"bound 3s; raise it with {GIT_TIMEOUT_ENV}" in message
    # The transport holds git's own pipes, so nothing is left holding them open.
    _await_gone(int(marker.read_text(encoding="utf-8")), "the stalling transport")


def test_this_repositorys_own_run_clone_stays_far_inside_the_default_bound(
    tmp_path: Path,
) -> None:
    """Size the ordinary bound against this repository's clone, not against a guess."""
    destination = tmp_path / "run-clone"
    started = time.monotonic()
    gitops.clone_sharing(
        REPO_ROOT,
        destination,
        origin=gitops.remote_url(REPO_ROOT),
        base=gitops.current_branch(REPO_ROOT),
    )
    elapsed = time.monotonic() - started

    assert gitops.head_sha(destination) == gitops.head_sha(REPO_ROOT)
    # The per-run clone is the largest ordinary git operation the lifecycle performs
    # against this repository. Holding it to a hundredth of the bound is what makes
    # "sized for a repository this size" a measurement rather than an assertion.
    assert elapsed * 100 < DEFAULT_TIMEOUT_SECONDS
    # A gate-running operation is given far more room than an ordinary one, because
    # this repository's own pre-push gate takes about fourteen minutes.
    assert DEFAULT_HOOK_TIMEOUT_SECONDS >= 4 * 14 * 60


def test_an_unusable_bound_is_refused_at_the_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    """A misconfigured bound fails loudly instead of silently reverting to unbounded."""
    for value, expected in (
        ("not-a-number", f"{GIT_TIMEOUT_ENV} must be a number of seconds"),
        ("0", "finite number of seconds above zero"),
        ("-1", "finite number of seconds above zero"),
        ("inf", "finite number of seconds above zero"),
    ):
        monkeypatch.setenv(GIT_TIMEOUT_ENV, value)
        with pytest.raises(GitError, match=expected):
            gitops.is_repo(REPO_ROOT)
