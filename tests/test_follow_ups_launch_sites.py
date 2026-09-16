"""Nothing in this repository launches `just follow-ups` except the success hook.

A finished run's follow-ups are verified by exactly two routes: the engine's success hook,
which `just orchestrate` wires to `scripts/run-ended.sh`, and a manager typing the recipe —
either `just follow-ups` itself or `just follow-ups-handle-comments`, which a manager types to
re-dispatch a run over people's new board comments and which reaches the one feedback path
by running `scripts/follow-ups.sh --feedback` rather than composing a launch of its own.
A scripted chain — a launcher running the follow-ups recipe once an attached launch
returns — was ruled out in favour of the hook, because a chain fires on a run the engine
did not judge complete and fires a second time beside the hook. So this gate holds the
tracked launchers to the two call sites that exist on purpose: the recipe's own line in the
justfile, and the hook's success branch. `tests/run_end_hooks/test_run_end_hooks_e2e.py` drives that
branch through a real launch; this holds that no other branch appeared beside it.

It reads the executable surfaces — the justfile, `scripts/`, `orchestrator/`, `graphs/` and
`.githooks/` — two ways. A shell, YAML or justfile line is scanned as text, skipping
comments and the lines that only *name* the recipe to a reader (`echo`, `printf`, a usage
string, a `fail` diagnostic). A Python file is parsed, because Python launches by argument
list and names the recipe in docstrings: a `"just", "follow-ups"` pair in a list or tuple,
or a string that names `scripts/follow-ups.sh` outside a docstring, is a launch. Markdown is
prose and is skipped. A launch spelled through a variable is missed, which is the gap a
reviewer reading a new launcher is left to close.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from orchestrator.root import REPO_ROOT

#: Where a launch could be written: every executable surface this repository tracks.
SURFACES = ("justfile", "scripts", "orchestrator", "graphs", ".githooks")

#: A line that runs the recipe or the script behind it.
LAUNCH = re.compile(r"\bjust\s+follow-ups\b|\bscripts/follow-ups\.sh\b")

#: A line that only names the recipe to a reader, by its first word.
NAMING = re.compile(r"^\s*(#|echo\b|printf\b|fail\b|usage=|\"|')")

#: The launches that exist on purpose, as file → the one line each holds.
ALLOWED = {
    "justfile": '@./scripts/follow-ups.sh "$@"',
    "scripts/run-ended.sh": (
        'output=$(cd -- "$checkout" && just follow-ups "$run" --detach) || status=$?'
    ),
    "scripts/follow-ups-handle-comments.sh": (
        'exec "$checkout/scripts/follow-ups.sh" "$run" --feedback "$feedback" '
        '${passed[@]+"${passed[@]}"}'
    ),
}


def _python_launches(source: str) -> list[str]:
    """Each launch a Python file makes, as the source segment that makes it."""
    tree = ast.parse(source)
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }
    found = []
    for node in ast.walk(tree):
        match node:
            case ast.List(elts=elements) | ast.Tuple(elts=elements):
                words = [
                    element.value if isinstance(element, ast.Constant) else None
                    for element in elements
                ]
                if any(
                    a == "just" and b == "follow-ups"
                    for a, b in zip(words, words[1:], strict=False)
                ):
                    found.append(ast.get_source_segment(source, node) or "")
            case ast.Constant(value=str(text)) if id(node) not in docstrings and re.search(
                r"\bscripts/follow-ups\.sh\b", text
            ):
                found.append(ast.get_source_segment(source, node) or "")
    return found


def _launches() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for surface in SURFACES:
        root = REPO_ROOT / surface
        files = [root] if root.is_file() else sorted(p for p in root.rglob("*") if p.is_file())
        for path in files:
            # Prose names the recipe to a reader and launches nothing.
            if "__pycache__" in path.parts or path.suffix == ".md":
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            if path.suffix == ".py":
                lines = _python_launches(text)
            else:
                lines = [
                    line.strip()
                    for line in text.splitlines()
                    if LAUNCH.search(line) and not NAMING.match(line)
                ]
            if lines:
                found[str(Path(path).relative_to(REPO_ROOT))] = lines
    return found


def test_only_the_success_hook_and_the_recipe_itself_launch_the_follow_ups_recipe() -> None:
    launches = _launches()

    assert launches == {name: [line] for name, line in ALLOWED.items()}, (
        f"the follow-ups recipe is launched from {launches}, and only the success hook "
        f"({ALLOWED['scripts/run-ended.sh']!r}), the recipe's own line and the comments "
        "recipe's feedback re-dispatch may launch it; "
        "a finished run's follow-ups are verified by the success hook or by a manager "
        "typing the recipe, never by a chain after another launch returns"
    )
