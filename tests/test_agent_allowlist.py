"""The manager's allowlist grants what `AGENTS.md` sanctions, and nothing it forbids.

`config/manager-allowlist.toml` is the one statement of the allowlist, and
`tests/manager_invocations.py` is the one table of what a manager is sanctioned to run
and what it is not. This gate holds the first to the second rule by rule, under a model of
how Claude Code matches a command against its rules — a prefix rule on a word boundary, a
`*` spanning anything, and a compound command held part by part — that `just
probe-allowlist` re-takes against a real Claude Code and a real Codex. It also keeps the
properties the allowlist has always had to keep: no blanket grant, the run- and
approval-acting recipes withheld in every spelling, and the `Stop` hook wired exactly.
Whether the two rendered copies still equal the source is
`tests/manager_allowlist/test_manager_allowlist_sync.py`'s question.
"""

from __future__ import annotations

import json
import re
import shlex
import tomllib
from typing import Any

import pytest
from manager_invocations import (
    BOTH,
    INVOCATIONS,
    PLACEHOLDERS,
    Invocation,
    placeholders,
)

from orchestrator.root import REPO_ROOT

SOURCE = REPO_ROOT / "config" / "manager-allowlist.toml"
SETTINGS = REPO_ROOT / ".claude" / "settings.json"
#: The `Stop` hook's command: the engine's stop guard in its own docs page's
#: Claude Code wiring, named by path out of this checkout's locked install — through
#: `$CLAUDE_PROJECT_DIR`, which is what makes one registration work from every worktree of
#: this repository — with nothing of this repository's between the harness and the verb.
#: Its one declared `--source` sits below the verb, which consults it: the preserved-branch
#: view's own `--stop-verdict`, answering the question the verb does not ask itself.
STOP_HOOK = (
    '"$CLAUDE_PROJECT_DIR/.venv/bin/onepipeline" stop-guard '
    "--wake-budget 2100 --format claude-code "
    """--source '"$CLAUDE_PROJECT_DIR/scripts/unpublished.sh" --stop-verdict' """
    "--source-timeout 20"
)
#: The `SessionStart` hook, as registered before the allowlist moved to its own source:
#: the sync that now writes this file holds only the permission lists, so this is the
#: proof it left the hook as it found it.
SESSION_START_HOOKS = [
    {
        "matcher": "startup|resume",
        "hooks": [
            {
                "type": "command",
                "command": 'bash "$CLAUDE_PROJECT_DIR/scripts/session-setup.sh"',
                "timeout": 300,
            }
        ],
    }
]
#: Seconds the declared source has, which the hook's own bound must exceed, or the harness
#: kills the whole guard before the verb can report a source that ran past its bound.
SOURCE_TIMEOUT = 20
#: The recipes that act on runs or record an approval, each withheld so that every use is
#: approved on its own: `stop` ends a run, `shutdown` at `--host` acts on runs this session
#: does not own, and `approve-design` records the user's own approval.
WITHHELD = ("just stop", "just shutdown", "just approve-design")
#: A grant of every shell command, and the bare version-control and GitHub CLI wildcards.
BLANKET = {"*", "Bash", "Bash(*)", "Bash(:*)"}
BLANKET_PREFIXES = {"git", "gh", "just", "git -C", "gh api"}
#: Programs whose bare prefix grant runs anything: an interpreter, a shell, or a runner of
#: either.
INTERPRETERS = {
    "python",
    "python3",
    "node",
    "bash",
    "sh",
    "zsh",
    "perl",
    "ruby",
    "deno",
    "bun",
    "npx",
    "uvx",
    "env",
    "xargs",
    "eval",
    "exec",
}
INTERPRETER_PREFIXES = {("uv", "run"), ("uv", "run", "python"), ("uv", "run", "python", "-m")}

#: What the placeholders stand for when a row is matched here: this checkout, its runs root,
#: and a worker's worktree under the host's `onevcs` workspaces.
HOME = "/home/manager"
SAMPLE = {name: f"sample-{name}" for name in PLACEHOLDERS} | {
    "checkout": f"{HOME}/ai-orchestrator",
    "runs": f"{HOME}/ai-orchestrator/runs",
    "workspace": f"{HOME}/.onevcs/workspaces/github.com-example/runs/s-1/worktree",
    "envelope": f"{HOME}/ai-orchestrator/scratch/envelopes/sample-run.json",
    "log": f"{HOME}/ai-orchestrator/.logs/watch.log",
}
#: The commands Claude Code runs as reads without a rule, on a path in its working
#: directory or one a `Read` rule names. A `cd` into the working directory needs no rule
#: either, and one anywhere else is refused.
READERS = {"cat", "tail", "head", "grep"}


def _source() -> dict[str, object]:
    return tomllib.loads(SOURCE.read_text(encoding="utf-8"))


def _allowed() -> list[str]:
    allow = _source()["allowed_tools"]
    assert isinstance(allow, list)
    return [str(rule) for rule in allow]


def _bash(rule: str) -> str | None:
    """A `Bash(...)` rule's pattern, or `None` for any other tool's rule."""
    found = re.fullmatch(r"Bash\((.*)\)", rule)
    return None if found is None else found.group(1)


def _path_rule(tool: str, rule: str) -> str | None:
    found = re.fullmatch(rf"{tool}\((.*)\)", rule)
    return None if found is None else found.group(1)


# llmlint: ignore-block[contracts_have_one_source_or_a_drift_gate] These model the two tools'
# published matching only to decide which source rule covers a row; the providers themselves are
# reconciled by `just probe-allowlist`, which drives every row of the same table through a real
# Claude Code and Codex turn, and a paid turn is what no deterministic tier may spend.
def _prefix(pattern: str) -> str:
    """The literal words a pattern starts with, before any wildcard."""
    return pattern.removesuffix(":*").split("*", 1)[0].strip()


def _matches(pattern: str, part: str) -> bool:
    """Claude Code's rule for one simple command: a prefix on a word boundary, or a glob."""
    if pattern.endswith(":*"):
        prefix = pattern[:-2]
        return part == prefix or part.startswith(prefix + " ")
    if "*" in pattern:
        return re.fullmatch(".*".join(map(re.escape, pattern.split("*"))), part) is not None
    return part == pattern


def _codex_expressible(pattern: str) -> bool:
    """What a Codex `prefix_rule` can say: literal leading words, and any suffix."""
    return pattern.endswith(":*") and "*" not in pattern[:-2]


# llmlint: ignore-end[contracts_have_one_source_or_a_drift_gate]


def _parts(command: str) -> list[str]:
    return [part.strip() for part in re.split(r" && |; ", command) if part.strip()]


def _filled(invocation: Invocation) -> str:
    return invocation.command.format(**SAMPLE)


def _within(path: str, directory: str) -> bool:
    return path == directory or path.startswith(directory.rstrip("/") + "/")


def _readable(path: str, rules: list[str]) -> bool:
    """Whether Claude Code reads `path` without asking: in its working directory, or named."""
    if _within(path, SAMPLE["checkout"]) or not path.startswith("/"):
        return True
    return any(
        (glob := _path_rule("Read", rule)) is not None
        and re.fullmatch(_path_glob(glob.replace("~", HOME, 1)), path) is not None
        for rule in rules
    )


def _path_glob(glob: str) -> str:
    """A `Read`/`Edit` rule's glob as a pattern: `**` spans directories, `*` one name."""
    return "".join(
        ".*" if part == "**" else "[^/]*" if part == "*" else re.escape(part)
        for part in re.split(r"(\*\*|\*)", glob)
    )


def _claude_covers(part: str, rules: list[str]) -> bool:
    words = part.split()
    if words[:1] == ["cd"] and len(words) == 2 and _within(words[1], SAMPLE["checkout"]):
        return True
    if words and words[0] in READERS and "/" in words[-1] and ">" not in part:
        return _readable(words[-1], rules)
    return any(_matches(pattern, part) for rule in rules if (pattern := _bash(rule)) is not None)


def _covered(invocation: Invocation, rules: list[str], tool: str) -> bool:
    """Whether every part of the row is let through by a rule, on one tool."""
    if invocation.kind == "write":
        relative = _filled(invocation).removeprefix(SAMPLE["checkout"] + "/")
        return tool == "claude-code" and any(
            (glob := _path_rule("Edit", rule)) is not None
            and re.fullmatch(_path_glob(glob), relative) is not None
            for rule in rules
        )
    if tool == "claude-code":
        return all(_claude_covers(part, rules) for part in _parts(_filled(invocation)))
    codex = [p for rule in rules if (p := _bash(rule)) is not None and _codex_expressible(p)]
    return all(any(_matches(p, part) for p in codex) for part in _parts(_filled(invocation)))


def test_the_source_holds_an_allow_list_and_nothing_else() -> None:
    """No `denied_tools`: a deny rule binds a bypass-mode dispatch, and Codex's rendering is
    held to allow decisions only. No hooks, settings, or run configuration either."""
    assert set(_source()) == {"allowed_tools"}, (
        f"{SOURCE.name} declares {sorted(set(_source()) - {'allowed_tools'})}; the manager's "
        "allowlist is an allow list and nothing else"
    )


def test_the_table_is_well_formed() -> None:
    ids = [invocation.id for invocation in INVOCATIONS]
    assert len(ids) == len(set(ids)), (
        f"row ids repeat: {sorted(i for i in ids if ids.count(i) > 1)}"
    )
    for invocation in INVOCATIONS:
        unknown = placeholders(invocation) - PLACEHOLDERS
        assert not unknown, f"{invocation.id} names unknown placeholders {sorted(unknown)}"
        assert (invocation.tools == BOTH) == (not invocation.why_one_tool), (
            f"{invocation.id} is held on {invocation.tools} and gives the reason "
            f"{invocation.why_one_tool!r}; a row held on one tool says why, and only then"
        )


@pytest.mark.reads_docs
def test_every_row_quotes_the_agents_md_passage_that_decides_it() -> None:
    document = " ".join((REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8").split())
    missing = [
        (invocation.id, invocation.sanctioned_by)
        for invocation in INVOCATIONS
        if " ".join(invocation.sanctioned_by.split()) not in document
    ]
    assert not missing, f"rows quote AGENTS.md passages it no longer states: {missing}"


@pytest.mark.parametrize(
    "invocation",
    [invocation for invocation in INVOCATIONS if invocation.verdict == "allow"],
    ids=lambda invocation: invocation.id,
)
def test_every_allowed_row_is_let_through_by_a_source_rule(invocation: Invocation) -> None:
    rules = _allowed()
    for tool in invocation.tools:
        assert _covered(invocation, rules, tool), (
            f"{invocation.id} (`{_filled(invocation)}`) is sanctioned and held on {tool}, "
            f"and no rule in {SOURCE.name} lets it through there"
        )


def _exercises(rule: str, invocation: Invocation) -> bool:
    """Whether `rule` is what lets some part of one allowed row through on Claude Code."""
    if invocation.kind == "write":
        return rule.startswith("Edit(") and _covered(invocation, [rule], "claude-code")
    parts = _parts(_filled(invocation))
    if rule.startswith("Read("):
        return any(part.split()[0] in READERS and _claude_covers(part, [rule]) for part in parts)
    pattern = _bash(rule)
    return pattern is not None and any(_matches(pattern, part) for part in parts)


def test_every_grant_is_exercised_by_a_sanctioned_row() -> None:
    """The table is the whole account of the source: a grant no allowed row needs is one
    nothing here has decided the manager should hold."""
    allowed = [invocation for invocation in INVOCATIONS if invocation.verdict == "allow"]
    idle = [rule for rule in _allowed() if not any(_exercises(rule, row) for row in allowed)]
    assert not idle, (
        f"{SOURCE.name} grants {idle}, which no allowed row of tests/manager_invocations.py "
        "exercises; add the row that sanctions each, or drop the grant"
    )


@pytest.mark.parametrize(
    "invocation",
    [i for i in INVOCATIONS if i.verdict == "allow" and "codex" not in i.tools],
    ids=lambda invocation: invocation.id,
)
def test_a_row_held_on_claude_code_alone_is_one_codex_cannot_hold(invocation: Invocation) -> None:
    """A row marked unexpressible for Codex really is, rather than merely unwritten."""
    assert not _covered(invocation, _allowed(), "codex"), (
        f"{invocation.id} is marked unexpressible for Codex ({invocation.why_one_tool}), and a "
        "Codex-expressible rule already covers it; hold it on both tools"
    )


@pytest.mark.parametrize(
    "invocation",
    [invocation for invocation in INVOCATIONS if invocation.verdict != "allow"],
    ids=lambda invocation: invocation.id,
)
def test_no_rule_lets_a_refused_or_ungranted_row_through(invocation: Invocation) -> None:
    """Neither whole, nor by a rule that is a prefix of its deciding command."""
    rules = _allowed()
    for tool in invocation.tools:
        assert not _covered(invocation, rules, tool), (
            f"{invocation.id} (`{_filled(invocation)}`) is {invocation.verdict} and "
            f"{SOURCE.name} lets it through on {tool}"
        )
    deciding = [part for part in _parts(_filled(invocation)) if not part.startswith("cd ")]
    prefixes = [
        rule
        for rule in rules
        if (pattern := _bash(rule)) is not None
        for part in deciding
        if _prefix(pattern) and _matches(pattern, part)
    ]
    assert not prefixes, f"{invocation.id} is {invocation.verdict} and {prefixes} match it"


def test_the_withheld_recipes_have_no_grant_in_any_spelling() -> None:
    """A claude-code rule matches by prefix, so a bare `Bash(just stop)`, a
    `Bash(just stop:*)` and a `Bash(just:*)` would each grant the recipe."""
    granted = [
        rule
        for rule in _allowed()
        if (pattern := _bash(rule)) is not None
        for recipe in WITHHELD
        if recipe.startswith(_prefix(pattern)) or _prefix(pattern).startswith(recipe)
    ]
    assert not granted, f"the allowlist grants a recipe that acts on runs or approvals: {granted}"
    unpublished = [rule for rule in _allowed() if rule.startswith("Bash(just unpublished")]
    assert all("*" not in rule for rule in unpublished), (
        f"an unpublished read grant admits a suffix that can acknowledge a branch: {unpublished}"
    )
    assert not any("--acknowledge" in rule for rule in _allowed())


def test_the_allowlist_grants_no_blanket_or_interpreter_wide_rule() -> None:
    blanket = [
        rule
        for rule in _allowed()
        if rule in BLANKET
        or (
            (pattern := _bash(rule)) is not None
            and "*" in pattern
            and _prefix(pattern) in BLANKET_PREFIXES
        )
    ]
    assert not blanket, f"the allowlist grants a blanket wildcard: {blanket}"
    interpreters = [
        rule
        for rule in _allowed()
        if (pattern := _bash(rule)) is not None
        and "*" in pattern
        and (
            (words := tuple(_prefix(pattern).split()))[:1] in {(name,) for name in INTERPRETERS}
            and len(words) == 1
            or words in INTERPRETER_PREFIXES
        )
    ]
    assert not interpreters, f"the allowlist grants an interpreter-wide rule: {interpreters}"


def test_the_grant_changes_the_manager_ruled_are_in_force() -> None:
    """The blanket add, commit and `uv run` grants are gone, and so are the `git log`,
    `diff` and `show` grants whose `--output` writes a file; the clone grant stays, and no
    rule puts a wildcard between `git -C` and the verb that makes it a read."""
    allowed = _allowed()
    for gone in (
        "Bash(git add:*)",
        "Bash(git commit:*)",
        "Bash(uv run:*)",
        "Bash(git log:*)",
        "Bash(git diff:*)",
        "Bash(git show:*)",
    ):
        assert gone not in allowed, f"{gone} is back"
    assert "Bash(git clone:*)" in allowed
    spanning = [rule for rule in allowed if (p := _bash(rule)) and "*" in p.removesuffix(":*")]
    assert not spanning, (
        f"{spanning} put a wildcard before the words that bound them, and Claude Code's `*` "
        "spans words: `git -C * log*` passed `git -C <path> merge log-fix`"
    )


#: Every option `just unfinished` accepts, all of them reads. The recipe carries no wildcard
#: grant — the session-targeted read is approved per use — so this set is what any future
#: grant would have to be reviewed against, and a new option fails here until it is.
UNFINISHED_READ_ONLY_OPTIONS = {"-h", "--help", "--session", "--json", "--print-surface"}


def test_no_wildcard_grant_covers_just_unfinished() -> None:
    """No suffix is granted on this recipe, and its option set stays pinned."""
    from orchestrator import unfinished

    wildcards = [r for r in _allowed() if r.startswith("Bash(just unfinished") and "*" in r]
    assert not wildcards, (
        f"`just unfinished` carries a wildcard grant again: {wildcards}; the session-targeted "
        "read is approved per use"
    )
    options = {
        option for action in unfinished._parser()._actions for option in action.option_strings
    }
    assert options == UNFINISHED_READ_ONLY_OPTIONS, (
        f"`just unfinished` now accepts {sorted(options - UNFINISHED_READ_ONLY_OPTIONS)}; "
        "review each before any grant admits a suffix on this recipe"
    )


def _settings() -> dict[str, Any]:
    loaded = json.loads(SETTINGS.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def test_the_rendering_leaves_the_session_start_hook_and_an_empty_deny_list() -> None:
    settings = _settings()
    assert settings["hooks"]["SessionStart"] == SESSION_START_HOOKS, (
        "the SessionStart hook is not the session setup registered before the allowlist moved; "
        "the sync holds the permission lists alone"
    )
    assert settings["permissions"].get("deny") == [], (
        "`.claude/settings.json` carries deny rules, which bypass mode still honours"
    )


def test_the_stop_hook_asks_which_runs_nothing_is_watching() -> None:
    """AGENTS.md's watch rule names this hook as what enforces its first property.

    Registered beside the `SessionStart` hook already there, as exactly the engine's
    documented wiring and nothing more, and with a bound of its own: a killed hook is a
    stop that was never guarded, so the harness's bound is kept as the page keeps it.
    `tests/unwatched/test_unwatched_and_stop_hook_e2e.py` runs this command, and
    `tests/unfinished/test_unfinished_e2e.py` runs it over its declared source.
    """
    hooks = _settings()["hooks"]
    assert "SessionStart" in hooks, "the session-setup hook is gone, so this is not that file"
    registered = hooks.get("Stop")
    assert registered, "no Stop hook is registered, so nothing asks what is unwatched"
    commands = [
        command
        for matcher in registered
        for command in matcher["hooks"]
        if command["type"] == "command"
    ]
    assert [command["command"] for command in commands] == [STOP_HOOK], (
        f"the Stop hook is not the engine's stop guard wired as its page gives: {commands}"
    )
    assert (REPO_ROOT / ".venv" / "bin" / "onepipeline").is_file(), (
        "the Stop hook names this checkout's locked engine, and there is none installed"
    )
    bounds = [command.get("timeout") for command in commands]
    assert all(isinstance(bound, int) and bound > 0 for bound in bounds), (
        f"the Stop hook is registered without a bound of its own: {bounds}"
    )
    assert all(bound > SOURCE_TIMEOUT for bound in bounds), (
        f"the Stop hook's bound {bounds} does not outlast its source's {SOURCE_TIMEOUT}s"
    )


@pytest.mark.reads_docs
def test_the_watch_environment_and_stop_guard_share_the_budget() -> None:
    settings = json.loads((REPO_ROOT / ".claude/settings.json").read_text("utf-8"))
    budget = int(settings["env"]["ONEPIPELINE_WAKE_BUDGET"])
    assert budget == 2100
    commands = [
        shlex.split(hook["command"])
        for matcher in settings["hooks"]["Stop"]
        for hook in matcher["hooks"]
        if hook["type"] == "command" and "stop-guard" in hook["command"]
    ]
    assert len(commands) == 1
    arguments = commands[0]
    assert int(arguments[arguments.index("--wake-budget") + 1]) == budget
    # AGENTS.md restates the budget for the manager who reads it; every copy there is
    # held to the one the harness configures, so neither can move alone.
    document = " ".join((REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8").split())
    seconds = {int(n) for n in re.findall(r"(?:WAKE_BUDGET=|--wake-budget )(\d+)", document)}
    minutes = {int(n) for n in re.findall(r"(\d+)-minute", document)}
    assert seconds == {budget}, f"AGENTS.md states the wake budget as {seconds}s"
    assert minutes == {budget // 60}, f"AGENTS.md states the wake budget as {minutes} min"
