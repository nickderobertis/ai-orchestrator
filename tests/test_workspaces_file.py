"""The workspaces file this host installs pools what it says, and can maintain what it pools.

`config/onevcs.workspaces.yml` is installed by `just repos-apply` as
`$ONEVCS_HOME/workspaces.yml`, and two of its claims are about this host rather than
about the file. Every rule carrying a `maintain` command names an identity whose
registered checkout is a Rust one; and every `maintain.command` is spawned with no shell
in an idle slot's worktree on every idle tick the engine sweeps the pool on, so a first
word that does not resolve on `PATH` would fail on every one of those ticks.
`scripts/session-setup.sh` installs the one command the file names today, pinned in one
place, and the installed binary is held to that pin here.

Which identities are Rust is each repository's to decide, and is read off the checkouts
this host holds, at their fetched base, by `tests/sibling_facts.py` — never kept as a
list. A Rust identity the file gives no `maintain` rule is upkeep this host is missing,
and is reported by name as `SiblingDrift` rather than failed: a sibling becoming Rust in
its own repository is not a defect here, and what would keep its slots swept without a
rule written for it is a maintenance default `onevcs` does not yet derive. A `maintain`
rule for an identity no held checkout makes Rust is a contradiction of the file itself,
and refuses a change that edits the registration.

The gates that read this host — its checkouts, its `PATH`, the binary it installed —
carry `reads_checkouts` and run in the uncached tier; the same readers are driven over
scratch checkouts in the memoized one, as are the gates about the file's own shape.

llmlint: ignore-file[shell_test_tiers_stay_split,test_tiers_split_by_project_not_by_marker] This
repository runs one Nx project and splits its tiers by pytest marker over four
`nx.json` keys. The subject of the marked gates here is this host — its registered
checkouts, its `PATH`, a binary it installed — which lives outside this workspace and so
outside every one of those keys; hence `reads_checkouts`, the uncached tier, exactly as
`tests/test_adopted_engine_carries_this_plan.py` reasons for its own subject. A project
of its own would need a key over the same nothing.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import warnings
from collections.abc import Mapping
from pathlib import Path
from typing import NamedTuple

import pytest
from registered_checkouts import (
    TRACKED_CHECKOUTS,
    RepoIdentity,
    UnreadableSibling,
    discover_checkouts,
)
from sibling_facts import (
    CARGO_MANIFEST,
    ENCLOSING_COMPARISON,
    SiblingDrift,
    advance,
    commit_change,
    is_rust,
    published_clone,
    scratch_checkout,
    settle_drift,
)

from orchestrator.root import REPO_ROOT

#: The tracked file, and where the recipe installs it.
TRACKED_WORKSPACES = REPO_ROOT / "config" / "onevcs.workspaces.yml"
#: The installer whose one pin the installed `cargo-sweep` is held to.
SESSION_SETUP = REPO_ROOT / "scripts" / "session-setup.sh"

#: The shared default for an identity no rule names: one warm slot per lender, and
#: sessions past it admitted without bound. A host that can afford more says so in its
#: own overlay, which `orchestrator/workspaces_overlay.py` composes onto this.
SHARED_POOL = 1
SHARED_OVERFLOW = "unlimited"
#: The one identity whose slots delete something on return — the innermost stage's log
#: — and what they delete.
THIS_REPOSITORY = "github.com/nickderobertis/ai-orchestrator"
DELETED_ON_RETURN = '[".logs/"]'
#: The maintenance every Rust identity's rule carries, exactly as the file spells it:
#: the argv `onevcs pool maintain` spawns with no shell, and its bound.
RUST_MAINTENANCE = '{command: ["cargo", "sweep", "--time", "7"], timeout: 30m}'

#: One rule of the file: its one-line `match:` flow mapping and the indented fields
#: under it, comments between them included, read from the text the way
#: `tests/test_repository_registration.py` reads the rules file, because the shape
#: asserted is the shape written.
RULE = re.compile(
    r"^  - match: \{host: (?P<host>[^,}]+), owner: (?P<owner>[^,}]+), name: (?P<name>[^,}]+)\}\n"
    r"(?P<fields>(?:^    \S.*\n)*)",
    re.MULTILINE,
)
RULE_FIELD = re.compile(r"^    (?P<key>[a-z_]+): (?P<value>.+)$", re.MULTILINE)
DEFAULT_FIELD = re.compile(r"^default:\n(?P<fields>(?:^  [a-z]+: .+\n)+)", re.MULTILINE)
#: The one place `scripts/session-setup.sh` pins the `cargo-sweep` it installs.
CARGO_SWEEP_PIN = re.compile(r'^readonly CARGO_SWEEP_VERSION="(?P<version>\d+\.\d+\.\d+)"$', re.M)
#: The first word of a `maintain.command` argv, which is what has to resolve on `PATH`.
MAINTAIN_COMMAND = re.compile(r'\{command: \["(?P<first>[^"]+)"')
#: The release the pin names, held in one place here as the file's header says it is.
PINNED_CARGO_SWEEP = "0.8.0"


class Rule(NamedTuple):
    """One rule as the file writes it."""

    identity: RepoIdentity
    fields: dict[str, str]


def rules() -> tuple[Rule, ...]:
    """Every rule of the tracked file, in the order it names them."""
    text = TRACKED_WORKSPACES.read_text(encoding="utf-8")
    read = tuple(
        Rule(
            identity=f"{found['host']}/{found['owner']}/{found['name']}",
            fields={field["key"]: field["value"] for field in RULE_FIELD.finditer(found["fields"])},
        )
        for found in RULE.finditer(text)
    )
    assert len(read) == text.count("- match:"), (
        "config/onevcs.workspaces.yml no longer writes every rule's match as a one-line "
        f"flow mapping, so only {len(read)} of {text.count('- match:')} rules were read back"
    )
    return read


def default() -> dict[str, str]:
    """The `default:` block, as key → value."""
    text = TRACKED_WORKSPACES.read_text(encoding="utf-8")
    found = DEFAULT_FIELD.search(text)
    assert found is not None, "config/onevcs.workspaces.yml declares no `default:` block"
    return {
        key: value
        for key, value in (line.strip().split(": ", 1) for line in found["fields"].splitlines())
    }


class RustIdentities(NamedTuple):
    """Which held identities are Rust, and a finding for each checkout that could not say."""

    found: dict[RepoIdentity, Path]
    unreadable: tuple[str, ...]


def rust_identities(manifest: Path = TRACKED_CHECKOUTS) -> RustIdentities:
    """Each identity ``manifest`` lists a held checkout of that is a Rust repository.

    Every held checkout of an identity is asked, not only the first listed: a
    publication checkout and its execution clone are one identity, and a `Cargo.toml`
    at either's base is the same repository being Rust. A checkout that cannot be read —
    its identity or its language — answers neither way, and is a finding in
    ``unreadable`` rather than a `no`.
    """
    discovered = discover_checkouts(manifest)
    found: dict[RepoIdentity, Path] = {}
    unreadable: list[str] = list(discovered.unreadable)
    for identity, paths in discovered.held.items():
        for path in paths:
            try:
                rust = is_rust(path)
            except UnreadableSibling as error:
                unreadable.append(f"{identity}: {error}")
                continue
            if rust:
                found.setdefault(identity, path)
    return RustIdentities(found, tuple(unreadable))


class Upkeep(NamedTuple):
    """How the file's `maintain` rules stand against what the held checkouts are."""

    #: Rust identities the file gives no `maintain` rule: upkeep this host is missing.
    unmaintained: list[str]
    #: Identities held as something other than Rust that a `maintain` rule names.
    not_rust: list[str]
    #: Held checkouts that could not be asked whether they are Rust, each with why.
    unreadable: tuple[str, ...] = ()


def upkeep(manifest: Path = TRACKED_CHECKOUTS) -> Upkeep:
    """The file's `maintain` rules, read against the checkouts ``manifest`` lists."""
    maintained = {rule.identity for rule in rules() if "maintain" in rule.fields}
    rust = rust_identities(manifest)
    held = discover_checkouts(manifest).held
    return Upkeep(
        unmaintained=[
            f"{identity} ({path}/{CARGO_MANIFEST}) is a Rust identity "
            "config/onevcs.workspaces.yml gives no `maintain` rule, so its warm slots "
            "are never swept"
            for identity, path in sorted(rust.found.items())
            if identity not in maintained
        ],
        not_rust=[
            f"config/onevcs.workspaces.yml maintains {identity} with `cargo sweep`, and "
            f"no held checkout of it carries a root {CARGO_MANIFEST}"
            for identity in sorted(maintained)
            if identity in held and identity not in rust.found
        ],
        unreadable=rust.unreadable,
    )


def report_upkeep(
    found: Upkeep,
    *,
    root: Path = REPO_ROOT,
    comparison: Mapping[str, str] = ENCLOSING_COMPARISON,
) -> None:
    """Report missing upkeep as drift, and settle the rest as drift is settled.

    A rule for a non-Rust identity, and a checkout that could not be asked, each fail a
    change editing the registration and are reported for any other.
    """
    if found.unmaintained:
        warnings.warn(
            SiblingDrift(
                "these Rust identities have no slot upkeep: "
                + "; ".join(found.unmaintained)
                + ". onevcs derives no maintenance default from a checkout, so each needs "
                f"a rule carrying `maintain: {RUST_MAINTENANCE}` until it does"
            ),
            stacklevel=2,
        )
    settle_drift([*found.not_rust, *found.unreadable], root=root, comparison=comparison)


def pinned_cargo_sweep() -> str:
    """The release `scripts/session-setup.sh` pins, read off the script."""
    found = CARGO_SWEEP_PIN.search(SESSION_SETUP.read_text(encoding="utf-8"))
    assert found is not None, (
        f"{SESSION_SETUP.name} no longer pins cargo-sweep as `readonly "
        'CARGO_SWEEP_VERSION="<version>"`, which is the one place the release is named'
    )
    return found["version"]


def test_the_shared_default_pools_one_slot_and_admits_every_session_past_it() -> None:
    """`pool: 1` and `overflow: unlimited` are the defaults every host starts from.

    One rather than more because the tracked file is the shared floor and a host's
    disk is the host's to size — its overlay raises the number, never this file; an
    unbounded overflow because the pool trades disk wear for speed and never blocks
    work behind it.
    """
    declared = default()

    assert declared.get("pool") == str(SHARED_POOL), declared
    assert declared.get("overflow") == SHARED_OVERFLOW, declared


def test_the_header_sends_a_host_to_its_own_overlay_for_its_pool_size() -> None:
    """The file says where a host sizes its pool, and that it is never here."""
    header = TRACKED_WORKSPACES.read_text(encoding="utf-8").split("\nversion:", 1)[0]

    assert "${XDG_CONFIG_HOME:-$HOME/.config}/ai-orchestrator/workspaces.yml" in header
    assert "A host sets its own pool size there, never by editing this file" in header


def test_this_repository_deletes_the_innermost_stages_log_on_every_return() -> None:
    """An `ai-orchestrator` slot returns without the `.logs/` its last session wrote.

    Every stage that captures output keeps it there, gitignored — so a return that
    cleans untracked files alone would hand the next session a log that reads as its
    own.
    """
    ours = [rule for rule in rules() if rule.identity == THIS_REPOSITORY]

    assert len(ours) == 1, f"the workspaces file names {THIS_REPOSITORY} {len(ours)} times"
    assert ours[0].fields.get("delete") == DELETED_ON_RETURN, ours[0].fields


def test_every_maintain_rule_is_the_rust_maintenance_and_no_rule_is_empty() -> None:
    """No rule is a placeholder, and each `maintain` is the one command the host provisions.

    A rule carrying nothing would match an identity and change nothing about it — a
    row that reads as configured and configures nothing — and a `maintain` spelled any
    other way is a command `scripts/session-setup.sh` did not install.
    """
    for rule in rules():
        assert rule.fields, f"{rule.identity}'s rule carries no field at all"
        if "maintain" in rule.fields:
            assert rule.fields["maintain"] == RUST_MAINTENANCE, (
                f"{rule.identity} is maintained by {rule.fields['maintain']!r}, not the "
                f"{RUST_MAINTENANCE!r} every Rust identity here shares"
            )


def test_the_cargo_sweep_pin_is_held_in_one_place() -> None:
    """The release named here is the one `scripts/session-setup.sh` installs."""
    assert pinned_cargo_sweep() == PINNED_CARGO_SWEEP


#: A Cargo workspace root, as a scratch repository that became Rust carries it.
CARGO_WORKSPACE = '[workspace]\nmembers = ["crates/*"]\n'


def _listed(manifest: Path, *paths: Path) -> Path:
    manifest.write_text("".join(f"{path}\n" for path in paths), encoding="utf-8")
    return manifest


def test_a_rust_identity_with_its_rule_is_maintained(tmp_path: Path) -> None:
    llmlint = scratch_checkout(
        tmp_path / "llmlint", "github.com/nickderobertis/llmlint", {"Cargo.toml": CARGO_WORKSPACE}
    )

    found = upkeep(_listed(tmp_path / "checkouts", llmlint))

    assert found == Upkeep(unmaintained=[], not_rust=[])


def test_a_producer_that_became_rust_since_registration_is_reported_never_failed(
    tmp_path: Path,
) -> None:
    """A sibling gaining a language is drift to report, whatever the change touches."""
    identity = "github.com/nickderobertis/unruled-producer"
    producer = scratch_checkout(tmp_path / "unruled-producer", identity, {"README.md": "x\n"})
    manifest = _listed(tmp_path / "checkouts", producer)
    assert upkeep(manifest) == Upkeep(unmaintained=[], not_rust=[])

    advance(producer, {"Cargo.toml": CARGO_WORKSPACE})
    found = upkeep(manifest)

    assert rust_identities(manifest) == RustIdentities({identity: producer}, ())
    assert found.not_rust == []
    assert [line.split(" ", 1)[0] for line in found.unmaintained] == [identity]
    with pytest.warns(SiblingDrift, match=identity):
        report_upkeep(found)


def test_a_maintain_rule_for_an_identity_held_as_not_rust_is_a_contradiction(
    tmp_path: Path,
) -> None:
    identity = "github.com/nickderobertis/onevcs"
    onevcs = scratch_checkout(tmp_path / "onevcs", identity, {"Cargo.toml": CARGO_WORKSPACE})
    manifest = _listed(tmp_path / "checkouts", onevcs)
    advance(onevcs, {"Cargo.toml": None, "package.json": "{}\n"})

    assert upkeep(manifest) == Upkeep(
        unmaintained=[],
        not_rust=[
            f"config/onevcs.workspaces.yml maintains {identity} with `cargo sweep`, and "
            f"no held checkout of it carries a root {CARGO_MANIFEST}"
        ],
    )


@pytest.mark.reads_checkouts
def test_every_rust_identity_this_host_holds_is_maintained_or_reported() -> None:
    """The readers over this host's own checkouts.

    A Rust identity left without a rule is named as drift and never fails; a rule for an
    identity no held checkout makes Rust is a command that fails on its first idle tick,
    and refuses a change that edits the registration.
    """
    report_upkeep(upkeep())


@pytest.mark.reads_checkouts
def test_every_maintain_command_resolves_on_this_hosts_path() -> None:
    """The first word of every `maintain.command` is a program this host can spawn.

    `onevcs pool maintain` spawns the argv with no shell, so what has to resolve is
    the first word exactly; a default naming a command the host lacks would fail on
    every idle tick the engine sweeps the pool on, and record each failure on the slot.
    """
    text = TRACKED_WORKSPACES.read_text(encoding="utf-8")
    commands = {found["first"] for found in MAINTAIN_COMMAND.finditer(text)}

    assert commands, "config/onevcs.workspaces.yml names no `maintain` command to resolve"
    unresolved = sorted(command for command in commands if shutil.which(command) is None)
    assert not unresolved, (
        f"{unresolved} do not resolve on this host's PATH, so every idle tick would fail "
        "to maintain the slots whose rule names them; provision each, or change the rule"
    )


@pytest.mark.reads_checkouts
def test_the_installed_cargo_sweep_is_the_pinned_release() -> None:
    """`cargo-sweep --version` on this host answers the release the installer pins.

    A binary at another release is what `scripts/session-setup.sh` reinstalls on the
    next session start; this holds the host to having done so, since the command every
    Rust slot is maintained with is whatever `cargo` resolves on that tick.
    """
    binary = shutil.which("cargo-sweep")
    assert binary is not None, (
        "no cargo-sweep on this host's PATH; `just session-setup` installs the pinned one"
    )

    answered = subprocess.run(
        [binary, "--version"], text=True, capture_output=True, timeout=30, check=False
    )

    assert answered.returncode == 0, answered.stderr
    assert answered.stdout.strip() == f"cargo-sweep {pinned_cargo_sweep()}", (
        f"{binary} answers {answered.stdout.strip()!r}, not the pinned "
        f"{pinned_cargo_sweep()}; `just session-setup` reinstalls the pinned release"
    )


@pytest.mark.parametrize(
    "changed", ["config/onevcs.workspaces.yml", "personas/crozier/crozier-corpus.yaml"]
)
def test_a_checkout_whose_language_cannot_be_read_is_settled_rather_than_read_as_not_rust(
    tmp_path: Path, changed: str
) -> None:
    """An OS error asking a checkout whether it is Rust is a finding, never a `no`."""
    if os.geteuid() == 0:
        pytest.skip("root enters a directory whatever its mode, so none is unenterable")
    checkout = scratch_checkout(
        tmp_path / "llmlint", "github.com/nickderobertis/llmlint", {"Cargo.toml": CARGO_WORKSPACE}
    )
    checkout.chmod(0)
    try:
        with pytest.raises(ValueError, match=f"{checkout} cannot be read") as raised:
            is_rust(checkout)
    finally:
        checkout.chmod(0o755)
    found = Upkeep(unmaintained=[], not_rust=[], unreadable=(str(raised.value),))
    clone = published_clone(tmp_path / "gated", (changed,))
    commit_change(clone, changed)

    if changed == "config/onevcs.workspaces.yml":
        with pytest.raises(pytest.fail.Exception, match=f"{checkout} cannot be read"):
            report_upkeep(found, root=clone, comparison={})
    else:
        with pytest.warns(SiblingDrift, match=f"{checkout} cannot be read"):
            report_upkeep(found, root=clone, comparison={})


@pytest.mark.parametrize(
    "changed", ["config/onevcs.workspaces.yml", "personas/crozier/crozier-corpus.yaml"]
)
def test_a_listed_checkout_whose_identity_cannot_be_read_is_settled_by_the_upkeep_check(
    tmp_path: Path, changed: str
) -> None:
    """Upkeep keeps the readable Rust identity and names the checkout it could not resolve."""
    if os.geteuid() == 0:
        pytest.skip("root enters a directory whatever its mode, so none is unenterable")
    identity = "github.com/nickderobertis/llmlint"
    llmlint = scratch_checkout(tmp_path / "llmlint", identity, {"Cargo.toml": CARGO_WORKSPACE})
    blocked = scratch_checkout(
        tmp_path / "onevcs", "github.com/nickderobertis/onevcs", {"Cargo.toml": CARGO_WORKSPACE}
    )
    manifest = _listed(tmp_path / "checkouts", blocked, llmlint)
    clone = published_clone(tmp_path / "gated", (changed,))
    commit_change(clone, changed)

    blocked.chmod(0)
    try:
        found = upkeep(manifest)
        rust = rust_identities(manifest)
    finally:
        blocked.chmod(0o755)

    assert rust.found == {identity: llmlint}
    assert len(found.unreadable) == 1 and f"{blocked} cannot be read" in found.unreadable[0]
    if changed == "config/onevcs.workspaces.yml":
        with pytest.raises(pytest.fail.Exception, match=f"{blocked} cannot be read"):
            report_upkeep(found, root=clone, comparison={})
    else:
        with pytest.warns(SiblingDrift, match=f"{blocked} cannot be read"):
            report_upkeep(found, root=clone, comparison={})
