# Packages

Packages are framework-independent shared contracts and implementation. A
package must not import from `apps/` or from the Python project. Public exports
are typed, documented, and covered through their consumer-facing API. Every
package project exposes `build`, `lint`, `test`, and `typecheck` Nx targets.
