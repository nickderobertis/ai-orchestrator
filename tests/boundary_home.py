"""An `onevcs` state root that answers the plan store's public-boundary questions for a test.

The tracked `onetaskgraph.yaml` switches the store's public boundary on: its `write_policy` asks
`onevcs boundary inspect` for the visibility of every repository an item names, and `onevcs
boundary check` before every write to a destination not verified private. Inspect answers
only for a repository the state root has **registered**, so under the root
`tests/onevcs_state_snapshot.py` exports — which registers none, on purpose — every
repository reads `unknown`, which is private, and every item naming one is refused at a
public board. That is the boundary working, and a test of
anything else needs the repositories its fixtures name to have a recorded visibility.

`registered_home()` builds such a root: the tracked rules composed through the real
`onevcs rules apply` with an overlay declaring each named identity's visibility — so the
host is never asked — and one stub checkout per identity, registered through the real
`onevcs register`, whose `origin` is that identity's GitHub URL. `environment()` points a
process at it with every git transport but `file` refused, the policy
`onevcs_state_snapshot.host_registry_environment()` states, so nothing that walks the
registered identities — a driver's idle pass, a sweep — can reach a network origin.
Nothing here doubles `onevcs`: it is the published CLI, answering from a registry the test
wrote through it.
"""

from __future__ import annotations

import atexit
import os
import shutil
import subprocess
import tempfile
from collections.abc import Iterable, Mapping
from pathlib import Path

import onevcs_state_snapshot
import pytest

from orchestrator import host_installs

REPO_ROOT = Path(__file__).resolve().parent.parent

#: The pinned `onevcs` the store's `write_policy` reaches through `PATH`.
ONEVCS = REPO_ROOT / ".venv" / "bin" / "onevcs"

#: The tracked base rules every composed root starts from.
TRACKED_RULES = REPO_ROOT / "config" / "onevcs.rules.yml"

#: This repository's identity, which most fixtures file their work against.
THIS_REPOSITORY = "github.com/nickderobertis/ai-orchestrator"

#: Every public repository a plan fixture of the suite names, registered `public` by
#: `answering()` unless a test names its own: this repository, each producer this host
#: installs a release of, the shipped examples' repositories and the synthetic ones fixtures
#: spell. A repository no root registers reads `unknown`, which is private.
FIXTURE_REPOSITORIES = (
    THIS_REPOSITORY,
    *(row.producer for row in host_installs.INSTALLED),
    "github.com/nickderobertis/some-service",
    "github.com/nickderobertis/some-client",
    "github.com/nickderobertis/some-docs",
    "github.com/nickderobertis/service",
    "github.com/nickderobertis/somewhere-else",
    "github.com/nickderobertis/unruled-producer",
    "github.com/scratchowner/service",
    "github.com/scratchowner/scratchname",
    "github.com/acme/app",
    "github.com/acme/unchanged",
    "github.com/a/b",
    "github.com/x/y",
)


def _split(identity: str) -> tuple[str, str, str]:
    host, owner, name = identity.removeprefix("https://").removesuffix(".git").split("/")
    return host, owner, name


def environment(
    base: Mapping[str, str] | None, home: Path, *, transports: bool = True
) -> dict[str, str]:
    """``base`` (this process's environment unless named) reading the state root ``home``.

    `PATH` leads with the pinned install's directory, because the tracked `write_policy`
    names `onevcs` by its bare name, and, with ``transports``, every git transport but `file`
    is refused — what a process that may walk the registered identities needs, and what a
    test cloning over a rewritten URL of its own cannot take.
    """
    composed = dict(os.environ if base is None else base)
    composed[onevcs_state_snapshot.ONEVCS_HOME] = str(home)
    composed["PATH"] = os.pathsep.join([str(ONEVCS.parent), composed.get("PATH", "")])
    if not transports:
        return composed
    count = int(composed.get(onevcs_state_snapshot.GIT_CONFIG_COUNT, "0") or "0")
    for key, value in onevcs_state_snapshot.TRANSPORT_POLICY:
        composed[f"GIT_CONFIG_KEY_{count}"] = key
        composed[f"GIT_CONFIG_VALUE_{count}"] = value
        count += 1
    composed[onevcs_state_snapshot.GIT_CONFIG_COUNT] = str(count)
    return composed


def _run(arguments: list[str], environment_: Mapping[str, str], cwd: Path | None = None) -> None:
    done = subprocess.run(
        arguments,
        cwd=cwd,
        env=dict(environment_),
        text=True,
        capture_output=True,
        check=False,
        timeout=120,
    )
    if done.returncode != 0:
        raise AssertionError(f"{arguments[:3]} failed: {done.stdout}{done.stderr}")


def registered_home(
    root: Path,
    public: Iterable[str] = (THIS_REPOSITORY,),
    private: Iterable[str] = (),
) -> Path:
    """A state root under ``root`` registering each identity, its visibility declared.

    ``public`` and ``private`` are `host/owner/name` identities. Answers the root, which
    `environment()` exports.
    """
    checkouts = root / "onevcs-checkouts"
    checkouts.mkdir(parents=True, exist_ok=True)
    public, private = list(public), list(private)
    home = declared_home(root, public, private)
    process = environment(None, home)
    declared = [*public, *private]
    for identity in declared:
        host, owner, name = _split(identity)
        checkout = checkouts / f"{host.replace('.', '-')}__{owner}__{name}"
        if checkout.exists():
            continue
        checkout.mkdir()
        _run(["git", "init", "-q", "-b", "main"], process, checkout)
        _run(
            ["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid"]
            + ["commit", "-q", "--allow-empty", "-m", "init"],
            process,
            checkout,
        )
        origin = f"https://{host}/{owner}/{name}.git"
        _run(["git", "remote", "add", "origin", origin], process, checkout)
        _run([str(ONEVCS), "register", str(checkout)], process)
    return home


#: The state roots this process built through `answering()`, by the identities they register.
_ANSWERING: dict[tuple[frozenset[str], frozenset[str]], Path] = {}


def answering(
    monkeypatch: pytest.MonkeyPatch,
    public: Iterable[str] = FIXTURE_REPOSITORIES,
    private: Iterable[str] = (),
) -> Path:
    """Point this test's process at a state root registering each identity, its visibility declared.

    Built once per process for each set of identities, because every identity costs a
    registration through the real `onevcs` and nothing a test writes reaches the root. Git's
    transports are left as they are: a test answered here walks no registered identity.
    """
    key = (frozenset(public), frozenset(private))
    home = _ANSWERING.get(key)
    if home is None:
        root = Path(tempfile.mkdtemp(prefix="boundary-home-"))
        atexit.register(shutil.rmtree, root, ignore_errors=True)
        home = registered_home(root, public=sorted(key[0]), private=sorted(key[1]))
        _ANSWERING[key] = home
    for name, value in environment(None, home, transports=False).items():
        if os.environ.get(name) != value:
            monkeypatch.setenv(name, value)
    return home


def declared_home(
    root: Path, public: Iterable[str] = FIXTURE_REPOSITORIES, private: Iterable[str] = ()
) -> Path:
    """A state root under ``root`` whose rules declare each identity's visibility, registering
    none of them.

    For a test that registers its own checkout of an identity — the one `onevcs resolve` then
    names as its publication checkout — and has the boundary classify that identity once it
    has: inspect answers only for a registered identity, from the rule declared here.
    """
    home = root / "onevcs-home"
    home.mkdir(parents=True, exist_ok=True)
    declared = [(identity, "public") for identity in public]
    declared += [(identity, "private") for identity in private]
    overlay = root / "onevcs-visibility.yml"
    lines = ["version: 4", "rules:"]
    for identity, visibility in declared:
        host, owner, name = _split(identity)
        lines.append(f"  - match: {{host: {host}, owner: {owner}, name: {name}}}")
        lines.append(f"    visibility: {visibility}")
    overlay.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _run(
        [str(ONEVCS), "rules", "apply", "--base", str(TRACKED_RULES), "--overlay", str(overlay)],
        environment(None, home, transports=False),
    )
    return home
