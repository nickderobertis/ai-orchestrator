"""The two rendered copies of the manager's allowlist equal its one source.

`config/manager-allowlist.toml` is the statement; `.claude/settings.json`'s
`permissions.allow` and `.codex/rules/oneharness.rules` are what `just sync-allowlist`
renders from it with the pinned `oneharness sync --exact`, committed so that a manager
session started directly in either tool reads them. This runs that same verb with
`--check`, which exits 1 on any difference — a source entry a rendering lacks, and an entry
added to a rendering by hand — and proves on scratch copies that both kinds are caught.

What the source may and may not reach is held here too: nothing discovers it, no role
config names it or carries a permission list of its own, and the Codex rendering holds
allow decisions and nothing else under `.codex/`.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

from manager_invocations import CODEX_EXACT, INVOCATIONS

#: This checkout, found from here rather than through `orchestrator`, which this project's
#: key leaves out.
REPO_ROOT = Path(__file__).resolve().parents[2]

SOURCE = REPO_ROOT / "config" / "manager-allowlist.toml"
#: The pinned CLI, installed by the lock beside the interpreter running this suite.
ONEHARNESS = Path(sys.executable).parent / "oneharness"
SETTINGS = Path(".claude") / "settings.json"
CODEX_RULES = Path(".codex") / "rules" / "oneharness.rules"
HARNESSES = "claude-code,codex"
#: The one statement a Codex rules file this repository commits may make.
ALLOW_RULE = re.compile(r'prefix_rule\(pattern=\[(?:"[^"]*"(?:, )?)+\], decision="allow"\)')


def _sync(cwd: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    """The pinned verb, over `cwd`, loading the source alone and nothing it could discover."""
    environment = {
        name: value for name, value in os.environ.items() if not name.startswith("ONEHARNESS_")
    }
    return subprocess.run(
        [
            str(ONEHARNESS),
            "sync",
            "--exact",
            "--config",
            str(SOURCE),
            "--harness",
            HARNESSES,
            "--cwd",
            str(cwd),
            "--format",
            "json",
            *extra,
        ],
        capture_output=True,
        text=True,
        check=False,
        env=environment,
    )


def _results(completed: subprocess.CompletedProcess[str]) -> dict[str, dict[str, Any]]:
    report = json.loads(completed.stdout)
    return {str(entry["harness"]): entry for entry in report["results"]}


def _copy(tmp_path: Path) -> Path:
    """The two rendered files, and nothing else, in a scratch project."""
    for relative in (SETTINGS, CODEX_RULES):
        (tmp_path / relative).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO_ROOT / relative, tmp_path / relative)
    return tmp_path


def test_both_committed_renderings_equal_the_source() -> None:
    checked = _sync(REPO_ROOT, "--check")
    assert checked.returncode == 0, (
        "a rendering of config/manager-allowlist.toml has drifted from it; edit the source "
        f"and run `just sync-allowlist`, never a rendered file:\n{checked.stdout}{checked.stderr}"
    )
    statuses = {harness: entry["status"] for harness, entry in _results(checked).items()}
    assert statuses == {"claude-code": "unchanged", "codex": "unchanged"}, statuses


def test_an_entry_added_to_the_claude_rendering_by_hand_is_caught(tmp_path: Path) -> None:
    project = _copy(tmp_path)
    settings = json.loads((project / SETTINGS).read_text(encoding="utf-8"))
    settings["permissions"]["allow"].append("Bash(just stop:*)")
    (project / SETTINGS).write_text(json.dumps(settings, indent=2), encoding="utf-8")

    checked = _sync(project, "--check")

    assert checked.returncode == 1, checked.stdout + checked.stderr
    assert "Bash(just stop:*)" in json.dumps(_results(checked)["claude-code"]), checked.stdout
    assert _results(checked)["codex"]["status"] == "unchanged"


def test_a_source_entry_missing_from_the_claude_rendering_is_caught(tmp_path: Path) -> None:
    project = _copy(tmp_path)
    settings = json.loads((project / SETTINGS).read_text(encoding="utf-8"))
    settings["permissions"]["allow"].remove("Bash(just watch:*)")
    (project / SETTINGS).write_text(json.dumps(settings, indent=2), encoding="utf-8")

    checked = _sync(project, "--check")

    assert checked.returncode == 1, checked.stdout + checked.stderr
    assert "Bash(just watch:*)" in json.dumps(_results(checked)["claude-code"]), checked.stdout


def test_an_entry_added_to_the_codex_rendering_by_hand_is_caught(tmp_path: Path) -> None:
    project = _copy(tmp_path)
    with (project / CODEX_RULES).open("a", encoding="utf-8") as rules:
        rules.write('\nprefix_rule(pattern=["just", "stop"], decision="allow")\n')

    checked = _sync(project, "--check")

    assert checked.returncode == 1, checked.stdout + checked.stderr
    assert _results(checked)["codex"]["status"] != "unchanged", checked.stdout
    assert _results(checked)["claude-code"]["status"] == "unchanged"


def test_the_codex_rendering_holds_allow_decisions_only() -> None:
    """No `forbidden` and no `prompt`: a dispatched Codex side in a linked worktree inherits
    this checkout's trust and loads the file, and under bypass only an allow is inert."""
    statements = [
        line
        for line in (REPO_ROOT / CODEX_RULES).read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert statements, "the Codex rendering holds no rule at all"
    other = [line for line in statements if not ALLOW_RULE.fullmatch(line)]
    assert not other, f"the Codex rendering holds statements other than allow rules: {other}"
    tracked = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files", ".codex"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert tracked in ([], [str(CODEX_RULES)]), (
        f"the tree carries {tracked} under .codex/; the rules file is the only one it may"
    )


def test_the_sync_reports_unmapped_each_exact_grant_and_path_rule_left_to_claude() -> None:
    """Each exact grant a row leaves to Claude Code, and every path rule, is one the sync
    reports it cannot render for Codex.

    An exact command is granted by the rule spelling it, which the sync must report rather
    than widen; a write or a read outside the working directory is granted by an `Edit` or
    `Read` rule, which governs no shell command Codex could hold.
    """
    unmapped = {
        str(entry["rule"])
        for entry in _results(_sync(REPO_ROOT, "--check"))["codex"]["unmapped_rules"]
    }
    source = _source_rules()
    assert unmapped <= set(source), f"the sync reports rules the source does not state: {unmapped}"
    held_on_claude_alone = [
        i for i in INVOCATIONS if i.verdict == "allow" and "codex" not in i.tools
    ]
    assert held_on_claude_alone, "the table names no row Codex cannot hold"
    for invocation in held_on_claude_alone:
        if invocation.why_one_tool == CODEX_EXACT:
            rule = f"Bash({invocation.command})"
            assert rule in source and rule in unmapped, (
                f"{invocation.id} is granted exactly by {rule}, which the sync does not report "
                f"unmapped for Codex: {sorted(unmapped)}"
            )
    for tool in ("Edit(", "Read("):
        path_rules = [rule for rule in source if rule.startswith(tool)]
        assert path_rules and set(path_rules) <= unmapped, (
            f"the sync maps a {tool[:-1]} rule for Codex: {path_rules}"
        )


def _source_rules() -> list[str]:
    rules = tomllib.loads(SOURCE.read_text(encoding="utf-8"))["allowed_tools"]
    assert isinstance(rules, list)
    return [str(rule) for rule in rules]


def test_nothing_discovers_the_source_and_no_role_config_carries_a_permission_list() -> None:
    """Loaded only by `--config`: a dispatched side never runs under the manager's policy."""
    environment = {
        name: value for name, value in os.environ.items() if not name.startswith("ONEHARNESS_")
    }
    discovered = subprocess.run(
        [str(ONEHARNESS), "config", "--format", "json"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=environment,
    )
    assert discovered.returncode == 0, discovered.stderr
    assert SOURCE.name not in json.dumps(json.loads(discovered.stdout).get("config_files")), (
        f"oneharness discovers {SOURCE.name} from this checkout"
    )
    for role in sorted(REPO_ROOT.glob("oneharness*.toml")):
        text = role.read_text(encoding="utf-8")
        assert SOURCE.name not in text, f"{role.name} names the manager's allowlist"
        assert not re.search(r"^\s*(allowed|denied)_tools\s*=", text, re.MULTILINE), (
            f"{role.name} carries a permission list; the role configs carry no manager policy"
        )
