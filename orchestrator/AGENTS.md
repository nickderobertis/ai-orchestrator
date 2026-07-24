# Orchestrator project

This Nx project owns the Python orchestration engine. Keep serialized contracts
backward compatible, validate external records at their reader boundary, and
exercise behavior through the real CLI/subprocess boundary. Run
`bunx nx run orchestrator:build|lint|test|typecheck` for project-local checks;
the workspace `just check` remains authoritative.
