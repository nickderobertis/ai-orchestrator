# Orchestration model

How the orchestrator turns one large task into many parallel onejudge runs. The
mechanics (dispatch, scheduling) are in `orchestrator/`; this doc is the judgment
behind them.

## The shape of the work

```
                   ┌─────────────┐
   large task ───▶ │ decompose   │ ──▶ a plan (DAG of subtasks)
                   └─────────────┘
                          │
                          ▼
                   ┌─────────────┐   run every subtask whose deps are done,
   plan  ────────▶ │  schedule   │   concurrently, bounded by `concurrency`
                   └─────────────┘
                          │
              one onejudge process per subtask, each with a persona
                          │
                          ▼
                   reports ──▶ re-plan the next layer from what came back
```

## 1. Decompose into a DAG

A subtask is a node with an `id`, a `persona`, the `task` prose, and `deps` (the
ids that must finish first). Two subtasks are independent when neither needs the
other's output — those are what run in parallel. Make dependencies explicit and
minimal: a node should depend only on what it truly consumes, so the scheduler
does not serialize work that could overlap. Name the interface between dependent
subtasks in the `task` prose (the file, the function signature, the contract) so
the downstream agent does not have to re-derive it.

## 2. Choose granularity (the core tradeoff)

Every onejudge is a fresh agent that pays a **fixed overhead** to prepare its
context before doing useful work — reading the repo, orienting, forming a plan.
Splitting buys parallelism and lets you match a sharper persona to the work; it
costs one more setup tax per split and more integration surface between pieces.

Split a subtask when **any** holds:

- The pieces are genuinely independent and there is spare concurrency to run them
  at once (real wall-clock win).
- The pieces want **different personas** (e.g. backend vs. frontend vs. docs).
- One piece is risky enough to want its own focused agent and review bar.

Keep a subtask whole when:

- Splitting would create pieces so small the setup overhead dominates the actual
  work (a fresh agent spending most of its turns just re-orienting).
- The pieces are tightly coupled — they share so much context that one agent
  holding it all is faster and less error-prone than several handing state back
  and forth.

When unsure, **err toward fewer, larger subtasks** and split further only if one
proves too big for one agent to hold. Over-splitting is the common failure: it
multiplies cost and latency for no parallelism gain.

## 3. Schedule for parallelism

`run-plan` schedules the DAG automatically: it runs every subtask whose deps have
completed, up to the plan's `concurrency`, and a dependent waits only for its own
deps — not for the whole previous layer. Set `concurrency` to the real ceiling
(cost, rate limits, machine), not higher; extra width past the DAG's available
parallelism does nothing.

A subtask that does not complete (hits its turn cap) **fails**, and its
dependents are **skipped** rather than run against a broken precondition. Read the
skipped set as the blast radius of a failure, fix or re-scope that subtask, and
re-dispatch.

## 4. Read results, then re-plan

Each node returns a onejudge report (completed? / verdicts / usage). Treat a plan
as one layer of a larger loop: dispatch what you can, read what came back, and
plan the next layer from reality rather than from the original guess. Coarse-grain
first; only split a node further once a run shows it was too big.

## Where this lives in code

- `orchestrator/plan.py` — plan validation + the scheduler (`run_plan`).
- `orchestrator/dispatch.py` — one subtask → one onejudge run.
- `orchestrator/config.py` — base ⊕ persona → the effective onejudge config.
- `examples/plan.example.json` — a worked diamond DAG.
