"""`just sync-allowlist` and `just probe-allowlist`, driven end to end.

`just sync-allowlist` runs the real recipe and the pinned `oneharness` over a scratch copy of
the files it reads and writes: a rendering someone edited by hand, and a source that moved,
are both put back to exactly the source, while the hooks beside the allowlist are left as
they were.

`just probe-allowlist` spends real Claude Code and Codex turns when an operator runs it, and
a turn is the one thing a journey here may not spend, so each paid tool is a double first on
`PATH`, answering the probe's prompt the way the real one does or refusing the turn for quota
as an exhausted identity does: a `claude` that runs each step it is not refusing and lists the
ones it refused in `permission_denials`, and a `codex` that runs a step outside its sandbox
only when the synced `.codex/rules/oneharness.rules` allows every part of it. Everything else
is real: the recipe, its wrapper, the sync rendering the source into the scratch project, the
stubs, the scratch `justfile`, and the marker each stub writes only when it ran.

llmlint: ignore-file[e2e_not_mocked,tests_mirror_real_usage] The double stands exactly where
AGENTS.md puts every double in this suite — the paid model — and nothing above it is
doubled. What a live turn proves about Claude Code's own matching is `just
probe-allowlist`'s output over the finished tree, which the dispatch that added it quotes;
what this proves is that the probe reads the table, renders the source, observes each row by
its own marker, falls through an exhausted identity, and leaves nothing behind.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

#: This checkout, found from here rather than through `orchestrator`, which this project's
#: key leaves out.
REPO_ROOT = Path(__file__).resolve().parents[2]

#: What the recipe reads and writes, copied into a scratch checkout of its own.
SYNCED = (
    Path("justfile"),
    Path("config") / "manager-allowlist.toml",
    Path(".claude") / "settings.json",
    Path(".codex") / "rules" / "oneharness.rules",
)
SETTINGS = Path(".claude") / "settings.json"
CODEX_RULES = Path(".codex") / "rules" / "oneharness.rules"
PROBE = REPO_ROOT / "scripts" / "allowlist-probe.py"


def _probe_module() -> ModuleType:
    """The probe itself, so the identities a journey seeds are the ones it reads."""
    spec = importlib.util.spec_from_file_location("allowlist_probe", PROBE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


#: `(identity, variable)` for each Claude and each Codex identity, in the chain's order.
CLAUDE_IDENTITIES = _probe_module()._identities("claude-code")
CODEX_IDENTITIES = _probe_module()._identities("codex")

# llmlint: ignore-block[contracts_have_one_source_or_a_drift_gate] The doubles stand at the
# paid-model boundary, which is the one place this suite may double; their event shapes are held to
# the real tools by `just probe-allowlist`'s live run over the same probe, which refuses any event
# it cannot read, and a paid turn is what no deterministic tier may spend.
#: A `claude -p` double. It records its argv and the rendered settings it was handed, then
#: answers as the real tool does: for an exhausted identity, an error naming the limit; else
#: each numbered step is run through bash — a write step writes its file — unless it names a
#: word in `FAKE_CLAUDE_REFUSE`, which it lists in `permission_denials` instead of running,
#: or one in `FAKE_CLAUDE_SKIP`, which it passes over as a model skipping a step would. An
#: identity marked exhausted refuses nothing until its `FAKE_CLAUDE_EXHAUST_ON_CALL`th turn,
#: `FAKE_CLAUDE_GARBLE` answers with something that is not a result at all,
#: `FAKE_CLAUDE_ERROR` with an error result saying that text,
#: `FAKE_CLAUDE_PHANTOM_DENIAL` adds a denial for that command, which it never requested,
#: `FAKE_CLAUDE_BAD_BLOCK` with an assistant event whose content is not a block,
#: `FAKE_CLAUDE_BAD_CONTENT` with one whose content is not a list,
#: every turn closes with the `system` notice the real tool writes after its result,
#: `FAKE_CLAUDE_TRAILING` writes a turn event after that, `FAKE_CLAUDE_SECOND_RESULT` a
#: second result refusing nothing, which read would erase the first's refusals,
#: `FAKE_CLAUDE_DENIAL_TOOL` names
#: the tool each refusal is recorded against, and
#: `FAKE_CLAUDE_RESULT` replaces the `DONE` a turn ends its result with. It writes the
#: `stream-json` events the real tool does: an assistant `tool_use` per shell step, then the
#: result. A step naming a word in `FAKE_CLAUDE_SHADOW` it reports running without running
#: it through `PATH`, as the real shell's own `grep` function never reaches the recorder.
FAKE_CLAUDE = r"""#!/usr/bin/env python3
import json, os, re, subprocess, sys
argv = sys.argv[1:]
log = os.environ["FAKE_CLAUDE_LOG"]
settings = argv[argv.index("--settings") + 1]
with open(log, "a") as out:
    out.write(json.dumps({"argv": argv, "config": os.environ.get("CLAUDE_CONFIG_DIR"),
                          "settings": json.load(open(settings))}) + "\n")
config = os.environ.get("CLAUDE_CONFIG_DIR")
calls = sum(json.loads(line)["config"] == config for line in open(log))
exhausted = os.environ.get("FAKE_CLAUDE_EXHAUSTED") in (config, "every identity")
if exhausted and calls >= int(os.environ.get("FAKE_CLAUDE_EXHAUST_ON_CALL", "1")):
    limit = "You've hit your weekly limit"
    print(json.dumps({"type": "result", "is_error": True, "result": limit}))
    sys.exit(1)
if os.environ.get("FAKE_CLAUDE_ERROR"):
    print(json.dumps({"type": "result", "is_error": True,
                      "result": os.environ["FAKE_CLAUDE_ERROR"]}))
    sys.exit(1)
if os.environ.get("FAKE_CLAUDE_BAD_CONTENT"):
    print(json.dumps({"type": "assistant", "message": {"content": "not a list"}}))
    sys.exit(0)
if os.environ.get("FAKE_CLAUDE_BAD_BLOCK"):
    print(json.dumps({"type": "assistant", "message": {"content": ["not a block"]}}))
    sys.exit(0)
if os.environ.get("FAKE_CLAUDE_GARBLE"):
    print("Something went wrong, and this is not JSON")
    sys.exit(0)
prompt = argv[argv.index("-p") + 1]
refuse = [w for w in os.environ.get("FAKE_CLAUDE_REFUSE", "").split(",") if w and not exhausted]
skip = [w for w in os.environ.get("FAKE_CLAUDE_SKIP", "").split(",") if w]
shadow = [w for w in os.environ.get("FAKE_CLAUDE_SHADOW", "").split(",") if w]
denials = []
for step in re.findall(r"^\d+\. (.*)$", prompt, re.MULTILINE):
    if any(word in step for word in skip):
        continue
    # llmlint: ignore-block[changed_behavior_has_e2e] The double stands at the paid model and
    # applies no grant of its own: whether Claude Code's `Edit(scratch/envelopes/*.json)` lets
    # its `Write` tool through is its own matching, which only a real turn shows, and `just
    # probe-allowlist`'s `envelope-write` row is that turn.
    write = re.match(r"Use your file-write tool \(not the shell\) to write the file (\S+)", step)
    # llmlint: ignore-end[changed_behavior_has_e2e]
    command = write.group(1) if write else step
    use = ({"type": "tool_use", "name": "Write", "input": {"file_path": command}} if write
           else {"type": "tool_use", "name": "Bash", "input": {"command": command}})
    print(json.dumps({"type": "assistant", "message": {"content": [use]}}))
    if any(word in command for word in refuse):
        key = "file_path" if write else "command"
        tool = os.environ.get("FAKE_CLAUDE_DENIAL_TOOL") or ("Write" if write else "Bash")
        denials.append({"tool_name": tool, "tool_input": {key: command}})
    elif write:
        # Claude Code's `Write` creates the directories a path needs, as the envelope's does.
        os.makedirs(os.path.dirname(command), exist_ok=True)
        open(command, "w").write("{}")
    elif any(word in command for word in shadow):
        pass
    else:
        subprocess.run(["bash", "-c", command], check=False, capture_output=True)
if os.environ.get("FAKE_CLAUDE_PHANTOM_DENIAL"):
    denials.append({"tool_name": "Bash",
                    "tool_input": {"command": os.environ["FAKE_CLAUDE_PHANTOM_DENIAL"]}})
result = os.environ.get("FAKE_CLAUDE_RESULT", "DONE")
print(json.dumps({"type": "result", "is_error": False, "result": result,
                  "permission_denials": denials}))
print(json.dumps({"type": "system", "subtype": "task_summary"}))
if os.environ.get("FAKE_CLAUDE_TRAILING"):
    print(json.dumps({"type": "assistant", "message": {"content": []}}))
if os.environ.get("FAKE_CLAUDE_SECOND_RESULT"):
    print(json.dumps({"type": "result", "is_error": False, "result": result,
                      "permission_denials": []}))
"""

#: A `codex exec` double. It holds the scratch `CODEX_HOME` to what the probe promises — a
#: copied login, the login shell off, the project trusted — refuses the turn for a login
#: marked exhausted, and otherwise runs a step only when every part of it starts with a
#: pattern the project's synced rules file allows; any other step it leaves unrun, as a
#: read-only sandbox leaves its marker unwritten. Either way it reports the step as the
#: `command_execution` event `codex exec --json` writes, and ends on the agent message
#: `DONE` — unless `FAKE_CODEX_UNFINISHED` is set, when it stops without one.
#: `FAKE_CODEX_RUN_ALL` runs every step, as a Codex whose rules let everything through, and
#: `FAKE_CODEX_WRAPPER` names the program it reports each step as run under. It closes the
#: stream with `turn.completed` unless `FAKE_CODEX_NO_COMPLETION` is set, writes an item
#: after it under `FAKE_CODEX_TRAILING_ITEM` and a second one under
#: `FAKE_CODEX_SECOND_COMPLETION`, and never reports a step naming a word in
#: `FAKE_CODEX_SKIP`, as a model declining to run it would. Under
#: `FAKE_CODEX_EXHAUST_AFTER_RUN` an exhausted login runs every step before it refuses.
#: `FAKE_CODEX_GARBLE` names one malformed line it writes instead of a turn, and
#: `FAKE_CODEX_FAIL_OUTPUT` reports one command whose output is that text, then exits 1.
#: `FAKE_CODEX_DONE_STARTED` reports its `DONE` only as a started item, never a finished one.
#: `FAKE_CODEX_PLAIN_LIMIT` names a limit in a stdout line that is not an event, then exits 1.
#: `FAKE_CODEX_LIMIT_GARBLED` has an exhausted login name its limit on stderr beside a stdout
#: line that is not an event.
FAKE_CODEX = r"""#!/usr/bin/env python3
import json, os, re, shlex, subprocess, sys, tomllib
home = os.environ["CODEX_HOME"]
config = tomllib.load(open(os.path.join(home, "config.toml"), "rb"))
assert config["allow_login_shell"] is False, config
assert config["projects"][os.getcwd()]["trust_level"] == "trusted", config
assert sys.argv[1:5] == ["exec", "--json", "--skip-git-repo-check", "--sandbox"], sys.argv
exhausted = "exhausted" in open(os.path.join(home, "auth.json")).read()
if exhausted and not os.environ.get("FAKE_CODEX_EXHAUST_AFTER_RUN"):
    if os.environ.get("FAKE_CODEX_LIMIT_GARBLED"):
        print("codex says hello")
        print("You've hit your usage limit.", file=sys.stderr)
        sys.exit(1)
    print(json.dumps({"type": "error", "message": "You've hit your usage limit."}))
    sys.exit(1)
rules = open(os.path.join(".codex", "rules", "oneharness.rules")).read()
patterns = [json.loads("[" + found + "]") for found in re.findall(r"pattern=\[(.*?)\]", rules)]
def allowed(part):
    words = shlex.split(part)
    return any(words[: len(pattern)] == pattern for pattern in patterns)
def event(item, kind="item.completed"):
    print(json.dumps({"type": kind, "item": item}))
run_all = bool(os.environ.get("FAKE_CODEX_RUN_ALL")) or exhausted
wrapper = os.environ.get("FAKE_CODEX_WRAPPER", "/bin/bash")
garble = os.environ.get("FAKE_CODEX_GARBLE")
if garble:
    print({"not-json": "codex says hello",
           "no-type": json.dumps({"item": {}}),
           "no-command": json.dumps({"type": "item.completed",
                                     "item": {"type": "command_execution"}}),
           "item-on-turn-event": json.dumps({"type": "turn.completed",
                                             "item": {"type": "agent_message", "text": "DONE"}}),
           "turn-failed": json.dumps({"type": "turn.failed", "error": {"message": "boom"}}),
           "unparseable": json.dumps({"type": "item.completed",
                                      "item": {"type": "command_execution",
                                               "command": "/bin/bash -c 'unterminated"}}),
           }[garble])
    sys.exit(0)
if os.environ.get("FAKE_CODEX_PLAIN_LIMIT"):
    print("You've hit your usage limit.")
    sys.exit(1)
if os.environ.get("FAKE_CODEX_FAIL_OUTPUT"):
    event({"type": "command_execution", "command": "/bin/bash -c 'just status'",
           "aggregated_output": os.environ["FAKE_CODEX_FAIL_OUTPUT"]})
    sys.exit(1)
skip = [w for w in os.environ.get("FAKE_CODEX_SKIP", "").split(",") if w]
for step in re.findall(r"^\d+\. (.*)$", sys.argv[-1], re.MULTILINE):
    if any(word in step for word in skip):
        continue
    if run_all or all(allowed(part) for part in re.split(r" && |; ", step)):
        subprocess.run(["bash", "-c", step], check=False, capture_output=True)
    event({"type": "command_execution", "command": wrapper + " -c " + shlex.quote(step)})
if exhausted:
    print(json.dumps({"type": "turn.failed",
                      "error": {"message": "You've hit your usage limit."}}))
    sys.exit(1)
if not os.environ.get("FAKE_CODEX_UNFINISHED"):
    event({"type": "agent_message", "text": "DONE"},
          "item.started" if os.environ.get("FAKE_CODEX_DONE_STARTED") else "item.completed")
    if not os.environ.get("FAKE_CODEX_NO_COMPLETION"):
        print(json.dumps({"type": "turn.completed", "usage": {}}))
        if os.environ.get("FAKE_CODEX_TRAILING_ITEM"):
            event({"type": "agent_message", "text": "one more thing"})
        if os.environ.get("FAKE_CODEX_SECOND_COMPLETION"):
            print(json.dumps({"type": "turn.completed", "usage": {}}))
"""


# llmlint: ignore-end[contracts_have_one_source_or_a_drift_gate]


def _environment() -> dict[str, str]:
    return {name: value for name, value in os.environ.items() if not name.startswith("ONEHARNESS_")}


def _scratch_checkout(destination: Path) -> Path:
    """The recipe's inputs and outputs, and this checkout's install beside them."""
    for relative in SYNCED:
        (destination / relative).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO_ROOT / relative, destination / relative)
    for name in ("pyproject.toml", "uv.lock"):
        shutil.copy(REPO_ROOT / name, destination / name)
    (destination / ".venv").symlink_to(REPO_ROOT / ".venv", target_is_directory=True)
    return destination


def _recipe(checkout: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "just",
            "--justfile",
            str(checkout / "justfile"),
            "--working-directory",
            str(checkout),
            *arguments,
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
        env={**_environment(), "UV_NO_SYNC": "1"},
    )


def test_sync_allowlist_puts_both_renderings_back_to_exactly_the_source(tmp_path: Path) -> None:
    checkout = _scratch_checkout(tmp_path)
    before = json.loads((checkout / SETTINGS).read_text(encoding="utf-8"))
    edited = json.loads((checkout / SETTINGS).read_text(encoding="utf-8"))
    edited["permissions"]["allow"].append("Bash(just stop:*)")
    (checkout / SETTINGS).write_text(json.dumps(edited), encoding="utf-8")
    (checkout / CODEX_RULES).write_text("# emptied by hand\n", encoding="utf-8")
    source = checkout / "config" / "manager-allowlist.toml"
    source.write_text(
        source.read_text(encoding="utf-8").replace(
            '"Bash(just sweep:*)",', '"Bash(just sweep:*)",\n    "Bash(just probe-journey:*)",'
        ),
        encoding="utf-8",
    )

    synced = _recipe(checkout, "sync-allowlist")

    assert synced.returncode == 0, synced.stdout + synced.stderr
    after = json.loads((checkout / SETTINGS).read_text(encoding="utf-8"))
    assert "Bash(just stop:*)" not in after["permissions"]["allow"]
    assert "Bash(just probe-journey:*)" in after["permissions"]["allow"]
    assert after["hooks"] == before["hooks"], "the sync moved a hook it was never handed"
    assert after["permissions"]["deny"] == []
    rules = (checkout / CODEX_RULES).read_text(encoding="utf-8")
    assert 'prefix_rule(pattern=["just", "probe-journey"], decision="allow")' in rules
    assert "unmapped" in synced.stdout + synced.stderr, (
        "the sync no longer reports the rules Codex cannot express"
    )
    again = _recipe(checkout, "sync-allowlist")
    assert again.returncode == 0 and "updated" not in again.stdout, again.stdout


#: The rows the Claude Code journeys drive: each allowed kind, a write, and two refusals.
CLAUDE_ROWS = ("status-cut", "cd-and-import", "envelope-write", "stop", "launch-edit")
#: The rows the Codex journey drives: an allowed row, a refusal, and two it cannot hold.
CODEX_ROWS = ("status-cut", "stop", "unfinished", "cd-and-status")


def _fake_bin(tmp_path: Path) -> Path:
    fake = tmp_path / "fake-bin"
    fake.mkdir()
    for name, body in (("claude", FAKE_CLAUDE), ("codex", FAKE_CODEX)):
        (fake / name).write_text(body, encoding="utf-8")
        (fake / name).chmod(0o755)
    return fake


def _path_without(linked: Path, path: str, program: str) -> str:
    """`path` with no `program` on it: each directory holding one is replaced by links, in
    one directory, to everything else it holds, so every other program resolves as before."""
    linked.mkdir()
    kept = []
    for entry in path.split(os.pathsep):
        directory = Path(entry)
        if not (directory / program).exists():
            kept.append(entry)
            continue
        if str(linked) not in kept:
            kept.append(str(linked))
        for tool in directory.iterdir():
            if tool.name != program and not (linked / tool.name).exists():
                (linked / tool.name).symlink_to(tool)
    return os.pathsep.join(kept)


def _run_probe(
    tmp_path: Path,
    tool: str | None,
    rows: tuple[str, ...],
    environment: dict[str, str],
    *extra: str,
    unstartable: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run the real recipe with the doubles first on `PATH`; `unstartable` names a double
    whose interpreter does not exist, with every other directory holding that program left
    off `PATH`, so starting it fails as a missing program's would and no real one answers."""
    scratch = tmp_path / "node-scratch"
    scratch.mkdir()
    arguments = [argument for row in rows for argument in ("--row", row)]
    fake_bin = _fake_bin(tmp_path)
    path = environment.get("PATH", os.environ["PATH"])
    if unstartable is not None:
        (fake_bin / unstartable).write_text("#!/nonexistent/interpreter\n", encoding="utf-8")
        path = _path_without(tmp_path / "path-without", path, unstartable)
    probed = subprocess.run(
        ["just", "probe-allowlist", *(("--tool", tool) if tool else ()), *arguments, *extra],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=600,
        env={
            **_environment(),
            **environment,
            "PATH": f"{fake_bin}{os.pathsep}{path}",
            "ONEPIPELINE_NODE_SCRATCH_DIR": str(scratch),
        },
    )
    assert list(scratch.iterdir()) == [], f"the probe left {list(scratch.iterdir())} behind"
    return probed


def _probe(
    tmp_path: Path,
    refuse: str,
    exhausted: bool,
    skip: str = "",
    *extra: str,
    rows: tuple[str, ...] = CLAUDE_ROWS,
    **fake: str,
) -> tuple[subprocess.CompletedProcess[str], list[dict[str, Any]]]:
    log = tmp_path / "claude-calls.jsonl"
    log.touch()
    identities = {
        variable: str(tmp_path / variable.lower())
        for _, variable in CLAUDE_IDENTITIES
        if variable is not None
    }
    for directory in identities.values():
        Path(directory).mkdir()
    first = identities[str(CLAUDE_IDENTITIES[0][1])]
    probed = _run_probe(
        tmp_path,
        "claude-code",
        rows,
        {
            **identities,
            "FAKE_CLAUDE_LOG": str(log),
            "FAKE_CLAUDE_REFUSE": refuse,
            "FAKE_CLAUDE_SKIP": skip,
            "FAKE_CLAUDE_EXHAUSTED": first if exhausted else "",
            **fake,
        },
        *extra,
    )
    calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    return probed, calls


def _line(output: str, row: str) -> str:
    found = [line for line in output.splitlines() if f" {row} " in line]
    assert len(found) == 1, output
    return found[0]


def test_the_probe_falls_through_an_exhausted_identity_and_reads_each_row(tmp_path: Path) -> None:
    probed, calls = _probe(tmp_path, refuse="just stop,launch.json", exhausted=True)

    assert probed.returncode == 0, probed.stdout + probed.stderr
    assert f"claude-code (as {CLAUDE_IDENTITIES[1][0]})" in probed.stdout
    assert [call["config"] for call in calls][:1] == [
        str(tmp_path / str(CLAUDE_IDENTITIES[0][1]).lower())
    ]
    first = calls[-1]
    assert "--permission-mode" in first["argv"] and "default" in first["argv"]
    assert "Bash(just status:*)" in first["settings"]["permissions"]["allow"]
    for row in ("status-cut", "cd-and-import", "envelope-write"):
        assert "let through  ok" in _line(probed.stdout, row)
    for row in ("stop", "launch-edit"):
        assert "refused      ok" in _line(probed.stdout, row)
    assert (
        "rows of tests/manager_invocations.py against config/manager-allowlist.toml: "
        "every row as the table says"
    ) in probed.stdout


def test_the_probe_fails_on_a_refused_row_a_tool_let_through(tmp_path: Path) -> None:
    probed, _ = _probe(tmp_path, refuse="launch.json", exhausted=False)

    assert probed.returncode == 1, probed.stdout + probed.stderr
    assert "let through  MISMATCH" in _line(probed.stdout, "stop")
    assert "1 row(s) disagree with the table" in probed.stdout


def test_a_step_the_tool_never_attempted_fails_an_allowed_row_and_is_named(
    tmp_path: Path,
) -> None:
    """Neither a marker nor a denial: reported `not attempted`, never read as refused — and
    under Claude Code, which records every refusal, never as a refused row's proof either."""
    probed, _ = _probe(tmp_path, refuse="launch.json", exhausted=False, skip="just status,stop")

    assert probed.returncode == 1, probed.stdout + probed.stderr
    assert "not attempted MISMATCH" in _line(probed.stdout, "status-cut")
    assert "not attempted MISMATCH" in _line(probed.stdout, "stop")
    assert "2 row(s) disagree with the table" in probed.stdout


def _codex_logins(tmp_path: Path) -> dict[str, str]:
    """A login per Codex identity of the chain, the first marked exhausted."""
    logins = {}
    for number, (_, variable) in enumerate(CODEX_IDENTITIES):
        login = tmp_path / f"codex-login-{number}"
        login.mkdir()
        (login / "auth.json").write_text(
            json.dumps({"exhausted": True} if number == 0 else {}), encoding="utf-8"
        )
        logins[variable or "CODEX_HOME"] = str(login)
    return logins


def test_the_probe_drives_codex_through_the_synced_rules_under_the_next_login(
    tmp_path: Path,
) -> None:
    """The first login is exhausted, so the chain's next Codex identity takes the turn."""
    logins = _codex_logins(tmp_path)

    probed = _run_probe(tmp_path, "codex", CODEX_ROWS, logins)

    assert probed.returncode == 0, probed.stdout + probed.stderr
    assert f"codex (as {CODEX_IDENTITIES[1][0]})" in probed.stdout
    assert "let through  ok" in _line(probed.stdout, "status-cut")
    assert "not through  ok" in _line(probed.stdout, "stop")
    for row in ("unfinished", "cd-and-status"):
        assert "unexpressible:" in _line(probed.stdout, row)
    for copied in logins.values():
        assert sorted(path.name for path in Path(copied).iterdir()) == ["auth.json"], (
            "the probe wrote into a login it was only to copy"
        )


@pytest.mark.parametrize(
    "ending",
    ["FAKE_CODEX_UNFINISHED", "FAKE_CODEX_NO_COMPLETION", "FAKE_CODEX_DONE_STARTED"],
    ids=["no-done", "no-completion", "done-only-started"],
)
def test_a_codex_turn_that_ends_without_finishing_is_refused_rather_than_read(
    tmp_path: Path, ending: str
) -> None:
    """Neither a turn that never says DONE nor one whose stream never completes is read."""
    probed = _run_probe(tmp_path, "codex", CODEX_ROWS, {**_codex_logins(tmp_path), ending: "1"})

    assert probed.returncode == 1, probed.stdout + probed.stderr
    assert "without DONE" in probed.stderr
    assert "let through" not in probed.stdout


def test_an_identity_exhausted_after_a_batch_ran_leaves_nothing_the_next_one_is_read_by(
    tmp_path: Path,
) -> None:
    """The first identity lets its first batch through — the refused `stop` among it — and
    is exhausted at its second turn; the next identity refuses `stop`, and only its own
    turns decide what the probe reports."""
    probed, calls = _probe(
        tmp_path,
        "just stop,launch.json",
        True,
        "",
        "--batch",
        "4",
        FAKE_CLAUDE_EXHAUST_ON_CALL="2",
    )

    assert probed.returncode == 0, probed.stdout + probed.stderr
    assert f"claude-code (as {CLAUDE_IDENTITIES[1][0]})" in probed.stdout
    assert [call["config"] for call in calls].count(calls[0]["config"]) == 2
    assert "refused      ok" in _line(probed.stdout, "stop")


def test_a_claude_error_that_names_no_limit_stops_the_probe_on_that_identity(
    tmp_path: Path,
) -> None:
    """An error that merely contains a limit word inside another ("generate") is the
    turn's own failure, quoted, and never a spent identity to fall through."""
    said = "Could not generate a response: the model is overloaded"
    probed, calls = _probe(tmp_path, "", False, FAKE_CLAUDE_ERROR=said)

    assert probed.returncode == 1, probed.stdout + probed.stderr
    assert said in probed.stderr
    assert {call["config"] for call in calls} == {calls[0]["config"]}
    assert "let through" not in probed.stdout


def test_a_claude_denial_for_a_command_the_turn_never_requested_is_refused_rather_than_read(
    tmp_path: Path,
) -> None:
    """A denial proves a row refused only when the turn asked to run it."""
    probed, _ = _probe(tmp_path, "", False, FAKE_CLAUDE_PHANTOM_DENIAL="just stop run-elsewhere")

    assert probed.returncode == 1, probed.stdout + probed.stderr
    assert "denials for what the turn never requested: ['just stop run-elsewhere']" in (
        probed.stderr
    )
    assert "let through" not in probed.stdout


def test_a_claude_answer_that_is_not_a_result_is_refused_rather_than_read(
    tmp_path: Path,
) -> None:
    probed, _ = _probe(tmp_path, "", False, FAKE_CLAUDE_GARBLE="1")

    assert probed.returncode == 1, probed.stdout + probed.stderr
    assert "did not finish the turn: not JSON" in probed.stderr
    assert "let through" not in probed.stdout


@pytest.mark.parametrize(
    "said", ["All steps attempted.\n\nDONE", "All 12 steps attempted. DONE"], ids=["line", "word"]
)
def test_a_claude_turn_that_says_more_before_its_done_is_read(tmp_path: Path, said: str) -> None:
    """A real model often reports what it attempted before the word it was asked to end on."""
    probed, _ = _probe(tmp_path, "just stop,launch.json", False, FAKE_CLAUDE_RESULT=said)

    assert probed.returncode == 0, probed.stdout + probed.stderr
    assert "refused      ok" in _line(probed.stdout, "stop")


def test_a_claude_turn_that_never_ends_on_done_is_refused_rather_than_read(
    tmp_path: Path,
) -> None:
    probed, _ = _probe(tmp_path, "", False, FAKE_CLAUDE_RESULT="DONE with half of them, stopping")

    assert probed.returncode == 1, probed.stdout + probed.stderr
    assert "did not finish the turn" in probed.stderr
    assert "let through" not in probed.stdout


def test_a_grep_claude_s_shell_runs_past_the_recorder_is_read_off_the_turn_s_own_stream(
    tmp_path: Path,
) -> None:
    """Claude Code's shell runs `grep` as its own function, so no marker is ever written:
    the turn's record of running the command, with no denial for it, is what lets it through."""
    probed, _ = _probe(
        tmp_path,
        "just stop",
        False,
        rows=("log-grep", "stop"),
        FAKE_CLAUDE_SHADOW="grep",
    )

    assert probed.returncode == 0, probed.stdout + probed.stderr
    assert "let through  ok" in _line(probed.stdout, "log-grep")
    assert "refused      ok" in _line(probed.stdout, "stop")


def test_a_shadowed_grep_the_turn_was_refused_is_not_read_as_let_through(tmp_path: Path) -> None:
    probed, _ = _probe(tmp_path, "grep", False, rows=("log-grep",), FAKE_CLAUDE_SHADOW="grep")

    assert probed.returncode == 1, probed.stdout + probed.stderr
    assert "refused      MISMATCH" in _line(probed.stdout, "log-grep")


def test_a_command_claude_ran_without_refusing_and_without_effect_is_named_so(
    tmp_path: Path,
) -> None:
    """Asked for, absent from `permission_denials`, and no marker: a rule let it through and
    something else stopped it, which proves neither verdict, so each row is a mismatch."""
    probed, _ = _probe(
        tmp_path,
        "",
        False,
        rows=("status-cut", "stop"),
        FAKE_CLAUDE_SHADOW="just status,just stop",
    )

    assert probed.returncode == 1, probed.stdout + probed.stderr
    assert "no effect    MISMATCH" in _line(probed.stdout, "status-cut")
    assert "no effect    MISMATCH" in _line(probed.stdout, "stop")


def test_a_chain_with_no_claude_identity_left_is_refused_naming_each(tmp_path: Path) -> None:
    probed, _ = _probe(tmp_path, "", False, FAKE_CLAUDE_EXHAUSTED="every identity")

    assert probed.returncode == 1, probed.stdout + probed.stderr
    assert "no claude-code identity could take a turn" in probed.stderr
    for identity, _variable in CLAUDE_IDENTITIES:
        assert f"{identity}: " in probed.stderr
    assert "let through" not in probed.stdout


def test_a_provider_that_cannot_be_started_is_named_with_what_to_do(tmp_path: Path) -> None:
    probed = _run_probe(tmp_path, "codex", CODEX_ROWS, _codex_logins(tmp_path), unstartable="codex")

    assert probed.returncode == 1, probed.stdout + probed.stderr
    assert "codex could not be started" in probed.stderr
    assert "install it or fix PATH, then retry" in probed.stderr
    assert "let through" not in probed.stdout


def test_a_claude_event_carrying_a_content_that_is_no_block_is_refused_rather_than_read(
    tmp_path: Path,
) -> None:
    probed, _ = _probe(tmp_path, "", False, FAKE_CLAUDE_BAD_BLOCK="1")

    assert probed.returncode == 1, probed.stdout + probed.stderr
    assert "not a claude-code stream: a content block that is not an object" in probed.stderr
    assert "let through" not in probed.stdout


def test_an_identity_exhausted_after_its_turn_ended_the_sleep_stops_the_probe(
    tmp_path: Path,
) -> None:
    """The first identity lets the kill row through and is exhausted at its next turn: the
    sleep is gone, so the next identity's kill row could not be read and the probe stops."""
    probed, _ = _probe(
        tmp_path,
        "core.hooksPath,launch.json",
        True,
        "",
        "--batch",
        "2",
        rows=("hooks-bypass", "kill", "launch-edit"),
        FAKE_CLAUDE_EXHAUST_ON_CALL="2",
    )

    assert probed.returncode == 1, probed.stdout + probed.stderr
    assert "ended the probe's own sleep" in probed.stderr
    assert "let through" not in probed.stdout


def test_a_codex_turn_that_never_runs_a_refused_row_holds_it_off_but_names_it_unattempted(
    tmp_path: Path,
) -> None:
    """Codex keeps no record of a command it declined, so a refused row it never ran is
    consistent with the table — reported `not attempted`, never as a refusal it observed —
    while an allowed row it never ran is a mismatch."""
    probed = _run_probe(
        tmp_path,
        "codex",
        CODEX_ROWS,
        {**_codex_logins(tmp_path), "FAKE_CODEX_SKIP": "just stop,just status"},
    )

    assert probed.returncode == 1, probed.stdout + probed.stderr
    assert "not attempted ok" in _line(probed.stdout, "stop")
    assert "not attempted MISMATCH" in _line(probed.stdout, "status-cut")


def test_a_codex_item_after_its_turn_completed_is_refused_rather_than_read(
    tmp_path: Path,
) -> None:
    probed = _run_probe(
        tmp_path, "codex", CODEX_ROWS, {**_codex_logins(tmp_path), "FAKE_CODEX_TRAILING_ITEM": "1"}
    )

    assert probed.returncode == 1, probed.stdout + probed.stderr
    assert "an item after `turn.completed`" in probed.stderr


def test_a_codex_turn_completed_twice_is_refused_rather_than_read(tmp_path: Path) -> None:
    probed = _run_probe(
        tmp_path,
        "codex",
        CODEX_ROWS,
        {**_codex_logins(tmp_path), "FAKE_CODEX_SECOND_COMPLETION": "1"},
    )

    assert probed.returncode == 1, probed.stdout + probed.stderr
    assert "a second `turn.completed`" in probed.stderr
    assert "let through" not in probed.stdout


def test_a_codex_failure_whose_command_output_names_a_limit_is_not_read_as_a_spent_login(
    tmp_path: Path,
) -> None:
    """A row's own output is the command's to say, so a limit named there leaves the login
    that ran it answering for the failed turn."""
    logins = _codex_logins(tmp_path)
    (Path(logins[CODEX_IDENTITIES[0][1] or "CODEX_HOME"]) / "auth.json").write_text(
        "{}", encoding="utf-8"
    )

    probed = _run_probe(
        tmp_path,
        "codex",
        CODEX_ROWS,
        {**logins, "FAKE_CODEX_FAIL_OUTPUT": "upstream says: rate limit exceeded"},
    )

    assert probed.returncode == 1, probed.stdout + probed.stderr
    assert f"codex as {CODEX_IDENTITIES[0][0]} exited 1 without DONE" in probed.stderr
    assert "let through" not in probed.stdout


def test_a_codex_limit_named_outside_its_event_stream_is_not_read_as_a_spent_login(
    tmp_path: Path,
) -> None:
    """A stdout line that is not an event is a stream the probe cannot read, whatever it says."""
    logins = _codex_logins(tmp_path)
    (Path(logins[CODEX_IDENTITIES[0][1] or "CODEX_HOME"]) / "auth.json").write_text(
        "{}", encoding="utf-8"
    )

    probed = _run_probe(tmp_path, "codex", CODEX_ROWS, {**logins, "FAKE_CODEX_PLAIN_LIMIT": "1"})

    assert probed.returncode == 1, probed.stdout + probed.stderr
    assert "a line that is not an event" in probed.stderr
    assert "no codex identity could take a turn" not in probed.stderr


def test_a_spent_codex_login_s_unreadable_stream_is_refused_not_passed_to_the_next(
    tmp_path: Path,
) -> None:
    """A limit its stderr names moves nothing on while its stdout is not a stream."""
    logins = _codex_logins(tmp_path)
    (Path(logins[CODEX_IDENTITIES[0][1] or "CODEX_HOME"]) / "auth.json").write_text(
        "exhausted", encoding="utf-8"
    )

    probed = _run_probe(tmp_path, "codex", CODEX_ROWS, {**logins, "FAKE_CODEX_LIMIT_GARBLED": "1"})

    assert probed.returncode == 1, probed.stdout + probed.stderr
    assert "a line that is not an event" in probed.stderr
    assert f"codex (as {CODEX_IDENTITIES[1][0]})" not in probed.stdout


@pytest.mark.parametrize(
    ("garble", "said"),
    [
        ("not-json", "a line that is not an event"),
        ("no-type", "an event with no type"),
        ("no-command", "an execution with no command"),
        ("unparseable", "a command no shell could parse"),
        ("item-on-turn-event", "an item on a `turn.completed` event"),
        ("turn-failed", "codex reported its turn failed"),
    ],
)
def test_a_codex_stream_the_probe_cannot_read_is_refused_with_a_next_step(
    tmp_path: Path, garble: str, said: str
) -> None:
    probed = _run_probe(
        tmp_path, "codex", CODEX_ROWS, {**_codex_logins(tmp_path), "FAKE_CODEX_GARBLE": garble}
    )

    assert probed.returncode == 1, probed.stdout + probed.stderr
    assert said in probed.stderr
    assert "retry" in probed.stderr or "re-run" in probed.stderr, probed.stderr
    assert "let through" not in probed.stdout


def test_a_scratch_directory_no_row_could_name_verbatim_is_refused_before_any_turn(
    tmp_path: Path,
) -> None:
    """Every row reaches a shell as written, so a scratch path needing quoting is refused."""
    spaced = tmp_path / "with space"
    spaced.mkdir()
    log = tmp_path / "claude-calls.jsonl"
    log.touch()

    probed = subprocess.run(
        ["just", "probe-allowlist", "--tool", "claude-code", "--row", "stop"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=600,
        env={
            **_environment(),
            "PATH": f"{_fake_bin(tmp_path)}{os.pathsep}{os.environ['PATH']}",
            "FAKE_CLAUDE_LOG": str(log),
            "ONEPIPELINE_NODE_SCRATCH_DIR": str(spaced),
        },
    )

    assert probed.returncode == 1, probed.stdout + probed.stderr
    assert "is not an existing absolute directory" in probed.stderr
    assert log.read_text(encoding="utf-8") == "", "a turn was spent before the refusal"
    assert list(spaced.iterdir()) == []


def test_a_codex_login_exhausted_after_running_its_rows_leaves_nothing_the_next_one_is_read_by(
    tmp_path: Path,
) -> None:
    """The first login runs every row, `just stop` among them, then reports its limit: the
    next login refuses `stop`, and only its own turn decides what the probe reports."""
    probed = _run_probe(
        tmp_path,
        "codex",
        ("status-cut", "stop"),
        {**_codex_logins(tmp_path), "FAKE_CODEX_EXHAUST_AFTER_RUN": "1"},
    )

    assert probed.returncode == 0, probed.stdout + probed.stderr
    assert f"codex (as {CODEX_IDENTITIES[1][0]})" in probed.stdout
    assert "not through  ok" in _line(probed.stdout, "stop")
    assert "let through  ok" in _line(probed.stdout, "status-cut")


def test_a_codex_command_run_under_no_shell_is_refused_rather_than_read(tmp_path: Path) -> None:
    probed = _run_probe(
        tmp_path,
        "codex",
        CODEX_ROWS,
        {**_codex_logins(tmp_path), "FAKE_CODEX_WRAPPER": "/usr/bin/python3"},
    )

    assert probed.returncode == 1, probed.stdout + probed.stderr
    assert "not a shell the probe reads" in probed.stderr
    assert "let through" not in probed.stdout


@pytest.mark.parametrize(
    ("fake", "said"),
    [
        ({"FAKE_CLAUDE_TRAILING": "1"}, "an event after the turn's result"),
        ({"FAKE_CLAUDE_SECOND_RESULT": "1"}, "a second result after the turn's first"),
        ({"FAKE_CLAUDE_DENIAL_TOOL": "WebFetch"}, "unreadable permission_denials"),
        ({"FAKE_CLAUDE_BAD_CONTENT": "1"}, "an assistant event with no content list"),
    ],
    ids=["event-after-result", "second-result", "denial-of-another-tool", "content-not-a-list"],
)
def test_a_claude_stream_the_probe_cannot_trust_is_refused_rather_than_read(
    tmp_path: Path, fake: dict[str, str], said: str
) -> None:
    probed, _ = _probe(tmp_path, "just stop,launch.json", False, **fake)

    assert probed.returncode == 1, probed.stdout + probed.stderr
    assert said in probed.stderr
    assert "let through" not in probed.stdout


def test_the_default_run_drives_both_tools_and_reads_each_alone(tmp_path: Path) -> None:
    """No `--tool`: Claude Code then Codex, the scratch state put back between them, so the
    row Codex refuses is not read off what Claude Code's turn left behind."""
    log = tmp_path / "claude-calls.jsonl"
    log.touch()
    identities = {
        variable: str(tmp_path / variable.lower())
        for _, variable in CLAUDE_IDENTITIES
        if variable is not None
    }
    for directory in identities.values():
        Path(directory).mkdir()

    probed = _run_probe(
        tmp_path,
        None,
        ("status-cut", "stop"),
        {
            **identities,
            **_codex_logins(tmp_path),
            "FAKE_CLAUDE_LOG": str(log),
            "FAKE_CLAUDE_REFUSE": "just stop",
        },
    )

    assert probed.returncode == 0, probed.stdout + probed.stderr
    for tool in ("claude-code", "codex"):
        lines = [line for line in probed.stdout.splitlines() if line.startswith(f"  {tool} ")]
        assert any(" status-cut " in line and "let through  ok" in line for line in lines), lines
        assert any(" stop " in line and " ok " in line for line in lines), lines
    assert "every row as the table says" in probed.stdout


def test_a_kill_row_claude_code_let_through_stops_the_probe_before_codex(tmp_path: Path) -> None:
    """Its sleep is gone, so Codex's kill row could prove nothing: the probe says what to do."""
    identities = {
        variable: str(tmp_path / variable.lower())
        for _, variable in CLAUDE_IDENTITIES
        if variable is not None
    }
    for directory in identities.values():
        Path(directory).mkdir()
    log = tmp_path / "claude-calls.jsonl"
    log.touch()

    probed = _run_probe(
        tmp_path,
        None,
        ("kill",),
        {**identities, **_codex_logins(tmp_path), "FAKE_CLAUDE_LOG": str(log)},
    )

    assert probed.returncode == 1, probed.stdout + probed.stderr
    assert "let through  MISMATCH" in _line(probed.stdout, "kill")
    assert "the kill row reached the probe's own sleep" in probed.stderr
    assert "run each tool alone with `--tool`" in probed.stderr
    assert not any(line.startswith("  codex ") for line in probed.stdout.splitlines())


def test_a_scratch_project_git_cannot_initialise_stops_the_probe_before_any_turn(
    tmp_path: Path,
) -> None:
    refusing = tmp_path / "refusing-git"
    refusing.mkdir()
    (refusing / "git").write_text("#!/bin/sh\necho 'git: refused' >&2\nexit 1\n", encoding="utf-8")
    (refusing / "git").chmod(0o755)
    log = tmp_path / "claude-calls.jsonl"
    log.touch()

    probed = _run_probe(
        tmp_path,
        "claude-code",
        ("status-cut",),
        {"FAKE_CLAUDE_LOG": str(log), "PATH": f"{refusing}{os.pathsep}{os.environ['PATH']}"},
    )

    assert probed.returncode == 1, probed.stdout + probed.stderr
    assert "git could not initialise the scratch project (git: refused)" in probed.stderr
    assert "then retry" in probed.stderr
    assert log.read_text(encoding="utf-8") == ""


def test_a_render_the_sync_refuses_stops_the_probe_before_any_turn(tmp_path: Path) -> None:
    """A `uv` that refuses to run `oneharness` stands in for a sync that cannot render."""
    real = shutil.which("uv")
    assert real is not None
    shim = tmp_path / "uv-shim"
    shim.mkdir()
    (shim / "uv").write_text(
        '#!/bin/sh\nif [ "$1 $2" = "run oneharness" ]; then echo "oneharness: refused" >&2; '
        f'exit 3; fi\nexec {real} "$@"\n',
        encoding="utf-8",
    )
    (shim / "uv").chmod(0o755)
    log = tmp_path / "claude-calls.jsonl"
    log.touch()

    probed = _run_probe(
        tmp_path,
        "claude-code",
        CLAUDE_ROWS,
        {"PATH": f"{shim}{os.pathsep}{os.environ['PATH']}", "FAKE_CLAUDE_LOG": str(log)},
    )

    assert probed.returncode == 1, probed.stdout + probed.stderr
    assert "`just sync-allowlist` refused to render the source" in probed.stderr
    assert log.read_text(encoding="utf-8") == "", "a turn was spent after the render failed"


def test_no_codex_login_anywhere_is_named_with_what_to_do(tmp_path: Path) -> None:
    logins = {}
    for number, (_, variable) in enumerate(CODEX_IDENTITIES):
        empty = tmp_path / f"codex-empty-{number}"
        empty.mkdir()
        logins[variable or "CODEX_HOME"] = str(empty)

    probed = _run_probe(tmp_path, "codex", CODEX_ROWS, logins)

    assert probed.returncode == 1, probed.stdout + probed.stderr
    assert "no codex identity could take a turn" in probed.stderr
    assert "`codex login`" in probed.stderr


def test_a_refused_row_codex_let_through_is_a_mismatch(tmp_path: Path) -> None:
    probed = _run_probe(
        tmp_path, "codex", CODEX_ROWS, {**_codex_logins(tmp_path), "FAKE_CODEX_RUN_ALL": "1"}
    )

    assert probed.returncode == 1, probed.stdout + probed.stderr
    assert "let through  MISMATCH" in _line(probed.stdout, "stop")
    assert "1 row(s) disagree with the table" in probed.stdout


def test_every_program_a_row_runs_is_one_the_probe_records() -> None:
    """A row reaching a program with no recorder would run the real one, unobserved."""
    assert _probe_module().unrecorded() == []


def test_a_row_running_a_program_through_a_pipe_or_substitution_is_named() -> None:
    """Its programs cannot be read off its parts, so the probe refuses it before any turn."""
    probe = _probe_module()
    first = probe.INVOCATIONS[0]
    rows = (
        first._replace(id="piped", command="just status {run} | tee out"),
        first._replace(id="substituted", command="just watch $(cat cursor)"),
        first._replace(id="sequenced", command="cd {checkout} && just status {run}"),
        first._replace(id="newlined", command="just status {run}\nrm -r {checkout}"),
    )

    assert probe.unrecorded(rows) == [
        "piped (a pipe, substitution or subshell)",
        "rm",
        "substituted (a pipe, substitution or subshell)",
    ]


def test_the_probe_fills_exactly_the_placeholders_the_table_declares(tmp_path: Path) -> None:
    """The table's declared set and the values the probe lays out are one contract."""
    probe = _probe_module()
    filled = probe._values(probe.Scratch(tmp_path), probe.INVOCATIONS[0], 1)

    assert set(filled) == sys.modules["manager_invocations"].PLACEHOLDERS


def test_a_row_writing_outside_the_scratch_tree_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    probe = _probe_module()
    scratch = probe.Scratch(tmp_path / "scratch")
    write = next(row for row in probe.INVOCATIONS if row.kind == "write")

    inside = scratch.project / "scratch" / "envelopes" / "run-probe.json"
    assert probe._written(scratch, write, str(inside)) == inside
    with pytest.raises(SystemExit):
        probe._written(scratch, write, str(tmp_path / "elsewhere.json"))
    assert "outside the probe's scratch tree" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        probe._written(scratch, write, str(scratch.root / ".." / "escaped.json"))


def test_a_scratch_directory_the_probe_cannot_write_is_named_with_what_to_do(
    tmp_path: Path,
) -> None:
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o555)
    try:
        probed = subprocess.run(
            ["just", "probe-allowlist", "--tool", "claude-code", "--row", "status-cut"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=300,
            env={
                **_environment(),
                "PATH": f"{_fake_bin(tmp_path)}{os.pathsep}{os.environ['PATH']}",
                "ONEPIPELINE_NODE_SCRATCH_DIR": str(locked),
            },
        )
    finally:
        locked.chmod(0o755)

    assert probed.returncode == 1, probed.stdout + probed.stderr
    assert "could not create a scratch directory under" in probed.stderr
    assert "then retry" in probed.stderr
    assert list(locked.iterdir()) == []


def test_a_codex_login_the_probe_cannot_read_is_named_with_what_to_do(tmp_path: Path) -> None:
    logins = _codex_logins(tmp_path)
    unreadable = Path(logins[CODEX_IDENTITIES[0][1] or "CODEX_HOME"]) / "auth.json"
    unreadable.chmod(0)
    try:
        probed = _run_probe(tmp_path, "codex", CODEX_ROWS, logins)
    finally:
        unreadable.chmod(0o600)

    assert probed.returncode == 1, probed.stdout + probed.stderr
    assert f"could not read or write {unreadable}" in probed.stderr
    assert "each login it copies is readable, then retry" in probed.stderr
    assert "Traceback" not in probed.stderr


#: The wrapper and every script it sources, which the project's key names.
WRAPPER_SCRIPTS = (
    "allowlist-probe.sh",
    "dispatch-env.sh",
    "claude-alt-config-dir.sh",
    "codex-alt-home.sh",
    "credentials-env.sh",
    "template-env.sh",
)


def _wrapper_checkout(tmp_path: Path, python: str | None, templates: bool = True) -> Path:
    """A scratch checkout holding the real wrapper and the scripts it sources, whose
    `.venv/bin/python` is `python`, a shell body, so the wrapper meets that interpreter. Its
    `templates/templates.yaml` is only there to be read, as the identities' environment
    requires, unless `templates` is false."""
    checkout = tmp_path / "checkout"
    (checkout / "scripts").mkdir(parents=True)
    for script in WRAPPER_SCRIPTS:
        shutil.copy2(REPO_ROOT / "scripts" / script, checkout / "scripts" / script)
    if templates:
        (checkout / "templates").mkdir()
        (checkout / "templates" / "templates.yaml").write_text("{}\n", encoding="utf-8")
    if python is not None:
        interpreter = checkout / ".venv" / "bin" / "python"
        interpreter.parent.mkdir(parents=True)
        interpreter.write_text(f"#!/bin/bash\n{python}\n", encoding="utf-8")
        interpreter.chmod(0o755)
    return checkout


def _wrapper(tmp_path: Path, checkout: Path) -> subprocess.CompletedProcess[str]:
    """Run the real wrapper in `checkout`, with a `PATH` holding only the system."""
    home = tmp_path / "home"
    home.mkdir()
    return subprocess.run(
        ["bash", str(checkout / "scripts" / "allowlist-probe.sh"), "--tool", "codex"],
        cwd=checkout,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
        env={"HOME": str(home), "PATH": "/usr/bin:/bin"},
    )


@pytest.mark.parametrize(
    ("python", "says"),
    [
        (None, "this checkout has no interpreter at .venv/bin/python; run `just bootstrap`"),
        ("kill -TERM $$", "the probe was terminated by signal 15"),
        ("exit 7", "the probe ended with exit 7, a status it never gives"),
    ],
    ids=["no-interpreter", "signal", "unknown-status"],
)
def test_the_wrapper_names_each_way_the_probe_could_not_run(
    tmp_path: Path, python: str | None, says: str
) -> None:
    probed = _wrapper(tmp_path, _wrapper_checkout(tmp_path, python))

    assert probed.returncode == 1, probed.stdout + probed.stderr
    assert f"probe-allowlist: {says}" in probed.stderr


def test_the_wrapper_stops_before_the_probe_when_the_identities_cannot_be_established(
    tmp_path: Path,
) -> None:
    probed = _wrapper(tmp_path, _wrapper_checkout(tmp_path, "exit 0", templates=False))

    assert probed.returncode == 1, probed.stdout + probed.stderr
    assert "carries no readable templates.yaml" in probed.stderr
    assert "probe-allowlist: could not establish the identities' environment" in probed.stderr


def test_the_wrapper_hands_the_probe_s_own_verdict_through(tmp_path: Path) -> None:
    probed = _wrapper(tmp_path, _wrapper_checkout(tmp_path, 'echo "$@"; exit 2'))

    assert probed.returncode == 2, probed.stdout + probed.stderr
    assert "allowlist-probe.py --tool codex" in probed.stdout
    assert probed.stderr == ""
