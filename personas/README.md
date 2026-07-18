# Personas

A **persona** is a small onejudge *delta* over `config/onejudge.base.yaml` that
defines one kind of worker: the agent's role (`agent.instructions`) and how the
simulated supervisor reviews it (`user.persona`). At dispatch time,
`orchestrator.config` merges base ⊕ persona ⊕ the CLI `--task` into one effective
onejudge config and runs it. Common settings live once in the base; only the
role-specific parts live here.

## Catalog

| Persona | Use it for |
| --- | --- |
| `engineer` | General implementation across server-side systems, UI, accessibility, and contract-aware libraries. |
| `planner` | Decomposing work into an actionable, dependency-ordered plan (no implementation). |
| `test-engineer` | Closing coverage gaps and writing realistic, un-mocked e2e tests. |
| `docs-writer` | READMEs, reference docs, durable AGENTS.md notes. |
| `researcher` | Answering questions with evidence cited from the actual source. |
| `reviewer` | Reviewing and integrating several agents' independently produced work in a complex DAG. |
| `pr-author` | Drafting a terse, diff-derived PR body for a completed lifecycle change. |
| `iac-author` | Project-specific infrastructure-as-code authoring and validation. |
| `crozier-corpus` | Project-specific Crozier corpus research and curation. |

## Adding a persona

Prefer a detailed task with explicit per-node `done_when` acceptance criteria (and
`max_turns` when needed) over a new persona. Add one only when a subtask needs a
genuinely distinct general role or review bar. The simulated-user supervisor
reviews every dispatch already; the dedicated `reviewer` is for multi-agent
integration, not ordinary review of one change.

```sh
just new-persona <name>      # scaffolds personas/<name>.yaml from _template.yaml
# edit agent.instructions (the role) and user.persona (the supervisor)
just validate-personas       # structural check; also part of `just check`
```

## The delta contract

`just validate-personas` enforces the shape of every `personas/*.yaml` (files
starting with `_`, like the template, are skipped):

- **Required:** `agent.instructions` (string), `user.persona` (string).
- **Optional:** `agent.name`, `user.done_when`, `user.max_turns` (int), `evals`.
- No other top-level keys — `task` comes from `--task`, and `provider` / `session`
  / the shared agent preamble come from the base config.

See `docs/onejudge-integration.md` for how a persona becomes an effective onejudge
config and how the two conversation sides are wired.
