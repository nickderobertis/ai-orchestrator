# Package projects

Each package under this directory is an Nx project with an explicit public API.
Packages must remain application-independent. Give every package the uniform
`build`, `lint`, `test`, and `typecheck` targets and add its own nested
`AGENTS.md` when it is created.
