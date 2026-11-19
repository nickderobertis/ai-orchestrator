#!/usr/bin/env python3
"""The two release-adoption rules only this host can answer, as a `plan check --check`.

Which adoptions can complete is `onevcs`'s to say, and the engine asks it at every plan load
(`onevcs::adoption_check`). Two rules read a fact `onevcs` does not hold — what *this host*
installs, :data:`orchestrator.host_installs.INSTALLED` — so they stay here:

* **uninstalled** — a node of this repository waiting on a release target whose artifact
  nothing here installs (a crate, or a package from a registry this host does not install
  that producer from): a dispatch here runs the engine's npm package, so such a wait is on
  something no dispatch ever runs;
* **default-taken** — a `published` node outside this repository, naming no `consumes`, behind
  a producer this host installs from (or one awaiting its first adoption): that producer's
  `default_target` is *this host's own* package, which says nothing about what the node's
  repository consumes.

It is a registered check of the engine's contract: the loaded plan on stdin, one JSON object
`{"refusals": [{node, field, reason}]}` on stdout and exit 0 when it ran. Every fact about a
repository comes from `onevcs` (`resolve`, `release targets --json`); a repository it cannot
answer for decides nothing. This checkout's own repository is its `origin` remote, as
`onevcs resolve` names its identity.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# The engine runs this file by its path, not as a module of an installed package, so this
# checkout's `orchestrator` package is importable only once its root is on `sys.path` above.
from orchestrator import host_installs  # noqa: E402 - needs the path set above
from orchestrator.record_shapes import (  # noqa: E402 - needs the path set above
    NodeId,
    hosted_origin,
)

#: How long one `onevcs` question may take before it is answered as unknown.
TIMEOUT_SECONDS = 60


def _asked(*arguments: str) -> dict[str, object] | None:
    """`onevcs`'s JSON answer, or ``None`` when it could not answer."""
    try:
        ran = subprocess.run(
            ["onevcs", *arguments],
            text=True,
            capture_output=True,
            timeout=TIMEOUT_SECONDS,
            check=False,
        )
        answer = json.loads(ran.stdout) if ran.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    return answer if isinstance(answer, dict) else None


#: An ssh remote's two forms, whose host and path `hosted_origin` reads once lifted out.
_SSH_REMOTE = re.compile(
    r"^(?:[^@/:]+@(?P<scp_host>[^:/]+):/?|ssh://(?:[^@/]+@)?(?P<ssh_host>[^/]+)/)(?P<path>.+)$"
)


def _own_origin() -> str | None:
    """This checkout's own normalized origin, read off its `origin` remote, or ``None``."""
    try:
        ran = subprocess.run(
            ["git", "-C", str(ROOT), "remote", "get-url", "origin"],
            text=True,
            capture_output=True,
            timeout=TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    url = ran.stdout.strip()
    ssh = _SSH_REMOTE.fullmatch(url)
    return hosted_origin(f"{ssh['scp_host'] or ssh['ssh_host']}/{ssh['path']}" if ssh else url)


class Host:
    """`onevcs`'s answers about the plan's repositories, each asked once."""

    def __init__(self) -> None:
        self._identities: dict[str, str | None] = {}
        self._targets: dict[str, dict[str, object] | None] = {}
        origin = _own_origin()
        self.own = None if origin is None else self.identity(origin) or origin

    def identity(self, repo: str) -> str | None:
        if repo not in self._identities:
            answer = _asked("resolve", repo)
            found = answer.get("identity") if answer else None
            self._identities[repo] = found if isinstance(found, str) and found else None
        return self._identities[repo]

    def targets(self, repo: str) -> dict[str, object] | None:
        if repo not in self._targets:
            self._targets[repo] = _asked("release", "targets", repo, "--json")
        return self._targets[repo]

    def is_own(self, repo: object) -> bool | None:
        if not isinstance(repo, str) or not repo or self.own is None:
            return None
        found = self.identity(repo)
        return None if found is None else found == self.own


class Release(NamedTuple):
    """A producer's `release targets --json` answer, decoded: who releases, and what."""

    identity: str | None
    #: Each target's artifact by its short name: the declaration's, else its probe's.
    artifacts: dict[str, str]
    default_target: str | None


class Node(NamedTuple):
    """The fields of one loaded plan node these rules read, decoded."""

    id: NodeId
    kind: str | None
    repo: str | None
    deps: tuple[NodeId, ...]
    consumes: dict[NodeId, str]
    adoption: str | None


def _text(value: object) -> str | None:
    """``value`` when it is a non-empty string, and ``None`` otherwise."""
    return value if isinstance(value, str) and value else None


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _listed(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def release(answer: Mapping[str, object]) -> Release | None:
    """``answer`` decoded, or ``None`` when it names no target: a producer releasing nothing."""
    if not _listed(answer.get("targets")):
        return None
    artifacts: dict[str, str] = {}
    for target in _listed(answer.get("targets")):
        name = _text(_mapping(target).get("name"))
        probed = _listed(_mapping(_mapping(target).get("probe")).get("args"))
        artifact = _text(probed[0]) if probed else None
        if name is not None and artifact is not None:
            artifacts[name] = artifact
    declared = _mapping(_mapping(answer.get("declaration")).get("declared"))
    for target in _listed(declared.get("target")):
        name, artifact = _text(_mapping(target).get("name")), _text(_mapping(target).get("id"))
        if name is not None and artifact is not None:
            artifacts[name] = artifact
    return Release(_text(answer.get("identity")), artifacts, _text(answer.get("default_target")))


def node(task: Mapping[str, object]) -> Node:
    """One loaded plan node, decoded; a field of the wrong shape reads as absent."""
    consumes = {
        NodeId(dependency): named
        for dependency, target in _mapping(task.get("consumes")).items()
        if isinstance(dependency, str) and (named := _text(target)) is not None
    }
    return Node(
        NodeId(str(task.get("id"))),
        _text(task.get("kind")),
        _text(task.get("repo")),
        tuple(NodeId(dep) for dep in _listed(task.get("deps")) if isinstance(dep, str)),
        consumes,
        _text(task.get("adoption")),
    )


def _releasing(consumer: Node, producer: Node, host: Host) -> Release | None:
    """The producer's release when it releases something into another repository."""
    if producer.repo is None:
        return None
    answer = host.targets(producer.repo)
    released = release(answer) if answer is not None else None
    if released is None:
        return None
    if consumer.repo is not None and host.identity(consumer.repo) == released.identity:
        return None
    return released


def _uninstalled(consumer: Node, dependency: NodeId, released: Release) -> dict[str, str] | None:
    target = consumer.consumes.get(dependency) or released.default_target
    artifact = released.artifacts.get(target) if target is not None else None
    if artifact is None or host_installs.by_artifact(artifact) is not None:
        return None
    installed = ", ".join(f"`{row.artifact}`" for row in host_installs.INSTALLED)
    return {
        "node": consumer.id,
        "field": "consumes",
        "reason": (
            f"uninstalled: this node of this host's repository waits on the "
            f"{target!r} target of {dependency!r}, the artifact `{artifact}`, "
            f"which nothing a dispatch here runs installs (this host installs "
            f"{installed}); name a package this host installs in "
            f"`consumes: {{{dependency}: <target>}}`"
        ),
    }


def _default_taken(consumer: Node, dependency: NodeId, released: Release) -> dict[str, str] | None:
    installs_it = released.identity is not None and (
        host_installs.by_producer(released.identity) is not None
        or released.identity in host_installs.AWAITING_FIRST_ADOPTION
    )
    if (
        consumer.adoption != "published"
        or dependency in consumer.consumes
        or released.default_target is None
        or not installs_it
    ):
        return None
    return {
        "node": consumer.id,
        "field": "consumes",
        "reason": (
            f"default-taken: this node adopts `published` behind {dependency!r} "
            f"naming no `consumes`, so it would wait on {released.identity}'s "
            f"default target {released.default_target!r}, which is the package this host "
            f"installs and says nothing about what {consumer.repo} consumes; name the "
            f"target it consumes in `consumes: {{{dependency}: <target>}}`"
        ),
    }


def refusals(plan: Mapping[str, object], host: Host) -> Iterator[dict[str, str]]:
    """Every node of ``plan`` either host-install rule refuses."""
    nodes = [node(task) for task in _listed(plan.get("tasks")) if isinstance(task, Mapping)]
    by_id = {one.id: one for one in nodes}
    for consumer in nodes:
        if consumer.kind in ("human", "command"):
            continue
        own = host.is_own(consumer.repo)
        for dependency in consumer.deps:
            producer = by_id.get(dependency)
            released = _releasing(consumer, producer, host) if producer is not None else None
            if released is None or own is None:
                continue
            refused = (
                _uninstalled(consumer, dependency, released)
                if own
                else _default_taken(consumer, dependency, released)
            )
            if refused is not None:
                yield refused


def main() -> int:
    """Read the loaded plan on stdin and answer the engine's check contract."""
    try:
        plan = json.load(sys.stdin)
    except ValueError as error:
        print(f"host_adoption_check: the plan on stdin is not JSON: {error}", file=sys.stderr)
        return 2
    if not isinstance(plan, Mapping):
        print("host_adoption_check: the plan on stdin is not a JSON object", file=sys.stderr)
        return 2
    json.dump({"refusals": list(refusals(plan, Host()))}, sys.stdout)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
