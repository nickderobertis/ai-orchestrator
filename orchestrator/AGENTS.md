# Orchestrator project

This Nx project owns three trust boundaries and nothing else: the history labels
validated before they reach a dispatched subprocess, the redaction rule
`scripts/preserved-log.sh` mirrors, and the plan a launch is about to dispatch —
checked against the review bar each of its nodes resolves to, out of the
`oneagentgraph` `onepipeline` links rather than out of `personas/`.

- Keep serialized contracts backward compatible, and validate an external record at
  its reader boundary rather than at its use.
- Exercise behavior through the real CLI or subprocess boundary.
- Coverage here is enforced at 100%: everything in this package is a boundary, so
  an uncovered statement is an unproven one.
