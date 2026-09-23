"""Each term of `tests/short_state.py`'s socket-address budget, held to what owns it.

That budget decides how long a launched journey's `XDG_STATE_HOME` may be, and every
launched journey here depends on it: a root past it makes oneharness refuse the control
socket, and the caller then takes the turn again without control — twice the provider
invocations, twice the wall clock, and nothing failing to say so. The arithmetic is three
terms owned by three different parties, so each is read back from its own:

* the address limit is the kernel's, and is measured by binding a real socket either side
  of it rather than taken from a constant;
* the layout under a state root is oneharness's, and is read out of the installed CLI's
  own documentation of `--session-dir` and `--control`;
* what is reserved for a session name is this host's own decision, and what makes it a
  sufficient one is the longest name the graphs and members this repository ships can
  compose — derived from those documents rather than restated.
"""

from __future__ import annotations

import re
import socket
import subprocess
from pathlib import Path
from typing import NamedTuple

import pytest
import short_state
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: The graph documents whose names and members a session name is composed from.
GRAPHS = REPO_ROOT / "graphs"

#: A graph document's own name, and the member keys under its `members:` mapping. Read
#: with a reader for these documents' shape rather than with a YAML parser, as every
#: other reader of them here is, because this workspace installs none.
GRAPH_NAME = re.compile(r"^name:\s*(\S+)\s*$", re.MULTILINE)
MEMBERS_BLOCK = re.compile(r"^members:$", re.MULTILINE)
MEMBER_KEY = re.compile(r"^  ([a-z0-9-]+):$", re.MULTILINE)

#: What `oneagentgraph` puts around a graph and a member when it composes a session name:
#: `<graph>-<millis>-<pid>-<member>-<side>`, where the epoch is in milliseconds and the
#: side is `skill` or `user`. Stated here rather than derived, because the producer
#: publishes no surface that answers it without running a graph — so it is reconciled
#: against the producer instead, by
#: `tests/e2e/test_orchestrate_launch_e2e.py`, which measures this overhead off the names
#: a real launch's controlled turns were really addressed by.
EPOCH_MILLIS_DIGITS = 13
LONGEST_SIDE = "skill"
SEPARATORS = 4

#: oneharness's own suffix on the socket file, which the reservation has to cover too.
SUFFIX = ".sock"

#: Where Linux states the largest process id it will issue.
PID_MAX = Path("/proc/sys/kernel/pid_max")


def composition_overhead() -> int:
    """Everything `oneagentgraph` adds around the graph and member names, at its widest."""
    pid_digits = len(PID_MAX.read_text(encoding="utf-8").strip())
    return EPOCH_MILLIS_DIGITS + pid_digits + len(LONGEST_SIDE) + SEPARATORS


class ShippedSessionParts(NamedTuple):
    """The two halves of a session name that belong to this repository."""

    graphs: frozenset[str]
    members: frozenset[str]


def shipped_session_parts() -> ShippedSessionParts:
    """Every graph name and member name the documents in `graphs/` declare.

    The two halves a session name is composed from that belong to this repository, which
    is why they are read out of the documents that declare them rather than listed.
    """
    graphs: set[str] = set()
    members: set[str] = set()
    for document in sorted(GRAPHS.glob("*.yaml")):
        text = document.read_text(encoding="utf-8")
        named = GRAPH_NAME.search(text)
        assert named is not None, f"{document} states no name"
        graphs.add(named.group(1))
        opened = MEMBERS_BLOCK.search(text)
        assert opened is not None, f"{document} declares no members"
        declared = MEMBER_KEY.findall(text[opened.end() :])
        assert declared, f"{document} declares an empty members mapping"
        members.update(declared)
    assert graphs and members, "no graph document named a graph and a member"
    return ShippedSessionParts(frozenset(graphs), frozenset(members))


def _bound(length: int) -> OSError | None:
    """Bind a real abstract-free Unix socket whose address is exactly ``length`` bytes."""
    path = "/tmp/" + "b" * (length - len("/tmp/"))  # noqa: S108 - the address under test
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as bound:
        try:
            bound.bind(path)
        except OSError as refused:
            return refused
    Path(path).unlink()
    return None


def test_the_kernel_takes_an_address_one_byte_under_the_limit_and_refuses_one_at_it() -> None:
    """`UNIX_SOCKET_ADDRESS_BYTES` counts the terminating NUL, measured both ways.

    Both halves, because a limit only ever asserted from below would pass on a kernel
    that allowed more and leave every journey here paying for a budget it did not need,
    and one only asserted from above would pass on a kernel that allowed less while every
    controlled turn was quietly re-taken.
    """
    longest = short_state.UNIX_SOCKET_ADDRESS_BYTES - 1
    assert _bound(longest) is None, (
        f"this kernel refused a {longest}-byte socket address, so "
        f"{short_state.UNIX_SOCKET_ADDRESS_BYTES} is not the limit the budget is computed "
        f"from and every launched journey's state root is budgeted too generously"
    )
    refused = _bound(longest + 1)
    assert refused is not None, (
        f"this kernel accepted a {longest + 1}-byte socket address, so the budget "
        f"reserves a byte no address needs"
    )


# llmlint: ignore-block[test_tiers_split_by_project_not_by_marker] `reads_checkouts` is
# not a narrower key inside a memoized tier: it moves this one test out of every memoized
# tier into the uncached `orchestrator:test-checkouts`, because its subject — the socket
# layout the installed `oneharness` under `.venv` documents — is outside this workspace and
# no `nx.json` glob hashes it. A project of its own would give this gate a key, and a
# memoized green would replay across the very oneharness upgrade that could move the
# socket, which is the one thing it exists to catch. `tests/conftest.py`'s own checkout
# guard states that reasoning where it enforces the marker, and every `reads_checkouts`
# test in this repository is tiered this way for it.
@pytest.mark.reads_checkouts
def test_the_installed_oneharness_still_puts_the_socket_where_the_budget_assumes(
    oneharness_bin: str,
) -> None:
    """The layout between a state root and the socket is read out of the CLI that makes it.

    Uncached, because the subject is the installed producer rather than this workspace: a
    memo keyed on the tree here would replay across the very oneharness upgrade that could
    move the socket.
    """
    documented = subprocess.run(  # noqa: S603 - the installed CLI, asked what it documents
        [oneharness_bin, "run", "--help"],
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )
    assert documented.returncode == 0, documented.stderr
    collapsed = " ".join(documented.stdout.split())
    layout = short_state.SOCKET_LAYOUT.strip("/").split("/")
    store, control = "/".join(layout[:2]), layout[2]
    assert f"<session-dir>/{control}/<NAME>.sock" in collapsed, (
        f"the installed oneharness no longer documents its control socket at "
        f"<session-dir>/{control}/<NAME>.sock, so {short_state.SOCKET_LAYOUT!r} is no "
        f"longer what a state root is followed by"
    )
    assert store in collapsed, (
        f"the installed oneharness no longer documents its session store under {store!r}, "
        f"so a state root is no longer followed by {short_state.SOCKET_LAYOUT!r}"
    )


# llmlint: ignore-end[test_tiers_split_by_project_not_by_marker]


def test_the_session_reservation_covers_the_longest_name_this_repository_can_compose() -> None:
    """What this host reserves is enough for the graphs and members it actually ships.

    Derived from the documents rather than restated: a graph or a member named longer
    than the reservation allows would refuse its own control socket, and the graph
    documents are where that name is decided.
    """
    graphs, members = shipped_session_parts()
    graph, member = max(graphs, key=len), max(members, key=len)
    longest = len(graph) + len(member) + composition_overhead() + len(SUFFIX)
    assert longest <= short_state.RESERVED_FOR_A_SESSION, (
        f"the longest session name this repository composes is {longest} bytes "
        f"({graph}-…-{member}-{LONGEST_SIDE}{SUFFIX}) where tests/short_state.py reserves "
        f"{short_state.RESERVED_FOR_A_SESSION}; raise the reservation and lower the budget "
        f"with it, or a controlled turn under it is refused for its address"
    )
