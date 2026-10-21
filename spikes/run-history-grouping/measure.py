"""Measure how reliably this host's conventions place each existing run under its plan project.

Spike harness, read-only. Run it from the repository root:

    .venv/bin/python spikes/run-history-grouping/measure.py [--json] [--runs-root DIR ...]

For every directory in each runs root (default: ``~/projects/ai-orchestrator/runs`` and
``~/ai-orchestrator/runs`` where it exists) it applies each grouping rule independently and
prints the kind and plan project each rule gives, whether they agree, the run's ended or
driven state and owning session, and the outcome: ``placed``, ``ambiguous`` (rules disagree,
or one rule gives several answers) or ``unplaced``.

What it reads, and nothing else:

* each run's ``launch.json`` and ``runs/.flows/<id>/members/*.json``;
* the plan store's local sources ``authoring`` and ``follow-up-runs``, through the locked
  ``onetaskgraph`` CLI's ``project list`` / ``task list`` with the checkout that owns the
  runs root as its working directory, so ``onetaskgraph.yaml``'s relative roots resolve
  there; the ``plans`` board is never read;
* the engine's ``onepipeline status <run> --json --no-providers`` and ``onepipeline runs
  --flat`` under ``ONEPIPELINE_RUNS_DIR=<root>`` for state and owner.

It writes nothing but its own standard output.

A plan project is named by its authoring project id (``authoring:<name>``), because that is
the one id every kind of run can reach: a planning run through its brief, a main run
launched from a ``plans:`` board copy through the authoring project's
``onetaskgraph.copies``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve()
REPO = HERE.parents[2]
BIN = REPO / ".venv" / "bin"
LOCAL_SOURCES = ("authoring", "follow-up-runs")

# The rules a backfill applies, in its order of precedence. Every rule is evaluated
# independently; the order matters only to `precedence`, never to `outcome`, which is the
# consensus of these rules alone.
RULES = (
    "copy-link",
    "brief-line",
    "task-title",
    "flow",
    "follow-ups",
    "plan-kind",
    "project-name*",
    "run-name*",
)
# The brief's name rules as stated, and the observed-convention extension: printed and
# checked against the consensus above, so a run they misplace is named, but not voted.
RAW_RULES = ("project-name", "run-name", "run-name+")
ALL_RULES = RULES + RAW_RULES
# Not in the spike's brief; measured beside the rules it lists.
EXTENSIONS = {"task-title", "project-name*", "run-name*", "run-name+"}

# The suffixes and prefix the brief names. The follow-ups suffix wraps any other.
FOLLOW_UPS = re.compile(r"^(?P<base>.+)-follow-ups(?:-(?P<n>\d+))?$")
SUFFIX_KIND = (
    ("-spike-brief", "spike-brief"),
    ("-spikes", "spikes"),
    ("-finalize", "finalize"),
    ("-design", "design"),
)
# The extension: conventions observed on this host that the brief does not list.
EXTRA_SUFFIX = re.compile(r"^(?P<base>.+?)-(?:repair|revise|plan)(?:-\d+)?$")

PLAN_LINE = re.compile(r"^Plan project:\s*(?P<p>[\w.-]+:[\w.-]+)\s*$", re.M)
TITLE_REF = re.compile(
    r"^(?:Write the design document for|Spike brief for)\s+(?P<p>[\w.-]+:[\w.-]+)\s*$"
)


@dataclass(frozen=True)
class Answer:
    """One rule's answer: a kind, a plan project, or both; ``None`` where it says nothing."""

    kind: str | None
    project: str | None
    why: str


@dataclass
class Store:
    projects: dict[str, dict[str, Any]] = field(default_factory=dict)  # id -> item
    tasks: dict[str, list[dict[str, Any]]] = field(default_factory=dict)  # id -> items
    copies: dict[str, list[str]] = field(default_factory=dict)  # board id -> authoring ids


@dataclass
class Run:
    root: Path
    run_id: str
    launch: dict[str, Any] | None
    flows: dict[str, list[str]]  # flow id -> every member run of that flow
    status: dict[str, Any] | None = None
    owner_tag: str | None = None
    answers: dict[str, list[Answer]] = field(default_factory=dict)


def sh(args: list[str], cwd: Path, env: dict[str, str] | None = None) -> str:
    done = subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True, check=False)
    if done.returncode != 0:
        raise RuntimeError(f"{' '.join(args)} exited {done.returncode}: {done.stderr.strip()}")
    return done.stdout


def load_store(checkout: Path) -> Store:
    store = Store()
    otg = str(BIN / "onetaskgraph")
    for source in LOCAL_SOURCES:
        for verb in ("project", "task"):
            page: str | None = None
            while True:
                args = [otg, verb, "list", "--source", source, "--json", "--limit", "10000"]
                if page:
                    args += ["--page", page]
                doc = json.loads(sh(args, checkout))
                if doc.get("errors"):
                    raise RuntimeError(f"{source} {verb} list: {doc['errors']}")
                for entry in doc["items"]:
                    item = entry["item"]
                    if verb == "project":
                        store.projects[entry["id"]] = item
                    else:
                        pid = f"{source}:{item.get('project') or ''}"
                        store.tasks.setdefault(pid, []).append(item)
                page = doc.get("next")
                if not page:
                    break
    for pid, item in store.projects.items():
        copies = (item.get("metadata") or {}).get("onetaskgraph.copies") or {}
        if isinstance(copies, dict):
            for target in copies.values():
                store.copies.setdefault(str(target), []).append(pid)
    return store


def load_runs(root: Path) -> list[Run]:
    flow_members: dict[str, list[str]] = defaultdict(list)
    flows_dir = root / ".flows"
    if flows_dir.is_dir():
        for record in sorted(flows_dir.glob("*/members/*.json")):
            flow_members[record.parent.parent.name].append(record.stem)
    flows_of: dict[str, dict[str, list[str]]] = defaultdict(dict)
    for flow, members in flow_members.items():
        for member in members:
            flows_of[member][flow] = members
    runs = []
    for entry in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")):
        launch_path = entry / "launch.json"
        launch = json.loads(launch_path.read_text()) if launch_path.is_file() else None
        runs.append(Run(root, entry.name, launch, flows_of.get(entry.name, {})))
    return runs


def read_state(runs: list[Run], checkout: Path) -> None:
    """Fill each run's engine status and the owner tag the engine's runs view prints."""
    op = str(BIN / "onepipeline")
    by_root: dict[Path, list[Run]] = defaultdict(list)
    for run in runs:
        by_root[run.root].append(run)
    for root, members in by_root.items():
        env = dict(os.environ, ONEPIPELINE_RUNS_DIR=str(root))
        # A session variable makes the view print `[mine]` instead of the owner's tag.
        env.pop("ONEPIPELINE_LAUNCHER_SESSION", None)
        tags: dict[str, str] = {}
        for line in sh([op, "runs", "--flat"], checkout, env).splitlines():
            m = re.match(r"^[ *]\s*(\S+)\s+\[([^\]]+)\]", line)
            if m:
                tags[m.group(1)] = m.group(2)

        def status(run: Run, env: dict[str, str] = env, tags: dict[str, str] = tags) -> None:
            run.owner_tag = tags.get(run.run_id)
            if run.launch is None:
                return
            try:
                out = sh([op, "status", run.run_id, "--json", "--no-providers"], checkout, env)
                run.status = json.loads(out)
            except (RuntimeError, json.JSONDecodeError) as err:
                run.status = {"word": "UNREADABLE", "driven": None, "error": str(err)[:200]}

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(status, members))


# ---------------------------------------------------------------------------- the rules


def strip_name(name: str) -> tuple[str | None, str]:
    """The brief's suffixes and prefix: ``(kind or None, base)``."""
    m = FOLLOW_UPS.match(name)
    if m:
        return "follow-ups", m.group("base")
    for suffix, kind in SUFFIX_KIND:
        if name.endswith(suffix):
            base = name[: -len(suffix)]
            return kind, base[len("brief-") :] if base.startswith("brief-") else base
    if name.startswith("brief-"):
        return "planning", name[len("brief-") :]
    return None, name


def strip_name_extended(name: str) -> tuple[str | None, str]:
    """The brief's conventions plus a ``plan-`` prefix and ``-repair``/``-revise``/``-plan``."""
    kind, base = strip_name(name)
    if kind == "follow-ups":
        return kind, base
    if base.startswith("plan-"):
        base = base[len("plan-") :]
        kind = kind or "planning"
    m = EXTRA_SUFFIX.match(base)
    if m:
        base = m.group("base")
        kind = kind or "planning"
    return kind, base


def is_planning_project(project: str, store: Store) -> bool:
    """A project that plans another (stamped, or a task naming a plan) is no plan itself."""
    item = store.projects.get(project) or {}
    if (item.get("metadata") or {}).get("orchestrator.plan-kind") is not None:
        return True
    return any(PLAN_LINE.search(task.get("content") or "") for task in store.tasks.get(project, []))


def name_answer(
    name: str, store: Store, *, extended: bool, refined: bool, what: str
) -> Answer | None:
    """A name's kind from its suffix, and ``authoring:<base>`` where that project exists.

    ``refined`` withholds the project when the base names a planning project: the name of a
    planning run names its own brief project, never the plan it plans.
    """
    kind, base = (strip_name_extended if extended else strip_name)(name)
    project: str | None = f"authoring:{base}"
    note = f"{what} base {base!r}" + (f", suffix gives {kind}" if kind else "")
    if project not in store.projects:
        note += "; no such project"
        project = None
    elif refined and is_planning_project(project, store):
        note += "; that project plans another, so it is no plan project"
        project = None
    if kind is None and project is None:
        return None
    return Answer(kind, project, note)


def launch_project(run: Run) -> str | None:
    return (run.launch or {}).get("project")


def launch_name(run: Run) -> str | None:
    proj = launch_project(run) or ""
    source, _, name = proj.partition(":")
    return name if source in LOCAL_SOURCES and name else None


def rule_run_name(run: Run, store: Store, *, extended: bool, refined: bool) -> list[Answer]:
    a = name_answer(run.run_id, store, extended=extended, refined=refined, what="run id")
    return [a] if a else []


def rule_project_name(run: Run, store: Store, *, refined: bool) -> list[Answer]:
    name = launch_name(run)
    if name is None:
        return []
    a = name_answer(name, store, extended=False, refined=refined, what="launch project")
    return [a] if a else []


def stamp_kind(stamp: Any) -> str | None:
    if isinstance(stamp, str):
        return stamp
    if not isinstance(stamp, dict):
        return None
    kind = stamp.get("kind")
    nodes = set(stamp.get("nodes") or [])
    if kind == "planning":
        if nodes == {"design-doc"}:
            return "design"
        if nodes == {"finalize"}:
            return "finalize"
        if nodes == {"spike-brief"}:
            return "spike-brief"
        return "planning"
    return str(kind) if kind else None


def rule_plan_kind(run: Run, store: Store) -> list[Answer]:
    """The launch project's stamp; an authoring project with none is read as a main plan."""
    proj = launch_project(run) or ""
    item = store.projects.get(proj)
    if item is None:
        return []
    stamp = (item.get("metadata") or {}).get("orchestrator.plan-kind")
    kind = stamp_kind(stamp)
    if kind is None:
        if is_planning_project(proj, store):
            return []
        return [Answer("main", None, f"{proj} carries no plan-kind stamp and plans nothing")]
    return [Answer(kind, None, f"{proj} stamped {json.dumps(stamp, separators=(',', ':'))}")]


def brief_lines(proj: str, store: Store) -> set[str]:
    found = {
        m.group("p")
        for task in store.tasks.get(proj, [])
        for m in PLAN_LINE.finditer(task.get("content") or "")
    }
    found.discard(proj)  # a main plan's tasks quoting their own project say nothing new
    return found


def rule_brief_line(run: Run, store: Store) -> list[Answer]:
    proj = launch_project(run) or ""
    out = []
    for p in sorted(brief_lines(proj, store)):
        if p in store.projects:
            out.append(Answer(None, p, f"a task of {proj} says 'Plan project: {p}'"))
        else:
            # Kept visible, never voted: a brief naming a project nobody created places nothing.
            out.append(Answer(None, None, f"a task of {proj} names {p}, which no source holds"))
    return out


def rule_task_title(run: Run, store: Store) -> list[Answer]:
    proj = launch_project(run) or ""
    found = {
        m.group("p")
        for task in store.tasks.get(proj, [])
        if (m := TITLE_REF.match(task.get("title") or ""))
    }
    return [
        Answer(None, p if p in store.projects else None, f"a task of {proj} is titled for {p}")
        for p in sorted(found)
    ]


def rule_copy_link(run: Run, store: Store) -> list[Answer]:
    proj = launch_project(run) or ""
    if proj.startswith("plans:"):
        origins = store.copies.get(proj, [])
        if not origins:
            return [
                Answer(
                    "main", None, f"launched from board copy {proj}; no authoring project links it"
                )
            ]
        return [Answer("main", o, f"{o} copies to {proj}") for o in sorted(origins)]
    item = store.projects.get(proj)
    if item and (item.get("metadata") or {}).get("onetaskgraph.copies"):
        return [Answer("main", proj, f"{proj} itself carries onetaskgraph.copies")]
    return []


def rule_flow(run: Run, store: Store, runs: dict[str, Run]) -> list[Answer]:
    """A flow's runs share a group: the plan projects any member's brief names."""
    out = []
    for flow, members in run.flows.items():
        named: set[str] = set()
        for member in members:
            other = runs.get(member)
            if other is not None:
                named |= {
                    p
                    for p in brief_lines(launch_project(other) or "", store)
                    if p in store.projects
                }
        out += [
            Answer(None, p, f"member of flow {flow}, whose brief names {p}") for p in sorted(named)
        ]
    return out


def evaluate(run: Run, store: Store, runs: dict[str, Run]) -> None:
    run.answers = {
        "copy-link": rule_copy_link(run, store),
        "brief-line": rule_brief_line(run, store),
        "task-title": rule_task_title(run, store),
        "flow": rule_flow(run, store, runs),
        "plan-kind": rule_plan_kind(run, store),
        "project-name*": rule_project_name(run, store, refined=True),
        "run-name*": rule_run_name(run, store, extended=True, refined=True),
        "project-name": rule_project_name(run, store, refined=False),
        "run-name": rule_run_name(run, store, extended=False, refined=False),
        "run-name+": rule_run_name(run, store, extended=True, refined=False),
    }


def apply_follow_ups(runs: list[Run], by_id: dict[str, Run]) -> None:
    """The follow-ups rule: a follow-ups run takes the group of the run its id names.

    Bases are judged first — shortest id first, so a follow-ups of a follow-ups run
    inherits what its own base was already given.
    """
    for run in sorted(runs, key=lambda r: len(r.run_id)):
        m = FOLLOW_UPS.match(run.run_id)
        if not m:
            run.answers["follow-ups"] = []
            continue
        base = by_id.get(m.group("base"))
        if base is None:
            run.answers["follow-ups"] = [
                Answer(
                    "follow-ups", None, f"follows run {m.group('base')!r}, not in this runs root"
                )
            ]
            continue
        verdict = judge(base)
        if verdict["outcome"] == "placed":
            run.answers["follow-ups"] = [
                Answer(
                    "follow-ups", verdict["projects"][0], f"follows run {base.run_id}, placed there"
                )
            ]
        else:
            run.answers["follow-ups"] = [
                Answer(
                    "follow-ups", None, f"follows run {base.run_id}, which is {verdict['outcome']}"
                )
            ]


def judge(run: Run) -> dict[str, Any]:
    """Consensus over the backfill rules: one kind and one project, or why not."""
    voted = {rule: run.answers.get(rule, []) for rule in RULES}
    kinds = {a.kind for answers in voted.values() for a in answers if a.kind}
    projects = {a.project for answers in voted.values() for a in answers if a.project}
    multi = [
        r for r, answers in voted.items() if len({a.project for a in answers if a.project}) > 1
    ]
    reasons = []
    if run.launch is None:
        reasons.append("no launch.json: not a run root")
    if len(kinds) > 1:
        reasons.append("kinds disagree: " + ", ".join(sorted(kinds)))
    if len(projects) > 1:
        reasons.append("projects disagree: " + ", ".join(sorted(projects)))
    if multi:
        reasons.append("several answers from one rule: " + ", ".join(multi))
    if run.launch is None:
        outcome = "unplaced"
    elif len(kinds) > 1 or len(projects) > 1:
        outcome = "ambiguous"
    elif not projects or not kinds:
        outcome = "unplaced"
        reasons.append("no rule gives a " + ("plan project" if not projects else "kind"))
    else:
        outcome = "placed"
    first_project = next(
        ({"rule": r, "project": a.project} for r in RULES for a in voted[r] if a.project), None
    )
    first_kind = next(
        ({"rule": r, "kind": a.kind} for r in RULES for a in voted[r] if a.kind), None
    )
    contradicted = sorted(
        {
            rule
            for rule in ALL_RULES
            for a in run.answers.get(rule, [])
            if (first_project and a.project and a.project != first_project["project"])
            or (first_kind and a.kind and a.kind != first_kind["kind"])
        }
    )
    supported_by = sorted({r for r in RULES for a in voted[r] if a.project and len(projects) == 1})
    return {
        "outcome": outcome,
        "supported_by": supported_by,
        "kinds": sorted(kinds),
        "projects": sorted(projects),
        "reasons": reasons,
        "precedence_project": first_project,
        "precedence_kind": first_kind,
        "disagrees_with_precedence": contradicted,
    }


# ---------------------------------------------------------------------------- reporting


def state(run: Run) -> dict[str, Any]:
    launch = run.launch or {}
    status = run.status or {}
    return {
        "word": status.get("word"),
        "liveness": status.get("liveness"),
        "driven": status.get("driven"),
        "ended": (status.get("ending") or None) is not None,
        "launch_project": launch.get("project"),
        "session": f"{launch.get('launcher', '?')}:{launch.get('session', '?')}"
        if launch
        else None,
        "owner_tag": run.owner_tag,
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs-root", action="append", type=Path)
    parser.add_argument("--json", action="store_true", help="print one JSON document")
    args = parser.parse_args(argv)
    roots = args.runs_root or [
        p
        for p in (
            Path.home() / "projects/ai-orchestrator/runs",
            Path.home() / "ai-orchestrator/runs",
        )
        if p.is_dir()
    ]
    all_runs: list[Run] = []
    stores: dict[Path, Store] = {}
    for root in roots:
        checkout = root.parent
        stores[root] = load_store(checkout)
        runs = load_runs(root)
        read_state(runs, checkout)
        by_id = {r.run_id: r for r in runs}
        for run in runs:
            evaluate(run, stores[root], by_id)
        apply_follow_ups(runs, by_id)
        all_runs += runs

    rows = []
    for run in all_runs:
        verdict = judge(run)
        rows.append(
            {
                "root": str(run.root),
                "run_id": run.run_id,
                **state(run),
                "flows": sorted(run.flows),
                "rules": {
                    rule: [a.__dict__ for a in run.answers.get(rule, [])] for rule in ALL_RULES
                },
                **verdict,
            }
        )

    summary: Counter[tuple[str, str]] = Counter()
    for row in rows:
        kind = (row["precedence_kind"] or {}).get("kind") or "none"
        summary[(kind, row["outcome"])] += 1
    misplaced: Counter[str] = Counter(
        rule
        for row in rows
        if row["outcome"] == "placed"
        for rule in row["disagrees_with_precedence"]
    )
    if args.json:
        json.dump(
            {
                "roots": [str(r) for r in roots],
                "rules": list(RULES),
                "raw_rules": list(RAW_RULES),
                "extensions": sorted(EXTENSIONS),
                "contradicting_placed_runs": dict(sorted(misplaced.items())),
                "runs": rows,
                "summary": [
                    {"kind": k, "outcome": o, "runs": n} for (k, o), n in sorted(summary.items())
                ],
            },
            sys.stdout,
            indent=1,
        )
        print()
        return 0

    for row in rows:
        driven = "driven" if row["driven"] else ("ended" if row["ended"] else "not driven")
        print(f"== {row['run_id']}  [{row['outcome'].upper()}]")
        print(f"   state: {row['word']} ({driven}); owner {row['owner_tag']} / {row['session']}")
        print(
            f"   launch project: {row['launch_project']}; flows: {', '.join(row['flows']) or '-'}"
        )
        for rule in ALL_RULES:
            label = rule + ("" if rule in RULES else " (not voted)")
            answers = row["rules"][rule]
            if not answers:
                print(f"   {label:26} -")
            for a in answers:
                print(
                    f"   {label:26} kind={a['kind'] or '-'} project={a['project'] or '-'}"
                    f"  ({a['why']})"
                )
        agree = (
            f"the rules agree (project from {', '.join(row['supported_by'])})"
            if row["outcome"] == "placed"
            else "; ".join(row["reasons"])
        )
        print(f"   => kinds {row['kinds'] or '-'} projects {row['projects'] or '-'}: {agree}")
        if row["disagrees_with_precedence"]:
            print(
                "   => disagrees with the first-precedence answer: "
                + ", ".join(row["disagrees_with_precedence"])
            )
    print()
    print("summary (kind by first-precedence answer, outcome by consensus):")
    for (kind, outcome), n in sorted(summary.items()):
        print(f"   {kind:12} {outcome:10} {n}")
    print(f"   total runs {len(rows)}")
    print("rules contradicting the answer of a placed run:")
    for rule, n in sorted(misplaced.items()):
        print(f"   {rule:14} {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
