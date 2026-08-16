# Personas

A **persona** is a small onejudge *delta* over `config/onejudge.base.yaml` that
defines one kind of agent: its role (`agent.instructions`) and how the
simulated supervisor reviews it (`user.persona`). At dispatch time,
`orchestrator.config` merges base ⊕ persona ⊕ the CLI `--task` into one effective
onejudge config and runs it. Common settings live once in the base; only the
role-specific parts live here.

## Catalog

| Persona | Use it for |
| --- | --- |
| `engineer` | General implementation and realistic testing across server-side systems, UI, accessibility, and contract-aware libraries. |
| `planner` | Decomposing work into an actionable, dependency-ordered plan (no implementation). |
| `orchestrator` | Actively monitoring an executing tracked graph: judging observed activity against the plan, surfacing what it finds, and applying only unambiguous in-allowlist fixes. |
| `check-in` | Synthesizing a read-only, durable-state-derived planner status update. |
| `docs-writer` | READMEs, reference docs, durable AGENTS.md notes. |
| `researcher` | Answering questions with evidence cited from the actual source. |
| `reviewer` | Reviewing and integrating several agents' independently produced work in a complex DAG. |
| `pr-author` | Drafting a terse, diff-derived PR body for a completed lifecycle change. |
| `crozier/crozier-corpus` | Crozier-specific corpus research and curation. |

The test suite requires these rows to match recursive persona discovery exactly.

## Adding a persona

Prefer a detailed task with an explicit `## Acceptance criteria` list — that list
*is* the node's review bar — plus `max_turns` when needed, over a new persona. Add
one only when a subtask needs a genuinely distinct general role or review bar. The
simulated-user supervisor reviews every dispatch already; the dedicated `reviewer`
is for multi-agent integration, not ordinary review of one change.

### Which of these files a dispatch actually reads

Only the ones a graph names **by path**. `graphs/dag-scope.yaml` points its
`orchestrator` and `check-in` members at `../personas/orchestrator.yaml` and
`../personas/check-in.yaml`, and those are the personas those members get.

A plan node is different. Its `persona` is a **name**, and `onepipeline` hands that
name to `oneagentgraph` as the node-scope worker's persona override, where a
built-in role of that name wins: `engineer`, `planner`, `reviewer`, `researcher`,
`docs-writer`, `orchestrator`, and `check-in` all resolve to roles compiled into the
tool, and this directory is not on the search path. Anything else is taken as a path
relative to `graphs/`, which is why the bare catalog name
`crozier/crozier-corpus` still fails a dispatch with
`cannot read graphs/crozier/crozier-corpus`.

Three consequences, all re-measured against onepipeline 0.6.3 by launching one-node
plans and reading the prompt each side of the dispatch was actually given:

- Editing `engineer.yaml` here does not change what an `engineer` node is dispatched
  with. The flat files whose names match a built-in are a catalog and a validation
  target, not the dispatch input. Measured by marking this file's `user.persona` and
  launching an `engineer` node: the marker reached no prompt in the run.
- **A repo-specific persona in this directory does dispatch — as a path.** Naming
  `../personas/crozier/crozier-corpus.yaml` on a node settles the dispatch with that
  file's `agent.instructions` as the worker's role and its `user.persona` as the
  supervisor's bar, both verbatim. Only the catalog *name* is unresolvable; the file
  is not inert. `oneagentgraph` 0.2.14 is the release that closed this, and the engine
  reaches it through onepipeline 0.6.3, which is the first release to link it.
- **A built-in role's own bar no longer replaces the shared one — it is added to it.**
  A `planner` node's completion criterion arrives as `Both of these must hold:` with
  `config/onejudge.base.yaml`'s "every acceptance criterion stated in the task is met"
  clause as (1) and the built-in role's own bar as (2). An `engineer` or `docs-writer`
  node, whose built-in role declares no bar, still gets the shared one alone. Same
  release, same reason.

Draft a new persona outside the tracked catalog first. Scaffold it under the
gitignored `scratch/personas/`, dispatch against that directory, and refine it in
place based on its task performance:

```sh
just new-persona <name> --persona-dir scratch/personas
# Then launch a one-node plan whose node names that persona as a path relative to
# graphs/:
just orchestrate scratch/draft.plan.json
```

Once proven, add it to this catalog through the orchestrator's isolated
self-dispatch lifecycle; do not edit the canonical checkout directly. General,
cross-repo roles use a flat name (`engineer`); repo-specific roles use a
slash-qualified name (`crozier/crozier-corpus`), stored as
`personas/crozier/crozier-corpus.yaml` — and dispatched by that *path* relative to
`graphs/` (`../personas/crozier/crozier-corpus.yaml`), never by the catalog name,
per the resolution measured above. `just new-persona <name>` creates either
layout, and `just validate-personas` checks the tracked catalog recursively.

## The delta contract

`just validate-personas` enforces the shape of every YAML persona recursively
under `personas/` (underscore-prefixed files and directories are skipped):

- **Required:** `agent.instructions` (string), `user.persona` (string).
- **Optional:** `agent.name`, `user.done_when`, `user.max_turns` (int), `evals`.
- No other top-level keys — `task` comes from `--task`, and `provider` / `session`
  / the shared agent preamble come from the base config.

See `docs/onejudge-integration.md` for how a persona becomes an effective onejudge
config and how the two conversation sides are wired.
