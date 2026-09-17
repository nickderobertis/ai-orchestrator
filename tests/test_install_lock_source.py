"""The workspace installer takes its hardened lock through one shared helper."""

from __future__ import annotations

import re

import pytest

from orchestrator.root import REPO_ROOT

#: The one source of the install lock.
HELPER = "scripts/install-lock.sh"

#: The function that takes it, which sourcing alone does not: a caller that sourced the
#: helper and never called it would provision unserialized.
ENTRY_POINT = "install_lock_take"

#: Every installer that locks in this checkout's `.logs`. Adding one here is the whole
#: obligation this gate imposes: a third installer that takes this lock has to say so.
INSTALLERS = ("scripts/workspace-install.sh",)

#: Preparing the lock directory, in the two shapes an installer would write it as if it
#: prepared its own. Matched on the *path* rather than on the command, because what must
#: not be duplicated is the handling of this directory and not the use of `mkdir`.
PREPARES_THE_LOCK_DIRECTORY = re.compile(r"(?:mkdir|chmod)[^\n]*\.logs")

#: Opening any of the descriptors the helper holds for the life of the sourcing shell:
#: 8 and 9 for the first lock a shell takes, 6 and 7 for a second, because a forced
#: workspace install over a shared tree holds that tree's lock and its own at once. An
#: installer that opened its own would either race the helper's or silently drop the
#: lock the helper is holding.
OPENS_A_LOCK_DESCRIPTOR = re.compile(r"exec\s+[6-9][<>]")


#: Arming the sourcing shell's EXIT trap. The helper's flock-less fallback owns that
#: trap — it is how every mutex directory the shell took is released — and `trap`
#: replaces rather than chains, so an installer arming its own would either drop that
#: release or have its own dropped, depending on which ran second.
ARMS_THE_EXIT_TRAP = re.compile(r"^[ \t]*trap\b[^\n#]*\bEXIT\b", re.MULTILINE)

#: Locking in this checkout's `.logs` by any spelling: through the helper's entry
#: point, or by preparing that directory as an installer that took its own lock would.
#: What the inventory above is held to, so a third installer cannot lock there and stay
#: off it — listed and unhardened would be the same gap this gate was written from.
LOCKS_IN_THE_LOGS_DIRECTORY = re.compile(
    rf"{ENTRY_POINT}\s|{PREPARES_THE_LOCK_DIRECTORY.pattern}|\.logs/[^\s\"']*\.lock"
)


def _script(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def test_the_inventory_names_every_script_that_locks_in_the_logs_directory() -> None:
    """`INSTALLERS` is held to the tree, so a third installer cannot escape it.

    The two tests below inspect only the scripts the inventory names, which is exactly
    the shape that lets a new installer take this lock its own way unnoticed. Every
    script under `scripts/` that locks in `.logs` — calling the helper, preparing the
    directory, or naming a lock file under it — has to be the helper or on the list.
    """
    locking = sorted(
        str(script.relative_to(REPO_ROOT))
        for script in (REPO_ROOT / "scripts").glob("*.sh")
        if LOCKS_IN_THE_LOGS_DIRECTORY.search(script.read_text(encoding="utf-8"))
    )
    # The helper itself spells the directory through its own variables and defines the
    # entry point rather than calling it, so it is not matched and need not be excluded.
    assert locking, "no script under scripts/ locks in .logs, so this gate reads nothing"
    unlisted = [script for script in locking if script not in INSTALLERS]
    assert not unlisted, (
        f"{unlisted} lock in this checkout's .logs and are not in INSTALLERS, so nothing "
        f"holds them to {HELPER}; add each to the inventory"
    )
    assert set(INSTALLERS) <= set(locking), (
        f"{sorted(set(INSTALLERS) - set(locking))} no longer lock in .logs at all; drop "
        f"them from INSTALLERS or restore the lock"
    )


def test_the_helper_defines_the_one_way_to_take_the_lock() -> None:
    """The gate is worth nothing if the name it looks for has moved."""
    assert f"{ENTRY_POINT}()" in _script(HELPER), (
        f"{HELPER} no longer defines {ENTRY_POINT}, so the installers below cannot be "
        f"reconciled against it; this gate names the entry point they call"
    )


@pytest.mark.parametrize("installer", INSTALLERS)
def test_every_installer_takes_the_lock_through_the_helper(installer: str) -> None:
    """Sourcing and calling, because sourcing alone takes no lock."""
    body = _script(installer)

    assert HELPER in body, (
        f"{installer} does not source {HELPER}, so it prepares the shared lock "
        f"directory its own way and can be hardened without its sibling"
    )
    assert f"{ENTRY_POINT} " in body, (
        f"{installer} sources {HELPER} but never calls {ENTRY_POINT}, so it holds no "
        f"lock at all and would provision concurrently with its sibling"
    )


@pytest.mark.parametrize("installer", INSTALLERS)
def test_no_installer_prepares_the_lock_directory_itself(installer: str) -> None:
    """The duplication this replaced, refused by name rather than left to review."""
    body = _script(installer)

    prepared = PREPARES_THE_LOCK_DIRECTORY.findall(body)
    assert not prepared, (
        f"{installer} prepares the shared lock directory itself ({prepared}); that "
        f"preparation belongs to {HELPER}, which is what stops one installer being "
        f"hardened against a swapped or linked `.logs` while its sibling is not"
    )
    opened = OPENS_A_LOCK_DESCRIPTOR.findall(body)
    assert not opened, (
        f"{installer} opens a lock descriptor itself ({opened}); {HELPER} holds "
        f"descriptors 6 to 9 for the life of the sourcing shell, so a second open "
        f"would drop the lock it is holding"
    )


@pytest.mark.parametrize("installer", INSTALLERS)
def test_no_installer_arms_the_exit_trap_the_helper_owns(installer: str) -> None:
    """The fallback's mutex is released by the one EXIT trap, and `trap` replaces.

    A `flock` lock dies with its holder; the fallback's directory is removed by nothing
    but the trap the helper arms in the sourcing shell. An installer arming an EXIT trap
    of its own after the lock is taken would replace that one and leave the mutex for
    the next install to wait the whole budget out on, and one arming it before would
    lose its own cleanup instead — so neither installer arms one at all.
    """
    body = _script(installer)

    armed = ARMS_THE_EXIT_TRAP.findall(body)
    assert not armed, (
        f"{installer} arms the sourcing shell's EXIT trap ({armed}); {HELPER} owns that "
        f"trap to release the fallback's mutex directories, and `trap` replaces rather "
        f"than chains, so one of the two releases would be lost"
    )
