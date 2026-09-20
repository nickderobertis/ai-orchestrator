"""The workspaces file this host installs pools what it says, and can maintain what it pools.

`config/onevcs.workspaces.yml` is installed by `just repos-apply` as
`$ONEVCS_HOME/workspaces.yml`, and two of its claims are about this host rather than
about the file. Every rule carrying a `maintain` command names an identity whose
registered checkout is a Rust one, and a Rust identity the file gives no such rule is a
warm `target/` that is never swept — so the file is reconciled against the checkouts
this host holds, by identity, and fails by name for each one it leaves out. And every
`maintain.command` is spawned with no shell in an idle slot's worktree on every idle
tick the engine sweeps the pool on, so a first word that does not resolve on `PATH`
would fail on every one of those ticks; `scripts/session-setup.sh` installs the one
command the file names today, pinned in one place, and the installed binary is held to
that pin here.

The gates that read this host — its checkouts, its `PATH`, the binary it installed —
carry `reads_checkouts` and run in the uncached tier: a memoized verdict about which
checkouts carry a `Cargo.toml` would replay green across the registration that adds a
Rust identity, which is the drift this exists to catch. The gates about the file's own
shape are keyed on the file and stay in the memoized tier.

llmlint: ignore-file[shell_test_tiers_stay_split,test_tiers_split_by_project_not_by_marker] This
repository runs one Nx project and splits its tiers by pytest marker over four
`nx.json` keys. The subject of the marked gates here is this host — its registered
checkouts, its `PATH`, a binary it installed — which lives outside this workspace and so
outside every one of those keys; hence `reads_checkouts`, the uncached tier, exactly as
`tests/test_adopted_engine_carries_this_plan.py` reasons for its own subject. A project
of its own would need a key over the same nothing.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import NamedTuple

import pytest
from registered_checkouts import RepoIdentity, held_checkouts

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
#: What makes a registered checkout a Rust one for this file's purposes.
CARGO_MANIFEST = "Cargo.toml"

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


def rust_identities() -> dict[RepoIdentity, Path]:
    """Each identity this host holds a checkout of whose root carries a `Cargo.toml`.

    Every held checkout of an identity is asked, not only the first listed: a
    publication checkout and its execution clone are one identity, and a `Cargo.toml`
    in either is the same repository being Rust.
    """
    return {
        identity: next(path for path in paths if (path / CARGO_MANIFEST).is_file())
        for identity, paths in held_checkouts().items()
        if any((path / CARGO_MANIFEST).is_file() for path in paths)
    }


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


@pytest.mark.reads_checkouts
def test_every_rust_identity_this_host_holds_has_a_maintain_rule() -> None:
    """Each registered identity with a root `Cargo.toml` is maintained, by name.

    Read off the checkouts this host holds rather than off a list, so a Rust
    repository registered after the file was written fails here — naming the identity
    and the checkout that makes it Rust — instead of keeping a warm `target/` that
    nothing ever sweeps. The reverse is held too: a `maintain` rule for an identity
    this host holds as something other than Rust is a command that will fail on its
    first idle tick.
    """
    maintained = {rule.identity for rule in rules() if "maintain" in rule.fields}
    rust = rust_identities()
    held = held_checkouts()

    unmaintained = {identity: path for identity, path in rust.items() if identity not in maintained}
    assert not unmaintained, (
        "config/onevcs.workspaces.yml gives no `maintain` rule to these Rust identities, "
        "whose warm slots would never be swept: "
        + ", ".join(
            f"{identity} ({path}/{CARGO_MANIFEST})" for identity, path in unmaintained.items()
        )
        + f". Add a rule carrying `maintain: {RUST_MAINTENANCE}` for each"
    )
    not_rust = sorted(
        identity for identity in maintained if identity in held and identity not in rust
    )
    assert not not_rust, (
        f"config/onevcs.workspaces.yml maintains {not_rust} with `cargo sweep`, and no "
        f"held checkout of theirs carries a root {CARGO_MANIFEST}"
    )


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
