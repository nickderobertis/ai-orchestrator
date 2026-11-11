---
title: "Status reads a live driver as dead while its journal advances"
status: "todo"
project: "dag-ui-truth-monitor-slice"
metadata:
  "orchestrator.follow-up-draft": {"schema": 1, "run": "dag-ui-truth-monitor-slice", "author": {"kind": "pacemaker", "node": null, "member": "check-in"}, "dispatch": {"scratch": "/tmp/onepipeline-scratch/check-in", "session": null, "cwd": "/home/nick/projects/ai-orchestrator", "branch": "main", "head": "4e58d297f1e76aad2887e7c9520d246a52d109e2"}, "transcript": "onepipeline monitor dag-ui-truth-monitor-slice --filter '{\"include\":[{\"member\":\"check-in\"}]}'", "repository": "github.com/nickderobertis/ai-orchestrator", "paths": ["justfile"], "drafted_at": "2026-09-17T06:19:08Z"}
---

## What happened
The run's status line read the driver as dead while its journal was still advancing.

## Where
The check-in member's reading of `just status`.

## Why it is out of scope
The member reports on the run; the status view is the engine's.

## Evidence
`just status dag-ui-truth-monitor-slice` printed DRIVER DEAD in the minute the journal recorded a node-started.
