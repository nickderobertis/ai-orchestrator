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

#: What `oneagentgraph` puts between those two when it composes a session name:
#: `<graph>-<millis>-<pid>-<member>-<side>`, then oneharness's own `.sock` suffix. The
#: epoch is in milliseconds and the side is `skill` or `user`; the process id's width is
#: this host's, read below rather than assumed.
EPOCH_MILLIS_DIGITS = 13
LONGEST_SIDE = "skill"
SEPARATORS = 4
SUFFIX = ".sock"

#: Where Linux states the largest process id it will issue.
PID_MAX = Path("/proc/sys/kernel/pid_max")


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


def _longest(pattern: re.Pattern[str], text: str) -> str:
    return max(pattern.findall(text), key=len, default="")


def test_the_session_reservation_covers_the_longest_name_this_repository_can_compose() -> None:
    """What this host reserves is enough for the graphs and members it actually ships.

    Derived from the documents rather than restated: a graph or a member named longer
    than the reservation allows would refuse its own control socket, and the graph
    documents are where that name is decided.
    """
    graph, member = "", ""
    for document in sorted(GRAPHS.glob("*.yaml")):
        text = document.read_text(encoding="utf-8")
        named = GRAPH_NAME.search(text)
        assert named is not None, f"{document} states no name"
        graph = max(graph, named.group(1), key=len)
        opened = MEMBERS_BLOCK.search(text)
        assert opened is not None, f"{document} declares no members"
        member = max(member, _longest(MEMBER_KEY, text[opened.end() :]), key=len)
    assert graph and member, "no graph document named a graph and a member"

    pid_digits = len(PID_MAX.read_text(encoding="utf-8").strip())
    longest = (
        len(graph)
        + EPOCH_MILLIS_DIGITS
        + pid_digits
        + len(member)
        + len(LONGEST_SIDE)
        + SEPARATORS
        + len(SUFFIX)
    )
    assert longest <= short_state.RESERVED_FOR_A_SESSION, (
        f"the longest session name this repository composes is {longest} bytes "
        f"({graph}-<{EPOCH_MILLIS_DIGITS} digits>-<{pid_digits} digits>-{member}-"
        f"{LONGEST_SIDE}{SUFFIX}) where tests/short_state.py reserves "
        f"{short_state.RESERVED_FOR_A_SESSION}; raise the reservation and lower the budget "
        f"with it, or a controlled turn under it is refused for its address"
    )
