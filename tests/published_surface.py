"""The command surface a pinned CLI actually has, read from the CLI itself.

This repository's prose is the operating manual for four published CLIs it does not
build, so every command and flag it names is a copy of somebody else's interface —
and a copy goes stale in silence. `docs/repo-lifecycle.md` accumulated four separate
flags on verbs that never had them, and an agent following it reached for raw `git`
because the documented path did not exist.

So the surface is resolved by asking the binary. `--help` is walked recursively from
the root, which is what makes a version bump re-derive the answer rather than replay
one: there is no table here to update, and a verb that goes away stops being in the
surface the moment the pin moves.
"""

from __future__ import annotations

import re
import subprocess
from functools import cache
from typing import NamedTuple

import pytest

from orchestrator.root import REPO_ROOT

#: The `help` pseudo-command clap adds to every level. It is not a verb anybody types
#: here, and recursing into it would walk the whole tree a second time.
HELP_COMMAND = "help"

#: A section heading in clap's help output, e.g. `Commands:` or `Options:`.
SECTION = re.compile(r"^(?P<name>[A-Za-z][A-Za-z ]*):$")

#: One entry under `Commands:`. clap indents these exactly two spaces; a wrapped
#: description line is indented far further, which is what this refuses to match.
COMMAND_ENTRY = re.compile(r"^ {2}(?P<name>[a-z][a-z0-9-]*)(?: {2,}.*)?$")

#: One entry under `Options:`. Both help widths put the flag itself first on the line,
#: so the entry is recognized by starting with a dash after its indent.
OPTION_ENTRY = re.compile(r"^ {2,6}(?P<spec>-.*)$")

#: A long flag, as it appears in an option spec or in prose. `--flag=value` is one
#: flag, so the name stops at the `=`.
LONG_FLAG = re.compile(r"--[a-z][a-z0-9-]*")

#: The first line of a verb that hands its arguments on to a sibling CLI's verb, e.g.
#: `onepipeline publish-branch`'s "Land a completed branch through the linked onevcs
#: `publish-branch`, …". Such a verb takes its own few flags on its `Usage:` line and
#: forwards every other one, so what it accepts is its own plus the named verb's —
#: read from the binary rather than declared, so a release that stops forwarding
#: stops widening the surface.
FORWARDS_TO = re.compile(r"\bthe linked (?P<tool>[a-z]+) `(?P<verb>[a-z][a-z0-9 -]*)`")


class Surface(NamedTuple):
    """One pinned CLI's whole command tree, as its own `--help` reports it.

    Both fields are keyed by the command path as a tuple — `()` for the root,
    `("rules", "check")` for a nested verb — because a path is what prose names and
    what a flag has to be checked against. A flat set of flag names would answer
    "does this tool have `--policy` anywhere", which is the question that let
    `--title` and `--policy` be documented on the one verb that lacks them.
    """

    #: The tool's binary name, as prose writes it.
    tool: str
    #: Every command path this tool accepts, including the root `()`.
    paths: frozenset[tuple[str, ...]]
    #: The long flags each path accepts, by path.
    flags: dict[tuple[str, ...], frozenset[str]]
    #: The sibling CLI verb, as `(tool, path)`, each forwarding path hands the rest of
    #: its arguments to.
    forwards: dict[tuple[str, ...], tuple[str, tuple[str, ...]]]

    def subcommands(self, path: tuple[str, ...]) -> frozenset[str]:
        """The names that continue `path` by one level."""
        return frozenset(
            candidate[len(path)]
            for candidate in self.paths
            if len(candidate) == len(path) + 1 and candidate[: len(path)] == path
        )

    def accepts(self, path: tuple[str, ...], flag: str) -> bool:
        """Whether `flag` is one this command path takes.

        The root's flags count at every level: `--help` is inherited, and a tool whose
        root declares a global flag accepts it on its verbs too.
        """
        return flag in self.accepted(path)

    def accepted(self, path: tuple[str, ...]) -> frozenset[str]:
        """Every long flag `path` takes: its own, the root's, and any it forwards."""
        own = self.flags.get(path, frozenset()) | self.flags[()]
        forwarded = self.forwards.get(path)
        if forwarded is None:
            return own
        tool, target = forwarded
        return own | surface_of(tool).accepted(target)


def _help(binary: str, path: tuple[str, ...]) -> str:
    """One command's help text, from the pinned binary."""
    reported = subprocess.run(
        [binary, *path, "--help"],
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )
    if reported.returncode != 0:
        pytest.fail(
            f"`{' '.join((binary, *path))} --help` exited {reported.returncode}, so the "
            f"published surface cannot be read from it:\n{reported.stderr}"
        )
    return reported.stdout


def _sections(help_text: str) -> tuple[frozenset[str], frozenset[str]]:
    """The subcommands and long flags one help page declares.

    A verb that takes its arguments as one trailing list declares its flags on its
    `Usage:` line alone, so that line's flags count as well as the `Options:` entries.
    """
    commands: set[str] = set()
    flags: set[str] = set()
    section = ""
    for line in help_text.splitlines():
        if line.startswith("Usage: "):
            flags.update(LONG_FLAG.findall(line))
            continue
        heading = SECTION.match(line)
        if heading is not None:
            section = heading.group("name")
            continue
        if not line.strip():
            continue
        match section:
            case "Commands":
                entry = COMMAND_ENTRY.match(line)
                if entry is not None and entry.group("name") != HELP_COMMAND:
                    commands.add(entry.group("name"))
            case "Options":
                entry = OPTION_ENTRY.match(line)
                if entry is None:
                    continue
                # The spec is everything before the first run of two spaces; past
                # that is the description, and a description routinely names other
                # flags in prose.
                spec = re.split(r" {2,}", entry.group("spec"), maxsplit=1)[0]
                flags.update(LONG_FLAG.findall(spec))
    return frozenset(commands), frozenset(flags)


def _walk(binary: str, tool: str) -> Surface:
    """Read the whole command tree by recursing through `--help`."""
    paths: set[tuple[str, ...]] = set()
    flags: dict[tuple[str, ...], frozenset[str]] = {}
    forwards: dict[tuple[str, ...], tuple[str, tuple[str, ...]]] = {}
    pending: list[tuple[str, ...]] = [()]
    while pending:
        path = pending.pop()
        if path in paths:
            continue
        paths.add(path)
        page = _help(binary, path)
        commands, declared = _sections(page)
        flags[path] = declared
        about = FORWARDS_TO.search(page.split("\n", 1)[0])
        if path and about is not None and about.group("tool") != tool:
            forwards[path] = (about.group("tool"), tuple(about.group("verb").split()))
        pending.extend((*path, command) for command in commands)
    return Surface(tool, frozenset(paths), flags, forwards)


@cache
def surface_of(tool: str) -> Surface:
    """The pinned `tool`'s surface, read once per session.

    Resolved against this worktree's own `.venv`, which is where `just bootstrap`
    installs the adopted release — the same binary every recipe reaches through
    `uv run`. A tool found on the ambient PATH instead could be any version, and a
    drift gate answering from the wrong one is worse than none.
    """
    binary = REPO_ROOT / ".venv" / "bin" / tool
    if not binary.is_file():
        pytest.fail(f"the pinned {tool} is not installed at {binary} — run 'just bootstrap'")
    return _walk(str(binary), tool)
