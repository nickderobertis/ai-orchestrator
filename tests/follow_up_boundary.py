"""The plan store's public boundary, answered for the follow-up suites' stand-in boards.

The tracked `onetaskgraph.yaml` lands the store's public boundary switched off, and these
suites cover it, so every environment composed here switches it on in a configuration home of
this process's own (`boundary_home.screened_config_home()`), which reads none of the host's
sources. Switched on, a follow-up ticket copied from the `drafts` source onto a board is
classified by the repositories it names — `onevcs boundary inspect` answering for each — and
refused at any board not declared `visibility: private` when one of them is not public. Under
the suite's exported `onevcs` state root no identity is registered, so every repository reads
`unknown`, which is private, and every copy onto a stand-in board would be refused.

So a stand-in board declares its visibility as the board it stands in for does — a stand-in
for the public `followups` board `public`, one for an organization's routed tracker
`private` — and the process reads a state root registering every public identity the
follow-up fixtures name, its visibility declared by rule. A routed fixture's repository is
registered nowhere, so it reads `unknown`, which is private, as an organization's does.
The root is built once per process through `boundary_home.registered_home()` — the real
`onevcs rules apply` and `onevcs register` — because every identity costs a registration
and no follow-up test writes to it.
"""

from __future__ import annotations

import atexit
import shutil
import tempfile
from collections.abc import Mapping
from pathlib import Path

import boundary_home
import onevcs_state_snapshot
import pytest

#: The variable the plan store reads its configuration home from.
CONFIG_HOME_ENV = "XDG_CONFIG_HOME"

#: Every public identity a follow-up fixture names, registered `public`.
PUBLIC_IDENTITIES = (
    boundary_home.THIS_REPOSITORY,
    "github.com/nickderobertis/some-service",
    "github.com/nickderobertis/another-service",
    "github.com/nickderobertis/other-service",
    "github.com/nickderobertis/oneharness",
    "github.com/nickderobertis/somewhere-else",
    "github.com/nickderobertis-fork/some-service",
    "github.com/o/s",
    "github.com/o/some-service",
    "github.com/o/other",
    "github.com/a/b",
    "github.com/example/service",
)

#: No private identity is registered: a repository the root does not register reads
#: `unknown`, which is private, so a routed fixture's repository is private by being absent.
PRIVATE_IDENTITIES: tuple[str, ...] = ()

#: The state root this process built, once `home()` has run.
_HOME: Path | None = None

#: The configuration home switching the boundary on that this process built, once
#: `config_home()` has run.
_CONFIG_HOME: Path | None = None


def home() -> Path:
    """The per-process state root registering every follow-up identity."""
    global _HOME  # noqa: PLW0603 - built once per process
    if _HOME is None:
        root = Path(tempfile.mkdtemp(prefix="follow-up-boundary-"))
        atexit.register(shutil.rmtree, root, ignore_errors=True)
        _HOME = boundary_home.registered_home(
            root, public=PUBLIC_IDENTITIES, private=PRIVATE_IDENTITIES
        )
    return _HOME


def config_home() -> Path:
    """The per-process configuration home whose plan-store user layer switches the boundary on."""
    global _CONFIG_HOME  # noqa: PLW0603 - built once per process
    if _CONFIG_HOME is None:
        root = Path(tempfile.mkdtemp(prefix="follow-up-screening-"))
        atexit.register(shutil.rmtree, root, ignore_errors=True)
        _CONFIG_HOME = boundary_home.screened_config_home(root)
    return _CONFIG_HOME


def visibility_variable(source: str) -> str:
    """The store's environment-layer name for ``source``'s declared visibility."""
    return f"ONETASKGRAPH_SOURCES__{source.upper().replace('-', '_')}__VISIBILITY"


def environment(
    base: Mapping[str, str] | None = None,
    *,
    public: tuple[str, ...] = (),
    private: tuple[str, ...] = (),
) -> dict[str, str]:
    """``base`` reading the follow-up state root, each named source's visibility declared."""
    composed = boundary_home.environment(base, home())
    composed[CONFIG_HOME_ENV] = str(config_home())
    for source in public:
        composed[visibility_variable(source)] = "public"
    for source in private:
        composed[visibility_variable(source)] = "private"
    return composed


def enable(
    monkeypatch: pytest.MonkeyPatch,
    *,
    public: tuple[str, ...] = (),
    private: tuple[str, ...] = (),
) -> None:
    """Point this test's process at the follow-up state root, declaring each source named."""
    composed = environment(public=public, private=private)
    for name in (onevcs_state_snapshot.ONEVCS_HOME, "PATH", "GIT_CONFIG_COUNT", CONFIG_HOME_ENV):
        monkeypatch.setenv(name, composed[name])
    for name, value in composed.items():
        if name.startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_", "ONETASKGRAPH_SOURCES__")):
            monkeypatch.setenv(name, value)


def declared_home(
    root: Path,
    public: tuple[str, ...] = PUBLIC_IDENTITIES,
    private: tuple[str, ...] = PRIVATE_IDENTITIES,
) -> Path:
    """`boundary_home.declared_home()` over the follow-up identities: rules, no registration."""
    return boundary_home.declared_home(root, public, private)
