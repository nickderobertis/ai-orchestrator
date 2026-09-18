"""`scripts/dispatch-env-hook.sh` prints what the driver-start resolvers establish.

The engine spawns it immediately before every node-scope dispatch and overlays the one
JSON document it prints on the driver's environment for that child alone — the seam
ai-orchestrator#1109 asked for, because a live driver re-reads its harness routing at
dispatch time but kept the environment it started with, so an `env_from` indirection
added while a run was live failed the run's next dispatch at provider startup.

Two properties are held here, and both are held on the real script under a real bash
in an environment built from nothing. The document is exactly the shape onepipeline's
`docs/contract.md` (**Dispatch-env hook**) accepts, `{"version": 1, "env": {...}}` with
every value a string, and its members are the variables the three resolvers establish
at driver start: the checkout's `.env` credentials, every Claude identity's config
directory and the alternate Codex home. And there is one definition of which resolvers
those are — `scripts/dispatch-env.sh` — that both the wrapper's driver-start arm and the
hook source, so a resolver added to one cannot be missing from the other.
`tests/e2e/test_orchestrate_launch_e2e.py` drives the hook through a real launch.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import NamedTuple

import pytest

from orchestrator.root import REPO_ROOT

HOOK = REPO_ROOT / "scripts" / "dispatch-env-hook.sh"
DEFINITION = REPO_ROOT / "scripts" / "dispatch-env.sh"
LAUNCH_WRAPPER = REPO_ROOT / "scripts" / "onepipeline.sh"

#: The resolvers `scripts/dispatch-env.sh` names, as `<helper>:<function>`, read off the
#: file rather than restated: the table there is the one definition and this module
#: holds both sourcing scripts to it.
RESOLVER_ENTRY = re.compile(r'^\s*"([a-z-]+\.sh):([a-z_]+)"\s*$', re.MULTILINE)

#: Every Claude identity's indirection, as `scripts/claude-alt-config-dir.sh` declares
#: them; each one is a member of the document whatever the host holds.
CLAUDE_INDIRECTIONS = (
    "ORCHESTRATOR_CLAUDE_ALT_CONFIG_DIR",
    "ORCHESTRATOR_CLAUDE_ALT2_CONFIG_DIR",
    "ORCHESTRATOR_CLAUDE_PRIMARY_BACKUP_CONFIG_DIR",
    "ORCHESTRATOR_CLAUDE_PRIMARY_CONFIG_DIR",
)
CODEX_INDIRECTION = "ORCHESTRATOR_CODEX_ALT_HOME"

#: An environment variable name, which is what every member of `env` has to be.
VARIABLE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class Ran(NamedTuple):
    """One run of the hook: its exit, the bytes it printed, and its diagnostics."""

    returncode: int
    stdout: str
    stderr: str


def _checkout(root: Path) -> Path:
    """A checkout-shaped directory holding the hook and everything it sources.

    Copied rather than run in place so that the `.env` the credentials resolver reads —
    the file beside `scripts/` — is this test's own and never the tree's, and so that a
    helper can be corrupted or removed to drive the refusals.
    """
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    for name in ("dispatch-env-hook.sh", "dispatch-env.sh", *_resolver_helpers()):
        copied = scripts / name
        copied.write_bytes((REPO_ROOT / "scripts" / name).read_bytes())
        copied.chmod(0o755)
    return root


def _resolver_helpers() -> tuple[str, ...]:
    """The helpers the definition names, in order."""
    return tuple(helper for helper, _ in RESOLVER_ENTRY.findall(DEFINITION.read_text("utf-8")))


def _run(checkout: Path, environment: dict[str, str], *arguments: str) -> Ran:
    """Run the hook as the engine does: the command itself, in a stated environment."""
    home = environment.get("HOME")
    if home is not None:
        Path(home).mkdir(exist_ok=True)
    completed = subprocess.run(
        [str(checkout / "scripts" / "dispatch-env-hook.sh"), *arguments],
        env={"PATH": "/usr/bin:/bin", **environment},
        cwd=checkout,
        text=True,
        capture_output=True,
        check=False,
    )
    return Ran(completed.returncode, completed.stdout, completed.stderr)


def _env(ran: Ran) -> dict[str, str]:
    """The `env` of the one document a successful run printed, held to the contract's shape.

    Parsed as pairs rather than with a bare `json.loads`, which keeps the last of two
    members of one name and says nothing, while a member named twice is malformed to
    the engine.
    """
    assert ran.returncode == 0, ran.stderr
    lines = ran.stdout.splitlines()
    assert len(lines) == 1, f"the hook printed {len(lines)} lines, not one document:\n{ran.stdout}"
    pairs: list[tuple[str, object]] = json.loads(lines[0], object_pairs_hook=lambda kv: kv)
    # llmlint: ignore[contracts_have_one_source_or_a_drift_gate] The shape is the engine's, and the installed engine does not carry the hook yet; the launch journeys drive the real reader once it does.  # noqa: E501
    assert [name for name, _ in pairs] == ["version", "env"], pairs
    version, members = (value for _, value in pairs)
    assert version == 1, pairs
    assert isinstance(members, list), pairs
    names = [name for name, _ in members]
    assert len(names) == len(set(names)), f"a member is named twice: {names}"
    for name, value in members:
        assert VARIABLE_NAME.match(name), f"{name!r} is not an environment variable name"
        assert isinstance(value, str), f"{name} carries {value!r}, which is not a string"
    return dict(members)


def test_the_document_carries_what_the_three_resolvers_establish(tmp_path: Path) -> None:
    """Every identity indirection, the Codex home and each `.env` name, as strings."""
    checkout = _checkout(tmp_path / "checkout")
    (checkout / ".env").write_text(
        'GH_PROJECTS_TOKEN="planted-by-the-hook-test"\nGH_PROJECTS_OWNER=nickderobertis\n',
        encoding="utf-8",
    )
    home = tmp_path / "home"

    env = _env(_run(checkout, {"HOME": str(home)}))

    assert env == {
        "GH_PROJECTS_TOKEN": "planted-by-the-hook-test",
        "GH_PROJECTS_OWNER": "nickderobertis",
        "ORCHESTRATOR_CLAUDE_ALT_CONFIG_DIR": str(home / ".claude-alt"),
        "ORCHESTRATOR_CLAUDE_ALT2_CONFIG_DIR": str(home / ".claude-alt2"),
        "ORCHESTRATOR_CLAUDE_PRIMARY_BACKUP_CONFIG_DIR": str(home / ".claude-primary-backup"),
        "ORCHESTRATOR_CLAUDE_PRIMARY_CONFIG_DIR": str(home / ".claude"),
        CODEX_INDIRECTION: str(home / ".codex-alt"),
    }
    # The Codex resolver's one side effect, which the hook has to keep: an absent home
    # falls through as `auth` and a missing one hard-fails.
    assert (home / ".codex-alt").is_dir()


def test_without_a_credentials_file_the_document_carries_the_indirections_alone(
    tmp_path: Path,
) -> None:
    checkout = _checkout(tmp_path / "checkout")

    env = _env(_run(checkout, {"HOME": str(tmp_path / "home")}))

    assert tuple(env) == (*CLAUDE_INDIRECTIONS, CODEX_INDIRECTION)


def test_the_environment_wins_over_the_credentials_file_as_it_does_at_driver_start(
    tmp_path: Path,
) -> None:
    """The document says what the driver-start arm exported, and that arm keeps an export."""
    checkout = _checkout(tmp_path / "checkout")
    (checkout / ".env").write_text("GH_PROJECTS_OWNER=from-the-file\n", encoding="utf-8")
    environment = {
        "HOME": str(tmp_path / "home"),
        "GH_PROJECTS_OWNER": "from-the-environment",
        "ORCHESTRATOR_CLAUDE_ALT_CONFIG_DIR": str(tmp_path / "an-override"),
    }

    env = _env(_run(checkout, environment))

    assert env["GH_PROJECTS_OWNER"] == "from-the-environment"
    assert env["ORCHESTRATOR_CLAUDE_ALT_CONFIG_DIR"] == str(tmp_path / "an-override")


def test_a_value_json_reserves_round_trips_and_a_name_is_printed_once(tmp_path: Path) -> None:
    """Quotes, backslashes and control characters are escaped; a repeated name is one member.

    A `.env` may spell a name twice, and a name it defines may be one the identity helpers
    also export; a JSON object naming a member twice is a malformed document to the engine.
    """
    checkout = _checkout(tmp_path / "checkout")
    awkward = 'a "quoted" \\ back\\slash\ttab — naïve ✓'
    (checkout / ".env").write_text(
        f"AWKWARD='{awkward}'\nAWKWARD=again\nORCHESTRATOR_CODEX_ALT_HOME={tmp_path}/codex\n",
        encoding="utf-8",
    )

    env = _env(_run(checkout, {"HOME": str(tmp_path / "home")}))

    assert env["AWKWARD"] == awkward
    assert list(env).count(CODEX_INDIRECTION) == 1
    assert env[CODEX_INDIRECTION] == f"{tmp_path}/codex"


def test_a_value_that_is_not_utf8_is_refused_by_name_without_its_bytes(tmp_path: Path) -> None:
    """Bytes JSON cannot carry refuse the document, naming the variable and nothing else."""
    checkout = _checkout(tmp_path / "checkout")
    (checkout / ".env").write_bytes(b"MOJIBAKE=abc\xff\xfedef\n")

    ran = _run(checkout, {"HOME": str(tmp_path / "home")})

    assert ran.returncode == 2, ran.stdout
    assert ran.stdout == "", "a refused run printed a document the engine would overlay"
    assert "MOJIBAKE" in ran.stderr
    assert "not well-formed UTF-8" in ran.stderr
    assert "abc" not in ran.stderr and "def" not in ran.stderr, ran.stderr


def test_a_resolvers_refusal_prints_no_document_and_names_the_hook(tmp_path: Path) -> None:
    """A relative override is the identity resolver's to refuse, attributed to the hook."""
    checkout = _checkout(tmp_path / "checkout")

    ran = _run(
        checkout,
        {"HOME": str(tmp_path / "home"), "ORCHESTRATOR_CLAUDE_ALT_CONFIG_DIR": "relative/dir"},
    )

    assert ran.returncode == 2, ran.stdout
    assert ran.stdout == "", "a refused run printed a document the engine would overlay"
    assert ran.stderr.startswith("dispatch-env-hook: "), ran.stderr
    assert "ORCHESTRATOR_CLAUDE_ALT_CONFIG_DIR" in ran.stderr
    assert "relative/dir" not in ran.stderr


def test_a_malformed_credentials_line_is_refused_without_its_value(tmp_path: Path) -> None:
    checkout = _checkout(tmp_path / "checkout")
    (checkout / ".env").write_text("GOOD=fine\n=no-name-planted-secret\n", encoding="utf-8")

    ran = _run(checkout, {"HOME": str(tmp_path / "home")})

    assert ran.returncode == 2, ran.stdout
    assert ran.stdout == ""
    assert "line 2" in ran.stderr
    assert "no-name-planted-secret" not in ran.stderr


def test_an_argument_is_refused_before_anything_is_resolved(tmp_path: Path) -> None:
    checkout = _checkout(tmp_path / "checkout")
    home = tmp_path / "home"

    ran = _run(checkout, {"HOME": str(home)}, "--help")

    assert ran.returncode == 2
    assert ran.stdout == ""
    assert "takes no arguments" in ran.stderr
    assert "--dispatch-env-hook" in ran.stderr
    assert not (home / ".codex-alt").exists(), "a refused run still ran a resolver"


@pytest.mark.parametrize("state", ["absent", "unloadable"])
def test_a_definition_the_hook_cannot_load_is_refused_by_name(tmp_path: Path, state: str) -> None:
    checkout = _checkout(tmp_path / "checkout")
    definition = checkout / "scripts" / "dispatch-env.sh"
    if state == "absent":
        definition.unlink()
    else:
        definition.write_text("this is ( not valid bash\n", encoding="utf-8")

    ran = _run(checkout, {"HOME": str(tmp_path / "home")})

    assert ran.returncode == 2
    assert ran.stdout == ""
    assert "dispatch-env.sh" in ran.stderr
    assert "just bootstrap" in ran.stderr


@pytest.mark.parametrize(
    ("hollowed", "function"),
    [
        ("dispatch-env.sh", "export_dispatch_environment"),
        ("codex-alt-home.sh", "ensure_codex_alt_home"),
    ],
)
def test_a_helper_that_loads_but_defines_no_resolver_is_refused_by_both_names(
    tmp_path: Path, hollowed: str, function: str
) -> None:
    """A readable, loadable helper missing its function is named, not left to bash."""
    checkout = _checkout(tmp_path / "checkout")
    (checkout / "scripts" / hollowed).write_text("# nothing defined here\n", encoding="utf-8")

    ran = _run(checkout, {"HOME": str(tmp_path / "home")})

    assert ran.returncode == 2
    assert ran.stdout == ""
    assert "command not found" not in ran.stderr, ran.stderr
    assert f"defines no {function}" in ran.stderr, ran.stderr
    assert hollowed in ran.stderr, ran.stderr


@pytest.mark.parametrize(
    ("state", "said"),
    [("absent", "not a readable regular file"), ("unloadable", "could not be loaded")],
)
def test_a_resolver_the_definition_cannot_load_is_refused_by_name(
    tmp_path: Path, state: str, said: str
) -> None:
    """A resolver the table names that is missing or corrupt is refused with its path.

    Both arms of the definition's load, because a missing helper and a half-written one
    are different remedies to an operator and the message says which it found.
    """
    checkout = _checkout(tmp_path / "checkout")
    resolver = checkout / "scripts" / "codex-alt-home.sh"
    if state == "absent":
        resolver.unlink()
    else:
        resolver.write_text("this is ( not valid bash\n", encoding="utf-8")

    ran = _run(checkout, {"HOME": str(tmp_path / "home")})

    assert ran.returncode == 2
    assert ran.stdout == ""
    assert str(resolver) in ran.stderr, ran.stderr
    assert said in ran.stderr, ran.stderr
    assert "just bootstrap" in ran.stderr, ran.stderr


def test_every_resolver_in_the_table_has_an_arm_recording_what_it_established() -> None:
    """The table and the `case` that reads each resolver's names cannot drift apart.

    A resolver added to the table without an arm would run and hand nothing on, which
    the hook could not tell from a resolver that establishes nothing.
    """
    definition = DEFINITION.read_text("utf-8")
    arms = re.findall(r"^\s+([a-z_]+)\)\s*$", definition, re.MULTILINE)

    assert sorted(arms) == sorted(function for _, function in RESOLVER_ENTRY.findall(definition))


def test_the_definition_names_the_three_resolvers_the_driver_start_arm_ran() -> None:
    """The table is the whole list, and each entry is a helper beside it with that function."""
    entries = RESOLVER_ENTRY.findall(DEFINITION.read_text("utf-8"))

    assert entries == [
        ("credentials-env.sh", "export_host_credentials"),
        ("claude-alt-config-dir.sh", "resolve_claude_alt_config_dir"),
        ("codex-alt-home.sh", "ensure_codex_alt_home"),
    ]
    for helper, function in entries:
        source = (REPO_ROOT / "scripts" / helper).read_text("utf-8")
        assert re.search(rf"^{function}\(\) \{{", source, re.MULTILINE), (
            f"scripts/{helper} does not define {function}, which the definition names"
        )


@pytest.mark.parametrize("script", [LAUNCH_WRAPPER, HOOK], ids=lambda path: path.name)
def test_both_sourcing_scripts_run_the_resolvers_through_the_one_definition(script: Path) -> None:
    """Neither the driver-start arm nor the hook names a resolver of its own.

    The failure this closes is the one the definition exists for: a resolver added to
    the wrapper and not to the hook — or the reverse — is exactly the drift that makes a
    dispatch's environment differ from its driver's again.
    """
    source = script.read_text("utf-8")
    code = "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("#"))

    assert "export_dispatch_environment" in code, f"{script.name} never runs the definition"
    for helper, function in RESOLVER_ENTRY.findall(DEFINITION.read_text("utf-8")):
        assert helper not in code, f"{script.name} sources {helper} beside the definition"
        assert function not in code, f"{script.name} calls {function} beside the definition"


def test_the_hook_is_executable_and_named_by_the_wrapper() -> None:
    """The wrapper names it by absolute path, so it has to be runnable as a command."""
    assert os.access(HOOK, os.X_OK), f"{HOOK.name} is not executable"
    assert '--dispatch-env-hook "$dispatch_env_hook"' in LAUNCH_WRAPPER.read_text("utf-8")
