# DAG layout

The renderer-neutral, deterministic DAG geometry contract shared by the web and
CLI renderers. `layoutDag()` validates IDs, endpoints, states, and acyclicity,
uses Dagre for stable ordering, and returns integer node rectangles, semantic
status style tokens, and routed edge polylines. Renderers must consume this view
model rather than calculating their own geometry.
