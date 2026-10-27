"""Every `uv run` this repository spells runs without syncing the environment.

Session setup is what provisions a checkout, and nothing else installs (AGENTS.md, "The
checks"); a `uv run` that synced on its way in would be a second installer, rewriting a
`.venv` that other processes are reading. What keeps one from syncing is its context, so
an invocation counts as unsynced where:

- it spells `--no-sync` itself;
- it is in the `justfile`, under the top-level `export UV_NO_SYNC := "1"` every recipe
  inherits;
- it is in a script under `scripts/` that exports `UV_NO_SYNC=1`, or sources
  `scripts/require-provisioned.sh`, which exports it, above its first `uv run`, so a
  caller that ran the script directly is covered as well as one that ran it from `just`;
- it is an Nx target's command, in a tracked `project.json` or `nx.json`, which Nx runs
  only under `scripts/nx.sh`, which exports it.

The manager's allowlist, its two renderings, `AGENTS.md` and `docs/` hold commands a
person or a manager types into a shell of their own, where nothing sets the context, so
each invocation there spells `--no-sync`. And no line of any of them unsets the variable
or sets it to anything but `1`.

An invocation is `uv run` followed by an argument on a line that is not a comment; `uv
run` quoted alone, as prose names it, is a mention.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Iterator

import pytest

from orchestrator.root import REPO_ROOT

#: Every file this scan reads is one git tracks, prose among them.
pytestmark = pytest.mark.reads_docs

#: `uv run` followed by an argument rather than by a quote closing a mention of it.
INVOCATION = re.compile(r"\buv run(?=[ \t]+[^\s'\"`)\]])")
#: An invocation that says it runs unsynced on its own.
UNSYNCED = re.compile(r"\buv run\b[^\n]*?--no-sync\b")
#: A line that takes the variable away, or gives it a value other than `1`.
UNSETS = re.compile(r"-u[ \t]+UV_NO_SYNC\b|\bunset[ \t]+UV_NO_SYNC\b|\bUV_NO_SYNC=(?![\"']?1\b)")

#: The export the `justfile` carries for every recipe it runs.
JUSTFILE_EXPORT = re.compile(r'^export UV_NO_SYNC := "1"$', re.MULTILINE)
#: What a script spells to establish the context for everything after it.
SCRIPT_CONTEXT = re.compile(r"^(?:export UV_NO_SYNC=1|\. .*require-provisioned\.sh\"?)$")
#: The script Nx runs every target under, and the line it runs Nx on.
NX_WRAPPER = "scripts/nx.sh"
NX_LAUNCH = re.compile(r"^[^#]*node_modules/\.bin/nx\b|^[^#]*\$nx_bin\b|^[^#]*\bNX_BIN\b")

#: The files where nothing sets the context, so each invocation spells `--no-sync`.
TYPED_BY_HAND = (
    "config/manager-allowlist.toml",
    ".claude/settings.json",
    ".codex/rules/oneharness.rules",
    "AGENTS.md",
)


def _tracked(*patterns: str) -> list[str]:
    listed = subprocess.run(
        ["git", "ls-files", "-z", "--", *patterns],
        cwd=REPO_ROOT,
        check=True,
        text=True,
        capture_output=True,
    ).stdout
    return sorted(path for path in listed.split("\0") if path)


def _read(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def _shell_lines(text: str) -> Iterator[tuple[int, str]]:
    """Every line of shell, numbered, that is not a comment."""
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.lstrip().startswith("#"):
            yield number, line


def invocations(text: str, *, shell: bool) -> list[tuple[int, str]]:
    """Each line of ``text`` that invokes `uv run`, with its number."""
    lines = _shell_lines(text) if shell else enumerate(text.splitlines(), start=1)
    return [(number, line) for number, line in lines if INVOCATION.search(line)]


def syncing_in_a_script(text: str) -> list[tuple[int, str]]:
    """The invocations of one script that would sync: those above its context, if any."""
    found = invocations(text, shell=True)
    context = next(
        (n for n, line in _shell_lines(text) if SCRIPT_CONTEXT.match(line.strip())), None
    )
    return [
        (number, line)
        for number, line in found
        if not UNSYNCED.search(line) and (context is None or number < context)
    ]


def syncing_by_hand(text: str) -> list[tuple[int, str]]:
    """The invocations typed with no context that do not spell `--no-sync` themselves."""
    return [
        (number, line)
        for number, line in invocations(text, shell=False)
        if not UNSYNCED.search(line)
    ]


def _commands(node: object) -> Iterator[str]:
    """Every Nx target command a parsed `project.json` or `nx.json` declares."""
    match node:
        case {"command": str(command), **rest}:
            yield command
            yield from _commands(rest)
        case {"commands": list(commands), **rest}:
            yield from (c if isinstance(c, str) else c.get("command", "") for c in commands)
            yield from _commands(rest)
        case dict():
            for value in node.values():
                yield from _commands(value)
        case list():
            for value in node:
                yield from _commands(value)


def _nx_files() -> list[str]:
    return [*_tracked("nx.json"), *_tracked("*project.json")]


def test_the_justfile_runs_every_recipe_unsynced() -> None:
    text = _read("justfile")
    assert JUSTFILE_EXPORT.search(text), (
        'the justfile no longer carries `export UV_NO_SYNC := "1"`, so every recipe\'s '
        "`uv run` would sync this checkout's environment on its way in"
    )
    assert invocations(text, shell=True), "the justfile runs no `uv run`; this reads nothing"


def test_every_script_establishes_the_context_before_its_first_uv_run() -> None:
    scripts = _tracked("scripts/*.sh")
    syncing = {script: found for script in scripts if (found := syncing_in_a_script(_read(script)))}
    assert any(invocations(_read(script), shell=True) for script in scripts), (
        "no script under scripts/ runs `uv run`; this scan has stopped matching them"
    )
    assert not syncing, (
        "these scripts run `uv run` with nothing above it exporting `UV_NO_SYNC=1` or "
        "sourcing scripts/require-provisioned.sh, so run directly they sync the "
        f"environment: {syncing}"
    )


def test_nx_runs_every_target_under_the_wrapper_that_exports_the_context() -> None:
    wrapper = _read(NX_WRAPPER)
    lines = list(_shell_lines(wrapper))
    exported = next((n for n, line in lines if line.strip() == "export UV_NO_SYNC=1"), None)
    launched = next((n for n, line in lines if NX_LAUNCH.search(line)), None)
    assert exported is not None and launched is not None and exported < launched, (
        f"{NX_WRAPPER} no longer exports `UV_NO_SYNC=1` before it runs Nx, so every "
        "target's `uv run` would sync"
    )
    commands = [command for path in _nx_files() for command in _commands(json.loads(_read(path)))]
    assert any(INVOCATION.search(command) for command in commands), (
        "no Nx target runs `uv run`; this scan has stopped reading their commands"
    )


def test_no_file_in_scope_takes_the_context_away() -> None:
    scope = [
        "justfile",
        *_tracked("scripts/*"),
        *_nx_files(),
        *TYPED_BY_HAND,
        *_tracked("docs/*"),
    ]
    taken = {
        path: [line for _, line in _shell_lines(_read(path)) if UNSETS.search(line)]
        for path in scope
    }
    assert not {path: lines for path, lines in taken.items() if lines}, (
        "these lines unset `UV_NO_SYNC` or set it to something other than 1, so a "
        f"`uv run` under them syncs: { {p: lines for p, lines in taken.items() if lines} }"
    )


@pytest.mark.parametrize("path", [*TYPED_BY_HAND, *_tracked("docs/*.md")])
def test_every_command_typed_by_hand_spells_no_sync(path: str) -> None:
    syncing = syncing_by_hand(_read(path))
    assert not syncing, (
        f"{path} gives a `uv run` to be typed into a shell where nothing exports "
        f"`UV_NO_SYNC`, so it would sync; spell it `uv run --no-sync`: {syncing}"
    )


def test_the_scan_tells_an_invocation_from_a_mention_and_a_context_from_none() -> None:
    """Each rule above, refusing the shape it exists for and passing its counterpart."""
    assert syncing_by_hand("run `uv run onevcs sweep <run-id>` once") == [
        (1, "run `uv run onevcs sweep <run-id>` once")
    ]
    assert syncing_by_hand("run `uv run --no-sync onevcs sweep` once") == []
    assert syncing_by_hand("every recipe that runs `uv run` exports it") == []

    unprovided = "#!/usr/bin/env bash\nset -euo pipefail\nexec uv run onevcs repos\n"
    assert syncing_in_a_script(unprovided) == [(3, "exec uv run onevcs repos")]
    late = "set -e\nuv run onevcs repos\nexport UV_NO_SYNC=1\n"
    assert syncing_in_a_script(late) == [(2, "uv run onevcs repos")]
    provided = "set -e\nexport UV_NO_SYNC=1\nuv run onevcs repos\n"
    assert syncing_in_a_script(provided) == []
    sourced = 'set -e\n. "$script_dir/require-provisioned.sh"\nuv run onevcs repos\n'
    assert syncing_in_a_script(sourced) == []
    assert syncing_in_a_script("# a `uv run onevcs` in a comment\n") == []
    assert syncing_in_a_script("echo \"not 'uv run' here\"\n") == []

    assert UNSETS.search("env -u UV_NO_SYNC uv run pytest")
    assert UNSETS.search("UV_NO_SYNC=0 uv run pytest")
    assert not UNSETS.search("export UV_NO_SYNC=1")
    assert not UNSETS.search('environment["UV_NO_SYNC"] = "1"')
