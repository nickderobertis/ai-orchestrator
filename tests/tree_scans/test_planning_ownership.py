"""What of this host serves planning, held to the list `AGENTS.md` states.

This host's planning is configuration: `templates/planning/`'s plan graphs, the judged rules
of `config/plan-checklist.llmlint.yml`, and the launch config's check and plan-edit hook. The
host code left serving it is a short named list — three residue files computing something
no library holds, and thin wrappers composing library verbs — stated once under
`AGENTS.md`'s **What of this host serves planning.** This gate reconciles that list with
the tree:

* **Retired means gone.** No tracked file names a module or script the cutover deleted
  (`RETIRED_MODULES`, `RETIRED_SCRIPTS`, `RETIRED_FILES`), outside recorded fixtures. No
  recipe was retired: every planning recipe kept its name and became a thin wrapper.
* **Only the named files speak planning.** Every tracked file under `orchestrator/` or
  `scripts/` that invokes a planning verb of the engine, store or graph runner
  (`PLANNING_VERB`) or reads or writes task, project or document records (`RECORD_VERB`),
  outside its comments and docstrings, is residue, a thin wrapper, or a shared module
  `SHARED` lists with the purpose that is not planning.
* **A wrapper composes and decides nothing.** Each thin wrapper's commands are library
  verbs and the shell's own plumbing; it runs no program `FOREIGN` names and sources no
  helper outside `SHARED`. The planning recipes likewise run only library verbs and named wrappers.
* **Nothing reached hides a rule.** Walking everything the planning road reaches — each
  planning recipe's body, every command a planning template's command step or a launch
  config names, then every script those exec or source and every module they import —
  finds only residue, wrappers and `SHARED` files, and no `SHARED` file invokes a planning
  verb except where its entry says why.
* **`AGENTS.md` says so.** It names each residue file with what it computes and each
  wrapper with the verbs it calls, every verb named being one its file calls, and it no
  longer says a correction to a node a review cleared is checked deterministically only.

It reads every tracked file, so every test here is `reads_docs`, the `tree-scans` project's
whole-workspace tier.
"""

from __future__ import annotations

import ast
import io
import re
import subprocess
import tokenize
from collections.abc import Iterator
from functools import cache
from pathlib import Path
from typing import NamedTuple

import pytest

from orchestrator.root import REPO_ROOT

pytestmark = pytest.mark.reads_docs

#: The `orchestrator/` modules the cutover deleted.
RETIRED_MODULES = (
    "accepted_opaque",
    "adoption_guard",
    "criteria_guard",
    "design_approval",
    "design_chain",
    "design_review",
    "envelope_review",
    "plan_budgets",
    "plan_check",
    "plan_checklist_budgets",
    "plan_copy",
    "plan_locations",
    "plan_records",
    "plan_review",
    "plan_units",
    "planned_tasks",
    "publication_guard",
    "spike_branches",
    "spike_brief",
    "spike_brief_budgets",
    "spike_brief_flow",
    "spike_flow",
    "spike_plan",
    "structural_guard",
    "task_body",
)
#: The `scripts/` files the cutover deleted, by stem.
RETIRED_SCRIPTS = (
    "envelope-review",
    "envelope-review-bar",
    "finish-plan",
    "launch-flow",
    "plan",
    "plan-brief",
    "plan-check",
    "plan-checklist",
    "plan-root-env",
    "review-plan",
    "spike-brief-ready",
)
#: Other files the cutover deleted, by path.
RETIRED_FILES = (
    "config/budgets-migration.yaml",
    "config/plan-review-verdict.schema.json",
    "docs/plan-review-refusals.json",
    "docs/plan-review-refusals.md",
)
#: Tracked trees that are recorded data rather than prose or code: a run's journal, a
#: model's transcript, a store snapshot, quoting what was true when it was recorded.
RECORDED = ("tests/fixtures/",)

#: The residue: host code computing something no library holds.
RESIDUE = (
    "scripts/render-diagram.sh",
    "orchestrator/host_adoption_check.py",
    "scripts/budget-design-doc-summary.sh",
)
#: The thin wrappers, as `AGENTS.md` names them.
WRAPPERS = (
    "scripts/review-plan-item.sh",
    "scripts/plan-attestation-check.sh",
    "templates/planning/planning.md.j2",
    "scripts/run-ended.sh",
    "justfile",
)
#: The planning recipes.
RECIPES = (
    "plan",
    "refresh-plan",
    "finish-plan",
    "check-plan",
    "review-plan",
    "copy-plan",
    "approve-design",
)


#: Shared host modules the planning road reaches, each with the purpose it serves that is
#: not planning. A module here holds no planning rule; where one matches `PLANNING_VERB` or
#: `RECORD_VERB` anyway, its entry says why.
class Shared(NamedTuple):
    """A shared module the planning road reaches: the purpose it serves that is not planning,
    and, where it matches a planning pattern anyway, why."""

    purpose: str
    matches: str | None = None


SHARED: dict[str, Shared] = {
    "scripts/onepipeline.sh": Shared(
        "the engine wrapper every verb goes through: the pinned binary, the template root, "
        "the acting session and the launch config",
        matches=(
            "it defaults `plan check` to `--require-rendered true`, the engine's own "
            "rendered-only check, which a `start` reads from the launch config instead"
        ),
    ),
    "scripts/orchestrate.sh": Shared("the orchestrate recipe's launch defaults and labels"),
    "scripts/plan-store.sh": Shared(
        "establishes the board credential, then runs the store verb given"
    ),
    "scripts/credentials-env.sh": Shared("loads the credentials every launch and board verb uses"),
    "scripts/require-provisioned.sh": Shared("refuses a checkout whose locked tools are absent"),
    "scripts/template-env.sh": Shared("exports this checkout's template root to the engine"),
    "scripts/launcher-session.sh": Shared("names the acting manager session"),
    "scripts/ask-manager-env.sh": Shared("exports the ask-manager seam every launch carries"),
    "scripts/decline-ruling-env.sh": Shared(
        "exports the decline-ruling seam the judge config's run-level grant names"
    ),
    "scripts/follow-up-draft.sh": Shared("drafts one follow-up into the drafts source"),
    "scripts/dispatch-env-hook.sh": Shared(
        "the engine's dispatch-env hook: each dispatch's environment"
    ),
    "scripts/dispatch-env.sh": Shared("composes each dispatch's environment"),
    "scripts/lock-timeout.sh": Shared("exports the publication lock timeout"),
    "scripts/nx.sh": Shared("runs Nx for the checks a dispatch's gate measures"),
    "scripts/preserved-log.sh": Shared("keeps a stage's redacted log"),
    "scripts/setup-llmlint.sh": Shared("installs the judged lint tier"),
    "orchestrator/plan_store.py": Shared(
        "the locked plan-store client the host's own readers read through",
        matches=(
            "it is the store client itself: every read the host's own readers make goes through it"
        ),
    ),
    "orchestrator/host_installs.py": Shared(
        "the table of what this host installs, read by `host_adoption_check.py` and the "
        "release-pin tests"
    ),
    "orchestrator/record_shapes.py": Shared(
        "normalized origins and record ids every reader shares"
    ),
    "orchestrator/run_reading.py": Shared("reads a run's settlement, as the engine publishes it"),
    "orchestrator/npm_installs.py": Shared("the engine's npm install, every verb's binary"),
    "orchestrator/gate_impact.py": Shared("measures what the gate leaves on the host"),
    "orchestrator/redaction.py": Shared("the redaction rule every kept log is written under"),
    "orchestrator/root.py": Shared("this checkout's root"),
}

#: A planning verb of the engine, the store or the graph runner.
PLANNING_VERB = re.compile(
    r"\bplan check\b|\bproject (?:create|copy|attest)\b|\battestation check\b"
    r"|\b(?:task|document) attest\b|\bonepipeline(?:\.sh)?\"? attest\b|--judge-only"
)
#: A read or write of task, project or document records, through the CLI or the SDK.
RECORD_VERB = re.compile(
    r"\b(?:task|project|document) (?:list|show|create|update|render|answers|graph|metadata)\b"
    r"|client\(\)\.(?:task|project|document)_"
)
#: A program a thin wrapper may not run: an interpreter or a tool that would hold logic of
#: its own. A wrapper composes library verbs with the shell's own plumbing — `jq`, `sed`,
#: `head`, `cat`, `mktemp` — and nothing else.
FOREIGN = re.compile(
    r"(?:^|[|;&(]|\$\(|\bthen|\bdo|\belse|\bexec)\s*"
    r"(python3?|uv|node|bun|npx|perl|ruby|awk|gawk|gh|curl|git)\b",
    re.MULTILINE,
)
#: Every path a thin wrapper may run, besides the named wrappers and `SHARED` helpers: the
#: locked library CLIs.
LIBRARY_PATHS = frozenset({".venv/bin/onetaskgraph", ".venv/bin/oneagentgraph"})


def _tracked() -> list[str]:
    listed = subprocess.run(
        ["git", "ls-files", "-z"], cwd=REPO_ROOT, capture_output=True, check=True
    ).stdout
    return [path for path in listed.decode().split("\0") if path]


@cache
def _text(path: str) -> str:
    try:
        return (REPO_ROOT / path).read_text(encoding="utf-8")
    except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
        return ""


@cache
def _code(path: str) -> str:
    """``path``'s text without its comments, and a Python file's without its docstrings."""
    text = _text(path)
    if path.endswith(".py"):
        try:
            tree = ast.parse(text)
        except SyntaxError:
            return text
        lines = text.splitlines()
        documented = set()
        for node in ast.walk(tree):
            body = getattr(node, "body", None)
            if isinstance(body, list) and body and isinstance(body[0], ast.Expr):
                value = body[0].value
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    documented.update(range(body[0].lineno - 1, body[0].end_lineno or 0))
        for token in tokenize.generate_tokens(io.StringIO(text).readline):
            if token.type == tokenize.COMMENT:
                row = token.start[0] - 1
                lines[row] = lines[row][: token.start[1]]
        return "\n".join(line for index, line in enumerate(lines) if index not in documented)
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def _retired_pattern() -> re.Pattern[str]:
    names = "|".join(RETIRED_MODULES)
    stems = "|".join(re.escape(stem) for stem in RETIRED_SCRIPTS)
    files = "|".join(re.escape(path) for path in RETIRED_FILES)
    return re.compile(
        rf"orchestrator[./](?:{names})\b"
        rf"|from orchestrator import [^\n]*\b(?:{names})\b"
        rf"|(?<![\w-])scripts/(?:{stems})\.sh\b"
        rf"|{files}"
    )


#: How a history note says it is one: it names what the planning cutover retired.
HISTORY_NOTE = re.compile(
    r"\bplanning cutover (?:retired|deleted)\b|\b(?:retired|deleted) (?:by|in|with) the planning"
    r" cutover\b"
)


def _paragraphs(text: str) -> Iterator[tuple[int, list[str]]]:
    """Each run of non-blank lines of ``text``, with the number of its first line."""
    block: list[str] = []
    start = 1
    for number, line in enumerate(text.splitlines(), start=1):
        if line.strip():
            if not block:
                start = number
            block.append(line)
        elif block:
            yield start, block
            block = []
    if block:
        yield start, block


def test_no_tracked_file_names_a_retired_module_script_or_file() -> None:
    """Outside recorded fixtures and history notes — a paragraph saying what the planning
    cutover retired or deleted, which `HISTORY_NOTE` recognizes."""
    retired = _retired_pattern()
    this = str(Path(__file__).resolve().relative_to(REPO_ROOT))
    found = [
        f"{path}:{start + offset}: {line.strip()[:160]}"
        for path in _tracked()
        if path != this and not path.startswith(RECORDED)
        for start, block in _paragraphs(_text(path))
        if not HISTORY_NOTE.search(re.sub(r"[\s#:]+", " ", " ".join(block)))
        for offset, line in enumerate(block)
        if retired.search(line)
    ]
    assert not found, "these name what the planning cutover deleted:\n" + "\n".join(found)


def test_every_retired_file_is_gone() -> None:
    present = [
        path
        for path in (
            *(f"orchestrator/{name}.py" for name in RETIRED_MODULES),
            *(f"scripts/{stem}.sh" for stem in RETIRED_SCRIPTS),
            *RETIRED_FILES,
        )
        if (REPO_ROOT / path).exists()
    ]
    assert not present, present


def _speaks_planning(path: str) -> str | None:
    code = _code(path)
    for pattern in (PLANNING_VERB, RECORD_VERB):
        if found := pattern.search(code):
            return found.group(0)
    return None


def test_only_the_named_files_invoke_a_planning_verb_or_touch_records() -> None:
    named = {*RESIDUE, *WRAPPERS}
    unnamed = [
        f"{path}: {verb!r}"
        for path in _tracked()
        if path.startswith(("orchestrator/", "scripts/"))
        and path not in named
        and (verb := _speaks_planning(path)) is not None
        and not (path in SHARED and SHARED[path].matches is not None)
    ]
    assert not unnamed, (
        "these files speak planning but are neither residue, a wrapper, nor a shared module "
        "whose `SHARED` entry says why it matches:\n" + "\n".join(unnamed)
    )


def test_every_shared_entry_that_says_why_it_matches_still_does() -> None:
    stale = [
        path
        for path, entry in SHARED.items()
        if entry.matches is not None and _speaks_planning(path) is None
    ]
    assert not stale, f"these no longer match, so their reason is stale: {stale}"


def _recipe(name: str) -> str:
    """The body of the justfile recipe ``name``."""
    lines = _text("justfile").splitlines()
    header = re.compile(rf"^{re.escape(name)}(?: [^:]*)?:")
    body: list[str] = []
    inside = False
    for line in lines:
        if not inside:
            inside = bool(header.match(line))
            continue
        if line and not line[0].isspace():
            break
        body.append(line)
    assert body, f"the justfile has no recipe {name!r}"
    return "\n".join(body)


def _spikes_command() -> str:
    text = _text("templates/planning/planning.md.j2")
    start = text.index("onepipeline.id: spikes")
    return text[start : text.index("  - id:", start)]


def _discard_lines() -> str:
    text = _text("scripts/run-ended.sh")
    start = text.index("discard_spike_branches() {")
    return text[start : text.index("\n}\n", start)]


def _wrapper_bodies() -> Iterator[tuple[str, str]]:
    for path in ("scripts/review-plan-item.sh", "scripts/plan-attestation-check.sh"):
        yield path, _text(path)
    yield "templates/planning/planning.md.j2 (spikes)", _spikes_command()
    yield "scripts/run-ended.sh (spike discard)", _discard_lines()
    for recipe in RECIPES:
        yield f"justfile ({recipe})", _recipe(recipe)


@pytest.mark.parametrize(("where", "body"), list(_wrapper_bodies()), ids=lambda one: str(one)[:40])
def test_a_thin_wrapper_composes_only_library_verbs(where: str, body: str) -> None:
    code = "\n".join(line for line in body.splitlines() if not line.lstrip().startswith("#"))
    foreign = sorted({found.group(1) for found in FOREIGN.finditer(code)})
    assert not foreign, f"{where} runs {foreign}, which is neither a library verb nor plumbing"
    assert not re.search(
        r"-m orchestrator|orchestrator/\w+\.py",
        code.replace("orchestrator/host_adoption_check.py", ""),
    ), f"{where} runs host Python other than the residue"
    named = {*RESIDUE, *WRAPPERS, *SHARED, *LIBRARY_PATHS}
    paths = set(re.findall(r"(?:\.venv/bin/\w+|scripts/[\w.-]+\.sh)", code))
    paths |= {f"scripts/{one}" for one in re.findall(r"\$script_dir/([\w.-]+\.sh)", code)}
    unnamed = sorted(path for path in paths if path not in named)
    assert not unnamed, f"{where} runs {unnamed}, which is no library verb or named helper"


def _references(path: str) -> set[str]:
    """Every file under `orchestrator/` or `scripts/` that ``path`` execs, sources or imports."""
    code = _code(path)
    found: set[str] = set()
    if path.endswith(".py"):
        try:
            tree = ast.parse(_text(path))
        except SyntaxError:
            tree = None
        for node in ast.walk(tree) if tree else ():
            match node:
                case ast.ImportFrom(module=str(module), names=names) if module.startswith(
                    "orchestrator"
                ):
                    parts = "/".join(module.split("."))
                    found.add(f"{parts}.py")
                    found.update(f"{parts}/{alias.name}.py" for alias in names)
                case ast.Import(names=names):
                    found.update(
                        alias.name.replace(".", "/") + ".py"
                        for alias in names
                        if alias.name.startswith("orchestrator.")
                    )
    for match in re.finditer(r"(?:scripts/|\$script_dir/|\"\$script_dir\"/)([\w.-]+\.sh)", code):
        found.add(f"scripts/{match.group(1)}")
    for match in re.finditer(r"(orchestrator/[\w/]+\.py)|-m (orchestrator(?:\.\w+)+)", code):
        found.add(match.group(1) or match.group(2).replace(".", "/") + ".py")
    return {one for one in found if (REPO_ROOT / one).is_file()}


def _reached() -> set[str]:
    seeds: set[str] = set()
    for recipe in RECIPES:
        seeds |= _references_in(_recipe(recipe))
    for template in sorted((REPO_ROOT / "templates" / "planning").glob("*.j2")):
        seeds |= _references_in(_text(str(template.relative_to(REPO_ROOT))))
    for config in ("config/onepipeline.launch.yaml", "config/onepipeline.workflows.yaml"):
        seeds |= _references_in(_code(config))
    reached: set[str] = set()
    pending = sorted(seeds)
    while pending:
        one = pending.pop()
        if one in reached:
            continue
        reached.add(one)
        pending.extend(sorted(_references(one) - reached))
    return reached


def _references_in(text: str) -> set[str]:
    found = {f"scripts/{name}" for name in re.findall(r"scripts/([\w.-]+\.sh)", text)}
    found |= set(re.findall(r"orchestrator/[\w/]+\.py", text))
    return {one for one in found if (REPO_ROOT / one).is_file()}


def test_everything_the_planning_road_reaches_is_named() -> None:
    reached = _reached()
    assert "scripts/review-plan-item.sh" in reached and "scripts/run-ended.sh" in reached, reached
    unnamed = sorted(reached - {*RESIDUE, *WRAPPERS, *SHARED})
    assert not unnamed, (
        "the planning road reaches these, which are neither residue, a wrapper, nor a shared "
        "module `SHARED` lists with its purpose:\n" + "\n".join(unnamed)
    )


def test_every_shared_entry_is_a_tracked_file() -> None:
    tracked = set(_tracked())
    assert not [path for path in SHARED if path not in tracked], sorted(set(SHARED) - tracked)


def _ownership_section() -> str:
    text = _text("AGENTS.md")
    start = text.index("**What of this host serves planning.**")
    return text[start : text.index("\n\n", text.index("\n- ", start))]


def _bullets() -> dict[str, str]:
    bullets = {}
    for line in _ownership_section().splitlines():
        if found := re.match(r"- `([^`]+)` — (.+)", line):
            bullets[found.group(1)] = found.group(2)
    return bullets


def test_agents_md_names_each_residue_file_with_what_it_computes() -> None:
    bullets = _bullets()
    for path in RESIDUE:
        assert path in bullets, f"AGENTS.md's ownership list does not name {path}"
        assert len(bullets[path].split()) >= 5, (path, bullets[path])


def test_agents_md_names_each_wrapper_with_verbs_it_calls() -> None:
    bullets = _bullets()
    assert set(bullets) == {*RESIDUE, *WRAPPERS}, sorted(bullets)
    for path in WRAPPERS:
        named = re.findall(
            r"`((?:onetaskgraph|onepipeline|oneagentgraph|llmlint|just) [^`]+)`", bullets[path]
        )
        assert named, f"AGENTS.md names no verb for {path}"
        code = _code(path) if path != "justfile" else "\n".join(_recipe(one) for one in RECIPES)
        for verb in named:
            words = verb.split()[1:]
            phrase = " ".join(word for word in words if not word.startswith("--"))
            flags = [word for word in words if word.startswith("--")]
            assert phrase in code or not phrase, f"{path} does not call `{verb}`"
            for flag in flags:
                assert flag in code, f"{path} does not pass {flag} (`{verb}`)"
    assert "`tests/tree_scans/test_planning_ownership.py` holds it" in re.sub(
        r"\s+", " ", _text("AGENTS.md")
    )


def test_agents_md_no_longer_says_a_cleared_correction_is_checked_deterministically() -> None:
    text = re.sub(r"\s+", " ", _text("AGENTS.md"))
    sentences = re.split(r"(?<=[.!?])\s", text)
    stale = [
        sentence
        for sentence in sentences
        if re.search(r"\bdeterministic", sentence, re.IGNORECASE)
        and re.search(r"\b(?:clear(?:ed|s)?|already reviewed)\b", sentence, re.IGNORECASE)
        and re.search(r"\b(?:correction|amend|retry|requeue|edit)", sentence, re.IGNORECASE)
    ]
    assert not stale, stale
    assert "plan_edit_hook" in text and "scripts/review-plan-item.sh" in text
