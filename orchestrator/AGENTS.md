# Orchestrator project

This Nx project owns two trust boundaries and nothing else: the history labels
validated before they reach a dispatched subprocess, and the redaction rule
`scripts/preserved-log.sh` mirrors.

- Keep serialized contracts backward compatible, and validate an external record at
  its reader boundary rather than at its use.
- Exercise behavior through the real CLI or subprocess boundary.
- Coverage here is enforced at 100%: everything in this package is a boundary, so
  an uncovered statement is an unproven one.
