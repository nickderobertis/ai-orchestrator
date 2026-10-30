"""A onetaskgraph user layer installed for one journey: the tracked example's shape.

A host keeps its private sources, and the routes that send an organization's work to its
own tracker, in onetaskgraph's user layer — `${XDG_CONFIG_HOME}/onetaskgraph/config.yaml` —
outside every checkout, beneath this repository's `onetaskgraph.yaml`. The tracked
`config/onetaskgraph.user-layer.example.yaml` states that layer's shape over synthetic
names, and `tests/plan_store_user_layer.py` keeps the host's real layer out of every process the
suite starts. So a journey about a host layer installs its own: the example as it is, or the
example with a Linear source replaced by a folder of Markdown — no check may reach a Linear
workspace — whose status mapping reads the state names the example's Linear mapping writes,
resolved through the store itself. The tracked configuration switches the store's public
boundary on, so the example declares every source it defines private, as a host doing an
organization's work declares them before adopting it, and a stand-in keeps that declaration.

`installed()` writes the layer under a configuration home of the journey's own that mirrors
the one this process was handed entry by entry, so every other tool reads what it read
before; a journey points `XDG_CONFIG_HOME` at what it answers.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Collection, Mapping
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml
from published_tools import ONETASKGRAPH_BIN

from orchestrator.root import REPO_ROOT

#: The tracked shape of a host's user layer, every name in it synthetic.
EXAMPLE = REPO_ROOT / "config" / "onetaskgraph.user-layer.example.yaml"

#: The variable a process reads its configuration home from.
XDG_CONFIG_HOME = "XDG_CONFIG_HOME"

#: The organization's two trackers the example declares, and the repositories routed there.
ORGANIZATION_PLANS = "hiddenco-plans"
ORGANIZATION_FOLLOWUPS = "hiddenco-followups"
ORGANIZATION_REPOSITORIES = "github.com/hiddenco/*"
#: The host's private stores; plans routes the mixed owner's private work to PRIVATE_PLANS.
PRIVATE_PLANS = "private-plans"
PRIVATE_FOLLOWUPS = "private-followups"
#: The credential the example's Linear sources name (`config/onetaskgraph.user-layer.example.yaml`);
#: the tracked document declares none.
LINEAR_KEY_ENV = "HIDDENCO_LINEAR_API_KEY"
#: Where a journey points the example's Linear sources: a loopback port nothing listens on, so a
#: request to one is refused on this machine and never reaches a Linear workspace.
UNSERVED_LINEAR = "http://127.0.0.1:9/graphql"


def example() -> dict[str, Any]:
    """The example layer, as the document it is."""
    document = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    assert isinstance(document, dict) and isinstance(document.get("sources"), dict), EXAMPLE
    return document


def _mirrored(home: Path) -> None:
    """Make ``home`` carry every entry of this process's configuration home but onetaskgraph's
    layer, each linked rather than copied, and each left as it is when already there."""
    source = Path(os.environ.get(XDG_CONFIG_HOME) or Path.home() / ".config")
    home.mkdir(parents=True, exist_ok=True)
    if not source.is_dir():
        return
    for entry in sorted(source.iterdir()):
        if entry.name == "onetaskgraph" or (home / entry.name).is_symlink():
            continue
        (home / entry.name).symlink_to(entry)
    store = source / "onetaskgraph"
    if store.is_dir():
        (home / "onetaskgraph").mkdir(exist_ok=True)
        for entry in sorted(store.iterdir()):
            linked = home / "onetaskgraph" / entry.name
            if entry.name != "config.yaml" and not linked.is_symlink():
                linked.symlink_to(entry)


def installed(home: Path, document: Mapping[str, Any] | None = None) -> Path:
    """Install ``document`` (the example unless named) as the user layer of ``home``.

    Answers ``home``, the configuration home a journey's `XDG_CONFIG_HOME` names.
    """
    _mirrored(home)
    layer = home / "onetaskgraph" / "config.yaml"
    layer.parent.mkdir(parents=True, exist_ok=True)
    layer.write_text(
        yaml.safe_dump(dict(example() if document is None else document), sort_keys=False),
        encoding="utf-8",
    )
    return home


def resolved(home: Path, cwd: Path = REPO_ROOT) -> dict[str, object]:
    """Every setting the installed CLI resolves in ``cwd`` with ``home``'s user layer beneath.

    Every `ONETASKGRAPH_` name of this process is left out, so what is read is the two
    documents rather than a layer a launch or this suite stated over them.
    """
    environment = {
        name: value for name, value in os.environ.items() if not name.startswith("ONETASKGRAPH_")
    }
    environment[XDG_CONFIG_HOME] = str(home)
    shown = subprocess.run(  # noqa: S603 - the installed plan-store CLI
        [str(ONETASKGRAPH_BIN), "--json", "config", "show"],
        cwd=cwd,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert shown.returncode == 0, shown.stderr
    return {str(entry["key"]): entry["value"] for entry in json.loads(shown.stdout)["settings"]}


def example_states(source: str, scratch: Path) -> dict[str, str]:
    """The state name each category of the example's ``source`` is written as, per kind.

    Keyed `category` or `category.kind`, as the store resolves a per-kind mapping, and read
    through the store with the example installed under ``scratch``.
    """
    settings = resolved(installed(scratch / "example-config-home"))
    prefix = f"sources.{source}.config.status_mapping."
    states = {
        key[len(prefix) :]: str(value) for key, value in settings.items() if key.startswith(prefix)
    }
    assert states, f"the example layer maps no state for {source}"
    return states


def stand_in(
    source: str, root: Path, scratch: Path, copied: Collection[str] = ()
) -> dict[str, Any]:
    """A `local-md` source at ``root`` reading the state names ``source`` writes.

    Declared `visibility: private`, as the example declares the source it stands in for.

    ``copied`` names each status a journey copies onto the stand-in carrying that category's
    own word as its name. Linear writes a copy's status by category, through its mapping; a
    folder takes the name it is handed and reads it back through a mapping of names, without
    case. So where a category's word is also the name of a state of *another* category —
    `backlog`, the proposal a follow-up ticket is filed as, against Linear's own `Backlog`,
    the state a person defers to — that state is left out and the word maps to its own
    category, which is the reading Linear gives the copy.
    """
    words = {word.casefold() for word in copied}
    mapping = {
        state: key.split(".")[0]
        for key, state in example_states(source, scratch).items()
        if state.casefold() not in words
    } | {word: word for word in copied}
    root.mkdir(parents=True, exist_ok=True)
    # llmlint: ignore-block[e2e_not_mocked] A local `local-md` source stands in for the
    # example's Linear source, because no check may reach a Linear workspace; the example's
    # Linear configuration is held to the plugin's own schema by
    # `tests/test_plan_source_roots.py` instead.
    return {
        "visibility": "private",
        "plugin": "local-md",
        "config": {"root": str(root), "status_mapping": mapping},
    }
    # llmlint: ignore-end[e2e_not_mocked]


def without_private_stores(document: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """``document`` (the example unless named) without the host's own private stores.

    The store reads every source declared private on each dependency read, and a journey
    about an organization's routing holds no credential for the host's own boards.
    Remove routes to those destinations with them.
    """
    layer = dict(example() if document is None else document)
    layer["sources"] = {
        name: dict(source)
        for name, source in layer["sources"].items()
        if name not in (PRIVATE_PLANS, PRIVATE_FOLLOWUPS)
    }
    # A removed destination cannot remain in a route: combined private routes require
    # a destination declared private at load time.
    for source in layer["sources"].values():
        if "routes" in source:
            source["routes"] = [
                route
                for route in source["routes"]
                if route["to"] not in (PRIVATE_PLANS, PRIVATE_FOLLOWUPS)
            ]
    return layer


def with_stand_ins(
    stand_ins: Mapping[str, Mapping[str, Any]], document: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """``document`` (the example unless named) with each named source replaced whole."""
    layer = dict(example() if document is None else document)
    sources = dict(layer["sources"])
    for name, replacement in stand_ins.items():
        assert name in sources, f"the layer declares no {name!r} source to stand in for"
        sources[name] = dict(replacement)
    layer["sources"] = sources
    return layer


class HostState(StrEnum):
    """The three hosts every plan-store recipe is driven on, the store's boundary switched on.

    `no-user-layer` is a host doing no organization's work, which declares nothing beneath the
    tracked document. `organization-private` declares the organization's sources and its own
    private stores private in its user layer, the example's shape over synthetic names, before
    adopting the tracked document. `organization-uncredentialed` is that layer in a process
    holding no credential for the organization's sources, as a dispatch whose harness masks it.
    """

    NO_USER_LAYER = "no-user-layer"
    ORGANIZATION_PRIVATE = "organization-private"
    ORGANIZATION_UNCREDENTIALED = "organization-uncredentialed"


def host_state(state: HostState, scratch: Path) -> dict[str, str | None]:
    """What places a process on ``state``: each variable to set, or to unset where `None`.

    The layer is the example (:data:`EXAMPLE`), every source of
    it declared private, installed under ``scratch``. The host's own two private boards are
    stood in by folders in both organization states, because their GitHub endpoint is not the
    loopback board and no check may reach the live one. The organization's two Linear trackers
    are stood in by folders where they are credentialed, because no check may reach a Linear
    workspace; uncredentialed, they stay Linear sources whose credential is unset and whose
    endpoint is :data:`UNSERVED_LINEAR`, so even a request made past it stays on this machine.
    """
    if state is HostState.NO_USER_LAYER:
        return {}
    boards = (PRIVATE_PLANS, PRIVATE_FOLLOWUPS)
    trackers = (
        ORGANIZATION_PLANS,
        ORGANIZATION_FOLLOWUPS,
    )
    stood_in = boards if state is HostState.ORGANIZATION_UNCREDENTIALED else (*boards, *trackers)
    layer = with_stand_ins(
        {name: stand_in(name, scratch / "stand-ins" / name, scratch) for name in stood_in}
    )
    if state is HostState.ORGANIZATION_UNCREDENTIALED:
        for name in trackers:
            layer["sources"][name]["config"]["endpoint"] = UNSERVED_LINEAR
    home = installed(scratch / "config-home", layer)
    return {XDG_CONFIG_HOME: str(home), LINEAR_KEY_ENV: None}
