---
title: "feat(admin): add dashboard"
project: "repo-plan-example"
status: "todo"
repositories: ["github.com/nickderobertis/some-service"]
depends_on: ["repo-plan-example/api"]
metadata:
  "onepipeline.id": "dashboard"
  "onepipeline.repo_type": "team"
  "onepipeline.steps": [{"id": "impl", "persona": "engineer", "task": "## What\nImplement a read-only `/admin` dashboard page that surfaces the health metadata, with tests that render the page and drive real requests.\n\n## Why\nOperators can poll the endpoint but have nowhere to see its metadata without a terminal, which is what keeps on-call reading raw JSON during an incident.\n\n## Acceptance criteria\n- The rendered dashboard exposes every field the health contract returns.\n- Tests render the page and drive real requests rather than asserting on internal state.\n- Access control is covered: an unauthorized viewer is refused, and a test proves it."}, {"id": "approval", "kind": "human", "task": "Exercise the dashboard in staging and approve final review.", "deps": ["impl"]}]
---


