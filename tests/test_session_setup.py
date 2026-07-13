"""Shell-level tests for session setup behavior."""

from __future__ import annotations

import subprocess
from pathlib import Path

from orchestrator import REPO_ROOT


def test_persist_session_env_writes_path_once(tmp_path: Path) -> None:
    env_file = tmp_path / "claude-env"
    script = REPO_ROOT / "scripts" / "session-setup.sh"
    for live_path in ("/usr/bin:/bin", f"{tmp_path}/.local/node/bin:/usr/bin:/bin"):
        proc = subprocess.run(
            [
                "bash",
                "-c",
                'source "$1"; persist_session_env',
                "test-persist-session-env",
                str(script),
            ],
            text=True,
            capture_output=True,
            env={
                "HOME": str(tmp_path),
                "PATH": live_path,
                "CLAUDE_ENV_FILE": str(env_file),
            },
        )
        assert proc.returncode == 0, proc.stderr

    lines = env_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("export PATH=")
    assert f"{tmp_path}/.local/node/bin" in lines[0]
