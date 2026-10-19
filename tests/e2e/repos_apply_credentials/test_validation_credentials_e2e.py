"""`just repos-apply` asks no credential helper for the checkout it validates against.

The apply validates the rules, release override and workspaces files in a scratch
registry holding one synthetic checkout, whose origin exists nowhere, and `release
targets` reads that origin. A host's helper asked for it does work that outlives the
apply: `gh auth git-credential`, holding no token in its config, falls back to a keyring
that autolaunches a session bus daemon, which the push gate counts as a leaked process.

Nothing leaves this machine. Git asks its helpers before it connects when the proxy it is
configured with names a user and no password, so a proxy on a port nothing listens on
makes the ask without a request reaching any host, and every `gh` call `onevcs` makes
goes to `tests/e2e/fake_gh.py` through `ONEVCS_GH`, the seam `onevcs` publishes for it.
The recipe, the script, `onevcs` and git are real.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import fake_gh
import pytest

from orchestrator.root import REPO_ROOT


def test_the_validation_checkout_asks_no_credential_helper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A global configuration naming a helper that records each ask stands in for a host's."""
    asked = tmp_path / "asked"
    global_config = tmp_path / "gitconfig"
    global_config.write_text(
        f'[credential]\n\thelper = "!f() {{ echo \\"$*\\" >> \'{asked}\'; }}; f"\n'
        "[http]\n\tproxy = http://probe@127.0.0.1:1\n",
        encoding="utf-8",
    )
    manifest = tmp_path / "checkouts"
    # Nothing to register: the subject is the checkout the apply makes for itself.
    manifest.write_text("# no checkouts\n", encoding="utf-8")
    config_home = tmp_path / "config"
    config_home.mkdir()
    gh_state = tmp_path / "gh-state"
    gh_state.mkdir()
    for name, value in {
        "GIT_CONFIG_GLOBAL": str(global_config),
        "GIT_TERMINAL_PROMPT": "0",
        # llmlint: ignore[e2e_not_mocked] Only the remote host's decisioning, at onevcs's seam.
        "ONEVCS_GH": str(fake_gh.__file__),
        "FAKE_GH_STATE": str(gh_state),
        "FAKE_GH_CALLS": str(tmp_path / "gh-calls"),
        "ONEVCS_HOME": str(tmp_path / "home"),
        "XDG_CONFIG_HOME": str(config_home),
    }.items():
        monkeypatch.setenv(name, value)

    result = subprocess.run(
        ["just", "repos-apply", "--checkouts", str(manifest)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert not asked.exists(), asked.read_text(encoding="utf-8")
