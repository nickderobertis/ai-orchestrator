"""Nothing this repository runs deletes from the oneharness history store.

A run journal's transcript reference names a session file in that store, and the
browser and `just transcript` read every turn whole from it, so the store is what every
reference depends on. `AGENTS.md` states who may delete from it — a person running
`oneharness history clear --yes`, and nothing here — and this is the check that keeps
that true of the tree: a tracked recipe, script, hook or package module that runs the
store's clear verb, or removes a path naming the store, is refused by name.

It reads the command lines rather than running them, so it is a floor rather than a
proof: a removal reaching the store through a path built somewhere it cannot see is
missed. What it does follow is the one indirection a script writes most, a variable
assigned from a store path and removed by name. Its two recognisers — the store's clear
verb, and the store's directory and the variable that moves it — are oneharness's to
name, so `tests/release_pins/test_history_store_scan_names.py` reconciles them against the
installed `oneharness` CLI: the verb against its own help, and the directory against where
a real history run puts its store.
"""

from __future__ import annotations

import re
import subprocess

import pytest

from orchestrator.root import REPO_ROOT

#: Every file this scan reads is one git tracks.
pytestmark = pytest.mark.reads_docs

#: What runs on this host when a person or the engine starts something here: the recipes,
#: the scripts behind them, the git hooks, the harness's own hook configurations, and the
#: package the recipes run.
RUNNABLE = re.compile(r"^(?:justfile$|scripts/|\.githooks/|\.claude/|\.codex/|orchestrator/)")

#: The store's own clear verb, however a command line spells the words between its parts.
CLEAR = re.compile(r"\bhistory(?:\s|\\)+clear\b")
#: A path or variable naming the host's history store: its directory under the state
#: home, or the variable oneharness reads it from.
STORE = re.compile(r"oneharness/history|ONEHARNESS_HISTORY_DIR\b")
#: A command or call that removes a path.
REMOVES = re.compile(
    r"\brm\b|\brmdir\b|\bunlink\b|\bshred\b|(?:^|\s)-delete\b|\brmtree\b|\bos\.remove\b"
    r"|\.unlink\(|\.rmdir\("
)
#: A shell or Python assignment, the name it assigns and what it assigns from.
ASSIGNED = re.compile(
    r"^\s*(?:export\s+|local\s+|readonly\s+)?(?P<name>[A-Za-z_]\w*)\s*=(?P<value>.*)$"
)


def _tracked() -> list[str]:
    listed = subprocess.run(
        ["git", "ls-files", "-z"],  # noqa: S607 - git from the search path, as the gate runs it
        cwd=REPO_ROOT,
        capture_output=True,
        check=True,
    ).stdout.decode()
    return [path for path in listed.split("\0") if path and RUNNABLE.match(path)]


def _offending(text: str) -> list[str]:
    """Each line of ``text`` that clears the store or removes a path naming it."""
    lines = text.replace("\\\n", " ").splitlines()
    held = {
        assigned["name"]
        for line in lines
        if (assigned := ASSIGNED.match(line)) and STORE.search(assigned["value"])
    }
    naming = re.compile(
        "|".join([STORE.pattern, *(rf"\$\{{?{name}\b|\b{name}\b" for name in sorted(held))])
    )
    return [
        f"line {number}: {line.strip()[:160]}"
        for number, line in enumerate(lines, start=1)
        if CLEAR.search(line) or (REMOVES.search(line) and naming.search(line))
    ]


def test_no_tracked_recipe_script_or_hook_deletes_from_the_history_store() -> None:
    found: dict[str, list[str]] = {}
    for path in _tracked():
        try:
            text = (REPO_ROOT / path).read_text(encoding="utf-8")
        except (UnicodeDecodeError, IsADirectoryError, FileNotFoundError):
            continue
        if offending := _offending(text):
            found[path] = offending

    assert found == {}, (
        "only a person running `oneharness history clear --yes` deletes oneharness history "
        "files (AGENTS.md); these tracked files would delete from the store every run "
        f"journal's transcript references read: {found}"
    )


@pytest.mark.parametrize(
    ("text", "offends"),
    [
        ("prune:\n    oneharness history clear --yes\n", True),
        ("exec oneharness history \\\n  clear --yes\n", True),
        ('rm -rf -- "${XDG_STATE_HOME:-$HOME/.local/state}/oneharness/history/old"\n', True),
        ('find "$HOME/.local/state/oneharness/history" -mtime +30 -delete\n', True),
        ('store="$HOME/.local/state/oneharness/history"\nrm -f -- "$store"/*.jsonl\n', True),
        ('shutil.rmtree(Path(os.environ["ONEHARNESS_HISTORY_DIR"]))\n', True),
        ('export ONEHARNESS_HISTORY_DIR="$scratch/history"\nrm -rf -- "$scratch"\n', False),
        ("oneharness history show --last 1\n", False),
        ('rm -rf -- "$run_root/scratch"\n', False),
    ],
    ids=[
        "a recipe clearing the store",
        "the clear verb continued over a line",
        "a removal under the state home's store",
        "a find deleting from the store",
        "a variable holding the store, removed by name",
        "a module removing the store the variable names",
        "a private store the script made, removed with its scratch",
        "a read of the store",
        "a removal naming nothing of the store",
    ],
)
def test_the_scan_tells_a_deletion_from_the_store_from_everything_else(
    text: str, offends: bool
) -> None:
    assert bool(_offending(text)) is offends
