# DAG UI applications

Applications are deployable shells. Keep business and layout logic in tagged
packages, consume server data through the typed telemetry client, and declare
the uniform `build`, `lint`, `test`, and `typecheck` Nx targets locally.

Use `type:app` plus one `scope:*` tag. Apps may depend on feature, data-access,
UI, and utility packages; they must not be imported by packages.
