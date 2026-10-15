"""No plan-store user layer of the invoking user's reaches a fixture of this suite.

`tests/plan_store_user_layer.py` is the rule, and the incident behind it; `tests/conftest.py`
arms it. The first journey runs a real fixture of this
tier — the copy `test_plan_copy_destination_ids_e2e.py` makes through `just plans` — as a
pytest session of its own loading this suite's conftest, under a configuration home holding
the very declaration that refused it. The second holds the sandbox to linking every other
entry of that home, and to leaving the home itself as it found it. The last holds the
helper's reading of where a configuration home is to the store's own.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import plan_store_user_layer
import pytest
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: The host declaration that refused every fixture's copy, verbatim.
PRIVATE_AUTHORING = "sources:\n  authoring:\n    visibility: private\n"
#: The fixture it refused, run whole.
FIXTURE = Path(__file__).with_name("test_plan_copy_destination_ids_e2e.py")
#: The installed plan store this checkout locks.
ONETASKGRAPH = REPO_ROOT / ".venv" / "bin" / "onetaskgraph"


def _invoking_user(config_home: Path) -> dict[str, str]:
    """This process's environment as a user whose configuration home is ``config_home``.

    Without this suite's record of the host's home or its coverage hand-off, so the session
    started under it takes ``config_home`` for the invoking user's, as a target would.
    """
    environment = {
        key: value
        for key, value in os.environ.items()
        if key != plan_store_user_layer.HOST_CONFIG_HOME_ENV
        and not key.startswith(("COV_CORE", "PYTEST_XDIST"))
    }
    environment[plan_store_user_layer.CONFIG_HOME_ENV] = str(config_home)
    return environment


def _user_layer(config_home: Path, text: str) -> Path:
    document = (
        config_home / plan_store_user_layer.STORE_DIRECTORY / plan_store_user_layer.USER_DOCUMENT
    )
    document.parent.mkdir(parents=True)
    document.write_text(text, encoding="utf-8")
    return document


# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge] `plan-tooling-plan-copy` is
# the leaf project keyed on `planToolingPlanCopyWorkspace`, and the one fixture this runs as a
# session of its own is that project's `test_plan_copy_destination_ids_e2e.py`, over the same
# recipe, wrapper and pin; the session costs seconds, and a project of its own would key on
# exactly what this one does.
def test_a_fixtures_copy_is_untouched_by_a_user_layer_that_would_refuse_it(
    tmp_path: Path,
) -> None:
    """A fixture copying a plan passes under the user layer that refused it on the host."""
    config_home = tmp_path / "config"
    layer = _user_layer(config_home, PRIVATE_AUTHORING)
    environment = _invoking_user(config_home)
    shown = subprocess.run(
        [str(ONETASKGRAPH), "config", "show", "--json"],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=True,
    )
    read = [
        setting
        for setting in json.loads(shown.stdout)["settings"]
        if setting["origin"].get("path") == str(layer)
    ]
    assert [(setting["key"], setting["value"]) for setting in read] == [
        ("sources.authoring.visibility", "private")
    ], f"the store did not read the layer at {layer}, so this proves nothing: {shown.stdout}"

    ran = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(FIXTURE),
            "-p",
            "no:cacheprovider",
            "--no-cov",
            "-q",
        ],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(600),
        check=False,
    )

    assert ran.returncode == 0, (
        f"a plan-store user layer at the invoking user's {layer} reached a fixture of this "
        f"suite:\n{ran.stdout[-6000:]}\n{ran.stderr[-2000:]}"
    )
    assert layer.read_text(encoding="utf-8") == PRIVATE_AUTHORING


# llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge]


def test_the_sandbox_links_every_other_entry_and_leaves_the_users_home_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every entry but the store's is the user's own; the store's is empty; nothing moved."""
    config_home = tmp_path / "config"
    layer = _user_layer(config_home, PRIVATE_AUTHORING)
    secrets = layer.with_name("secrets.env")
    secrets.write_text("GH_PROJECTS_TOKEN=from-the-users-store\n", encoding="utf-8")
    hosts = config_home / "gh" / "hosts.yml"
    hosts.parent.mkdir()
    hosts.write_text("github.com: {}\n", encoding="utf-8")
    before = {path: path.read_bytes() for path in config_home.rglob("*") if path.is_file()}
    for key, value in _invoking_user(config_home).items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv(plan_store_user_layer.HOST_CONFIG_HOME_ENV, raising=False)

    sandbox = plan_store_user_layer.isolate()

    assert os.environ[plan_store_user_layer.CONFIG_HOME_ENV] == str(sandbox)
    assert os.environ[plan_store_user_layer.HOST_CONFIG_HOME_ENV] == str(config_home)
    assert (sandbox / "gh" / "hosts.yml").read_text(encoding="utf-8") == "github.com: {}\n"
    store = sandbox / plan_store_user_layer.STORE_DIRECTORY
    assert store.is_dir() and not store.is_symlink() and list(store.iterdir()) == []
    shown = subprocess.run(
        [str(ONETASKGRAPH), "config", "show", "--json"],
        cwd=REPO_ROOT,
        env=dict(os.environ),
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=True,
    )
    origins = {setting["origin"].get("path") for setting in json.loads(shown.stdout)["settings"]}
    assert not any(str(path).startswith(str(config_home)) for path in origins), origins
    # A pytest process this one starts links the same user's home, not this sandbox.
    nested = plan_store_user_layer.isolate()
    assert (nested / "gh").resolve() == (config_home / "gh").resolve()
    assert {path: path.read_bytes() for path in config_home.rglob("*") if path.is_file()} == (
        before
    )


@pytest.mark.parametrize(
    ("xdg", "home"),
    [("config", "home"), ("", "home"), (None, "home"), (None, None)],
    ids=["named", "empty-named", "home-only", "neither"],
)
def test_the_configuration_home_is_where_the_store_reads_its_user_layer(
    tmp_path: Path, xdg: str | None, home: str | None
) -> None:
    """`configuration_home` answers the directory the pinned store reads a user layer under.

    For each way an environment can state it — named, named empty, derived from `HOME`, or
    neither — a layer written where the helper says is the one the store reports reading,
    and with neither the store reads none at all.
    """
    environment = {"PATH": os.environ["PATH"]}
    if xdg is not None:
        environment[plan_store_user_layer.CONFIG_HOME_ENV] = str(tmp_path / xdg) if xdg else ""
    if home is not None:
        environment["HOME"] = str(tmp_path / home)
    answered = plan_store_user_layer.configuration_home(environment)
    # A setting complete on its own, since no project document beside it names a source.
    layer = None if answered is None else _user_layer(answered, "page_size: 7\n")
    shown = subprocess.run(
        [str(ONETASKGRAPH), "config", "show", "--json"],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=True,
    )

    read = {
        setting["origin"]["path"]
        for setting in json.loads(shown.stdout)["settings"]
        if setting["origin"].get("layer") == "file"
    }
    assert read == ({str(layer)} if layer is not None else set()), shown.stdout
