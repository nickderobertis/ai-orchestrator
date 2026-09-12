"""Refuse a plan whose release adoption could never complete on this host.

A node that waits on another repository's release, or that adopts one, states that
wait in three fields the engine reads — ``adoption``, ``consumes`` and its publication
policy — and several combinations of them fail only after every dispatch has been paid
for, or never fail at all:

* a ``published`` node behind a releasing dependency with **no target to wait on** is
  held for ever. The engine answers a target it cannot name as *not answered*, and "not
  answered" never releases a hold, so nobody is told anything until a surface is read;
* a ``fast`` node behind a releasing dependency, publishing under a policy that **opens
  no change request**, is published as a draft carrying a reason when the release is not
  out — and ``local-direct`` refuses a draft by name, so the node fails at its last step
  after hours of correct work;
* a node of this repository consuming a **crate or an npm launcher** waits on an artifact
  nothing here installs: a dispatch runs the engine wheel, and a crate reaches this host
  only through the wheel that links it;
* a node outside this repository taking a producer's ``default_target`` has taken **this
  host's own wheel**, which the override alone can set and which says nothing about
  what that node consumes.

Each is a field a matcher can read before anything is dispatched, and this module reads
them, modelled on :mod:`orchestrator.publication_guard`: every field leniently, ``onevcs``
through the same bounded subprocess and answered ``None`` when it cannot say, and every
rule **written to miss** for a repository this host cannot answer for — an unregistered
one, a declaration it cannot read, a CLI that is absent — because a plan is checked
against repositories this checkout may never have seen.

**What this module never reads is a task's prose.** Whether a plan *should* have
adopted, and whether an adopting task's criteria name the ``config/<pin>.version`` the
wheel governs — and the right one for the fix — are readings of meaning, and they are
`just review-plan`'s judged question; every refusal here that borders that question says
so, because two tiers refusing each other's required wording is the incident
:mod:`orchestrator.criteria_guard`'s docstring records. A node of this repository
adopting a wheel :mod:`orchestrator.host_installs` names is accepted here whatever its
criteria say.

Two shapes are not re-implemented, because the engine's own loader refuses them and
`just check-plan` runs that loader first: a ``consumes`` naming a node that is not one of
the node's ``deps``, and a ``consumes`` on a node whose publication opens no change
request.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections.abc import Iterator, Mapping
from enum import StrEnum
from typing import Any, NamedTuple

from orchestrator import host_installs
from orchestrator.plan_store import NodeId
from orchestrator.project_store import hosted_origin
from orchestrator.publication_guard import (
    HUMAN,
    ONEVCS,
    TIMEOUT_SECONDS,
    Destination,
    Refusal,
    Workflow,
    destination,
)
from orchestrator.root import REPO_ROOT

#: The tier that reads what this one deliberately does not: a task's prose. Named in
#: every refusal that borders its question, so a plan's author is sent to the right
#: reader rather than to a rewording this matcher would accept and that one refuse.
JUDGED_TIER = "just review-plan"

#: The tracked override `just repos-apply` installs as this host's `releases.yml`, which
#: is where a producer's `default_target` is set and the one remedy a refusal about a
#: missing default can name.
OVERRIDE = "config/onevcs.releases.yml"


class Adoption(StrEnum):
    """When a node launches relative to a dependency's release, in the engine's words.

    Two rungs the engine and `onevcs` share: `fast` launches on branch readiness and
    `published` holds until the release carrying the work exists. A word outside the two
    is answered as ``None`` — unknown — so a release that added a third would have this
    module decide nothing about it rather than guess which of these it meant.
    `tests/test_adoption_guard.py` reconciles the two against the installed `onevcs`
    rather than against this list, so a vocabulary that grew brings the enum due instead
    of leaving every node reading as unknown.
    """

    FAST = "fast"
    PUBLISHED = "published"

    @classmethod
    def known(cls, named: object) -> Adoption | None:
        """``named`` as an adoption, or ``None`` for anything that is not one."""
        if not isinstance(named, str):
            return None
        try:
            return cls(named)
        except ValueError:
            return None


class Target(NamedTuple):
    """One release target a repository resolves, as `onevcs release targets` lists it."""

    #: The short name a plan's `consumes` and the override's `default_target` use.
    name: str
    #: The registry-qualified artifact the producer's declaration gives that name
    #: (``pypi:onepipeline-cli``), or ``None`` when the answer carried none.
    artifact: str | None


class Targets(NamedTuple):
    """What `onevcs release targets <repo> --json` answers for one repository.

    The fields this module reads out of that verb's answer, narrowed where they are read.
    Held to the installed `onevcs` rather than to this declaration: the check-plan
    journeys in `tests/plan_tooling/test_check_plan_recipe_e2e.py` ask the real verb
    about real scratch producers and assert on what comes back through these fields.
    """

    #: The identity the repository resolves to, which is how two spellings of one
    #: repository are told to be one.
    identity: str
    #: The adoption the repository and global rungs resolve together — `fast` when the
    #: answer names none, which is `onevcs`'s own default, and ``None`` for a word this
    #: module does not know.
    adoption: Adoption | None
    #: The target a consumer naming no `consumes` entry gets, or ``None`` for none. Only
    #: the host override sets one, and `onevcs` refuses an override naming a target the
    #: producer does not resolve, so a default that arrives here is one of ``targets``.
    default_target: str | None
    targets: tuple[Target, ...]

    def named(self, name: str) -> Target | None:
        """The target called ``name``, or ``None`` when this repository resolves none."""
        return next((target for target in self.targets if target.name == name), None)

    @property
    def names(self) -> str:
        """Every target name, for a refusal that has to say what *is* resolvable."""
        return ", ".join(target.name for target in self.targets) or "none"


class Node(NamedTuple):
    """One node of a plan that may wait on a release, read at the plan's boundary."""

    id: NodeId
    #: The repository its work lands in, or ``None`` for a node naming none — which
    #: still has dependencies and may still hold on one.
    repo: str | None
    deps: tuple[NodeId, ...]
    #: The adoption the node states for itself, when it states one this module knows.
    adoption: Adoption | None
    #: What it consumes, keyed by dependency node id, kept as written: the engine has
    #: already refused a key naming a node outside `deps`, and each value is narrowed
    #: where a rule reads it.
    consumes: Mapping[str, Any]
    #: The publication policy it states for itself, when it states one the published
    #: vocabulary knows; ``None`` is a node publishing under its repository's.
    merge_policy: Workflow | None

    def consumes_from(self, dependency: str) -> str | None:
        """The target this node names for ``dependency``, or ``None`` where it names none."""
        named = self.consumes.get(dependency)
        return named if isinstance(named, str) and named else None


class AdoptionError(ValueError):
    """A plan node whose release adoption could never complete on this host."""


def nodes(plan: object) -> Iterator[Node]:
    """Every dispatched node of ``plan`` in the order stated, read leniently.

    Top-level tasks only, for the reason :func:`orchestrator.publication_guard.publishing_nodes`
    gives: a lifecycle node's steps are dispatches on the parent's branch, and the
    dependencies and the publication those steps end in are the parent's.
    """
    match plan:
        case {"tasks": [*tasks]}:
            pass
        case _:
            return
    for task in tasks:
        match task:
            # A guard rather than a value pattern, as `publishing_nodes` explains: a bare
            # name in a pattern captures, and would match every node.
            case {"kind": kind} if kind == HUMAN:
                continue
            case {"id": str(node_id)} if node_id:
                pass
            case _:
                continue
        repo = task.get("repo")
        deps = task.get("deps")
        consumes = task.get("consumes")
        policy = task.get("merge_policy")
        yield Node(
            NodeId(node_id),
            repo if isinstance(repo, str) and repo else None,
            tuple(NodeId(one) for one in deps if isinstance(one, str))
            if isinstance(deps, list)
            else (),
            Adoption.known(task.get("adoption")),
            consumes if isinstance(consumes, Mapping) else {},
            Workflow.known(policy) if isinstance(policy, str) else None,
        )


def _asked(arguments: list[str]) -> str | None:
    """What ``onevcs`` answered, or ``None`` when it could not answer at all.

    The same collapse :mod:`orchestrator.publication_guard` makes, for the same reason:
    an unregistered repository, a CLI that is not installed, and one that did not return
    are one answer to a plan's author, who is being told nothing rather than refused.
    """
    found = shutil.which(ONEVCS)
    if found is None:
        return None
    try:
        asked = subprocess.run(
            [found, *arguments],
            text=True,
            capture_output=True,
            timeout=TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return asked.stdout if asked.returncode == 0 else None


def _artifacts(answer: Mapping[str, Any]) -> dict[str, str]:
    """Each declared target's artifact id by name, from the producer's own declaration."""
    declaration = answer.get("declaration")
    declared = declaration.get("declared") if isinstance(declaration, Mapping) else None
    listed = declared.get("target") if isinstance(declared, Mapping) else None
    found: dict[str, str] = {}
    for target in listed if isinstance(listed, list) else []:
        match target:
            case {"name": str(name), "id": str(artifact)} if name and artifact:
                found[name] = artifact
            case _:
                continue
    return found


def _probed_artifact(target: Mapping[str, Any]) -> str | None:
    """The artifact a declared target's probe is asked about, or ``None``."""
    probe = target.get("probe")
    arguments = probe.get("args") if isinstance(probe, Mapping) else None
    match arguments:
        case [str(artifact), *_] if artifact:
            return artifact
        case _:
            return None


def targets(repo: str) -> Targets | None:
    """What `onevcs release targets` answers for ``repo``, or ``None`` when it cannot.

    The two optional fields are read as their absence means rather than as unavailable
    evidence: `onevcs` answers no `adoption` for a repository no rung names, which
    resolves `fast`, and no `default_target` for a producer the override gives none —
    and the rules decide on those resolved values. What is ``None`` is an answer that
    is not the shape this reads at all.
    """
    answered = _asked(["release", "targets", repo, "--json"])
    if answered is None:
        return None
    try:
        # llmlint: ignore[boundary_inputs_validated] `onevcs release targets`'s own JSON
        # answer; every field taken out of it is narrowed on the lines below.
        answer = json.loads(answered)
    except ValueError:
        return None
    match answer:
        case {"identity": str(identity), "targets": [*listed]} if identity:
            pass
        case _:
            return None
    artifacts = _artifacts(answer)
    resolved: list[Target] = []
    for target in listed:
        match target:
            case {"name": str(name)} if name:
                resolved.append(Target(name, artifacts.get(name) or _probed_artifact(target)))
            case _:
                continue
    default = answer.get("default_target")
    adoption = answer.get("adoption")
    return Targets(
        identity,
        Adoption.FAST if adoption is None else Adoption.known(adoption),
        default if isinstance(default, str) and default else None,
        tuple(resolved),
    )


def this_hosts_origin() -> str | None:
    """The normalized origin of this checkout's own repository, or ``None``.

    Read off the `origin` remote rather than spelled, so a fork of this harness checks
    plans against its own identity; ``None`` for a checkout with no such remote, which
    leaves the two rules about this repository's nodes deciding nothing.
    """
    try:
        asked = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "remote", "get-url", "origin"],
            text=True,
            capture_output=True,
            timeout=TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if asked.returncode != 0:
        return None
    return hosted_origin(_clone_url_path(asked.stdout.strip()))


#: The two forms an ssh remote takes — scp-like `git@github.com:owner/name.git` and
#: `ssh://git@github.com/owner/name.git` — which `hosted_origin` does not read: the host
#: and path are what that reader normalizes, so they are lifted out before it is asked.
_SSH_REMOTE = re.compile(
    r"^(?:[^@/:]+@(?P<scp_host>[^:/]+):/?|ssh://(?:[^@/]+@)?(?P<ssh_host>[^/]+)/)(?P<path>.+)$"
)


def _clone_url_path(url: str) -> str:
    """``url`` with an ssh remote rewritten to the `host/path` its origin is filed under."""
    ssh = _SSH_REMOTE.fullmatch(url)
    return f"{ssh['scp_host'] or ssh['ssh_host']}/{ssh['path']}" if ssh else url


class Host:
    """This host's answers about a plan's repositories, each asked once.

    A plan states the same repository on most of its nodes, and every rule asks about
    the same two or three things, so each verb is asked once per repository a plan names
    rather than once per node — and the answer is kept whether it was an answer or a
    silence, because a repository this host could not resolve once will not resolve on
    the next node either.
    """

    def __init__(self) -> None:
        self._targets: dict[str, Targets | None] = {}
        self._destinations: dict[str, Destination | None] = {}
        #: This host's own repository, asked of git once for the whole plan.
        self.own_origin = this_hosts_origin()

    def targets(self, repo: str) -> Targets | None:
        if repo not in self._targets:
            self._targets[repo] = targets(repo)
        return self._targets[repo]

    def destination(self, repo: str) -> Destination | None:
        if repo not in self._destinations:
            self._destinations[repo] = destination(repo)
        return self._destinations[repo]

    def is_own(self, repo: str | None) -> bool | None:
        """Whether ``repo`` is this host's own repository, or ``None`` when nobody can say.

        Compared as normalized origins: directly when ``repo`` is one, and through the
        identity `onevcs` resolves it to when it is an alias or a path. An identity whose
        origin is a path is not this host's hosted repository, which is an answer; an
        alias this host cannot resolve, a node naming no repository, or a checkout with
        no origin of its own, is not.
        """
        own = self.own_origin
        if own is None or repo is None:
            return None
        direct = hosted_origin(repo)
        if direct is not None:
            return direct == own
        resolved = self.destination(repo)
        if resolved is None or resolved.origin is None:
            return None
        return hosted_origin(resolved.origin) == own


def resolved_adoption(node: Node, host: Host) -> Adoption | None:
    """The adoption ``node`` launches under: its own, then its repository's, then `fast`.

    The engine's four rungs, with the repository and global ones answered together by
    `onevcs`. A repository this host cannot answer for resolves `fast`, which is what the
    engine resolves for a node whose repository names no rung; ``None`` only when the
    answer that decides it is a word this module does not know.
    """
    if node.adoption is not None:
        return node.adoption
    answered = host.targets(node.repo) if node.repo is not None else None
    return Adoption.FAST if answered is None else answered.adoption


def publication(node: Node, host: Host) -> Workflow | None:
    """The policy ``node`` publishes under: its own, else its repository's, else unknown."""
    if node.merge_policy is not None:
        return node.merge_policy
    resolved = host.destination(node.repo) if node.repo is not None else None
    return None if resolved is None else resolved.workflow


def releasing(node: Node, dependency: Node, host: Host) -> Targets | None:
    """``dependency``'s targets when landing there releases something ``node`` waits on.

    The engine's own rule, restated: a dependency releases when its repository is
    outside the depending node's own and resolves at least one target. One whose node
    names no repository, whose repository resolves no target, or whose repository this
    host cannot answer for releases nothing — and two spellings of one repository are
    told apart by the identity `onevcs` resolves each to, where it resolves both.
    """
    if dependency.repo is None:
        return None
    answered = host.targets(dependency.repo)
    if answered is None or not answered.targets:
        return None
    if node.repo is not None:
        if node.repo == dependency.repo:
            return None
        own = host.targets(node.repo)
        if own is not None and own.identity == answered.identity:
            return None
    return answered


def resolved_target(node: Node, dependency: str, answered: Targets) -> Target | None:
    """The target ``node`` waits on from ``dependency``: named, else the default, else none.

    A named target the producer does not resolve is ``None`` here too, which R2 refuses
    by name; the default is one of the producer's targets by `onevcs`'s own validation.
    """
    named = node.consumes_from(dependency)
    if named is not None:
        return answered.named(named)
    if answered.default_target is not None:
        return answered.named(answered.default_target)
    return None


def _unresolved_target_refusals(
    node: Node, by_id: Mapping[NodeId, Node], host: Host
) -> Iterator[Refusal]:
    """R2: a `consumes` entry naming a target its dependency's repository does not resolve."""
    for dependency, named in node.consumes.items():
        producer = by_id.get(NodeId(dependency))
        if not isinstance(named, str) or producer is None or producer.repo is None:
            continue
        answered = host.targets(producer.repo)
        if answered is None or answered.named(named) is not None:
            continue
        yield Refusal(
            node=node.id,
            field="consumes",
            reason=(
                f"this node consumes the target {named!r} of its dependency "
                f"{dependency!r}, and that dependency's repository ({answered.identity}) "
                f"resolves no target by that name — it resolves: {answered.names}. A wait "
                f"on a target the producer does not release is one no probe can answer. "
                f"Name one of the targets it resolves in `consumes: {{{dependency}: "
                f"<target>}}`, or drop the entry to take the producer's default target."
            ),
        )


def _held_forever_refusals(
    node: Node, by_id: Mapping[NodeId, Node], host: Host, adoption: Adoption | None
) -> Iterator[Refusal]:
    """R3: a `published` node behind a release with no target to wait on."""
    if adoption is not Adoption.PUBLISHED:
        return
    for dependency in node.deps:
        producer = by_id.get(dependency)
        if producer is None:
            continue
        answered = releasing(node, producer, host)
        # A target the node named itself is R2's when it does not resolve; what is
        # refused here is the node naming nothing and the producer defaulting nothing.
        if answered is None or node.consumes_from(dependency) is not None:
            continue
        if answered.default_target is not None:
            continue
        yield Refusal(
            node=node.id,
            field="adoption",
            reason=(
                f"this node adopts `published`, its dependency {dependency!r} lands in a "
                f"repository that releases ({answered.identity} resolves: {answered.names}), "
                f"and no target is resolved for that wait: the node names no "
                f"`consumes: {{{dependency}: <target>}}` and the producer has no "
                f"`default_target`. The engine answers a target it cannot name as "
                f"not-answered, and not-answered never releases a hold, so this node would "
                f"be held for ever with nothing to say why. Name the target in `consumes` "
                f"(which needs this node to publish under a change-request policy), or "
                f"give that producer a `default_target` in {OVERRIDE}."
            ),
        )


def _draft_refused_refusal(
    node: Node, by_id: Mapping[NodeId, Node], host: Host, adoption: Adoption | None
) -> Refusal | None:
    """R4: a `fast` node behind a release, publishing where no change request opens."""
    if adoption is not Adoption.FAST:
        return None
    policy = publication(node, host)
    if policy is None or policy.opens_a_change_request:
        return None
    released = [
        f"{dependency} ({answered.identity})"
        for dependency in node.deps
        if (producer := by_id.get(dependency)) is not None
        and (answered := releasing(node, producer, host)) is not None
    ]
    if not released:
        return None
    opening = ", ".join(one.value for one in Workflow if one.opens_a_change_request)
    return Refusal(
        node=node.id,
        field="adoption",
        reason=(
            f"this node adopts `fast` behind a dependency that releases "
            f"({', '.join(released)}) while it publishes under {policy.value!r}, which "
            f"opens no change request. When the release is not out at publication, a "
            f"`fast` node is published as a draft carrying the reason, and "
            f"{Workflow.LOCAL_DIRECT.value!r} refuses a draft by name — so this node would "
            f"fail at its last step after all of its work. State `adoption: published` "
            f"so it waits for the release, publish it under a policy that opens a change "
            f"request ({opening}), or drop the edge if the work does not need the release."
        ),
    )


def _uninstalled_artifact_refusals(
    node: Node, by_id: Mapping[NodeId, Node], host: Host
) -> Iterator[Refusal]:
    """R5: a node of this repository waiting on an artifact nothing here installs."""
    if host.is_own(node.repo) is not True:
        return
    for dependency in node.deps:
        producer = by_id.get(dependency)
        if producer is None:
            continue
        answered = releasing(node, producer, host)
        if answered is None:
            continue
        target = resolved_target(node, dependency, answered)
        if target is None or target.artifact is None:
            continue
        if host_installs.by_artifact(target.artifact) is not None:
            continue
        installed = ", ".join(f"`{row.artifact}`" for row in host_installs.INSTALLED)
        yield Refusal(
            node=node.id,
            field="consumes",
            reason=(
                f"this node of this host's own repository waits on the {target.name!r} "
                f"target of its dependency {dependency!r}, which is the artifact "
                f"`{target.artifact}` — and nothing a dispatch here runs installs it. This "
                f"host installs exactly {installed}; a crate reaches it only through the "
                f"engine wheel, so the chain is an `onepipeline` node that links the crate "
                f"and a node here that adopts that node's `pypi`. Name a wheel this host "
                f"installs in `consumes: {{{dependency}: <target>}}`, or restructure the "
                f"plan through the wheel. Which `config/<pin>.version` that wheel governs, "
                f"and whether this node's criteria name it, is `{JUDGED_TIER}`'s question "
                f"rather than this check's."
            ),
        )


def _default_taken_refusals(
    node: Node, by_id: Mapping[NodeId, Node], host: Host, adoption: Adoption | None
) -> Iterator[Refusal]:
    """R7: a node outside this repository taking a default only this host's wheel sets."""
    if adoption is not Adoption.PUBLISHED or host.is_own(node.repo) is not False:
        return
    for dependency in node.deps:
        producer = by_id.get(dependency)
        if producer is None or node.consumes_from(dependency) is not None:
            continue
        answered = releasing(node, producer, host)
        if answered is None or answered.default_target is None:
            continue
        yield Refusal(
            node=node.id,
            field="consumes",
            reason=(
                f"this node adopts `published` behind its dependency {dependency!r} and "
                f"names no `consumes` entry for it, so it would wait on that producer's "
                f"`default_target` ({answered.default_target!r}) — a default only this "
                f"host's override sets, naming the wheel *this host* installs, which says "
                f"nothing about what this node's repository ({node.repo}) consumes. Write "
                f"`consumes: {{{dependency}: <target>}}` naming the target this node "
                f"actually consumes; that producer resolves: {answered.names}."
            ),
        )


def refusals(plan: object) -> list[Refusal]:
    """Everything about ``plan``'s release adoption this host can see will not complete.

    Everything rather than the first thing, for the reason
    :func:`orchestrator.publication_guard.refusals` gives: the fields are independent
    and a plan's author reads one report and makes one pass. Each rule that borders a
    judged question says so in its refusal, and no rule reads a task's prose.
    """
    host = Host()
    read = list(nodes(plan))
    by_id = {node.id: node for node in read}
    found: list[Refusal] = []
    for node in read:
        adoption = resolved_adoption(node, host)
        found.extend(_unresolved_target_refusals(node, by_id, host))
        found.extend(_held_forever_refusals(node, by_id, host, adoption))
        refused = _draft_refused_refusal(node, by_id, host, adoption)
        if refused is not None:
            found.append(refused)
        found.extend(_uninstalled_artifact_refusals(node, by_id, host))
        found.extend(_default_taken_refusals(node, by_id, host, adoption))
    return found


def check_plan(plan: object) -> None:
    """Raise :class:`AdoptionError` for the first thing this host would refuse.

    The raising face of :func:`refusals`, for the path that reports one refusal at a
    time; `orchestrator/plan_check.py` reports them all through the verb. The message
    names the field beside the node, so the two paths report the same three things.
    """
    for refused in refusals(plan):
        raise AdoptionError(f"{refused.node}: {refused.field}: {refused.reason}")
