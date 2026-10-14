"""The suite's plan-store cache boundary, held from the side of a process it spawns.

`tests/plan_store_cache.py` is the rule and `tests/conftest.py` arms it. These drive the
locked plan-store CLI through `subprocess`, the way every journey starts one, and never read,
list or compare the host's own cache directory: an "outside" location here is one nothing
creates, because a spawn the guard refuses is never started.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import plan_store_cache
import pytest
from published_tools import ONETASKGRAPH_BIN

from orchestrator.root import REPO_ROOT

#: A location outside every temporary tree, named rather than looked up so no test reads
#: the host's real cache to find one.
ELSEWHERE = "/nonexistent/plan-store-cache-outside-the-suite"


def _spawned(environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(ONETASKGRAPH_BIN), "--version"],
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )


def test_the_environment_the_suite_hands_a_process_keeps_its_cache_under_pytest_s_base(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """Every cache location a spawned store would resolve from the suite's own environment
    is under pytest's base, and it starts."""
    base = tmp_path_factory.getbasetemp()
    assert plan_store_cache.outside(dict(os.environ), os.getcwd(), base) == []
    home = Path(os.environ[plan_store_cache.CACHE_HOME_ENV])
    assert home.is_relative_to(base), home
    assert _spawned(dict(os.environ)).returncode == 0


@pytest.mark.parametrize(
    ("variable", "value"),
    [
        (plan_store_cache.CACHE_HOME_ENV, ELSEWHERE),
        ("ONETASKGRAPH_SOURCES__FOLLOWUPS__CACHE__DIR", ELSEWHERE),
    ],
)
def test_a_spawn_reaching_a_cache_outside_the_tree_is_refused_naming_it(
    variable: str, value: str
) -> None:
    """A cache home or a source's `cache.dir` pointed outside fails the spawn, by name."""
    with pytest.raises(RuntimeError, match="plan-store cache outside") as refused:
        _spawned({**os.environ, variable: value})
    assert ELSEWHERE in str(refused.value)


def test_the_home_a_store_falls_back_to_is_held_to_the_tree_too(tmp_path: Path) -> None:
    """With no `XDG_CACHE_HOME`, `$HOME/.cache` is the cache home: outside is refused,
    a `HOME` under the test's own directory starts."""
    unset = {
        key: value for key, value in os.environ.items() if key != plan_store_cache.CACHE_HOME_ENV
    }
    with pytest.raises(RuntimeError, match="plan-store cache outside"):
        _spawned({**unset, "HOME": ELSEWHERE})
    assert _spawned({**unset, "HOME": str(tmp_path)}).returncode == 0


def test_a_process_with_no_store_to_reach_is_not_held_to_the_tree() -> None:
    """A shell filter started with a home outside is started: it can start no store.

    The cache home is still the suite's for it, from `tests/conftest.py`; what is narrowed is
    only which spawns fail a test, so one whose environment is the subject of its own test
    (a `HOME` a redaction filter is handed) keeps that environment.
    """
    started = subprocess.run(
        ["bash", "-c", "true"],
        env={"PATH": "/usr/bin:/bin", "HOME": ELSEWHERE},
        check=False,
    )
    assert started.returncode == 0
    assert plan_store_cache.reaches_the_store(["just", "plans"], os.getcwd())
    assert plan_store_cache.reaches_the_store(
        ["python3", "-m", "orchestrator.follow_up_tickets"], os.getcwd()
    )
    assert not plan_store_cache.reaches_the_store(
        ["python3", "-m", "orchestrator.no_such_module"], os.getcwd()
    )
    assert plan_store_cache.reaches_the_store(
        ["bash", "-c", "set -e; onetaskgraph --version"], os.getcwd()
    )
    assert plan_store_cache.reaches_the_store("just plans", os.getcwd())


def test_an_unguarded_block_states_why_and_holds_spawns_again_after_it() -> None:
    """The one exemption names its reason, and the guard is back once the block ends."""
    with pytest.raises(ValueError, match="states why"), plan_store_cache.unguarded(" "):
        pass
    outside = {**os.environ, plan_store_cache.CACHE_HOME_ENV: ELSEWHERE}
    with plan_store_cache.unguarded("this test's subject is the exemption itself"):
        assert _spawned(outside).returncode == 0
    with pytest.raises(RuntimeError, match="plan-store cache outside"):
        _spawned(outside)


@pytest.mark.reads_checkouts
@pytest.mark.parametrize("source", ["followups", "hellopatient-followups"])
def test_the_store_reads_a_source_s_cache_dir_from_the_variable_the_suite_drops(
    source: str, tmp_path: Path
) -> None:
    """The variable spelling the suite drops is the one the installed store resolves.

    Read through `config show`, whose answer names the layer each value came from, with every
    other `ONETASKGRAPH_` name left out, as `tests/test_plan_source_roots.py` reads the file.
    """
    variable = f"ONETASKGRAPH_SOURCES__{source.upper().replace('-', '_')}__CACHE__DIR"
    assert plan_store_cache.CACHE_DIR_VARIABLE.match(variable), variable
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("ONETASKGRAPH_")
    }
    named = tmp_path / "named"
    shown = subprocess.run(
        [str(ONETASKGRAPH_BIN), "--json", "config", "show"],
        cwd=REPO_ROOT,
        env={**environment, variable: str(named)},
        text=True,
        capture_output=True,
        check=False,
    )
    assert shown.returncode == 0, shown.stderr
    settings = {entry["key"]: entry["value"] for entry in json.loads(shown.stdout)["settings"]}
    assert settings.get(f"sources.{source}.cache.dir") == str(named), settings


@pytest.mark.reads_checkouts
def test_each_kept_tool_caches_under_the_cache_home_in_the_directory_the_suite_keeps(
    tmp_path: Path,
) -> None:
    """uv, Playwright and llmlint each answer where their cache goes when the home moves.

    Asked of each tool, with its own variable unset and the cache home under this test's
    directory, so `KEPT` restates what the installed tools do rather than what they once did.
    """
    home = tmp_path / "cache"
    environment = {
        **{key: value for key, value in os.environ.items() if key not in plan_store_cache.KEPT},
        plan_store_cache.CACHE_HOME_ENV: str(home),
    }
    uv = subprocess.run(
        ["uv", "cache", "dir"], env=environment, text=True, capture_output=True, check=False
    )
    assert uv.returncode == 0, uv.stderr
    assert Path(uv.stdout.strip()) == home / plan_store_cache.KEPT["UV_CACHE_DIR"], uv.stdout
    playwright = subprocess.run(
        [str(REPO_ROOT / "node_modules" / ".bin" / "playwright"), "install", "--dry-run"],
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert playwright.returncode == 0, playwright.stderr
    located = re.findall(r"Install location:\s+(\S+)", playwright.stdout)
    assert located, playwright.stdout
    assert {Path(path).parent for path in located} == {
        home / plan_store_cache.KEPT["PLAYWRIGHT_BROWSERS_PATH"]
    }, playwright.stdout
    llmlint = subprocess.run(
        ["llmlint", "plugins", "--format", "json"],
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert llmlint.returncode == 0, llmlint.stderr
    reported = Path(json.loads(llmlint.stdout)["dir"])
    assert reported == home / plan_store_cache.KEPT["LLMLINT_CACHE_DIR"], llmlint.stdout
