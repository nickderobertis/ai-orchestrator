---
title: "review"
project: "plan-example"
status: "todo"
depends_on: ["plan-example/api", "plan-example/ui"]
metadata:
  "onepipeline.id": "review"
  "onepipeline.persona": "reviewer"
---

## What
Review the URL shortener end to end (api + ui + docs) for correctness, missing edge cases, and security, and report verified, severity-ranked findings.

## Why
The api, ui, and docs subtasks each landed against their own slice, so nobody has yet read the three together for the gaps that only appear at their seams.

## Acceptance criteria
- Each finding names the file and line it is in and the concrete input or state that triggers it.
- Each finding was verified against the code rather than inferred from a diff alone.
- Findings are ranked most severe first, and the report says plainly when a dimension turned up nothing.
