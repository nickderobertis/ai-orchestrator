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
| `planner` | Decomposing work into a dependency-ordered plan cut at contract seams, asking the manager at every fork and recording its exceptions, under a judge that holds the plan to the original goals (no implementation). |
| `orchestrator` | Actively monitoring an executing tracked graph: judging observed activity against the plan, surfacing what it finds, and applying only unambiguous in-allowlist fixes. |
| `check-in` | Synthesizing a read-only, durable-state-derived planner status update. |
| `docs-writer` | READMEs, reference docs, durable AGENTS.md notes. |
| `researcher` | Answering questions with evidence cited from the actual source. |
| `reviewer` | Reviewing and integrating several agents' independently produced work in a complex DAG. |
| `pr-author` | Names the role that drafts a terse, diff-derived PR body for a completed lifecycle change. This file is `graphs/pr-author.yaml`'s member **label** and nothing else: a single-sided member layers no persona, so the drafting prose it is actually given lives in that member's own `task`. Never dispatched as a plan node's persona either, which `onepipeline` refuses by name. |
| `design-doc` | Writing the one short design document a person reviews a finished plan as — and reviewing it. Its six sections, their order, and the properties it is judged on are stated once in `config/design-doc-template.md`; this file names that path and restates none of it. Dispatched by path (`../personas/design-doc.yaml`), never by this catalog name, which would resolve against the roles built into `oneagentgraph` and read no file here. `graphs/design-doc.yaml` is the node-scope graph that pairs it with the two harness configs whose identity order is deliberately the reverse of this host's ordinary pairing. |
| `crozier/crozier-corpus` | Crozier-specific corpus research and curation. |

The test suite requires these rows to match recursive persona discovery exactly.

`engineer` is deliberately absent. Every plan names it as a bare `engineer`, which
resolves to the role built into `oneagentgraph` — so the file that used to sit here
was read by nothing but `just validate-personas` and by readers who reasonably took
it for the bar a node is judged under. It is gone rather than corrected: this
repository has no lever over that review bar, and a catalog entry implying otherwise
is worse than none. Amend an engineer node's review bar in its own task, as
`## Acceptance criteria`; change the role itself upstream, in `oneagentgraph`. The
four flat files still here whose names a built-in claims — `docs-writer`, `planner`,
`researcher`, `reviewer` — are inert for exactly the same reason, and each is kept
only for what its row above describes.

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
built-in role of that name wins. `oneagentgraph` 0.3.15 ships exactly five:
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
the pinned oneagentgraph 0.3.15 CLI, and once against the oneagentgraph the adopted
`onepipeline` links, which is the one a dispatch reads and is 0.3.15 as well. `tests/e2e/test_shipped_persona_catalog_e2e.py`
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

Three consequences, the first two re-measured against onepipeline 0.23.0 and the
oneagentgraph 0.3.15 it links, by launching a plan whose two nodes name `engineer` and
`reviewer` and reading the completion criterion each dispatch's supervisor was
handed. Do **not** argue one of them forward from a source file that stayed
byte-identical: the accounts that did named `src/agentgraph.rs` and `src/graph.rs`,
and the crate has carried neither path since before 0.2.18 — so that argument was
reading nothing. What holds these between launches is this repository's own suite,
which re-takes both measurements on every gate run:
`tests/e2e/test_shipped_persona_catalog_e2e.py` resolves the shipped set from the
pinned binary, and `tests/e2e/test_orchestrate_launch_e2e.py` reads the composed
completion criterion out of a real dispatch.

- Editing a flat file here whose name a built-in claims does not change what a node
  naming that role is dispatched with. Those files are a catalog and a validation
  target, not the dispatch input — which is why `engineer.yaml` is gone rather than
  maintained, and why `orchestrator/criteria_guard.py` resolves a node's bar out of
  the `oneagentgraph` `onepipeline` links instead of out of this directory.
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
just orchestrate authoring:draft
```

Once proven, add it to this catalog through the orchestrator's isolated
self-dispatch lifecycle; do not edit the canonical checkout directly. General,
cross-repo roles use a flat name (`orchestrator`); repo-specific roles use a
slash-qualified name (`crozier/crozier-corpus`), stored as
`personas/crozier/crozier-corpus.yaml` — and dispatched by that *path* relative to
`graphs/` (`../personas/crozier/crozier-corpus.yaml`), never by the catalog name,
per the resolution measured above. `just new-persona <name>` creates either
layout, and `just validate-personas` checks the tracked catalog recursively.

A repo-specific persona also states which repository it is for by the directory it
sits in, and `just check` holds its prose to that repository. Two reconciliations do
it, both in the uncached `test-checkouts` tier, and they are separate because they
catch separate failures:

- **The commands it demands.** Every `` `just <recipe>` `` it names is reconciled
  against the recipes that repository's registered checkout actually defines
  (`tests/test_persona_recipe_drift.py`). A review bar the worker never sees,
  demanding a recipe nobody can run, fails finished work — which is what
  `crozier-corpus.yaml` did while it asked for a `just gate` crozier has no recipe
  for.
- **The names it uses of that repository.** Every backticked path it names must be a
  path that repository tracks, and every `` `Type { field: … }` `` literal it writes
  must name fields that repository's own declaration of `Type` declares
  (`tests/test_persona_identifier_drift.py`). A recipe check cannot catch this: the
  same `crozier-corpus.yaml` went on describing a corpus registered by an allowlist
  of files that `matched`, months after crozier inverted it to an exclusion list of
  files that are `unmatched`, and the recipe it named for measuring one —
  crozier's `fixtures-candidates` — still exists there as a backward-compatible
  alias. Every name resolved, and the model they described asked for the opposite of
  the work.

`tests/e2e/test_persona_review_bar_e2e.py` asks both of the bar a real supervisor was
handed on a real graph run, which is where either failure takes effect. A flat,
cross-repo persona names no repository and is outside both reconciliations.

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
oneagentgraph `onepipeline` links, which is `0.3.15` at onepipeline v0.23.0. Read that
from what the release *resolved*, never from its `Cargo.toml` requirement — that
requirement is a caret one and permits versions the build did not resolve, so it is
not evidence of what a dispatch reads. The installed wheel is the source and
`tests/test_linked_libraries.py` is what reads it: `onepipeline-cli` ships a
CycloneDX SBOM under its `dist-info/sboms/` declaring one version per linked crate,
and that gate reconciles this sentence against it on every gate run. So the two
numbers agree today and do not have to: the pin is 0.3.15 and the linked reader is
0.3.15. They have not always. In an earlier cycle they read 0.3.3 and 0.3.4, and what
separated them is how long a cancelled process tree is left before Windows ends its
job, that a member whose tree cannot be found is not a member proven idle, and — in
0.3.3 — the conversation label and the oneharness-session pointer a member's turns
are published with. They agree today because this host now reconciles every pin
against what the engine wheel resolved rather than moving them one at a time. Both
read the shape below, which is the agreement that matters.

**That last one is the case where such a gap is the whole story, and it is worth
reading before predicting what a bump here buys.** The label
and the pointer are what makes a run's transcripts openable in the DAG Observatory,
and they are stamped by whichever oneagentgraph *runs the graph* — which is the
linked one, not this pin. So adopting 0.3.3 here moved `just validate-personas` and
nothing a dispatch writes, and this host paid for that literally: for a whole
release cycle `config/oneagentgraph.version` read 0.3.3, every dispatched run
recorded no `oneharness-session` event at all, and nothing said so — a missing event
looks exactly like a turn that did nothing. What fixed it was moving
`config/onepipeline.version`. The same gate now refuses a linked `oneagentgraph`
below the producer release, which is the check that was missing rather than a second
copy of the number.

The producer was then observed directly, on 2026-08-19, on a real `just orchestrate`
run of one direct `engineer` node (`producer-probe-0-8-3`): 4 `oneharness-session`
events, each carrying its `role`/`turn`/`identity` payload and one
`oneharness_session` artifact reference, and 3 turn envelopes — `turn-started`,
`turn-activity`, `turn-completed` — carrying a `<stream>.<member>` session label.
Two things that observation is worth keeping for. The `oneharness-session` events
carry no `session` label of their own, so counting one is not a way of counting the
other. And **that `session` label is not onevcs's session token**, which shares the
key name, is spelled `s-<hex>`, and names a worktree lease rather than a
conversation; the producer's value is the graph stream joined to the member that
spoke and always ends in `.<member>` — `.worker`, `.judge` — where onevcs's never
does. That pair has been confused once already.

That agreement is why the shape here moved in one change rather than two. The
previous spelling put the role in a top-level `agent:` block, and 0.2.18 refused
today's shape exactly as hard as the reverse: there is no alias, no flag, and no
deprecation period in either direction. The pinned oneagentgraph 0.3.15 refuses the
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
