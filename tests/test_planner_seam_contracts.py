"""The contracts the manager/planner seam restates are reconciled with their source.

`scripts/plan-brief.sh` and `scripts/ask-manager.sh` are shell, and shell cannot import.
So each of them holds a copy of a contract that is owned somewhere else — the task
template `personas/planner.yaml` states, and the run grammar the installed
`onemessagebus` checks — and a copy is only sound while something reconciles it. The
third copy runs the other way: the ask shim declares what its environment must carry,
and the journeys that prove a launch builds it hold their own list of those names.

Every copy fails quietly if it drifts, which is why they are gated here rather than
reviewed. A `PLAN_REQUIRED_SECTIONS` that no longer matches the template lets a brief
through that is not a task, or refuses one that is. A `SAFE_RUN_ID` wider than the bus's
grammar composes a channel directory for a run the bus's own `onejudge` codec refuses to
serve, so a question waits on a channel no judge side will ever answer; one narrower
refuses to ask on a run the engine really launched. And an input the shim starts
requiring that no journey checks for is a launch path free to stop providing it — which
is exactly how a whole launch path came to export the seam nowhere at all, unnoticed for
every run this host had ever driven.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from orchestrator.root import REPO_ROOT

#: The grammar both planning entry points read a manager's brief through, and the prose
#: that says what a task is written in. `just plan` and `just finish-plan` are two
#: launches of one flow and each is given the same brief, so what a brief has to be is
#: stated once, in the helper they both source — a second copy in either script is a
#: launch path that could accept a brief the other refuses. That template is the
#: **planner's** half of the decomposition doctrine, so it lives in the persona that
#: travels with the dispatch rather than in `AGENTS.md`, which stays in this checkout;
#: `tests/test_decomposition_guidance.py` is what keeps it in exactly one of them.
PLAN_SCRIPT = REPO_ROOT / "scripts" / "plan-brief.sh"
TASK_TEMPLATE = REPO_ROOT / "personas" / "planner.yaml"

#: The shim a dispatched agent asks its manager through, which composes the run's channel
#: directory from the run id before handing the question to `onemessagebus ask`.
ASK_SCRIPT = REPO_ROOT / "scripts" / "ask-manager.sh"

#: The journeys that measure what each launch shape hands a dispatch, and the shape
#: their list of required inputs is written in. Read textually rather than imported: it
#: is a pytest module whose import would collect fixtures, and reading a declaration is
#: what every other gate in this file does.
LAUNCH_JOURNEYS = REPO_ROOT / "tests" / "ask_seam" / "test_launch_ask_seam_e2e.py"
CHECKED_INPUT = re.compile(r'Input\(\s*"([A-Z0-9_]+)"')

#: How `scripts/ask-manager.sh` declares an environment variable it cannot ask without,
#: in the `Environment:` block of its own header. The `(optional)` ones are deliberately
#: not matched: a launch that provides none of them is still a launch an agent can ask
#: from.
REQUIRED_INPUT = re.compile(r"^#\s+([A-Z0-9_]+)\s+\(required\)", re.MULTILINE)

#: `PLAN_REQUIRED_SECTIONS=("## What" "## Why" "## Acceptance criteria")`, read out of
#: the helper rather than restated, so this gate compares the shell's own list.
DECLARED_SECTIONS = re.compile(r"PLAN_REQUIRED_SECTIONS=\(([^)]*)\)")

#: How `personas/planner.yaml` names the same three, in the list that requires them of
#: every task: "Write every node's task — and every step's task — with these headings,
#: in this order:", followed by one bullet per heading. Deliberately stopping at
#: `## Additional info`: the same list states that fourth heading and states it as
#: conditional, so it is not one a brief can be refused for lacking. Matched as code
#: spans, so the prose around them can be reworded without this gate caring, and
#: matched against flattened prose, because the persona is a hard-wrapped YAML block
#: scalar and every one of these phrases is longer than the line it sits on.
TEMPLATE_LIST = re.compile(
    r"Write every node's task.*?in this order:(?P<named>.*?)`## Additional info`"
)
SPAN = re.compile(r"`(##[^`]+)`")

#: The shim's one declaration of what a run id may be before it is composed into a path.
SHIM_GRAMMAR = re.compile(r"^SAFE_RUN_ID='(?P<pattern>[^']+)'$", re.MULTILINE)

#: The bus's own statement of what a run id is: `is_safe_run` in the `onejudge` codec,
#: which refuses a frame whose run is not one word. Read at the tag of
#: `config/onemessagebus.version`, the command line the shim and the observer's judge side
#: both exec, whose `onemessagebus-agent` is built from the same commit.
BUS_GRAMMAR_SOURCE = "onemessagebus-agent/src/codec/onejudge.rs"
SAFE_RUN = re.compile(r"pub fn is_safe_run\(run: &str\) -> bool \{(?P<body>.*?)\n\}", re.DOTALL)
#: The two closures that body is made of — the first byte's, then every byte's — each
#: read as its binding and the condition it tests, up to the parenthesis closing it.
CLOSURE = re.compile(r"\.(?P<call>is_some_and|all)\(\|(?P<name>\w+)\|")
#: The three condition terms that body is written in, each naming the bytes it admits.
ALPHANUMERIC_TERM = "{name}.is_ascii_alphanumeric()"
EQUALS_TERM = re.compile(r"^{name} == b'(?P<byte>.)'$")
MATCHES_TERM = re.compile(r"^matches!\({name}, (?P<bytes>b'.'(?:\s*\|\s*b'.')*)\)$")

#: The stand-in for the bus the shim execs once it has accepted a run: it exits 0, so a
#: candidate the shim hands on reads as accepted and one it refuses exits 2 before this
#: runs. The published CLI is the only thing doubled; the shim's own check is what answers.
BUS_STAND_IN = "#!/bin/sh\nexit 0\n"

#: Every character a run id could begin or continue with that the two sides might
#: disagree about: the whole of ASCII but NUL, which no argv or environment value can
#: carry, and Latin-1 beyond it, where a locale-aware bracket range is widest.
CANDIDATES = tuple(chr(code) for code in range(1, 256))


def _flat(prose: str) -> str:
    """Collapse every run of whitespace, so a wrapped phrase reads as one line."""
    return " ".join(prose.split())


def _closing(text: str) -> str:
    """`text` up to the parenthesis that closes the call it sits inside."""
    depth = 0
    for at, character in enumerate(text):
        if character == "(":
            depth += 1
        elif character == ")":
            if depth == 0:
                return text[:at]
            depth -= 1
    raise AssertionError(f"an unclosed call in the bus's run grammar: {text!r}")


def _admitted(name: str, condition: str) -> frozenset[str]:
    """The ASCII characters one closure's condition admits, read term by term.

    Refuses a term it cannot read rather than skipping it: a grammar read as narrower
    than it is would pass a shim that refuses real runs.
    """
    admitted: set[str] = set()
    for term in (part.strip() for part in condition.split("||")):
        if term == ALPHANUMERIC_TERM.format(name=name):
            admitted.update(c for c in map(chr, range(128)) if c.isalnum())
        elif matched := re.match(EQUALS_TERM.pattern.format(name=name), term):
            admitted.add(matched["byte"])
        elif matched := re.match(MATCHES_TERM.pattern.format(name=name), term):
            admitted.update(re.findall(r"b'(.)'", matched["bytes"]))
        else:
            raise AssertionError(
                f"the bus's run grammar tests {term!r}, which this gate cannot read as a set "
                "of bytes; re-read `is_safe_run` and teach the gate the term"
            )
    return frozenset(admitted)


def _bus_grammar() -> tuple[frozenset[str], frozenset[str]]:
    """What the pinned bus admits as a run's first character and as every character."""
    from test_engine_contracts import Engine, _pinned_tag, _source

    bus = Engine("onemessagebus", _pinned_tag("onemessagebus"), "crates")
    declared = SAFE_RUN.search(_source(bus, BUS_GRAMMAR_SOURCE))
    assert declared is not None, (
        f"onemessagebus {bus.ref} no longer declares `is_safe_run` in {BUS_GRAMMAR_SOURCE}, "
        "so the grammar the shim restates has no source to be read from here"
    )
    closures = {
        found["call"]: _admitted(found["name"], _closing(declared["body"][found.end() :]).strip())
        for found in CLOSURE.finditer(declared["body"])
    }
    assert set(closures) == {"is_some_and", "all"} and declared["body"].count("&&") == 1, (
        "onemessagebus's `is_safe_run` is no longer one first-byte test and one every-byte "
        f"test joined by `&&`, which is the shape this gate reads: {declared['body']!r}"
    )
    return closures["is_some_and"], closures["all"]


def _shim_admits(candidates: list[str], directory: Path) -> set[str]:
    """Which candidates the shim itself accepts as a run id, asked by running it.

    Each candidate is handed over as `ONEPIPELINE_RUN_ID` under the host's own locale, so
    whatever the shim does to narrow a bracket range — or fails to — is what answers. A
    run of the real script rather than of its pattern, because the pattern alone cannot
    say which locale the shim matches it under.
    """
    stand_in = directory / "onemessagebus"
    stand_in.write_text(BUS_STAND_IN, encoding="utf-8")
    stand_in.chmod(0o755)
    bash = shutil.which("bash")
    assert bash is not None, "bash is not on this host's PATH"
    accepted = set()
    for candidate in candidates:
        environment = {
            **os.environ,
            "PATH": f"{directory}{os.pathsep}{os.path.dirname(bash)}",
            "ONEPIPELINE_RUN_ID": candidate,
            "ONEPIPELINE_RUNS_DIR": str(directory / "runs"),
        }
        asked = subprocess.run(
            [bash, str(ASK_SCRIPT), "Which base?"],
            env=environment,
            capture_output=True,
            timeout=60,
            check=False,
        )
        assert asked.returncode in (0, 2), (
            f"{ASK_SCRIPT.name} exited {asked.returncode} for run id {candidate!r}: "
            f"{asked.stderr.decode(errors='replace')}"
        )
        if asked.returncode == 0:
            accepted.add(candidate)
    return accepted


def test_the_brief_template_the_plan_recipe_requires_is_the_one_the_doctrine_states() -> None:
    """`just plan` refuses exactly the sections a task is required to have.

    The brief a manager writes IS the dispatched task, so the recipe checks it against
    the template every task here is written in — and holds the copy of that template in
    shell, where nothing can import the prose that owns it. Drift in either direction
    is silent and costly: a section dropped here lets a brief through that dispatches a
    planner with no review bar, and one added here refuses a brief that is a perfectly
    good task — which is why `## Additional info`, stated in the same list but stated as
    conditional, is on neither side of this comparison.
    """
    declared = DECLARED_SECTIONS.search(PLAN_SCRIPT.read_text(encoding="utf-8"))
    assert declared is not None, f"{PLAN_SCRIPT.name} declares no PLAN_REQUIRED_SECTIONS"
    required = tuple(re.findall(r'"([^"]+)"', declared.group(1)))
    assert required, f"{PLAN_SCRIPT.name} requires no section at all"

    listed = TEMPLATE_LIST.search(_flat(TASK_TEMPLATE.read_text(encoding="utf-8")))
    assert listed is not None, (
        f"{TASK_TEMPLATE.name} no longer states the task template in the list this gate "
        "reads it from; update the pattern here together with that list"
    )
    stated = tuple(SPAN.findall(listed.group("named")))

    assert required == stated, (
        f"{PLAN_SCRIPT.name} requires {list(required)} of a brief, but "
        f"{TASK_TEMPLATE.name} states a task is written with {list(stated)}"
    )


# llmlint: ignore[test_tiers_split_by_project_not_by_marker] `reads_checkouts` moves this one
# gate into the uncached `orchestrator:test-checkouts`, because its subject — the bus's own
# source at the pinned tag, in a checkout `config/onevcs.checkouts` registers — is outside
# this workspace and no `nx.json` glob hashes it; `tests/conftest.py`'s checkout guard states
# that reasoning where it enforces the marker.
@pytest.mark.reads_checkouts
def test_the_run_ids_the_ask_shim_accepts_are_the_ones_the_bus_accepts(tmp_path: Path) -> None:
    """A run id is the same word to the ask shim and to the bus it hands the question to.

    The shim checks the run before composing `<runs root>/<run>/channel` from it; the bus
    checks it before serving a frame on that run. So the two are compared character by
    character, as a run's first character and as every later one, with the shim's side
    answered by running the shim — a locale-aware bracket range is a real way for the
    shell to admit a letter the bus's ASCII test refuses.
    """
    written = SHIM_GRAMMAR.search(ASK_SCRIPT.read_text(encoding="utf-8"))
    assert written is not None, f"{ASK_SCRIPT.name} declares no SAFE_RUN_ID"
    pattern = written["pattern"]
    first, every = _bus_grammar()
    assert "a" in first and "a" in every, (
        "the bus no longer admits `a` in a run id, so this gate's probe for every later "
        "character — `a` followed by it — asks the wrong question"
    )

    accepted = _shim_admits(["", *CANDIDATES, *(f"a{c}" for c in CANDIDATES)], tmp_path)

    assert "" not in accepted, f"{ASK_SCRIPT.name} accepts an empty run id, which the bus refuses"
    for position, shim, bus in (
        ("first", {c for c in CANDIDATES if c in accepted}, set(first)),
        ("later", {c for c in CANDIDATES if f"a{c}" in accepted}, set(every)),
    ):
        assert shim == bus, (
            f"as a run id's {position} character, {ASK_SCRIPT.name}'s {pattern!r} admits "
            f"{sorted(shim - bus)} the bus refuses and refuses {sorted(bus - shim)} the bus "
            "admits; the shim would ask on a channel no judge side serves, or refuse a real run"
        )


def test_every_input_the_wrapper_requires_is_one_a_launch_is_measured_for() -> None:
    """A launch is proven to build exactly what the shim refuses without.

    The shim's header names each variable it cannot ask without, and the journeys name
    each one they read out of a real dispatch's environment. Only one direction is an
    error: a required input nothing measures is a launch path free to drop it, and the
    failure lands on some agent's first blocking question rather than in the suite. The
    journeys may check *more* than the shim strictly requires — the seam itself is one
    such name, since a shim never reads the variable that names it.
    """
    required = set(REQUIRED_INPUT.findall(ASK_SCRIPT.read_text(encoding="utf-8")))
    assert required, (
        f"{ASK_SCRIPT.name}'s header declares no required environment at all; this gate "
        "reads the `(required)` lines of its `Environment:` block"
    )

    measured = set(CHECKED_INPUT.findall(LAUNCH_JOURNEYS.read_text(encoding="utf-8")))

    assert required <= measured, (
        f"{ASK_SCRIPT.name} requires {sorted(required - measured)} of the environment a "
        f"launch builds, and {LAUNCH_JOURNEYS.name} measures no launch shape for it"
    )
