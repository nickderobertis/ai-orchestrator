#!/usr/bin/env python3
"""`tests/e2e/fake_codex.py`, in front of the one refusal codex makes before it does anything.

Run outside every git repository, the real codex exits 1 and says, on stderr,
``Not inside a trusted directory and --skip-git-repo-check was not specified.`` — unless its
argv carries ``--dangerously-bypass-approvals-and-sandbox``, which is what `oneharness` builds
for a `bypass` turn, or ``--skip-git-repo-check``. `oneharness` classifies that stderr as
`untrusted-directory` and moves the chain on. This is how the first real dispatch of
`graphs/follow-up.yaml`'s member failed: its working directory is the agent graph's scratch
directory, no repository, and its turn ran in `default` mode. `fake_codex.py` never refuses a
directory, so a journey over it passed while the real provider refused every turn.

**The refusal is decided from what this process was actually given**: the directory codex is
told with ``-C``/``--cd`` when `oneharness` names one and this process's working directory
otherwise, and the argv above. Nothing a journey sets says "refuse" or "don't"; pointing
``ONEHARNESS_BIN_CODEX`` here rather than at `fake_codex.py` is the whole choice a journey
makes. A turn it does not refuse is handed to `fake_codex.py` unchanged, in the same process
image, so every variable that stand-in reads keeps its meaning.

The refusal text and the trust arguments are restated from codex, so
`tests/e2e/test_fake_codex_untrusted_directory_e2e.py` reconciles both against the pinned
`oneharness`: that it classifies this refusal as an untrusted directory, and that the command
it builds for a `bypass` turn carries one of these arguments.

Keep this deterministic and stdlib-only — this file *is* the provider binary.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REFUSAL = "Not inside a trusted directory and --skip-git-repo-check was not specified."

TRUST_ARGUMENTS = frozenset({"--dangerously-bypass-approvals-and-sandbox", "--skip-git-repo-check"})

FAKE_CODEX = Path(__file__).resolve().with_name("fake_codex.py")


def working_directory(argv: list[str]) -> Path:
    """Where codex would run: the `-C`/`--cd` it is told, or where it was started."""
    named = next(
        (argv[at + 1] for at, word in enumerate(argv[:-1]) if word in ("-C", "--cd")), None
    )
    return Path(named or os.getcwd()).resolve()


def inside_a_repository(directory: Path) -> bool:
    """Whether `directory` or any directory above it holds a git repository's `.git`."""
    return any((candidate / ".git").exists() for candidate in (directory, *directory.parents))


def refused(argv: list[str]) -> bool:
    """Whether codex refuses this launch before it starts a turn."""
    if {"--version", "-v"}.intersection(argv):
        return False
    if TRUST_ARGUMENTS.intersection(argv):
        return False
    return not inside_a_repository(working_directory(argv))


def main() -> int:
    argv = sys.argv[1:]
    if refused(argv):
        print("Reading additional input from stdin...", file=sys.stderr)
        print(REFUSAL, file=sys.stderr)
        return 1
    os.execv(sys.executable, [sys.executable, str(FAKE_CODEX), *argv])  # noqa: S606 - the stand-in
    return 0  # pragma: no cover - execv does not return


if __name__ == "__main__":
    raise SystemExit(main())
