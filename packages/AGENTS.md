# Shared DAG UI packages

Packages are independently testable TypeScript libraries. Every package declares
local `build`, `lint`, `test`, and `typecheck` Nx targets, a `scope:*` tag, and
exactly one layer tag: `type:feature`, `type:data-access`, `type:ui`, or
`type:util`.

Keep framework-independent contracts and layout logic out of applications.
Validate external JSON at the data-access boundary and preserve deterministic
output from shared rendering code.
