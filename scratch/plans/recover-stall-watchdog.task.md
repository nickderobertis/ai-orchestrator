## What
Integrate and publish the ALREADY-COMPLETE "surface stalled worker trees" feature. It is fully built and
committed on the preserved lifecycle branch `ai-orchestrator/engineer/028c22fb-b24644af14`
(tip `7893cae`, 1 commit ahead of its original base `main`); a machine kill took out its `run-plan`
executor right after the agent committed, before verify/publish closeout ran. **Do NOT reimplement it.**
Integrate + resolve + verify + publish only:

1. From your working branch cut off current `main`, merge the preserved branch
   (`git merge ai-orchestrator/engineer/028c22fb-b24644af14`). This brings in: new
   `orchestrator/watchdog.py` (stall/progress detection), substantial changes to
   `orchestrator/dispatch.py` (+139 lines), `docs/onejudge-integration.md`, and unit tests in
   `tests/test_dispatch_unit.py`.
2. **EXPECT A CONFLICT in `orchestrator/dispatch.py`.** Current `main` already includes the dag-goals-index
   recovery, which also edited `dispatch.py`. Resolve it by keeping BOTH intents: the DAG-goals
   coordination hooks already on `main` AND this branch's stall-watchdog surfacing. Do not drop either.
   Read both sides carefully; the two changes touch dispatch for different purposes and must coexist. Also
   reconcile `docs/onejudge-integration.md` if it conflicts.
3. Run `just gate` fully green with the enforced 95% line coverage held, then publish through the
   registered workflow (local direct merge to `main`).

## Why
This is reliability feature #3 of the in-flight program: investigate the closeout hang and add a dispatch
stall/progress watchdog so a wedged or silently-stalled worker tree is surfaced instead of hanging the
whole orchestration — the exact class of failure (a parked/dead executor) that repeatedly cost this
program time. The work is done and gate-green in intent; the user wants it landed, not redone. It merges
last because it shares `dispatch.py` with dag-goals-index and must reconcile against it.

## Acceptance criteria
- The stall-watchdog change from `7893cae` is present on `main`: `orchestrator/watchdog.py` exists and
  dispatch surfaces stalled/no-progress worker trees.
- The dag-goals-index coordination changes to `dispatch.py` that were already on `main` are PRESERVED
  intact alongside the watchdog changes (verify both features' behavior survives the merge).
- The unit coverage in `tests/test_dispatch_unit.py` is present and passing.
- `just gate` passes with 95% coverage held and no relevant checks skipped; the change is published to `main`.

## Additional info
- Preserved branch: `ai-orchestrator/engineer/028c22fb-b24644af14` (tip `7893cae`), reachable in the
  isolated execution checkout. Original base: `main` at `c0599ff` (before the sibling recoveries landed).
- CONCURRENCY: this is the LAST of the three sibling recoveries; publish once dag-goals-index has landed on
  `main`. If your publication loses a race, fetch latest `main`, re-merge/resolve, re-run `just gate`, and
  retry publication. The merge queue routes local publications through `merge_queue_turn`.
