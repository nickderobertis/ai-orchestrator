# Orchestrator project

This Nx project owns the Python orchestration package and its CLI entry points.
Keep public command behavior backward compatible and validate file, journal, and
subprocess inputs at their trust boundaries.

Run its uniform targets through `scripts/nx.sh nx run orchestrator:<target>`,
where `<target>` is `build`, `lint`, `test`, or `typecheck`. Tests use real git,
onejudge, and process boundaries; only the paid model provider is replaced by the
repository's deterministic protocol backend.
