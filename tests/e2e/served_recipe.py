"""Start a served `just` recipe so its whole tree can be stopped, and stop it.

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
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess

from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT


def serve(command: list[str], environment: dict[str, str] | None = None) -> subprocess.Popen[str]:
    """Start ``command`` from the repository root, in a session of its own."""
    return subprocess.Popen(
        command,
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )


def stop(*processes: subprocess.Popen[str]) -> None:
    """Stop each served recipe's process group, and reap the process that led it.

    The reap waits for the leader's pipes to close as well as for its exit, so a group
    is killed when anything in it still holds them once the hang guard elapses.
    """

    def signalled(process: subprocess.Popen[str], number: int) -> None:
        # A group already gone is one that exited on its own, and is reaped below.
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, number)

    for process in processes:
        signalled(process, signal.SIGTERM)
    for process in processes:
        try:
            process.communicate(timeout=e2e_timeout(30))
        except subprocess.TimeoutExpired:
            signalled(process, signal.SIGKILL)
            process.communicate()
