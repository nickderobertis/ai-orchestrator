"""The plan store in each host state the tracked public boundary meets.

The tracked `onetaskgraph.yaml` switches the store's public boundary on and declares
`authoring` private, so every host reads its sources screened. A host meets that in one of
three states (`onetaskgraph_user_layer.HostState`): no user layer at all; a user layer of
`config/onetaskgraph.user-layer.example.yaml`'s shape, over synthetic names, declaring its
organization's sources and its own stores private; and that layer in a process holding no
credential for the organization's sources. The journey here reads the store's configuration
and its sources in all three, as every recipe over the store does. How a plan's records are
screened and routed when written or copied is the plan store's own, journeyed in its
repository; the planning road over them is `tests/e2e/planning/`'s.

Nothing is stood in: the store, its layer and `onevcs` are the real ones.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

import boundary_home
import onetaskgraph_user_layer
import pytest
from github_board import AUTHORING_ROOT_ENV, AUTHORING_SOURCE
from onetaskgraph_user_layer import HostState, host_state
from published_tools import ONETASKGRAPH_BIN
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: The organization's two trackers the example layer declares private.
ORGANIZATION = (
    onetaskgraph_user_layer.ORGANIZATION_PLANS,
    onetaskgraph_user_layer.ORGANIZATION_FOLLOWUPS,
)


class Host(NamedTuple):
    """The state a journey runs in, and the folder each stood-in private source writes."""

    state: HostState
    scratch: Path


#: The binaries `bun.lock` installs that a decoy shadows on every journey's search path.
DECOYED = ("onepipeline", "onepipeline-api")


@contextlib.contextmanager
def _decoys_ahead(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """An `onepipeline` and an `onepipeline-api` first on the search path every recipe here
    inherits, each refusing and recording that it ran; and, once the journey is over, that
    none ran — so every engine call the journey makes reached the locked npm install rather
    than whichever binary of that name a host's PATH offers first."""
    decoys, ran = tmp_path / "decoys", tmp_path / "decoys-ran.log"
    decoys.mkdir()
    for name in DECOYED:
        decoy = decoys / name
        decoy.write_text(
            f'#!/bin/sh\necho "{name} $*" >> "{ran}"\n'
            f'echo "decoy {name}: not the binary bun.lock installs" >&2\nexit 97\n',
            encoding="utf-8",
        )
        decoy.chmod(0o755)
    monkeypatch.setenv("PATH", os.pathsep.join((str(decoys), os.environ.get("PATH", ""))))
    yield
    assert not ran.exists(), f"a decoy ahead of the locked install ran: {ran.read_text()}"


@pytest.fixture(params=list(HostState), ids=[state.value for state in HostState])
def host(
    request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Host]:
    """This test's process, and every one it starts, on one host state, with a decoy engine
    and read API ahead of the locked ones on its search path (:func:`_decoys_ahead`).

    The `onevcs` state root answering the write policy registers every public repository the
    fixtures name, and the authoring root is the journey's own.
    """
    state = HostState(request.param)
    for name, value in host_state(state, tmp_path).items():
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    boundary_home.answering(monkeypatch)
    authoring = tmp_path / "authoring"
    authoring.mkdir()
    monkeypatch.setenv(AUTHORING_ROOT_ENV, str(authoring))
    with _decoys_ahead(tmp_path, monkeypatch):
        yield Host(state, tmp_path)


def _run(command: list[str], **stated: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - this checkout's recipes and the installed CLIs
        command,
        cwd=REPO_ROOT,
        env={**os.environ, **stated},
        text=True,
        capture_output=True,
        stdin=subprocess.DEVNULL,
        timeout=e2e_timeout(300),
        check=False,
    )


def _store(*arguments: str) -> object:
    shown = _run([str(ONETASKGRAPH_BIN), "--json", *arguments])
    assert shown.returncode == 0, shown.stderr
    return json.loads(shown.stdout)


def test_each_state_reads_the_organization_s_sources_as_its_layer_declares_them(
    host: Host,
) -> None:
    """What every journey here runs under, which a journey passing under no layer would not show.

    The tracked policy and `authoring` private in every state; the organization's sources
    absent with no layer, private and answering with it, and private and unable to
    authenticate without their credential.
    """
    shown = _store("config", "show")
    assert isinstance(shown, dict)
    settings = {str(entry["key"]): entry["value"] for entry in shown["settings"]}
    listed = _store("sources", "list")
    assert isinstance(listed, list)
    states = {str(source["source"]): source for source in listed}

    assert settings.get("write_policy.check_command"), settings
    assert settings.get(f"sources.{AUTHORING_SOURCE}.visibility") == "private", settings
    for name in ORGANIZATION:
        if host.state is HostState.NO_USER_LAYER:
            assert name not in states, states
            continue
        assert settings.get(f"sources.{name}.visibility") == "private", settings
        if host.state is HostState.ORGANIZATION_PRIVATE:
            assert states[name]["state"] != "unavailable", states[name]
        else:
            assert states[name]["state"] == "unavailable", states[name]
            assert states[name]["error"]["kind"] == "auth", states[name]
