# DAG layout package

This package owns the deterministic, framework-agnostic DAG view model shared
by browser and CLI renderers. Do not add DOM, React, terminal, or SVG-rendering
dependencies. Preserve stable node/edge IDs and deterministic ordering.
