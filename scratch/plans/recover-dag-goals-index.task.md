## What
Integrate and publish the ALREADY-COMPLETE "coordinate active DAG goals" feature. It is fully built and
committed on the preserved lifecycle branch `ai-orchestrator/engineer/c0b1895a-5c97423449`
(tip `980dfa4`, 1 commit ahead of its base `main`); a machine kill took out its `run-plan` executor right
after the agent committed, before verify/publish closeout ran. **Do NOT reimplement it.** Integrate +
verify + publish only:

1. From your working branch cut off current `main`, merge the preserved branch
   (`git merge ai-orchestrator/engineer/c0b1895a-5c97423449`). This brings in: new `orchestrator/goals.py`
   (active-DAG-goal coordination), and changes to `orchestrator/graph.py`, `orchestrator/dispatch.py`,
   `orchestrator/journal.py`, `orchestrator/plan.py`, `pyproject.toml`, `justfile`,
   `docs/orchestration.md`, the example plans, plus e2e `tests/e2e/test_goals_e2e.py` and a tweak to
   `tests/e2e/test_tracked_graph_e2e.py`.
2. Resolve any merge conflict with current `main` keeping both sides' intent. Current `main` may already
   include a sibling recovery (retry-resume, `lifecycle.py` only) — disjoint from this branch, so a clean
   merge is expected.
3. Run `just gate` fully green with the enforced 95% line coverage held, then publish through the
   registered workflow (local direct merge to `main`).

## Why
This is Feature 2a of the in-flight reliability program: DAGs are tied to goals and the harness gains
cross-DAG awareness — a central active-goals coordination surface plus a same-project concurrency guard so
concurrent orchestrations over one project cooperate instead of racing. It is the prerequisite for the
cross-DAG-dependency feature (`run:<run_id>#<node_id>`) that follows. The work is done and gate-green in
intent; the user wants it landed, not redone.

## Acceptance criteria
- The DAG-goals coordination change from `980dfa4` is present on `main`: `orchestrator/goals.py` exists and
  is wired through dispatch/graph/journal, active goals are coordinated centrally, and a same-project
  concurrency guard is in effect.
- The e2e coverage `tests/e2e/test_goals_e2e.py` is present and passing.
- `just gate` passes with 95% coverage held and no relevant checks skipped; the change is published to `main`.

## Additional info
- Preserved branch: `ai-orchestrator/engineer/c0b1895a-5c97423449` (tip `980dfa4`), reachable in the
  isolated execution checkout. Base: `main`.
- CONCURRENCY: the stall-watchdog recovery also edits `orchestrator/dispatch.py` and will be published
  AFTER this one so it resolves against your changes — you do not need to coordinate with it. If your own
  publication loses a race to `main`, fetch latest `main`, re-merge/resolve, re-run `just gate`, and retry
  publication. The merge queue routes local publications through `merge_queue_turn`.
