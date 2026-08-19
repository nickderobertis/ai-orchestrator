# Personas

A **persona** is a small onejudge *config fragment* layered over
`config/onejudge.base.yaml` that defines one kind of agent: its role
(`system_prompt`) and how the simulated supervisor reviews it (`user.persona`). At
dispatch time, `oneagentgraph` merges base ⊕ persona ⊕ the CLI `--task` into one
effective onejudge config and runs it. Common settings live once in the base; only
the role-specific parts live here.

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
built-in role of that name wins. `oneagentgraph` 0.3.1 ships exactly five:
`docs-writer`, `engineer`, `planner`, `researcher`, and `reviewer`. This directory
is not on the search path, so **any other name is taken as a path relative to
`graphs/`** — which is why `crozier/crozier-corpus` fails a dispatch with `cannot
read graphs/crozier/crozier-corpus`, and equally why `orchestrator` and `check-in`
do. Those two are not built in and never were; `graphs/dag-scope.yaml` names them
`../personas/orchestrator.yaml` and `../personas/check-in.yaml` for that reason, and
naming either as a plan node's `persona` fails the same way `crozier/…` does.
`pr-author` is refused earlier still and by name: `onepipeline` dispatches change
request drafting under it, so a plan node's own worker may not run as it.

That the shipped set is those five and no more is measured two ways — once against
the pinned oneagentgraph 0.3.1 CLI, and once against the 0.3.0 `onepipeline` links,
which is the one a dispatch reads. `tests/e2e/test_shipped_persona_catalog_e2e.py`
runs each:

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

Three consequences, the first two re-measured against onepipeline 0.8.1 and the
oneagentgraph 0.3.0 it links, by launching a plan whose two nodes name `engineer` and
`reviewer` and reading the completion criterion each dispatch's supervisor was
handed. Do **not** argue one of them forward from a source file that stayed
byte-identical: the accounts that did named `src/agentgraph.rs` and `src/graph.rs`,
and the crate has carried neither path since before 0.2.18 — so that argument was
reading nothing. What holds these between launches is this repository's own suite,
which re-takes both measurements on every gate run:
`tests/e2e/test_shipped_persona_catalog_e2e.py` resolves the shipped set from the
pinned binary, and `tests/e2e/test_orchestrate_launch_e2e.py` reads the composed
completion criterion out of a real dispatch.

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
  file's `system_prompt` as the worker's role and its `user.persona` as the
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
under `personas/` (underscore-prefixed files and directories are skipped). Since
oneagentgraph 0.3.0 a persona **is** a onejudge config fragment, validated against
onejudge's own config schema rather than against a second copy of it — so what a
persona may say is what onejudge accepts, minus the four fields the member's own
launch decides:

- **Refused:** `provider` (the member's `agent:` and `judge:` decide it), `session`
  (the run names it), `task` (it comes from `--task` or the member's own `task:`),
  and `skill` (a relative path resolves against the base config, so name it there).
- **Optional, and onejudge's:** `system_prompt` (the role, appended after the
  base's shared preamble), `user.persona`, `user.done_when` (a *second* bar,
  enforced alongside the base's), `user.max_turns`, `evals`, `assessment`.
- **Optional, and oneagentgraph's own two keys, consumed by the merge:** `name`
  (the `persona` label on this member's events; absent, the ref's file name is
  used) and `user.done_when_replaces_base` (true when this role's bar must stand in
  for the base's instead; needs a `user.done_when` to stand in with).
- **Nothing is required.** onejudge asks for nothing but a task and a task never
  comes from a persona, so a file carrying only a `system_prompt` is complete. The
  files here all carry both halves anyway, because a dispatched role wants a review
  bar as well as a role.

The same rule holds for `config/onejudge.base.yaml`: it is a onejudge config, so
its shared preamble is the top-level `system_prompt` too.

`oneagentgraph`'s own `docs/persona-format.md` at the pinned release is the
authoritative spec for all of it.

### Which oneagentgraph reads these files

Two different ones, and what they have to agree on is the persona **shape**.
`just validate-personas` runs the oneagentgraph **CLI** that
`config/oneagentgraph.version` pins; what reads a persona at **dispatch** is the
oneagentgraph `onepipeline` links, which is
`0.3.0` at onepipeline v0.8.1, confirmed from that tag's `Cargo.lock` rather than
from its `Cargo.toml` requirement — a caret requirement permits a version the lock
has not resolved, so the requirement is not evidence of what a dispatch reads.
Those two numbers are not equal today and do not have to be: the pin is 0.3.1 and
the linked reader is 0.3.0, and the only thing 0.3.1 changed is how long a cancelled
process tree is left before Windows ends its job. Both read the shape below, which
is the agreement that matters.

That agreement is why the shape here moved in one change rather than two. The
previous spelling put the role in a top-level `agent:` block, and 0.2.18 refused
today's shape exactly as hard as the reverse: there is no alias, no flag, and no
deprecation period in either direction. The pinned oneagentgraph 0.3.1 refuses the
previous shape outright, naming the field to write instead:

```
$ oneagentgraph persona validate <a file with an `agent:` block>
invalid config: …: `agent` is not a persona key: a persona is a onejudge config
fragment, and onejudge has no `agent` field. To migrate this file, write
`agent.instructions` as the top-level `system_prompt`, …
```

So a split *across that break* — the CLI on one shape and the linked reader on the
other — would certify files that produce no member at all, and `graphs/dag-scope.yaml` names
`../personas/orchestrator.yaml` and `../personas/check-in.yaml` **by path**, so the
monitor and the pacemaker are what a split would silently cost.
`tests/e2e/test_path_dispatched_personas_e2e.py` is what keeps that from being an
argument: it runs each path-named persona through a real `oneagentgraph run` and
fails if either stops loading.
