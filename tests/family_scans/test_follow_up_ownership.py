"""onefollowup owns the follow-up loop; this host keeps only the wrappers that configure it.

Every follow-up behaviour — a draft's record, a ticket's record, the comment markers, the
receipts, the verification and the answers — is onefollowup's, configured by the root
`onefollowup.yaml` and the plan templates under `templates/`. This repository keeps the thin
wrappers that hand it what the environment and the arguments say, and nothing else may
grow back. Two properties are held, over every tracked file under `orchestrator/` and
`scripts/` and every recipe body in the `justfile`:

* **Only the named wrappers touch the loop.** Code that invokes `onefollowup`, or reads or
  writes a follow-up draft, a ticket record, a comment marker, a receipt or onefollowup's
  working files, is one of the wrappers below, and a file added outside that list that does
  any of it fails.
* **Each wrapper only composes.** Every command it runs is `onefollowup`, the plan store's
  `project create` with the engine's template resolution, the engine's launch, or — the
  budget-inputs hook alone — the engine's per-change telemetry, `onevcs resolve`,
  `onebudgetspec` and `jq`, beside the shell's own plumbing.
"""

from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

import pytest

from orchestrator.root import REPO_ROOT

#: What touching the follow-up loop looks like in code beside invoking onefollowup: its
#: working files, the drafts root, the ticket and draft record keys, and the comment markers.
TOUCHES = re.compile(
    r"/\.venv/bin/onefollowup\b"
    r"|\.onefollowup\b"
    r"|(?<![\w-])\.follow-ups\b"
    r"|orchestrator\.follow-up"
    r"|orchestrator:follow-up-comment"
    r"|\breceipts/"
)

#: How onefollowup is named where it is run: by its name, or by a path ending in it.
ONEFOLLOWUP = re.compile(r"^(?:\S*/)?onefollowup$")

#: The wrappers this task names, each with the commands it may run beyond shell plumbing.
LAUNCH = frozenset({"onepipeline", "onetaskgraph", "./scripts/onepipeline.sh"})
WRAPPERS: dict[str, frozenset[str]] = {
    "scripts/follow-up.sh": frozenset(),
    "scripts/follow-up-draft.sh": frozenset({"onefollowup", "onepipeline", "jq"}),
    "scripts/follow-up-budget-inputs.sh": frozenset(
        {"onepipeline", "onevcs", "onebudgetspec", "jq"}
    ),
    "recipe:follow-up": frozenset(),
    "recipe:follow-ups": LAUNCH | {"onefollowup", "jq"},
    "recipe:follow-ups-answer-comments": LAUNCH | {"onefollowup"},
    "recipe:follow-ups-answer-batch": LAUNCH,
    "recipe:groom": LAUNCH | {"onefollowup", "jq"},
}
#: `scripts/run-ended.sh`'s success branch runs `just follow-ups`, so it is a wrapper by
#: what it calls rather than by what it touches; it is named for that reason.
RUN_ENDED = "scripts/run-ended.sh"

#: The shell's own plumbing, which composes and decides nothing about follow-ups.
PLUMBING = frozenset(
    {
        "set",
        "export",
        "local",
        "echo",
        "printf",
        "cat",
        "exit",
        "exec",
        "shift",
        "case",
        "esac",
        "if",
        "then",
        "else",
        "elif",
        "fi",
        "for",
        "while",
        "do",
        "done",
        "until",
        "read",
        "return",
        "trap",
        "rm",
        "mktemp",
        "dirname",
        "basename",
        "cd",
        "pwd",
        "sort",
        "tee",
        "date",
        "tr",
        "[",
        "[[",
        "test",
        ":",
        "true",
        "false",
        "continue",
        "break",
        "{",
        "}",
        "(",
        ")",
        "!",
        "command",
        "wait",
    }
)


def _code_lines(text: str) -> list[str]:
    """``text``'s lines that are code, full-line comments and shellcheck notes dropped."""
    return [line for line in text.splitlines() if not line.lstrip().startswith("#")]


def _python_touches(text: str) -> bool:
    """Whether a Python module's code — its strings bar docstrings — touches the loop."""
    tree = ast.parse(text)
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }
    strings = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]
    invoked = any(
        isinstance(node, ast.List | ast.Tuple)
        and node.elts
        and isinstance(node.elts[0], ast.Constant)
        and isinstance(node.elts[0].value, str)
        and ONEFOLLOWUP.match(node.elts[0].value)
        for node in ast.walk(tree)
    )
    return invoked or any(TOUCHES.search(piece) for piece in strings)


def touches(path: str, text: str) -> bool:
    """Whether the code of the file at ``path``, holding ``text``, touches the loop."""
    if path.endswith(".py"):
        return _python_touches(text)
    ran = commands(text)
    return any(ONEFOLLOWUP.match(head) for head in ran) or any(
        TOUCHES.search(line) for line in _code_lines(text)
    )


def recipes(justfile: str) -> dict[str, str]:
    """Each recipe's body in ``justfile``, by name."""
    found: dict[str, str] = {}
    name: str | None = None
    for line in justfile.splitlines():
        header = re.match(r"^([a-z][a-z0-9-]*)\b[^:=]*:(?!=)", line)
        if header and not line.startswith((" ", "\t")):
            name = header.group(1)
            found[name] = ""
        elif name is not None and line.startswith((" ", "\t")):
            found[name] += line + "\n"
        elif line.strip() and not line.startswith("#"):
            name = None
    return found


def _plain(text: str) -> str:
    """Shell ``text`` with comments, heredoc bodies, quoted strings and arithmetic blanked."""
    text = "\n".join(_code_lines(text))
    text = re.sub(r"<<-?'?(\w+)'?\n.*?\n\s*\1\n", "\n", text, flags=re.DOTALL)
    text = re.sub(r"'[^']*'", "''", text)
    text = re.sub(r'"(?:\\.|[^"\\])*"', lambda found: _head_or_blank(found.group(0)), text)
    return re.sub(r"\$\(\(.*?\)\)", "0", text)


def _head_or_blank(quoted: str) -> str:
    """A double-quoted word kept where it names a program, ``""`` otherwise."""
    inner = quoted[1:-1]
    return inner if re.fullmatch(r"\$\{?[A-Za-z_]\w*\}?|[\w./-]+", inner) else '""'


def commands(text: str) -> set[str]:
    """The head of every simple command in shell ``text``, comments and quotes aside."""
    heads: set[str] = set()
    plain = _plain(text).replace("\\\n", " ")
    for line in plain.splitlines():
        for segment in re.split(r"\|\||&&|[|;]|\$\(|<\(|\bthen\b|\bdo\b|\belse\b", line):
            words = segment.strip().split()
            while words and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", words[0]):
                words = words[1:]
            if not words or words[0].startswith(("-", '"', "$", ">", "<", "*", "#", ")")):
                continue
            if re.fullmatch(r"\d+", words[0]):
                continue
            if re.fullmatch(r"[\w./\[\]{}()!:-]+", words[0]) and not words[0].endswith(")"):
                heads.add(words[0])
    return heads


def defined(text: str) -> set[str]:
    """The shell functions ``text`` defines, whose bodies are scanned as part of ``text``."""
    return set(re.findall(r"^\s*([A-Za-z_][\w-]*)\(\)\s*\{", text, re.MULTILINE))


def _tracked() -> dict[str, str]:
    listed = subprocess.run(
        ["git", "ls-files", "orchestrator", "scripts"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.split()
    return {
        path: (REPO_ROOT / path).read_text(encoding="utf-8")
        for path in listed
        if (REPO_ROOT / path).is_file() and path.endswith((".py", ".sh"))
    }


def _subjects() -> dict[str, str]:
    subjects = _tracked()
    justfile = (REPO_ROOT / "justfile").read_text(encoding="utf-8")
    subjects.update({f"recipe:{name}": body for name, body in recipes(justfile).items()})
    return subjects


def test_only_the_named_wrappers_touch_the_follow_up_loop() -> None:
    touching = {name for name, text in _subjects().items() if touches(name, text)}

    assert touching <= set(WRAPPERS), sorted(touching - set(WRAPPERS))
    assert "scripts/follow-up-draft.sh" in touching
    assert "recipe:follow-ups-answer-comments" in touching


#: The one helper a wrapper may source: the definition of which manager session is acting,
#: whose answer names the owner `onefollowup.yaml`'s review sink hands the engine.
SOURCED = re.compile(r'^\s*\. "[^"]*/scripts/launcher-session\.sh"$')


def _sources_only_the_session_helper(text: str) -> bool:
    """Whether every line of ``text`` that sources a file sources the session helper."""
    sourcing = [line for line in _code_lines(text) if re.match(r"^\s*\.\s", line)]
    return all(SOURCED.match(line) for line in sourcing)


@pytest.mark.parametrize("wrapper", sorted(WRAPPERS))
def test_each_wrapper_runs_only_the_verbs_it_composes(wrapper: str) -> None:
    text = _subjects()[wrapper]
    allowed = WRAPPERS[wrapper] | PLUMBING | {"just", "./scripts/follow-up-draft.sh"}
    allowed |= {"$draft", "$engine", "$onefollowup"} | defined(text)
    if _sources_only_the_session_helper(text):
        allowed |= {"."}

    ran = commands(text)

    assert ran <= allowed, sorted(ran - allowed)
    assert "python" not in text
    assert "orchestrator." not in text


def test_a_wrapper_sourcing_anything_but_the_session_helper_is_not_composing() -> None:
    """Sourcing runs another file's code in the wrapper, so only the session helper may be."""
    assert _sources_only_the_session_helper('    . "$checkout/scripts/launcher-session.sh"\n')
    assert not _sources_only_the_session_helper('. "$checkout/scripts/follow-up-logic.sh"\n')
    assert not _sources_only_the_session_helper(". ./launcher-session.sh\n")


def test_the_success_branch_of_the_run_end_hook_only_launches_the_follow_ups_recipe() -> None:
    text = (REPO_ROOT / RUN_ENDED).read_text(encoding="utf-8")
    success = text[text.index("    success)") : text.index("    failure)")]

    assert "just follow-ups" in success
    assert not touches(RUN_ENDED, success)


def test_a_file_outside_the_list_that_touches_the_loop_is_named(tmp_path: Path) -> None:
    """The scan reads code, not prose: each way of touching the loop is caught, a mention is not."""
    assert touches("scripts/new.sh", "onefollowup draft --batch x\n")
    assert touches("scripts/new.sh", 'cat "$root/.onefollowup/state/x.json"\n')
    assert touches("orchestrator/new.py", 'KEY = "orchestrator.follow-up"\n')
    assert touches("orchestrator/new.py", 'run(["onefollowup", "publish"])\n')
    assert touches("orchestrator/new.py", 'MARK = "<!-- orchestrator:follow-up-comment"\n')
    assert not touches("scripts/new.sh", "# onefollowup owns this\n")
    assert not touches("orchestrator/new.py", '"""onefollowup owns this."""\n')
    assert not touches("orchestrator/new.py", 'ROW = "pypi:onefollowup-cli"\n')


#: The follow-up wrappers that are handed a run id, which the engine alone says the shape of.
RUN_ID_HOLDERS = (RUN_ENDED, "scripts/follow-up-budget-inputs.sh", "scripts/follow-up-draft.sh")
#: A character-class grammar a script would hold a run id to: a bracket class anchored whole.
RUN_ID_GRAMMAR = re.compile(r"\^\[[^\]]*A-Za-z[^\]]*\][+*]?(?:\[[^\]]*\][+*])?\$")


def test_no_follow_up_wrapper_holds_a_run_id_to_a_grammar_of_its_own() -> None:
    """What a run id is, is the engine's: a wrapper restating a grammar refuses ids it mints.

    The engine mints run ids with a dot in them, which the grammar these scripts once shared
    refused, so each passes the id through and lets the engine's own reads refuse a run it
    does not know.
    """
    restated = {
        script: RUN_ID_GRAMMAR.findall((REPO_ROOT / script).read_text(encoding="utf-8"))
        for script in RUN_ID_HOLDERS
    }

    assert not {script: found for script, found in restated.items() if found}, restated
    assert RUN_ID_GRAMMAR.search("RUN_ID='^[A-Za-z0-9_-]+$'")
    assert RUN_ID_GRAMMAR.search("'^[A-Za-z0-9_][A-Za-z0-9_-]*$'")
