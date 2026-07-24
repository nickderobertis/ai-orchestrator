# Application projects

Each application under this directory is an Nx project. Applications may compose
package APIs but must not be imported by packages. Give every application the
uniform `build`, `lint`, `test`, and `typecheck` targets and add its own nested
`AGENTS.md` when it is created.
