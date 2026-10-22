"""Start a served or launching `just` recipe so its whole tree can be stopped, and stop it.

A server a journey starts through a recipe is never the process `Popen` returns: `just`
runs the recipe in a shell, the wrapper script `exec`s `uv run`, and `uv run` starts the
published server under itself. The server itself exits on `SIGTERM`, and so does `uv`,
taking the server with it — but `just` is the one process a caller holds, and signalling
it alone does not reach either.

`just` before 1.41.0 catches `SIGTERM` and keeps waiting on its recipe rather than
forwarding the signal to it; forwarding to running children arrived in 1.41.0, as the
"Signal Handling" section of `just`'s README documents. This repository does not
provision `just`, so a host may run such a version, and there a teardown that signals
`just` alone leaves the server bound to its port and holding the test's pipes open: the
wait for it hangs until its guard, and the next journey binds beside a server still
answering. So `serve` starts the recipe in a session of its own, which makes the whole
tree one process group whose leader is the process started here, and `stop` signals
that group — whichever `just` is installed, and nothing found any other way.

A `just orchestrate` launch is the same tree with more under it: the run's observer
graph, its `onemessagebus` monitor and the dispatches it forks. Killing the launcher
alone left those running past the test that started them (ai-orchestrator#1467), so a
launch is started with `serve` too and ended with `stop_launch`, which first stops the
run through the recipe — the engine's own detached children, a retained driver among
them, are the engine's to reap — and then stops the launcher's group, whatever the stop
answered and whatever the journey did before it.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import tempfile
import time
from typing import IO

from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: A child stream as `subprocess` takes one: one of its constants, or a file to write to.
Stream = int | IO[str] | None


def serve(
    command: list[str],
    environment: dict[str, str] | None = None,
    *,
    stdin: Stream = None,
    stdout: Stream = subprocess.PIPE,
    stderr: Stream = subprocess.PIPE,
) -> subprocess.Popen[str]:
    """Start ``command`` from the repository root, in a session of its own.

    Its streams are pipes unless the caller names others: an attached launch streams a
    run's whole merged feed, which a pipe nobody drains fills until the launch wedges.
    """
    return subprocess.Popen(
        command,
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
        start_new_session=True,
    )


def exit_code(process: subprocess.Popen[str]) -> int | None:
    """Read a child's exit status without reaping its process-group leader."""
    status = os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
    if status is None:
        return None
    return status.si_status if status.si_code == os.CLD_EXITED else -status.si_status


def _exited(process: subprocess.Popen[str], seconds: float) -> bool:
    """Wait up to ``seconds`` for the leader to exit, leaving it unreaped, and say if it did.

    Unreaped, its pid — which is the group's id — cannot be handed to another process,
    so the `SIGKILL` that follows reaches this group and no other.
    """
    limit = time.monotonic() + seconds
    while process.returncode is None:
        if os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None:
            return True
        if time.monotonic() >= limit:
            return False
        time.sleep(0.05)
    return True


def stop(*processes: subprocess.Popen[str]) -> None:
    """Stop each recipe's whole process group, and reap the process that led it.

    `SIGTERM` first, so what is in the group can end itself; then, once the leader has
    exited or the hang guard elapsed, `SIGKILL` to the group whether or not anything is
    left in it, because a member that ignored the first signal says nothing about itself
    once its leader is gone.
    """

    def signalled(process: subprocess.Popen[str], number: int) -> None:
        # A group already gone is one that exited on its own, and is reaped below.
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, number)

    for process in processes:
        signalled(process, signal.SIGTERM)
    for process in processes:
        _exited(process, e2e_timeout(30))
        signalled(process, signal.SIGKILL)
        process.wait()
        # Every writer in the group is dead, so nothing is left to drain; a pipe is closed
        # rather than read, because a process outside the group holding it would block a read.
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                stream.close()


# Detached launches remain owned and unreaped until their fixture stops the run.
_pending_launchers: dict[tuple[str, str], list[subprocess.Popen[str]]] = {}


def attached(
    command: list[str], environment: dict[str, str], *, seconds: float = 300
) -> subprocess.CompletedProcess[str]:
    """Run ``command`` in a session of its own until it returns, and leave nothing of it.

    For a launch a journey waits on — attached to settlement, or one that hands back
    once it has detached — rather than one it holds open beside itself. Whatever the
    launch left is stopped through `stop_launch` before its group is cleaned up. A
    successful detached launch stays unreaped until the fixture calls `stop_launch`,
    so its driver can continue serving the journey. Its output goes to files rather than
    pipes, so the leader can be waited for unreaped without a full pipe stalling it.
    """
    with (
        tempfile.TemporaryFile("w+", encoding="utf-8") as out,
        tempfile.TemporaryFile("w+", encoding="utf-8") as err,
    ):
        process = serve(command, environment, stdin=subprocess.DEVNULL, stdout=out, stderr=err)
        returned = False
        code: int | None = None
        launch_run = (
            command[2].split(":", 1)[-1] if command[:2] == ["just", "orchestrate"] else None
        )
        try:
            returned = _exited(process, e2e_timeout(seconds))
            if launch_run is not None:
                code = exit_code(process)
        finally:
            if launch_run is None:
                stop(process)
            elif returned and code == 0 and "--detach" in command:
                key = (environment["ONEPIPELINE_RUNS_DIR"], launch_run)
                _pending_launchers.setdefault(key, []).append(process)
            else:
                stop_launch(launch_run, environment, process)
        out.seek(0)
        err.seek(0)
        said, complained = out.read(), err.read()
    if not returned:
        raise subprocess.TimeoutExpired(command, seconds, output=said, stderr=complained)
    return subprocess.CompletedProcess(
        command,
        code if launch_run is not None and code is not None else process.returncode,
        said,
        complained,
    )


def stop_launch(
    run: str, environment: dict[str, str], *launchers: subprocess.Popen[str]
) -> subprocess.CompletedProcess[str]:
    """End a launched run and every process its launchers started, and say what stop said.

    The run is stopped first, through `just stop` as an operator stops one, because what
    the engine detached from the launcher's group is the engine's to end; then each
    launcher's group is stopped whatever that answered, so a refused or failed stop
    still leaves nothing of the launch running.
    """
    pending = _pending_launchers.pop((environment.get("ONEPIPELINE_RUNS_DIR", ""), run), [])
    try:
        stopped = attached(["just", "stop", run], environment, seconds=120)
    finally:
        stop(*pending, *launchers)
    return stopped
