## What
Integrate and publish the ALREADY-COMPLETE "resume preserved lifecycle retries" feature. It is
fully built and committed on the preserved lifecycle branch
`ai-orchestrator/engineer/ae3002ee-e4927b540a` (tip `405c454`, 1 commit ahead of `main`); a
machine kill took out its `run-plan` executor right after the agent committed, before verify/publish
closeout ran. **Do NOT reimplement it.** Integrate + verify + publish only:

1. From your working branch cut off current `main`, merge the preserved branch
   (`git merge ai-orchestrator/engineer/ae3002ee-e4927b540a`). This brings in the retry-resume fix in
   `orchestrator/lifecycle.py` and its e2e coverage in `tests/e2e/test_lifecycle_e2e.py`.
2. Resolve any merge conflict with current `main` keeping both sides' intent (this branch touches only
   `lifecycle.py` + its e2e test, so a clean merge is expected).
3. Run `just gate` fully green with the enforced 95% line coverage held, then publish through the
   registered workflow (local direct merge to `main`).

## Why
This is reliability feature #1 of the in-flight program: when a lifecycle node is retried and its prior
attempt left committed work on a preserved branch, the retry must RESUME/recover that branch instead of
restarting from scratch — the exact failure mode that just cost this program a full round of parallel work
when a machine kill interrupted closeout. The feature is done and gate-green in intent; the user wants it
landed, not redone.

## Acceptance criteria
- The retry-resume change from `405c454` is present on `main`: a retried lifecycle attempt with a
  committed preserved branch resumes/recovers it rather than restarting, and auto-retry is preserved.
- The e2e coverage from the preserved branch is present and passing.
- `just gate` passes with 95% coverage held and no relevant checks skipped; the change is published to `main`.

## Additional info
- Preserved branch: `ai-orchestrator/engineer/ae3002ee-e4927b540a` (tip `405c454`), reachable in the
  isolated execution checkout. Base: current `main` (`c0599ff`).
- CONCURRENCY: other recovery publications (dag-goals-index, stall-watchdog) may land on `main` while you
  work. If publication loses a race, fetch latest `main`, re-merge/resolve, re-run `just gate`, and retry
  publication — do not abandon the work. The merge queue routes local publications through
  `merge_queue_turn`; a base-advanced conflict should resolve-and-requeue rather than abort.
