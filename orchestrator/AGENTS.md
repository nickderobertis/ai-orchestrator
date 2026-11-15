# Orchestrator project

This Nx project owns three trust boundaries and nothing else: the history labels
validated before they reach a dispatched subprocess, the redaction rule
`scripts/preserved-log.sh` mirrors, and the release adoptions of the plan a launch is
about to dispatch — the two host-install rules `host_adoption_check.py` holds against
what this host installs. Judging a plan's tasks is the plan store's and the judges', never
this package's.

- Keep serialized contracts backward compatible, and validate an external record at
  its reader boundary rather than at its use.
- Exercise behavior through the real CLI or subprocess boundary.
- Coverage here is enforced at 100%: everything in this package is a boundary, so
  an uncovered statement is an unproven one.
