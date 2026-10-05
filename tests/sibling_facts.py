"""What a registered sibling repository decides about itself, and how this suite settles it.

Several reconciliations here compare this repository's registration — the override's
``default_target`` per producer, the table of what this host installs, the workspaces
file's ``maintain`` rules — with what another repository declares: its
`release-targets.toml`, whether it is a Rust workspace. Those facts are the sibling's to
decide, and they move in the sibling's own repository with no change here, so nothing in
this suite restates them. They are read from the checkout this host holds, at its fetched
base, every time they are asked.

**What a disagreement fails.** A producer gaining a target, a language or an artifact is
normal development and is no disagreement at all. A true contradiction — an override
``default_target`` the producer's declaration no longer carries, a row whose artifact the
producer now calls something else — is one, and it refuses exactly the change that edits
the registration it contradicts: a tree that touches none of :data:`REGISTRATION_FILES`
against its comparison base has the contradiction reported as :class:`SiblingDrift`, a
warning, rather than failed. That keeps every unrelated push hermetic to a sibling's
movement, while the change registering, adopting or re-pointing a producer — the one that
can act on the contradiction — is still refused for it. onevcs refuses the contradiction
itself at the release interaction that would act on it, which is where it blocks the one
consumer it matters to.

**The same settlement holds every check whose subject is a sibling's live state** — a
persona's recipes and identifiers against the repository it reviews, a dialect or an
exemption copied from a sibling's source at its head — because each is a sibling moving
in its own repository, and ai-orchestrator#1529 rules that such movement refuses no push
that leaves the registration alone. :func:`settle_drift` is the one place that decision
is made, so a check reporting a sibling's live state hands it its findings rather than
asserting them.
"""

from __future__ import annotations

import os
import re
import subprocess
import tomllib
import warnings
from collections.abc import Collection, Mapping, Sequence
from pathlib import Path
from typing import NamedTuple

import pytest
from registered_checkouts import (
    TRACKED_CHECKOUTS,
    RepoIdentity,
    UnreadableSibling,
    discover_checkouts,
    sibling_git,
)

from orchestrator.host_installs import AWAITING_FIRST_ADOPTION, Installed, by_producer
from orchestrator.root import REPO_ROOT

#: The files that say which repositories this host registers, how it routes, pools and
#: adopts them, and what it installs from each. A change touching one of them is the
#: change a sibling's contradiction of them is refused for.
REGISTRATION_FILES = (
    "config/onevcs.checkouts",
    "config/onevcs.rules.yml",
    "config/onevcs.releases.yml",
    "config/onevcs.workspaces.yml",
    "orchestrator/host_installs.py",
)

#: The override this host tracks, which `just repos-apply` installs.
RELEASES = REPO_ROOT / "config" / "onevcs.releases.yml"
#: How the gate resolves the base a change is compared against.
COMPARISON_BASE = REPO_ROOT / "scripts" / "comparison-base.sh"

#: A repository's own release declaration, at its root.
DECLARATION = "release-targets.toml"
#: What makes a repository a Rust one for the workspaces file's purposes.
CARGO_MANIFEST = "Cargo.toml"

#: How a checkout's fetched base is resolved. `origin/HEAD` is the remote's own answer
#: and is asked first; the fallbacks are for a clone that never recorded one. A
#: remote-tracking ref moves only when somebody fetches, never when a dispatch checks
#: out a branch in a shared checkout, so it says what the repository publishes rather
#: than what another manager's worktree is sitting on.
REMOTE_BASES = ("origin/HEAD", "origin/main", "origin/master")

#: The comparison identity of the change being gated, captured when this module is
#: imported — at collection, before `tests/conftest.py`'s autouse fixture strips both
#: spellings from every test so the suite's own scratch pushes carry none. Which base a
#: publication judged its change against is what decides whether that change touched the
#: registration, so it is read here once and handed only to the live comparison.
ENCLOSING_COMPARISON: Mapping[str, str] = {
    key: value
    for key, value in os.environ.items()
    if key.startswith(("ORCHESTRATOR_COMPARISON_", "ONEVCS_COMPARISON_"))
}

#: One rule of the override, as the tracked file writes every rule: a one-line flow
#: `match` naming the repository, then its fields one per indented line. Read as text
#: because this package ships no YAML parser for it; a rule spelled any other way fails
#: the count in :func:`override_default_targets` rather than being skipped.
RELEASE_RULE = re.compile(
    r"- match: \{host: (?P<host>[^,]+), owner: (?P<owner>[^,]+), name: (?P<name>[^}]+)\}\n"
    r"(?P<fields>(?:    \S.*\n)*)"
)


class UnreadableDeclaration(UnreadableSibling):
    """A sibling's release declaration, at its fetched base, is not a document a parser reads.

    Its message is the finding: which checkout, which ref, and the parser's own diagnostic.
    """


class SiblingDrift(UserWarning):
    """A sibling disagrees with this repository, in a change that left the registration alone."""


def _git(checkout: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    # This repository's own trees and the scratch ones a test builds, never a sibling's,
    # which is read through `sibling_git` so a failure to read it is a finding. Replaced
    # rather than strict: what is read here is refs, names and diagnostics.
    return subprocess.run(
        ["git", "-C", str(checkout), *arguments],
        text=True,
        errors="replace",
        capture_output=True,
        timeout=120,
        check=False,
    )


def fetched_base(checkout: Path) -> str | None:
    """The remote-tracking ref standing for ``checkout``'s base branch, or None.

    Raises :class:`UnreadableSibling` where ``checkout`` cannot be read at all, which is
    no answer about its base.
    """
    for candidate in REMOTE_BASES:
        resolved = sibling_git(
            checkout, "rev-parse", "--verify", "--quiet", f"{candidate}^{{commit}}"
        )
        if resolved.returncode == 0:
            return candidate
    return None


def declared_targets(checkout: Path) -> dict[str, str] | None:
    """Each target's id by name, as ``checkout``'s repository declares it at its fetched base.

    None where it declares none: no declaration, an empty one, or no fetched base to read
    one at — a release this host has never fetched the declaration of is one it could not
    await either. Handed to a TOML parser rather than scanned, so a malformed document
    raises :class:`UnreadableDeclaration`, and a checkout that cannot be read at all
    :class:`UnreadableSibling`, rather than either reading as declaring nothing; a caller
    reading a sibling's live state settles both as findings with :func:`read_declarations`.
    """
    base = fetched_base(checkout)
    if base is None:
        return None
    shown = sibling_git(checkout, "show", f"{base}:{DECLARATION}")
    if shown.returncode != 0:
        return None
    try:
        document = shown.stdout.decode("utf-8")
    except UnicodeDecodeError as error:
        raise UnreadableDeclaration(
            f"{checkout}'s {DECLARATION} at {base} is not UTF-8 ({error}), which TOML "
            "requires, so what it declares cannot be read; that repository's next fetched "
            "base decides it"
        ) from error
    try:
        declared = tomllib.loads(document).get("target", [])
    except tomllib.TOMLDecodeError as error:
        raise UnreadableDeclaration(
            f"{checkout}'s {DECLARATION} at {base} is not valid TOML ({error}), so what it "
            "declares cannot be read; that repository's next fetched base decides it"
        ) from error
    # Parsing is not reading: a document whose `target` is not an array of tables parses
    # and still says nothing this reader understands, so it is the same finding.
    if not isinstance(declared, list) or not all(isinstance(row, dict) for row in declared):
        raise UnreadableDeclaration(
            f"{checkout}'s {DECLARATION} at {base} parses, but its `target` is not an array "
            "of tables, so what it declares cannot be read; that repository's next fetched "
            "base decides it"
        )
    # A sibling's declaration schema is its own to move: a target is keyed by the `name`
    # the override and a `consumes` call it, and one a later schema spells without one by
    # its id, so a schema change here never reads as declaring fewer targets.
    return {
        str(target.get("name") or target.get("id") or f"#{index}"): str(target.get("id"))
        for index, target in enumerate(declared)
    } or None


class Declarations(NamedTuple):
    """What each held sibling declares, and a finding for each whose declaration is unreadable."""

    found: dict[RepoIdentity, dict[str, str] | None]
    unreadable: list[str]


def read_declarations(checkouts: Mapping[RepoIdentity, Path]) -> Declarations:
    """Each checkout's declaration, with an unreadable one a finding rather than an exception.

    An identity whose declaration is unreadable is left out of ``found``, so it is judged
    by nothing else, and named in ``unreadable`` for the caller to settle.
    """
    found: dict[RepoIdentity, dict[str, str] | None] = {}
    unreadable: list[str] = []
    for identity, checkout in checkouts.items():
        try:
            found[identity] = declared_targets(checkout)
        except UnreadableSibling as error:
            unreadable.append(f"{identity}: {error}")
    return Declarations(found, unreadable)


class Declaring(NamedTuple):
    """How many targets each declaring sibling declares, and a finding for each unreadable one."""

    counts: dict[RepoIdentity, int]
    unreadable: list[str]


def declaring_identities(manifest: Path = TRACKED_CHECKOUTS) -> Declaring:
    """How many release targets each repository ``manifest`` lists declares at its base.

    Derived from the declarations every time it is asked, since which repositories declare
    and how many is each repository's own to decide; read at the base for the reason
    :data:`REMOTE_BASES` gives. A repository whose declaration could not be read is a
    finding in ``unreadable``, for the caller to settle, as is a listed checkout whose
    identity could not be read.
    """
    discovered = discover_checkouts(manifest)
    read = read_declarations({identity: paths[0] for identity, paths in discovered.held.items()})
    counts = {
        identity: len(declared) for identity, declared in read.found.items() if declared is not None
    }
    return Declaring(counts, [*discovered.unreadable, *read.unreadable])


def is_rust(checkout: Path) -> bool:
    """Whether ``checkout``'s repository carries a root `Cargo.toml` at its fetched base.

    A checkout with no fetched base is asked of its working tree, which is all it has.
    Raises :class:`UnreadableSibling` where ``checkout`` cannot be read at all, since that
    answers neither way.
    """
    base = fetched_base(checkout)
    if base is None:
        return (checkout / CARGO_MANIFEST).is_file()
    return sibling_git(checkout, "cat-file", "-e", f"{base}:{CARGO_MANIFEST}").returncode == 0


def override_default_targets(text: str) -> dict[RepoIdentity, str]:
    """Each producer's ``default_target`` as the override ``text`` writes it."""
    rules = list(RELEASE_RULE.finditer(text))
    assert len(rules) == text.count("- match:"), (
        "config/onevcs.releases.yml no longer writes every rule's match as a one-line flow "
        f"mapping, so only {len(rules)} of {text.count('- match:')} rules were read back"
    )
    defaults: dict[RepoIdentity, str] = {}
    for rule in rules:
        named = re.search(r"^    default_target: (\S+)$", rule["fields"], re.MULTILINE)
        if named is not None:
            defaults[f"{rule['host']}/{rule['owner']}/{rule['name']}"] = named.group(1)
    return defaults


def release_contradictions(
    defaults: Mapping[RepoIdentity, str],
    declarations: Mapping[RepoIdentity, dict[str, str] | None],
    rows: Sequence[Installed],
    awaiting: Collection[RepoIdentity],
) -> list[str]:
    """Every way a producer's declaration contradicts the override and the table.

    ``declarations`` holds what each producer this host could read declares, None for
    one declaring nothing; a producer absent from it was not read and is not judged.
    Only a contradiction is named: a default target the declaration does not carry, or
    one it carries under an id other than the artifact the table says this host installs.
    A producer declaring targets the override never names, or more targets than it did, is
    no contradiction; and a producer awaiting its first adoption may declare nothing yet.
    """
    installed = {row.producer: row for row in rows}
    found: list[str] = []
    for producer, target in sorted(defaults.items()):
        if producer not in declarations:
            continue
        declared = declarations[producer]
        if declared is None:
            if producer not in awaiting:
                found.append(
                    f"{producer} declares no release targets at its base, and "
                    f"config/onevcs.releases.yml names {target!r} as its default target"
                )
            continue
        if target not in declared:
            found.append(
                f"{producer} no longer declares the default target {target!r} "
                f"config/onevcs.releases.yml names for it; it declares {sorted(declared)}"
            )
            continue
        row = installed.get(producer)
        if row is not None and declared[target] != row.artifact:
            found.append(
                f"{producer} declares its {target!r} target as {declared[target]!r}, and "
                f"orchestrator/host_installs.py says this host installs {row.artifact!r} "
                "under it"
            )
    return found


def default_target_complaint(
    identity: RepoIdentity, resolved: str | None, defaults: Mapping[RepoIdentity, str]
) -> str | None:
    """Why the default target ``identity`` resolves disagrees with the table, or None.

    A producer this host installs resolves its row's target; a producer awaiting its
    first adoption resolves the override's default target with no row, which is the
    state the override names it in on purpose; every other identity resolves none.
    """
    row = by_producer(identity)
    if row is not None:
        expected: str | None = row.target
        why = f"the wheel this host installs ({row.artifact})"
    elif identity in AWAITING_FIRST_ADOPTION:
        expected = defaults.get(identity)
        why = "the target config/onevcs.releases.yml names ahead of its first adoption"
    else:
        expected = None
        why = "none, since orchestrator/host_installs.py says this host installs nothing from it"
    if resolved == expected:
        return None
    return (
        f"{identity} resolves the default target {resolved!r} rather than {expected!r}, "
        f"{why}; a `published` node depending on it would wait on the wrong artifact, or "
        "on nothing"
    )


def registration_changes(
    root: Path = REPO_ROOT, comparison: Mapping[str, str] = ENCLOSING_COMPARISON
) -> tuple[str, ...] | None:
    """The registration files ``root``'s tree changes against its comparison base.

    The change is everything between the merge base with the ref
    `scripts/comparison-base.sh` resolves and the working tree — committed, staged,
    unstaged and untracked — because that is what a push of this tree would carry and
    what a worker's own run of the checks is about to commit. None when no base can be
    resolved, since then nothing shows the change leaves the registration alone.
    """
    resolved = subprocess.run(
        [str(COMPARISON_BASE)],
        cwd=root,
        env={**os.environ, **comparison},
        text=True,
        capture_output=True,
        timeout=120,
        check=False,
    )
    if resolved.returncode != 0:
        return None
    merge_base = _git(root, "merge-base", resolved.stdout.strip(), "HEAD")
    if merge_base.returncode != 0:
        return None
    changed = _git(root, "diff", "--name-only", merge_base.stdout.strip(), "--")
    untracked = _git(root, "ls-files", "--others", "--exclude-standard")
    if changed.returncode != 0 or untracked.returncode != 0:
        return None
    paths = {*changed.stdout.splitlines(), *untracked.stdout.splitlines()}
    return tuple(path for path in REGISTRATION_FILES if path in paths)


def settle_drift(
    findings: Sequence[str],
    *,
    root: Path = REPO_ROOT,
    comparison: Mapping[str, str] = ENCLOSING_COMPARISON,
) -> None:
    """Fail ``findings`` for a change that touches the registration; report them otherwise.

    Each finding is one way a registered sibling's live state disagrees with what this
    repository states of it, worded so a reader can act on it alone.
    """
    if not findings:
        return
    listed = "\n".join(f"- {finding}" for finding in findings)
    touched = registration_changes(root, comparison)
    if touched is None:
        pytest.fail(
            "a registered sibling repository disagrees with what this repository states of "
            "it, and no comparison base resolved to show this change leaves the registration "
            f"files alone:\n{listed}"
        )
    if touched:
        pytest.fail(
            f"this change edits {', '.join(touched)}, and a registered sibling repository "
            "disagrees with what this repository states of it; reconcile the two before "
            f"changing the registration:\n{listed}"
        )
    # llmlint: ignore-block[contracts_have_one_source_or_a_drift_gate] The drift gate is the
    # failure above, held to the change that edits the registration this contradicts:
    # ai-orchestrator#1529 rules that a sibling's own movement refuses no unrelated push,
    # since the producer is the source of its declaration and onevcs refuses the
    # contradiction at the release interaction that would act on it.
    warnings.warn(
        SiblingDrift(
            "a registered sibling repository disagrees with what this repository states of "
            "it; this change touches no registration file, so it is reported rather than "
            f"failed, and the change that next edits one is refused for it:\n{listed}"
        ),
        stacklevel=2,
    )
    # llmlint: ignore-end[contracts_have_one_source_or_a_drift_gate]


def held_checkouts(
    manifest: Path = TRACKED_CHECKOUTS,
    *,
    root: Path = REPO_ROOT,
    comparison: Mapping[str, str] = ENCLOSING_COMPARISON,
) -> dict[RepoIdentity, list[Path]]:
    """Every checkout ``manifest`` lists that this host holds, by identity, in order.

    A listed checkout this host has but cannot resolve an identity for is settled with
    :func:`settle_drift` against the change at ``root``, and the readable ones are kept:
    which checkouts a sibling's directory and origin make readable is that sibling's live
    state, so it is drift like any other read of it.
    """
    discovered = discover_checkouts(manifest)
    settle_drift(list(discovered.unreadable), root=root, comparison=comparison)
    return discovered.held


def registered_checkouts(
    manifest: Path = TRACKED_CHECKOUTS,
    *,
    root: Path = REPO_ROOT,
    comparison: Mapping[str, str] = ENCLOSING_COMPARISON,
) -> dict[RepoIdentity, Path]:
    """The first listed held checkout of each identity, which a reconciliation searches.

    Settled as :func:`held_checkouts` settles. A host holding none of them resolves
    nothing, which is what each caller reports rather than asserts on.
    """
    return {
        identity: paths[0]
        for identity, paths in held_checkouts(manifest, root=root, comparison=comparison).items()
    }


def scratch_checkout(
    directory: Path, identity: RepoIdentity, files: Mapping[str, str | bytes]
) -> Path:
    """A real checkout of ``identity`` whose fetched base carries ``files``.

    The origin is the identity's own URL, which is what `held_checkouts` resolves an
    identity from; the base is a remote-tracking `origin/main` set to the commit, which
    is where every reader above looks. :func:`advance` moves it on.
    """
    directory.mkdir(parents=True)
    _run(directory, "init", "-q", "-b", "main")
    _run(directory, "remote", "add", "origin", f"https://{identity}.git")
    _run(directory, "commit", "-q", "--allow-empty", "-m", "chore: start")
    return advance(directory, files)


def advance(checkout: Path, files: Mapping[str, str | bytes | None]) -> Path:
    """Commit ``files`` on ``checkout``'s base — None deletes one — and fetch it as published.

    Bytes are written as they are, so a test can commit a file no UTF-8 reader reads.
    """
    for relative, content in files.items():
        path = checkout / relative
        if content is None:
            path.unlink()
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, bytes):
                path.write_bytes(content)
            else:
                path.write_text(content, encoding="utf-8")
    _run(checkout, "add", "-A")
    _run(checkout, "commit", "-q", "--allow-empty", "-m", "chore: move the base")
    _run(checkout, "update-ref", "refs/remotes/origin/main", "HEAD")
    return checkout


def published_clone(directory: Path, tracked: Sequence[str] = ()) -> Path:
    """A clone of this repository's shape whose `origin/main` carries every registration file.

    Stands in for the tree being gated: the clone's `origin` is a real bare repository, so
    the real `scripts/comparison-base.sh` resolves its base the way a publication's would,
    and :func:`commit_change` makes the change :func:`settle_drift` is asked about. The
    base also carries ``tracked`` and a README, the files a change leaving the registration
    alone edits.
    """
    seed = directory / "seed"
    seed.mkdir(parents=True)
    _run(seed, "init", "-q", "-b", "main")
    for relative in (*REGISTRATION_FILES, "README.md", *tracked):
        (seed / relative).parent.mkdir(parents=True, exist_ok=True)
        (seed / relative).write_text("base\n", encoding="utf-8")
    _run(seed, "add", "-A")
    _run(seed, "commit", "-q", "-m", "chore: base")
    origin = directory / "origin.git"
    _run(directory, "clone", "-q", "--bare", str(seed), str(origin))
    clone = directory / "clone"
    _run(directory, "clone", "-q", str(origin), str(clone))
    _run(clone, "switch", "-q", "-c", "work")
    return clone


def commit_change(clone: Path, relative: str) -> None:
    """Commit an edit to ``relative`` on the clone's working branch."""
    (clone / relative).write_text("changed\n", encoding="utf-8")
    _run(clone, "commit", "-q", "-am", f"feat: change {relative}")


def declaration(targets: Mapping[str, str]) -> str:
    """A `release-targets.toml` declaring each ``name: id`` target, and nothing a probe runs."""
    rows = "".join(
        f'\n[[target]]\nid = "{artifact}"\nname = "{name}"\n'
        f'what = "The {name} target, as a scratch producer declares it."\n'
        "published_by = \"Nothing: a test's stand-in for a producer's declaration.\"\n"
        for name, artifact in targets.items()
    )
    return f'schema_version = 1\nprobe = "scripts/release-probe.sh"\n{rows}'


def _run(checkout: Path, *arguments: str) -> None:
    done = _git(checkout, *arguments)
    assert done.returncode == 0, f"git {' '.join(arguments)} in {checkout}: {done.stderr}"
