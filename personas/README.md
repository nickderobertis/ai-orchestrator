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
| `planner` | Decomposing work into a dependency-ordered plan cut at contract seams, asking the manager at every fork and recording its exceptions, under a judge that holds the plan to the original goals (no implementation). |
| `orchestrator` | Actively monitoring an executing tracked graph: judging observed activity against the plan, surfacing what it finds, and applying only unambiguous in-allowlist fixes. |
| `check-in` | Synthesizing a read-only, durable-state-derived planner status update. |
| `docs-writer` | READMEs, reference docs, durable AGENTS.md notes. |
| `researcher` | Answering questions with evidence cited from the actual source. |
| `reviewer` | Reviewing and integrating several agents' independently produced work in a complex DAG. |
| `pr-author` | Names the role that drafts a terse, diff-derived PR body for a completed lifecycle change. This file is `graphs/pr-author.yaml`'s member **label** and nothing else: a single-sided member layers no persona, so the drafting prose it is actually given lives in that member's own `task`. Never dispatched as a plan node's persona either, which `onepipeline` refuses by name. |
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
built-in role of that name wins. `oneagentgraph` 0.2.18 ships exactly five:
`docs-writer`, `engineer`, `planner`, `researcher`, and `reviewer`. This directory
is not on the search path, so **any other name is taken as a path relative to
`graphs/`** — which is why `crozier/crozier-corpus` fails a dispatch with `cannot
read graphs/crozier/crozier-corpus`, and equally why `orchestrator` and `check-in`
do. Those two are not built in and never were; `graphs/dag-scope.yaml` names them
`../personas/orchestrator.yaml` and `../personas/check-in.yaml` for that reason, and
naming either as a plan node's `persona` fails the same way `crozier/…` does.
`pr-author` is refused earlier still and by name: `onepipeline` dispatches change
request drafting under it, so a plan node's own worker may not run as it.

That the shipped set is those five and no more is measured two ways, both against
oneagentgraph 0.2.18 — `tests/e2e/test_shipped_persona_catalog_e2e.py` runs each:

- A graph carrying its own `personas` catalog is refused when one of its files
  collides with a shipped name (`persona "engineer" names both …/engineer.yaml in
  this graph's catalog and one this crate ships`). Only those five collide;
  `orchestrator`, `check-in`, and `pr-author` are accepted alongside the catalog
  exactly as an invented name is, because nothing ships under them.
- A plan node naming `persona: orchestrator` settles `failed
  (infrastructure-failure)` with `oneagentgraph: invalid config: cannot read
  <launch-dir>/graphs/orchestrator` — the path-resolution failure, reached because
  no built-in claimed the name first. It costs no agent turn: the dispatch dies in
  config validation, before a harness is launched.

Three consequences, the first two re-measured against onepipeline 0.7.5 and
oneagentgraph 0.2.18 by launching a plan whose two nodes name `engineer` and
`reviewer` and reading the completion criterion each dispatch's supervisor was
handed. The launch was against 0.7.0; every bump since has carried it forward on
the narrower evidence that `src/agentgraph.rs` and `src/graph.rs` — the whole of
what composes a member and resolves its persona — cannot have moved the answer.
Through 0.7.1 both were byte-identical with the launched crate. 0.7.2 left
`src/graph.rs` byte-identical and added to `src/agentgraph.rs` exactly one thing: a
`process()` accessor returning the backend's pid, whose one caller registers
dispatch ownership so a teardown can aim at the right process. No composition or
persona-resolution path reads it. 0.7.3 through 0.7.5 leave **both files
byte-identical with 0.7.2**; the whole of what those three releases touched is the
ledger and the journal, the report retention path, the drafting ending, and the
views that read them. So there is still no path by which the answer could have
moved:

- Editing `engineer.yaml` here does not change what an `engineer` node is dispatched
  with. The flat files whose names match a built-in are a catalog and a validation
  target, not the dispatch input.
- A built-in role's own `user.done_when` is enforced **alongside**
  `config/onejudge.base.yaml`'s, not instead of it. The `reviewer` node's supervisor
  was given "Both of these must hold: 1. every acceptance criterion stated in the
  task is met… 2. the review reports verified, severity-ranked findings each tied to
  specific code". So every dispatch gets the shared acceptance-criteria clause,
  whichever of the five it names — a role replaces the base's bar only by declaring
  `user.done_when_replaces_base`, and none of the shipped five does. The
  `user.done_when` in this directory's `planner.yaml`, `researcher.yaml`, and
  `reviewer.yaml` still does not reach a dispatch; the built-in's does.
- **A repo-specific persona in this directory does dispatch — as a path.** Naming
  `../personas/crozier/crozier-corpus.yaml` on a node settles the dispatch with that
  file's `agent.instructions` as the worker's role and its `user.persona` as the
  supervisor's bar, both verbatim. Only the catalog *name* is unresolvable; the file
  is not inert. `oneagentgraph` 0.2.14 is the release that closed this, and the
  engine has reached it since onepipeline 0.6.3, the first release to link it.

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
- **Optional:** `agent.name`, `user.done_when` (a *second* bar, enforced alongside
  the base's), `user.done_when_replaces_base` (true when this role's bar must stand
  in for the base's instead; needs a `user.done_when` to stand in with),
  `user.max_turns` (int), `evals`.
- No other top-level keys — `task` comes from `--task`, and `provider` / `session`
  / the shared agent preamble come from the base config.

See `docs/onejudge-integration.md` for how a persona becomes an effective onejudge
config and how the two conversation sides are wired.

### Why that shape stays, even though a newer one is published

`oneagentgraph` 0.3.0 replaces it: a persona becomes a onejudge config fragment, so
`agent.instructions` moves to a top-level `system_prompt`, `agent.name` to a
top-level `name`, and everything under `user:` stays. That release's own
`docs/persona-format.md` says the previous spelling "no longer loads, anywhere" —
no alias, no flag, no deprecation period — and states the same rule for a base
config, so `config/onejudge.base.yaml` would move in the same change.

**Adopting it here is blocked, and not by taste.** `just validate-personas` runs
the oneagentgraph *CLI*, but what reads a persona at dispatch is the oneagentgraph
`onepipeline` **links** — 0.2.18 at onepipeline v0.7.5, confirmed from that tag's
`Cargo.lock`. Bumping the CLI alone would certify a shape the dispatching reader
cannot load, and `graphs/dag-scope.yaml` names `../personas/orchestrator.yaml` and
`../personas/check-in.yaml` **by path**, so the monitor and the pacemaker would
stop being produced on every orchestrated run.

The pinned oneagentgraph 0.2.18 refuses the new shape outright rather than
degrading quietly — its `Persona` carries `deny_unknown_fields` at every level —
and that was measured on the installed stack, with a 0.2.18-shaped control beside
each probe: `persona validate` and `oneagentgraph validate` both exit 2 on
``unknown field `name`, expected one of `agent`, `user`, `evals` ``, and a real
`oneagentgraph run` of a one-member graph naming such a file by path dies at config
validation before `graph-started` while the control reaches `graph-settled` with
exit 0.

**What unblocks it: an `onepipeline` release whose `Cargo.lock` resolves
`oneagentgraph 0.3.0`.** Read the lock, not the `Cargo.toml` requirement: a caret
requirement permits a version the lock has not resolved, so a `Cargo.toml` naming
0.3.0 is not evidence that a dispatch reads it. Then the pin, every file here, and
`config/onejudge.base.yaml` move together in one change.
