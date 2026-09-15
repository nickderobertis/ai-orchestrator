"""The follow-up agent is single-sided, so the task `just follow-ups` composes is all it is given.

`graphs/follow-up.yaml`'s `worker` is a `kind: oneharness` member with no judge side. Two
things about that member make the composed task — rendered from `config/follow-up-task.md`
and `orchestrator/follow_up_tickets.py` — the one copy of the agent's instructions, and each
would silently stop being true if the graph moved:

* **it claims no `task` of its own.** `oneagentgraph` hands the node's composed task to every
  member that claims none, and a member's own `task` *replaces* it as the whole prompt —
  `graphs/dag-scope.yaml`'s `check-in` member says so where it claims one;
* **it layers no persona.** A single-sided member has no onejudge base config to layer a
  persona delta over, so `personas/follow-up.yaml` is named for the label alone, exactly as
  `tests/test_pacemaker_discipline.py` holds for the pacemaker — and a judge added here would
  turn it into a two-party member reading that persona instead.

A third thing is where the member's **mode** is declared. Its turn works in the agent graph's
scratch directory, not a repository, so it has to run in `bypass` — and the oneagentgraph the
engine links admits no `mode` on a single-sided member, so `oneharness.follow-up.toml` is the
one place that says so. A `mode` on the member would be a second answer that could disagree.

`tests/plan_tooling/test_follow_ups_recipe_e2e.py` launches a direct node naming this graph
through the installed engine and reads the composed task out of the member's own turn; this
holds, on every tree, the arrangement that journey measured.
"""

from __future__ import annotations

import subprocess
import tomllib

from orchestrator.root import REPO_ROOT

GRAPH = "graphs/follow-up.yaml"
PERSONA = "personas/follow-up.yaml"
CONFIG = "oneharness.follow-up.toml"

#: The one member, and exactly the fields it declares. A `task`, a `judge`, an `agent` or a
#: `base_config` beside these is the drift this gate exists for.
WORKER = "worker"
DECLARED = {
    "kind": "oneharness",
    "oneharness_config": f"../{CONFIG}",
    "persona": f"../{PERSONA}",
}

#: The `oneagentgraph` this checkout installs, which is what validates a graph for a launch.
ONEAGENTGRAPH_BIN = REPO_ROOT / ".venv" / "bin" / "oneagentgraph"


def _members() -> dict[str, dict[str, str]]:
    """Every member of the graph and the scalar fields it declares, read off the document.

    A reader written for this document's own shape rather than a YAML library, which the
    workspace does not install; `oneagentgraph validate` below is what holds it well-formed.
    """
    lines = (REPO_ROOT / GRAPH).read_text(encoding="utf-8").splitlines()
    opened = lines.index("members:")
    members: dict[str, dict[str, str]] = {}
    for line in lines[opened + 1 :]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if indent == 0:
            break
        key, _, value = line.strip().partition(":")
        if indent == 2:
            members[key] = {}
        else:
            members[list(members)[-1]][key] = value.strip()
    return members


def test_the_graph_declares_one_single_sided_worker_on_its_own_config_and_no_judge() -> None:
    members = _members()

    assert list(members) == [WORKER], (
        f"{GRAPH} declares {sorted(members)}, where the follow-up agent is one member"
    )
    assert members[WORKER] == DECLARED, (
        f"{GRAPH}'s `{WORKER}` declares {members[WORKER]}. It must be a single-sided "
        f"`kind: oneharness` member on {CONFIG}, naming {PERSONA} for its label, with no "
        "judge and no `task` of its own — a `task` would replace the composed task as the "
        "whole prompt, and a judge would layer a persona the composed task does not carry"
    )


def test_the_member_reads_its_own_config_and_never_the_judges() -> None:
    named = (REPO_ROOT / "graphs" / DECLARED["oneharness_config"]).resolve()

    assert named == (REPO_ROOT / CONFIG).resolve()
    assert named.is_file()
    assert named != (REPO_ROOT / "oneharness.judge.toml").resolve()


def test_the_mode_is_bypass_and_declared_in_the_config_alone() -> None:
    """The turn's working directory is no repository, so `default` mode leaves it unable to act."""
    declared = tomllib.loads((REPO_ROOT / CONFIG).read_text(encoding="utf-8")).get("mode")

    assert declared == "bypass", (
        f"{CONFIG} declares mode {declared!r}. The follow-up agent works in its graph's scratch "
        "directory, which is not a repository: in any mode but `bypass` codex refuses the turn "
        "as an untrusted directory and Claude Code is denied every tool it needs"
    )
    assert "mode" not in _members()[WORKER], (
        f"{GRAPH}'s `{WORKER}` declares a `mode`. The linked oneagentgraph admits none on a "
        f"single-sided member, and {CONFIG} is where the turn's mode is decided"
    )


def test_the_installed_graph_runner_accepts_the_graph() -> None:
    validated = subprocess.run(  # noqa: S603 - the pinned graph runner, validating a tracked graph
        [str(ONEAGENTGRAPH_BIN), "validate", str(REPO_ROOT / GRAPH)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert validated.returncode == 0, validated.stdout + validated.stderr
    assert "1 member(s) OK" in validated.stdout + validated.stderr


def test_the_persona_records_that_nothing_layers_it() -> None:
    """The persona says why it holds no instructions, so its next editor looks at the task."""
    header = (REPO_ROOT / PERSONA).read_text(encoding="utf-8").split("\n", 1)[0]

    assert header.startswith("# llmlint: ignore-file[changed_behavior_has_e2e]"), header
    assert GRAPH in header, f"{PERSONA}'s directive no longer names {GRAPH}"
    assert "config/follow-up-task.md" in header, (
        f"{PERSONA}'s directive no longer names the template the agent's instructions live in"
    )
    assert "tests/test_follow_up_agent_arrangement.py" in header
