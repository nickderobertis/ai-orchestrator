"""No plan-store user layer of the invoking user's reaches a fixture of this suite.

`tests/plan_store_user_layer.py` is the rule, and the incident behind it; `tests/conftest.py` arms
it. The first journey runs a real fixture of this tier — the copy
`test_plan_copy_destination_ids_e2e.py` makes through `just plans` — as a pytest session of its own
loading this suite's conftest, under a configuration home holding a declaration that refuses it: a
private store of the host's whose root cannot be read. The second holds the sandbox to linking every
other entry of that home, and to leaving the home itself as it found it; the third holds back
ai-orchestrator's own host checkout list and rules overlay while linking that directory's other
entries. The next holds the helper's reading of where a configuration home is to the store's own,
and the last three hold real Bun to the cache the user's `.npmrc` names once the home moves, to a
cache the environment already states, and to stating none when `bun pm cache` cannot answer.
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

#: A host declaration that refuses every fixture's copy: a private store this host keeps
#: whose root it cannot read, which the store walks before every copy now that the tracked
#: document switches its public boundary on. The incident's own declaration, `authoring`
#: private, is the tracked document's now, so a layer stating it reads nothing of its own.
HOSTILE_LAYER = (
    "sources:\n"
    "  hostprivate:\n"
    "    plugin: local-md\n"
    "    visibility: private\n"
    "    config:\n"
    "      root: /nonexistent/host-private-store\n"
)
#: The fixture it refused, run whole.
FIXTURE = Path(__file__).with_name("test_plan_copy_destination_ids_e2e.py")
#: The installed plan store this checkout locks.
ONETASKGRAPH = REPO_ROOT / ".venv" / "bin" / "onetaskgraph"


@pytest.fixture(autouse=True)
def _source_settings_restored(monkeypatch: pytest.MonkeyPatch) -> None:
    """Undo, after each test, the source settings `isolate()` unsets in this process.

    Those settings are the roots `tests/conftest.py` stated for the whole session, which every
    later test in this worker reads; each is unset through ``monkeypatch`` so its teardown
    states it again.
    """
    for name in [
        name for name in os.environ if name.startswith(plan_store_user_layer.SOURCE_SETTING_PREFIX)
    ]:
        monkeypatch.delenv(name)


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
    layer = _user_layer(config_home, HOSTILE_LAYER)
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
    assert ("sources.hostprivate.visibility", "private") in [
        (setting["key"], setting["value"]) for setting in read
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
    assert layer.read_text(encoding="utf-8") == HOSTILE_LAYER


# llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge]


def test_the_sandbox_links_every_other_entry_and_leaves_the_users_home_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every entry but the store's is the user's own; the store's is empty; nothing moved."""
    config_home = tmp_path / "config"
    layer = _user_layer(config_home, HOSTILE_LAYER)
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


def test_the_sandbox_holds_back_the_hosts_checkout_list_and_rules_overlay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ai-orchestrator's two host layers stay out; its other entries are the user's own.

    Read through `scripts/registered-checkouts.sh`, the selection `just repos-apply` and `just
    repos-bootstrap` make: with the host's own list held back it answers the tracked one, and
    the user's home is left as it was found.
    """
    config_home = tmp_path / "config"
    orchestrator = config_home / plan_store_user_layer.ORCHESTRATOR_DIRECTORY
    orchestrator.mkdir(parents=True)
    for layer in plan_store_user_layer.HOST_LAYERS:
        (orchestrator / layer).write_text("# the host's own\n", encoding="utf-8")
    (orchestrator / "workspaces.yml").write_text("version: 1\n", encoding="utf-8")
    before = {path: path.read_bytes() for path in config_home.rglob("*") if path.is_file()}
    for key, value in _invoking_user(config_home).items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv(plan_store_user_layer.HOST_CONFIG_HOME_ENV, raising=False)

    sandbox = plan_store_user_layer.isolate()

    held = sandbox / plan_store_user_layer.ORCHESTRATOR_DIRECTORY
    assert held.is_dir() and not held.is_symlink()
    assert sorted(entry.name for entry in held.iterdir()) == ["workspaces.yml"]
    assert (held / "workspaces.yml").resolve() == (orchestrator / "workspaces.yml").resolve()
    selected = subprocess.run(
        ["bash", "-c", 'source scripts/registered-checkouts.sh && default_checkouts_file "$PWD"'],
        cwd=REPO_ROOT,
        env=dict(os.environ),
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=True,
    )
    assert selected.stdout.strip() == str(REPO_ROOT / "config" / "onevcs.checkouts")
    assert {path: path.read_bytes() for path in config_home.rglob("*") if path.is_file()} == (
        before
    )


def test_a_source_setting_the_host_states_in_the_environment_reaches_no_test(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A host's own environment settings of a source are unset, a tracked source's among them.

    Read back through the installed store, so what is held is what a test's store reads: the
    tracked source answers from the tracked document, and a source only the host's environment
    defined is gone.
    """
    stated = tmp_path / "hosts-own-examples"
    stated.mkdir()
    monkeypatch.setenv("ONETASKGRAPH_SOURCES__EXAMPLES__CONFIG__ROOT", str(stated))
    monkeypatch.setenv("ONETASKGRAPH_SOURCES__HOSTONLY__PLUGIN", "local-md")
    monkeypatch.setenv("ONETASKGRAPH_SOURCES__HOSTONLY__CONFIG__ROOT", str(tmp_path))
    monkeypatch.delenv(plan_store_user_layer.HOST_CONFIG_HOME_ENV, raising=False)

    plan_store_user_layer.isolate()

    assert not [
        name for name in os.environ if name.startswith(plan_store_user_layer.SOURCE_SETTING_PREFIX)
    ]
    shown = subprocess.run(
        [str(ONETASKGRAPH), "config", "show", "--json"],
        cwd=REPO_ROOT,
        env=dict(os.environ),
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=True,
    )
    settings = {setting["key"]: setting for setting in json.loads(shown.stdout)["settings"]}
    assert not any(key.startswith("sources.hostonly.") for key in settings), sorted(settings)
    root = settings["sources.examples.config.root"]
    assert str(stated) not in str(root["value"]), root
    assert root["origin"]["layer"] == "file", root


# llmlint: ignore-block[expensive_tests_stay_behind_their_own_edge] This spawns one local
# `onetaskgraph config show` — no network, no model, under a second — to hold the same
# `plan_store_user_layer.isolate()` boundary the tests beside it hold in this project; no
# existing edge is narrower, and a project for one such call would split one boundary's tests.
def test_default_sources_the_host_states_in_the_environment_reach_no_test(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A host's default sources naming a source only its user layer declares are unset.

    This host's checkout `.env` states such a list; with the user layer held back, the store
    refused every read for the missing source. Read back through the installed store, which
    then answers from the tracked document's `default_sources`.
    """
    monkeypatch.setenv(plan_store_user_layer.DEFAULT_SOURCES_ENV, "authoring,private-plans")
    monkeypatch.delenv(plan_store_user_layer.HOST_CONFIG_HOME_ENV, raising=False)

    plan_store_user_layer.isolate()

    assert plan_store_user_layer.DEFAULT_SOURCES_ENV not in os.environ
    shown = subprocess.run(
        [str(ONETASKGRAPH), "config", "show", "--json"],
        cwd=REPO_ROOT,
        env=dict(os.environ),
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )
    assert shown.returncode == 0, shown.stderr
    settings = {setting["key"]: setting for setting in json.loads(shown.stdout)["settings"]}
    defaults = settings["default_sources"]
    assert "private-plans" not in str(defaults["value"]), defaults
    assert defaults["origin"]["layer"] == "file", defaults


# llmlint: ignore-end[expensive_tests_stay_behind_their_own_edge]


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


def _bun_cache_under(environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bun", "pm", "cache"],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )


def _as_bun_user(monkeypatch: pytest.MonkeyPatch, home: Path, config_home: Path | None) -> None:
    """This process as a user of ``home`` with no Bun cache stated in the environment."""
    monkeypatch.setenv("HOME", str(home))
    if config_home is None:
        monkeypatch.delenv(plan_store_user_layer.CONFIG_HOME_ENV, raising=False)
    else:
        monkeypatch.setenv(plan_store_user_layer.CONFIG_HOME_ENV, str(config_home))
    monkeypatch.delenv(plan_store_user_layer.HOST_CONFIG_HOME_ENV, raising=False)
    monkeypatch.delenv(plan_store_user_layer.BUN_CACHE_ENV, raising=False)


def test_bun_keeps_the_cache_the_users_npmrc_names_once_the_home_moves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `cache=` line in `~/.npmrc` still decides Bun's cache under the sandboxed home."""
    home = tmp_path / "home"
    home.mkdir()
    cache = tmp_path / "npm-cache"
    (home / ".npmrc").write_text(f"cache={cache}\n", encoding="utf-8")
    _as_bun_user(monkeypatch, home, None)

    sandbox = plan_store_user_layer.isolate()

    assert os.environ[plan_store_user_layer.CONFIG_HOME_ENV] == str(sandbox)
    assert os.environ[plan_store_user_layer.BUN_CACHE_ENV] == str(cache)
    assert _bun_cache_under(dict(os.environ)).stdout.strip() == str(cache)
    # Without the stated cache Bun reads `.npmrc` from the moved home and loses the line.
    unstated = {
        key: value
        for key, value in os.environ.items()
        if key != plan_store_user_layer.BUN_CACHE_ENV
    }
    assert _bun_cache_under(unstated).stdout.strip() != str(cache)


def test_a_bun_cache_the_environment_already_states_is_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An explicit `BUN_INSTALL_CACHE_DIR` wins over what the user's `.npmrc` names."""
    home = tmp_path / "home"
    home.mkdir()
    (home / ".npmrc").write_text(f"cache={tmp_path / 'npm-cache'}\n", encoding="utf-8")
    _as_bun_user(monkeypatch, home, None)
    stated = tmp_path / "stated-cache"
    monkeypatch.setenv(plan_store_user_layer.BUN_CACHE_ENV, str(stated))

    plan_store_user_layer.isolate()

    assert os.environ[plan_store_user_layer.BUN_CACHE_ENV] == str(stated)
    assert _bun_cache_under(dict(os.environ)).stdout.strip() == str(stated)


def test_a_bun_that_cannot_answer_leaves_its_cache_unstated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `bun pm cache` that fails states nothing, rather than a cache Bun never named."""
    config_home = tmp_path / "config"
    config_home.mkdir()
    (config_home / ".bunfig.toml").write_text("this is [not toml\n", encoding="utf-8")
    _as_bun_user(monkeypatch, tmp_path / "home", config_home)
    refused = _bun_cache_under(dict(os.environ))
    assert refused.returncode != 0, f"Bun answered, so this proves nothing: {refused.stdout}"

    sandbox = plan_store_user_layer.isolate()

    assert os.environ[plan_store_user_layer.CONFIG_HOME_ENV] == str(sandbox)
    assert plan_store_user_layer.BUN_CACHE_ENV not in os.environ
