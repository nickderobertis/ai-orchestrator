"""A launch builds its environment from a table; it does not inherit one.

The engine hands every dispatch the environment its driver started with, so whatever a
planner's shell was carrying reached every worker. One of those names was doing real
damage: `VIRTUAL_ENV`, which on this host is the canonical checkout's `.venv`, so a
worker's ordinary `uv pip install` in its own worktree wrote into the environment every
concurrent node and the manager share (ai-orchestrator#1162).

The answer is not a special case for `uv pip`. `scripts/dispatch-env.sh` carries the
whitelist a launch's environment is *constructed* from, and
`scripts/onepipeline.sh`'s launch arms apply it — the last place a name can be taken out
of a driver's environment at all, since the engine's dispatch-env hook can only add to
one. What is held here is the table's own shape, and the construction driven for real
under a bare `env -i`: the tables are the one place the list is stated, every entry
carries the reason it is there, and the Python-environment class is refused whatever a
family would otherwise admit.

`tests/e2e/test_orchestrate_launch_e2e.py` reads the other half — what a real dispatched
turn's provider is handed — off the turn rather than off the table.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from orchestrator.root import REPO_ROOT

DEFINITION = REPO_ROOT / "scripts" / "dispatch-env.sh"
LAUNCH_WRAPPER = REPO_ROOT / "scripts" / "onepipeline.sh"

#: The three tables, by the names `scripts/dispatch-env.sh` declares them under.
REFUSED = "DISPATCH_ENVIRONMENT_REFUSED_NAMES"
KEPT_NAMES = "DISPATCH_ENVIRONMENT_KEPT_NAMES"
KEPT_PREFIXES = "DISPATCH_ENVIRONMENT_KEPT_PREFIXES"

#: One `[NAME]="reason"` member of an associative array literal.
MEMBER = re.compile(r'^\s*\[([A-Za-z_][A-Za-z0-9_]*)\]="([^"]*)"\s*$', re.MULTILINE)

#: How short a reason may be and still be one. A bare restatement of the name
#: ("the home directory") says nothing a reader could act on; the entries here explain
#: what a dispatch does with the variable, which is what makes a missing one diagnosable.
SHORTEST_REASON = 40

#: The class ai-orchestrator#1162 closes: the parent shell's Python environment, every
#: member of which redirects an install or an import away from the worktree a dispatch
#: is working in and into whatever the launching session had activated.
THE_REFUSED_CLASS = ("VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT", "PYTHONHOME", "CONDA_PREFIX")

#: Families a dispatch cannot work without, each named by the ticket: the harness and
#: engine namespaces, every provider's own, the forge and the tool chains. Not the whole
#: table — that is the file's to state — but the ones whose absence would break a
#: dispatch in a way this test is here to catch before a run does.
REQUIRED_PREFIXES = (
    "ONEHARNESS_",
    "ONEPIPELINE_",
    "ONEVCS_",
    "ONEAGENTGRAPH_",
    "ONEJUDGE_",
    "ONEMESSAGEBUS_",
    "ONETASKGRAPH_",
    "LLMLINT_",
    "ORCHESTRATOR_",
    "CLAUDE",
    "CODEX_",
    "ANTHROPIC_",
    "OPENAI_",
    "GH_",
    "GITHUB_",
    "GIT_",
    "SSH_",
    "CARGO_",
    "RUSTUP_",
    "NX_",
    "BUN_",
    "NODE_",
    "UV_",
    "DOCKER_",
    "ASDF_",
    "XDG_",
    "LC_",
)


def _table(name: str) -> dict[str, str]:
    """One declared associative array, as name → reason."""
    source = DEFINITION.read_text(encoding="utf-8")
    opened = re.search(rf"^declare -A {name}=\($", source, re.MULTILINE)
    assert opened is not None, f"scripts/dispatch-env.sh declares no {name}"
    closed = source.index("\n)\n", opened.end())
    members = dict(MEMBER.findall(source[opened.end() : closed]))
    assert members, f"{name} is declared with no entries"
    return members


@pytest.mark.parametrize("table", [REFUSED, KEPT_NAMES, KEPT_PREFIXES])
def test_every_entry_of_the_table_carries_the_reason_it_is_there(table: str) -> None:
    """A missing entry has to read as a diagnosable omission rather than a mystery.

    The risk the whitelist carries is an omitted variable breaking a tool inside a
    dispatch in a way nobody foresees. What makes that recoverable is that every entry
    beside it says what a dispatch does with its variable, so the person reading the
    table can tell whether theirs belongs.
    """
    thin = {
        name: reason
        for name, reason in _table(table).items()
        if len(reason.strip()) < SHORTEST_REASON
    }

    assert not thin, (
        f"{table} carries entries whose reason says too little to act on: {sorted(thin)}"
    )


@pytest.mark.parametrize("name", THE_REFUSED_CLASS)
def test_the_python_environment_class_is_refused_by_name(name: str) -> None:
    """Each one names the environment an install or an import lands in, and is the defect."""
    assert name in _table(REFUSED), (
        f"{name} is not refused, so a launch hands it to every dispatch and a worker's "
        "install lands in the launching session's environment (ai-orchestrator#1162)"
    )


@pytest.mark.parametrize("prefix", REQUIRED_PREFIXES)
def test_the_families_a_dispatch_reads_are_kept(prefix: str) -> None:
    assert prefix in _table(KEPT_PREFIXES), (
        f"{prefix} is on no kept family, so every variable under it is dropped from a "
        "dispatch's environment"
    )


def test_a_refused_name_is_not_also_kept() -> None:
    """The two exact tables cannot both claim a name; the file would read two ways."""
    both = set(_table(REFUSED)) & set(_table(KEPT_NAMES))

    assert not both, f"these names are both refused and kept: {sorted(both)}"


def test_the_launch_wrapper_states_no_list_of_its_own() -> None:
    """The table is the one place the list is stated, so the wrapper names no variable.

    A second list in the launcher is how the hook and the driver-start arm drifted apart
    before `scripts/dispatch-env.sh` existed, and it is the same failure here: a name
    added to one place and not the other is a dispatch whose environment differs from
    the one the table describes.
    """
    code = "\n".join(
        line
        for line in LAUNCH_WRAPPER.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    )

    assert "construct_dispatch_environment" in code, (
        "scripts/onepipeline.sh never builds a launch's environment from the table"
    )
    for table in (REFUSED, KEPT_NAMES, KEPT_PREFIXES):
        assert table not in code, (
            f"scripts/onepipeline.sh reads {table} itself rather than through the "
            "construction, which is a second place the list is decided"
        )
    assert "unset" not in code, (
        "scripts/onepipeline.sh drops a variable of its own, beside the table"
    )


def _constructed(tmp_path: Path, planted: dict[str, str]) -> dict[str, str]:
    """Run the real resolvers and the real construction, from an environment of nothing.

    `env -i` rather than a filtered copy of this process's: what is under test is which
    names survive, and a starting environment this test did not state whole would leave
    that unreadable. The checkout is the tracked one — the helpers being driven are its
    — and `HOME` is this test's, because the identity resolver creates directories under
    whatever it is given.
    """
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    script = (
        ". scripts/dispatch-env.sh\n"
        "export_dispatch_environment probe\n"
        "construct_dispatch_environment probe\n"
        "compgen -e | sort\n"
        'printf "PATH_HEAD=%s\\n" "${PATH%%:*}"\n'
    )
    completed = subprocess.run(
        [
            "env",
            "-i",
            f"HOME={home}",
            "PATH=/usr/bin:/bin",
            *[f"{k}={v}" for k, v in planted.items()],
            "bash",
            "-c",
            script,
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    kept = {}
    for line in completed.stdout.splitlines():
        if line.startswith("PATH_HEAD="):
            kept["PATH_HEAD"] = line[len("PATH_HEAD=") :]
        else:
            kept[line] = ""
    return kept


#: A name no table admits and nothing on this host sets — what an ambient variable a
#: launching shell happened to be carrying looks like to the construction.
AMBIENT = "AIO_1162_AMBIENT"


def test_the_construction_drops_the_python_environment_and_an_ambient_name(
    tmp_path: Path,
) -> None:
    """Driven for real: the tracked helper, a bare `env -i`, and what survives it."""
    kept = _constructed(
        tmp_path,
        {
            "VIRTUAL_ENV": "/somewhere/else/.venv",
            "VIRTUAL_ENV_PROMPT": "(else)",
            "UV_PROJECT_ENVIRONMENT": "/somewhere/else/.venv",
            "PYTHONHOME": "/somewhere/else",
            "PYTHONPATH": "/somewhere/else",
            "CONDA_PREFIX": "/somewhere/else/conda",
            AMBIENT: "carried-in-by-the-launching-shell",
        },
    )

    dropped = [
        name
        for name in (
            "VIRTUAL_ENV",
            "VIRTUAL_ENV_PROMPT",
            "UV_PROJECT_ENVIRONMENT",
            "PYTHONHOME",
            "PYTHONPATH",
            "CONDA_PREFIX",
            AMBIENT,
        )
        if name in kept
    ]
    assert not dropped, f"the construction kept {dropped}, which a dispatch would inherit"


def test_the_construction_keeps_what_the_tables_and_the_resolvers_name(
    tmp_path: Path,
) -> None:
    """The other direction: a whitelist that kept nothing would pass the test above.

    Each of these is something a dispatch cannot work without — the process's own home,
    an identity indirection every `oneharness.*.toml` names as an `env_from` source, a
    harness selection, and a forge credential — and a family or a resolver is what puts
    each one here.
    """
    kept = _constructed(tmp_path, {"ONEHARNESS_HARNESSES": "codex:primary", "GH_TOKEN": "planted"})

    for name in (
        "HOME",
        "PATH",
        "ONEHARNESS_HARNESSES",
        "GH_TOKEN",
        "ORCHESTRATOR_CLAUDE_ALT_CONFIG_DIR",
        "ORCHESTRATOR_CLAUDE_ALT2_CONFIG_DIR",
        "ORCHESTRATOR_CLAUDE_PRIMARY_BACKUP_CONFIG_DIR",
        "ORCHESTRATOR_CLAUDE_PRIMARY_CONFIG_DIR",
        "ORCHESTRATOR_CODEX_ALT_HOME",
    ):
        assert name in kept, (
            f"the construction dropped {name}, which a dispatch reads; add it to a table "
            "in scripts/dispatch-env.sh with the reason it belongs there"
        )


def test_the_worktree_environment_is_put_at_the_front_of_a_dispatchs_path(
    tmp_path: Path,
) -> None:
    """What replaces the inherited `VIRTUAL_ENV`: the venv of wherever the dispatch is.

    Relative on purpose. A launch does not know where a node will be placed, and the
    engine hands a dispatch-env hook no worktree path either, so there is no absolute
    answer at the moment this is built — while a dispatch's working directory *is* its
    worktree, which makes the relative entry resolve to that repository's own environment
    where it has one and match nothing where it does not.
    """
    kept = _constructed(tmp_path, {})

    assert kept["PATH_HEAD"] == ".venv/bin", (
        f"a dispatch's PATH begins with {kept['PATH_HEAD']!r} rather than its own "
        "worktree's environment, so a bare `python` is whichever one the host offers"
    )


def _kept_prefixes() -> tuple[str, ...]:
    """Every family the kept-prefix table names, read out of its declaration."""
    text = DEFINITION.read_text(encoding="utf-8")
    declared = re.search(rf"declare -A {KEPT_PREFIXES}=\((.*?)\n\)", text, re.DOTALL)
    assert declared is not None, f"{DEFINITION} declares no {KEPT_PREFIXES}"
    prefixes = tuple(name for name, _ in MEMBER.findall(declared.group(1)))
    assert prefixes, f"{KEPT_PREFIXES} names no family"
    return prefixes


def test_a_name_planted_in_every_kept_family_survives_the_construction(
    tmp_path: Path,
) -> None:
    """Each family the table keeps keeps a name nobody listed, which is what a family is for.

    One made-up member per family rather than the names a host happens to set, because
    what a family promises is the names added to it later.
    """
    planted = {f"{prefix}AIO_1162_PROBE": "planted" for prefix in _kept_prefixes()}

    kept = _constructed(tmp_path, planted)

    dropped = sorted(name for name in planted if name not in kept)
    assert not dropped, f"the construction dropped {dropped}, members of families it keeps"


def _table_names(table: str) -> tuple[str, ...]:
    """Every name one of the exact-name tables states, read out of its declaration."""
    text = DEFINITION.read_text(encoding="utf-8")
    declared = re.search(rf"declare -A {table}=\((.*?)\n\)", text, re.DOTALL)
    assert declared is not None, f"{DEFINITION} declares no {table}"
    names = tuple(name for name, _ in MEMBER.findall(declared.group(1)))
    assert names, f"{table} names nothing"
    return names


#: Set by `_constructed` itself, so planting them would replace the start it states.
STATED_BY_THE_HARNESS = ("HOME", "PATH")


def test_every_name_the_kept_table_states_survives_the_construction(tmp_path: Path) -> None:
    """Each exact name the table keeps is kept, not only the few a dispatch is known to read.

    Planted with a directory, a value every entry tolerates — bash drops an inherited
    `OLDPWD` that names none — save `BASH_ENV`, which the `bash` the construction runs
    in sources, and so is handed an empty file.
    """
    empty = tmp_path / "empty"
    empty.write_text("", encoding="utf-8")
    planted = {
        name: str(empty if name == "BASH_ENV" else tmp_path)
        for name in _table_names(KEPT_NAMES)
        if name not in STATED_BY_THE_HARNESS
    }

    kept = _constructed(tmp_path, planted)

    dropped = sorted(name for name in planted if name not in kept)
    assert not dropped, f"the construction dropped {dropped}, which its kept table names"


def test_every_name_the_refused_table_states_is_dropped_by_the_construction(
    tmp_path: Path,
) -> None:
    """Each refusal holds, the ones beside the class `THE_REFUSED_CLASS` names included."""
    planted = {name: "/somewhere/else" for name in _table_names(REFUSED)}

    kept = _constructed(tmp_path, planted)

    passed_on = sorted(name for name in planted if name in kept)
    assert not passed_on, f"the construction kept {passed_on}, which its refused table names"


def test_a_name_the_construction_cannot_drop_refuses_the_launch(tmp_path: Path) -> None:
    """A readonly name no table admits is reported, never passed on in silence.

    Bash cannot unset a name it holds readonly, and a launch that went on anyway would
    hand every dispatch the very kind of name the construction exists to take away.
    """
    home = tmp_path / "home"
    home.mkdir()
    script = (
        ". scripts/dispatch-env.sh\n"
        "export_dispatch_environment probe\n"
        f"declare -rx {AMBIENT}=carried-in\n"
        "construct_dispatch_environment probe\n"
        'echo "went on"\n'
    )
    completed = subprocess.run(
        ["env", "-i", f"HOME={home}", "PATH=/usr/bin:/bin", "bash", "-c", script],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 2, completed.stdout + completed.stderr
    assert "went on" not in completed.stdout, completed.stdout
    assert f"probe: {AMBIENT} is on no dispatch-environment table" in completed.stderr, (
        completed.stderr
    )
