"""The plan store's cache, held inside this process's own temporary tree.

The adopted onetaskgraph keeps a remote source's cache under
`${XDG_CACHE_HOME:-$HOME/.cache}/onetaskgraph/<digest of the remote>`, or under a source's
`cache.dir`, which `ONETASKGRAPH_SOURCES__<NAME>__CACHE__DIR` sets from the environment, and
this checkout's `onetaskgraph.yaml` turns it on for the four board sources. Left at the
host's cache home, every journey that points `followups` or `plans` at a loopback board
would write entries into the directory every manager's process on this host reads, and a
journey's own reads could be served from entries an earlier run left there — so a verdict
would depend on what the host happened to hold. `tests/conftest.py` points the cache home
at a directory under pytest's base — each test's own, each module's for its module-scoped
fixtures, the session's for the rest — for every process this one spawns, drops
any inherited `cache.dir` variable, and installs `guard` so a spawn that can start a store
and whose environment would still reach a cache elsewhere fails the test that made it.

`XDG_CACHE_HOME` is also where uv and Playwright keep what they install and llmlint keeps
the plugins it fetched, so moving it would send every `uv run` a journey spawns to download
its packages again; `KEPT` names those and
`tests/conftest.py` states each at the host's own location before the move, so they read
exactly what they read before.
"""

from __future__ import annotations

import os
import re
import shlex
import sys
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path

from orchestrator.root import REPO_ROOT

#: The variable the store reads its default cache home from, before `$HOME/.cache`.
CACHE_HOME_ENV = "XDG_CACHE_HOME"
#: A source's `cache.dir` stated in the environment, the store's layered spelling of it.
CACHE_DIR_VARIABLE = re.compile(r"^ONETASKGRAPH_SOURCES__[A-Z0-9_]+__CACHE__DIR$")
#: The other tools whose default cache follows `XDG_CACHE_HOME`, each with the directory
#: under the cache home it defaults to: kept where the host keeps them.
KEPT = {
    "UV_CACHE_DIR": "uv",
    "PLAYWRIGHT_BROWSERS_PATH": "ms-playwright",
    "LLMLINT_CACHE_DIR": "llmlint/plugins",
}
#: The executables that start a store reading this checkout's configuration: the store
#: itself, the engine and the read API that link it, and the recipes that reach any of them.
STORE_ENTRYPOINTS = frozenset({"onetaskgraph", "onepipeline", "onepipeline-api", "just"})
#: Where this checkout's own wrappers over those live.
SCRIPTS = REPO_ROOT / "scripts"

_tree: Path | None = None
_installed = False


def host_cache_home(environment: Mapping[str, str]) -> Path | None:
    """The cache home ``environment`` names: `XDG_CACHE_HOME`, else `$HOME/.cache`, else none.

    The store's own rule (`onetaskgraph-core`'s `cache_home`), an empty value read as unset.
    """
    if xdg := environment.get(CACHE_HOME_ENV):
        return Path(xdg)
    if home := environment.get("HOME"):
        return Path(home) / ".cache"
    return None


def locations(environment: Mapping[str, str], cwd: str | os.PathLike[str]) -> list[Path]:
    """Every directory a store started with ``environment`` in ``cwd`` could cache under.

    A relative value is the process's own business, so it is measured from ``cwd``, where
    the process would measure it.
    """
    named = [host_cache_home(environment)]
    named += [Path(value) for key, value in environment.items() if CACHE_DIR_VARIABLE.match(key)]
    return [Path(os.path.realpath(Path(cwd) / path)) for path in named if path is not None]


def outside(environment: Mapping[str, str], cwd: str | os.PathLike[str], tree: Path) -> list[Path]:
    """The cache locations ``environment`` reaches that are not under ``tree``."""
    held = Path(os.path.realpath(tree))
    return [path for path in locations(environment, cwd) if not path.is_relative_to(held)]


def handed() -> dict[str, str]:
    """The cache home this process hands every process it spawns, as one stated variable.

    For an environment a journey builds from scratch rather than over this process's own:
    stated beside its `HOME`, so a store it starts caches where every other one does.
    """
    return {CACHE_HOME_ENV: os.environ[CACHE_HOME_ENV]}


def _words(argv: object) -> list[str]:
    """Every word ``argv`` hands a process, a shell's command string split into its own.

    A word that will not split as a shell would — an unbalanced quote — is kept whole.
    """
    match argv:
        case str() | bytes():
            given = [os.fsdecode(argv)]
        case Sequence():
            given = [os.fsdecode(w) for w in argv if isinstance(w, str | bytes | os.PathLike)]
        case _:
            return []
    words: list[str] = []
    for word in given:
        try:
            words += shlex.split(word)
        except ValueError:
            words.append(word)
    return words


def reaches_the_store(argv: object, cwd: str | os.PathLike[str]) -> bool:
    """Whether a process started as ``argv`` can start a plan store of this checkout.

    One of :data:`STORE_ENTRYPOINTS`, a wrapper under :data:`SCRIPTS`, or one of this
    checkout's own `orchestrator` modules run with `-m`, named anywhere among its words — a
    shell's command string included. A process naming none of these — a shell filter, a
    stand-in module — has no store to reach, whatever its environment says. A relative path
    is measured from ``cwd``, where the process would measure it.
    """
    words = _words(argv)
    for at, word in enumerate(words):
        if Path(word).name in STORE_ENTRYPOINTS or (Path(cwd) / word).is_relative_to(SCRIPTS):
            return True
        if word == "-m" and at + 1 < len(words) and words[at + 1].startswith("orchestrator."):
            module = words[at + 1].removeprefix("orchestrator.")
            if (REPO_ROOT / "orchestrator" / f"{module}.py").is_file():
                return True
    return False


def _audit(event: str, args: tuple[object, ...]) -> None:
    """Refuse a store-reaching spawn whose environment reaches a cache outside the tree."""
    if event != "subprocess.Popen" or _tree is None:
        return
    _executable, argv, cwd, env = args
    started_in = os.fsdecode(cwd) if isinstance(cwd, str | bytes | os.PathLike) else os.getcwd()
    if not reaches_the_store(argv, started_in):
        return
    environment = os.environ if env is None else env
    if not isinstance(environment, Mapping):
        return
    textual = {
        os.fsdecode(key): os.fsdecode(value)
        for key, value in environment.items()
        if isinstance(key, str | bytes) and isinstance(value, str | bytes)
    }
    escaped = outside(textual, started_in, _tree)
    if escaped:
        raise RuntimeError(
            f"{argv!r} would be started with a plan-store cache outside this test's "
            f"temporary tree {_tree}: {[str(path) for path in escaped]}. Point "
            f"{CACHE_HOME_ENV} (or HOME, when it is unset) and any source's cache.dir under "
            "tmp_path; tests/plan_store_cache.py says why"
        )


def guard(tree: Path) -> None:
    """Hold every later spawn of this process to a plan-store cache under ``tree``.

    An audit hook cannot be removed, so it is installed once and `release` disarms it.
    """
    global _tree, _installed  # noqa: PLW0603 - one hook per process, disarmed by `release`
    if not _installed:
        sys.addaudithook(_audit)
        _installed = True
    _tree = tree


def release() -> None:
    """Disarm the guard `guard` armed."""
    global _tree  # noqa: PLW0603 - see `guard`
    _tree = None


@contextmanager
def unguarded(reason: str) -> Iterator[None]:
    """Start the spawns inside this block unheld, for one whose subject is that cache home.

    Only for a journey proving that a script refuses a cache home it cannot use — a relative
    or a current-directory one — before it does anything: the unusable value is the input
    under test, and the refusal is what keeps a store from ever being reached with it.
    ``reason`` says that at the site, and a blank one is refused.
    """
    global _tree  # noqa: PLW0603 - see `guard`; put back as it was on the way out
    if not reason.strip():
        raise ValueError("an unguarded spawn states why its cache home is the subject")
    held, _tree = _tree, None
    try:
        yield
    finally:
        _tree = held
