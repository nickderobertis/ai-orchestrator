"""Which spike branches a plan's successful main run discards, read for the success hook.

A plan's spikes keep their branches on their origins, named
``<host prefix>/<plan native id>/spike-<topic>`` (:mod:`orchestrator.spike_plan` states the
convention), so the workers who build the plan start from their harnesses — and so a failed
run's retries and recovery still can. Once the plan's main run ends with every node done,
`scripts/run-ended.sh` discards them, and this is what it asks which they are:

* **the plan** is the project the run was launched from, as its launch record names it. A
  plan is drafted in the ``authoring`` source and launched from the board copy of it, so a
  launched project another source holds is traced back to the ``authoring`` project whose
  copies name it, and the plan's native id is that project's — the id its spikes were
  launched under;
* **the repositories** are the ones the run's plan names a node changing, read off the
  plan the run recorded, because a spike measures a repository its plan works in;
* **the branches** are each repository's origin's own answer to
  ``git ls-remote --heads origin '*/<plan>/spike-*'`` in its registered publication
  checkout, which `onevcs resolve` names — the same query that lists them from any machine.

Nothing here deletes anything: the hook discards each branch with `just reclaim-branch
<branch> --repo <repository> --discard`, so the deletion is `onevcs`'s, under its own rules.
A repository that cannot be listed is reported beside the branches of the others rather than
ending the listing, because one unreachable origin is no reason to keep every other plan's
spikes.

The answer is one line per finding on stdout, tab-separated, so the hook reads it without a
JSON parser: ``branch<TAB><repository><TAB><branch>`` for each branch to discard, and
``unlisted<TAB><repository><TAB><why>`` for a repository whose branches could not be listed.
A ``why`` is folded onto one line with no tab, because it carries text other tools wrote and a
line break in it would read as another record.
"""

from __future__ import annotations

import fnmatch
import json
import re
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import NamedTuple, NewType

from orchestrator import design_approval, plan_store, spike_plan
from orchestrator.plan_store import QualifiedProjectId
from orchestrator.root import REPO_ROOT
from orchestrator.spike_plan import Branch

#: A plan's native id: what its spike branches are named by, between the host's prefix and
#: `spike-<topic>`.
PlanId = NewType("PlanId", str)

#: What a plan's native id may be for its branches to be listed by it. It becomes part of a
#: pattern every branch the hook then *deletes* is matched by, so a character a pattern reads
#: — `*`, `?`, `[` — or a `..` would widen that selection past this plan's spikes.
PLAN_ID = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*(/[A-Za-z0-9_][A-Za-z0-9_.-]*)*\Z")

#: The source a plan is drafted in, and so the one whose native ids its spikes are named by.
AUTHORING = "authoring"

#: The run's launch record and the plan it recorded, under its run root.
LAUNCH_RECORD = "launch.json"
PLAN_RECORD = "plan.json"

#: How long one `onevcs resolve` or one `git ls-remote` may take before it is reported.
TIMEOUT_SECONDS = 120


class Unlisted(Exception):
    """A repository whose spike branches could not be listed, and why."""


def _read(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise OSError(f"{path} could not be read: {exc}") from exc


def launched_project(run_root: Path) -> QualifiedProjectId:
    """The qualified project the run at ``run_root`` was launched from."""
    record = _read(run_root / LAUNCH_RECORD)
    project = record.get("project") if isinstance(record, dict) else None
    if not isinstance(project, str) or not design_approval.QUALIFIED.fullmatch(project):
        raise OSError(f"{run_root / LAUNCH_RECORD} names no qualified project")
    return QualifiedProjectId(project)


def plan_id(native: str) -> PlanId:
    """``native`` as a plan id spike branches are named and listed by, or ``OSError``."""
    if not PLAN_ID.fullmatch(native) or ".." in native:
        raise OSError(
            f"the plan's native id {native!r} holds a character a branch pattern reads, so its "
            f"spike branches cannot be listed by it without risking another's; list them with "
            f"`git ls-remote --heads origin '*/spike-*'` and discard each by name"
        )
    return PlanId(native)


def plan_native_id(project: QualifiedProjectId) -> PlanId:
    """The native id ``project``'s spikes were launched under: its ``authoring`` project's."""
    source, _, native = project.partition(":")
    if source == AUTHORING:
        return plan_id(native)
    # A source that cannot be read answers as one holding no copy of the plan: the launched
    # project's own id is then the best name there is, and listing under it discards at
    # worst nothing, where refusing would keep every spike of every such plan.
    try:
        drafted_projects = plan_store.read_projects(AUTHORING)
    except OSError:
        return plan_id(native)
    for drafted in drafted_projects:
        copies = drafted.metadata.get(plan_store.COPIES_KEY)
        if isinstance(copies, dict) and project in copies.values():
            return plan_id(str(drafted.qualified_id).partition(":")[2])
    return plan_id(native)


def repositories(run_root: Path) -> list[str]:
    """Every repository a node of the run's recorded plan changes, in the order it names them."""
    path = run_root / PLAN_RECORD
    plan = _read(path)
    tasks = plan.get("tasks") if isinstance(plan, dict) else None
    if not isinstance(tasks, list) or not all(isinstance(task, dict) for task in tasks):
        raise OSError(f"{path} records no list of nodes")
    named: list[str] = []
    for task in tasks:
        repo = task.get("repo")
        # A tab or a line break would split the one-line record the hook reads this as.
        if repo is not None and (
            not isinstance(repo, str) or not repo or any(c in repo for c in "\t\r\n")
        ):
            raise OSError(f"{path} records a node whose repository is not a name: {repo!r}")
        if repo is not None and repo not in named:
            named.append(repo)
    return named


def answer(command: Sequence[str], cwd: Path | None = None) -> str:
    try:
        done = subprocess.run(
            list(command),
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Unlisted(f"`{' '.join(command)}` did not run: {exc}") from exc
    if done.returncode != 0:
        said = done.stderr.strip().splitlines()
        raise Unlisted(
            f"`{' '.join(command)}` exited {done.returncode}" + (f": {said[-1]}" if said else "")
        )
    return done.stdout


def installed_onevcs() -> str:
    installed = REPO_ROOT / ".venv" / "bin" / "onevcs"
    return str(installed) if installed.is_file() else (shutil.which("onevcs") or "onevcs")


class Repository(NamedTuple):
    """The identity and registered publication checkout onevcs resolved."""

    identity: str
    checkout: Path


def resolve(repository: str) -> Repository:
    """Resolve a spike's own repository through onevcs, refusing an unreadable answer."""
    resolved = answer([installed_onevcs(), "resolve", repository])
    try:
        identity = json.loads(resolved)
        checkout = identity.get("publication_checkout")
    except (ValueError, AttributeError) as exc:
        raise Unlisted(f"`onevcs resolve {repository}` did not answer with its identity") from exc
    if not isinstance(checkout, str) or not checkout:
        raise Unlisted(f"`onevcs resolve {repository}` named no publication checkout")
    name = identity.get("identity")
    if not isinstance(name, str) or not name:
        raise Unlisted(f"`onevcs resolve {repository}` named no identity")
    return Repository(name, Path(checkout))


def spike_branches(
    repository: str,
    plan: PlanId,
    *,
    local: bool = False,
    resolved: Repository | None = None,
) -> list[Branch]:
    """The plan's origin branches, optionally including its registered local checkouts."""
    held = resolve(repository) if resolved is None else resolved
    listed = answer(
        ["git", "ls-remote", "--heads", "origin", f"*/{plan}/{spike_plan.SPIKE_PREFIX}*"],
        cwd=held.checkout,
    )
    lines = [line for line in listed.splitlines() if line]
    kept: dict[Branch, str] = {}
    for line in lines:
        branch = _spike_branch(line, plan)
        if branch in kept:
            raise Unlisted(f"`git ls-remote` answered duplicate branch {branch}")
        kept[branch] = line.split("\t")[0]
    if local:
        for path in registered_checkouts(held.identity):
            refs = answer(
                [
                    "git",
                    "for-each-ref",
                    "--format=%(objectname)%09%(refname)",
                    "refs/heads",
                ],
                cwd=path,
            )
            seen: set[Branch] = set()
            for line in refs.splitlines():
                matched = LS_REMOTE_LINE.fullmatch(line)
                if matched is None:
                    raise Unlisted(f"`git for-each-ref` answered an unreadable ref: {line!r}")
                name = matched["branch"]
                if not (
                    fnmatch.fnmatchcase(name, f"*/{plan}/{spike_plan.SPIKE_PREFIX}*")
                    or fnmatch.fnmatchcase(name, f"{plan}/{spike_plan.SPIKE_PREFIX}*")
                ):
                    continue
                branch = _spike_branch(line, plan)
                head = line.split("\t")[0]
                if branch in seen or (branch in kept and kept[branch] != head):
                    raise Unlisted(f"registered checkouts answered conflicting branch {branch}")
                seen.add(branch)
                kept[branch] = head
    return list(kept)


def registered_checkouts(identity: object) -> list[Path]:
    """The checkouts of one resolved identity, off the existing onevcs repos table."""
    if not isinstance(identity, str) or not identity:
        raise Unlisted("`onevcs resolve` named no identity")
    listing = answer([installed_onevcs(), "repos"])
    current: str | None = None
    paths: list[Path] = []
    for line in listing.splitlines():
        if line.startswith("  "):
            matched = re.fullmatch(r"  [^\t\s][^\t]*\t(\S[^\t]*)", line)
            if matched is None or current is None:
                raise Unlisted(f"`onevcs repos` answered an unreadable checkout: {line!r}")
            if current == identity:
                paths.append(Path(matched[1]))
        else:
            matched = re.fullmatch(r"([^\s\t]+)\t(\S[^\t]*)", line)
            if matched is None:
                raise Unlisted(f"`onevcs repos` answered an unreadable identity: {line!r}")
            current = matched[1]
    if not paths:
        raise Unlisted(f"`onevcs repos` named no registered checkouts for {identity}")
    return paths


#: One line of `git ls-remote --heads`: a commit, a tab, and the ref it names.
LS_REMOTE_LINE = re.compile(r"[0-9a-f]{40,64}\trefs/heads/(?P<branch>\S+)\Z")


def _spike_branch(line: str, plan: PlanId) -> Branch:
    """The branch ``line`` names, once it is one of ``plan``'s spikes, or :class:`Unlisted`.

    Re-read here rather than trusted to the pattern git matched with, because what this
    answers is handed to a deletion: a line that is not a ref, or a ref that is not
    `[<prefix>/]<plan>/spike-<topic>`, stops that repository's listing instead.
    """
    matched = LS_REMOTE_LINE.fullmatch(line)
    if matched is None:
        raise Unlisted(f"`git ls-remote` answered a line that names no branch: {line!r}")
    branch = matched["branch"]
    own = rf"(?:.+/)?{re.escape(plan)}/{re.escape(spike_plan.SPIKE_PREFIX)}[^/]+"
    if re.fullmatch(own, branch) is None:
        raise Unlisted(f"`git ls-remote` named {branch}, which is not a spike branch of {plan}")
    try:
        answer(["git", "check-ref-format", f"refs/heads/{branch}"])
    except Unlisted as exc:
        raise Unlisted(f"the listing named an unsafe branch: {branch!r}: {exc}") from exc
    return Branch(branch)


def main(argv: Sequence[str] | None = None) -> int:
    """`python -m orchestrator.spike_branches <run root>`, from `scripts/run-ended.sh`."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 1:
        print("usage: python -m orchestrator.spike_branches <run root>", file=sys.stderr)
        return 2
    run_root = Path(arguments[0])
    try:
        plan = plan_native_id(launched_project(run_root))
        named = repositories(run_root)
    except OSError as exc:
        print(f"spike-branches: {exc}", file=sys.stderr)
        return 2
    for repository in named:
        try:
            for branch in spike_branches(repository, plan):
                print(f"branch\t{repository}\t{branch}")
        except Unlisted as exc:
            print(f"unlisted\t{repository}\t{' '.join(str(exc).split())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
