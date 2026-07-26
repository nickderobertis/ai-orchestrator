# Personas

A **persona** is a small onejudge *delta* over `config/onejudge.base.yaml` that
defines one kind of agent: its role (`agent.instructions`) and how the
simulated supervisor reviews it (`user.persona`). At dispatch time,
`orchestrator.config` merges base ⊕ persona ⊕ the CLI `--task` into one effective
onejudge config and runs it. Common settings live once in the base; only the
role-specific parts live here.

## Catalog

`orchestrator-validate-personas` is the drift gate for this catalog: validation
fails unless its rows exactly match recursive persona discovery.

| Persona | Use it for |
| --- | --- |
| `engineer` | General implementation and realistic testing across server-side systems, UI, accessibility, and contract-aware libraries. |
| `planner` | Decomposing work into an actionable, dependency-ordered plan (no implementation). |
| `orchestrator` | Executing a tracked graph round by round under a live planner's supervision. |
| `check-in` | Sending a read-only, durable-state-derived planner status update. |
| `docs-writer` | READMEs, reference docs, durable AGENTS.md notes. |
| `researcher` | Answering questions with evidence cited from the actual source. |
| `reviewer` | Reviewing and integrating several agents' independently produced work in a complex DAG. |
| `pr-author` | Drafting a terse, diff-derived PR body for a completed lifecycle change. |
| `crozier/crozier-corpus` | Crozier-specific corpus research and curation. |

The test suite requires these rows to match recursive persona discovery exactly.

## Adding a persona

Prefer a detailed task with explicit per-node `done_when` acceptance criteria (and
`max_turns` when needed) over a new persona. Add one only when a subtask needs a
genuinely distinct general role or review bar. The simulated-user supervisor
reviews every dispatch already; the dedicated `reviewer` is for multi-agent
integration, not ordinary review of one change.

Draft a new persona outside the tracked catalog first. Scaffold it under the
gitignored `scratch/personas/`, dispatch against that directory, and refine it in
place based on its task performance:

```sh
just new-persona <name> --persona-dir scratch/personas
just dispatch <name> "<task>" --persona-dir scratch/personas
```

Once proven, add it to this catalog through the orchestrator's isolated
self-dispatch lifecycle; do not edit the canonical checkout directly. General,
cross-repo roles use a flat name (`engineer`); repo-specific roles use a
slash-qualified name (`crozier/crozier-corpus`), stored as
`personas/crozier/crozier-corpus.yaml`. `just new-persona <name>` creates either
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
