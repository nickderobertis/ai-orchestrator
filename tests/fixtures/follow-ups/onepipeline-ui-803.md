---
# onepipeline-ui#803 on the `followups` board, one of the tickets this host's own module wrote at
# schema 15 (ai-orchestrator 8bf72a30), read on 2026-11-16 after the relabel set its record's
# `schema` to 14 and wrote its `impact` from its body lines. Its record and body as they stand
# there, with one term replaced: the repository `onepipeline-ui` is the verifying journey's
# public scratch identity `some-service`, the one repository its registry declares public, in
# the record, the title and the body alike. The `<a id>` anchors the GitHub store adds on write
# are left out, as a read takes them off.
title: "some-service: The scroll-back journey must survive the first-screen handoff"
origin: "drafts:A-large-run-s-first-screen-opens-in-under-one-second/tickets/scroll-journey-survives-first-screen-handoff"
metadata:
  orchestrator.follow-up: {"schema": 14, "root_cause": "scroll-journey-survives-first-screen-handoff", "repository": "github.com/nickderobertis/some-service", "created_by_run": "A-large-run-s-first-screen-opens-in-under-one-second", "owning_runs": ["A-large-run-s-first-screen-opens-in-under-one-second"], "drafts": ["drafts:A-large-run-s-first-screen-opens-in-under-one-second/drafts/20261113T131539Z-make-the-read-latency-scroll-back-journey-wait-o", "drafts:A-large-run-s-first-screen-opens-in-under-one-second/drafts/20261114T105411Z-dag-ui-live-updates-resume-journey-reads-its-cur"], "draft_sources": {"drafts:A-large-run-s-first-screen-opens-in-under-one-second/drafts/20261113T131539Z-make-the-read-latency-scroll-back-journey-wait-o": "author node; node first-view-under-1s-3", "drafts:A-large-run-s-first-screen-opens-in-under-one-second/drafts/20261114T105411Z-dag-ui-live-updates-resume-journey-reads-its-cur": "author node; node overall-first-screen-sub1s-2"}, "transcripts": {"drafts:A-large-run-s-first-screen-opens-in-under-one-second/drafts/20261113T131539Z-make-the-read-latency-scroll-back-journey-wait-o": "onepipeline transcript A-large-run-s-first-screen-opens-in-under-one-second first-view-under-1s-3", "drafts:A-large-run-s-first-screen-opens-in-under-one-second/drafts/20261114T105411Z-dag-ui-live-updates-resume-journey-reads-its-cur": "onepipeline transcript A-large-run-s-first-screen-opens-in-under-one-second overall-first-screen-sub1s-2"}, "basis": {"github.com/nickderobertis/some-service": "929baf62ea466e965fec94aa096332ede41a0b67"}, "witnesses": ["github.com/nickderobertis/some-service apps/dag-ui-e2e/read-latency/wire-and-errors.spec.ts:137 at 929baf62ea466e965fec94aa096332ede41a0b67", "github.com/nickderobertis/some-service apps/dag-ui-e2e/read-latency/wire-and-errors.spec.ts:145 at 929baf62ea466e965fec94aa096332ede41a0b67"], "verified_at": "2026-11-15T11:30:16Z", "host": "shulgin", "priority_estimate": "low", "frequency": "intermittent", "board_estimate": "low", "impact": {"Severity": "medium", "Workaround": "Rerun the unchanged journey after the transient failure.", "Severity with the workaround": "low"}}
---
## Root cause

The scroll-back check assumes a fixed one-second pause leaves a stable page. The first-screen handoff can still replace its button during scrolling, so a correct page intermittently fails the journey on a busy host.

## Impact

**Who is affected.** Developers publishing UI changes.
**What it costs them.** An intermittent false gate failure requires rerunning the journey.
**What it costs the product owner.** The affected workflow is unreliable in these conditions and consumes additional operator time; no broad outage is established.

<details>
<summary>Cost of the workaround</summary>

- Workaround: Rerun the unchanged journey after the transient failure.
- Applying it: Rerun the unchanged journey after the transient failure. This costs an extra operator step per occurrence.
- Side effects on the outcome: none; the intended result is preserved.
- Discovery: the transcript supplies the workaround, but the failure itself does not reliably direct a new operator to it; an isolated rerun is an ordinary response to the reported test failure.

</details>

<details>
<summary>Priority calculation</summary>

- Severity: medium
- Severity with the workaround: low
- Priority estimate: low (severity with the workaround low; fires intermittently; 1 occurrence; not raised)

</details>

## Examples

Two dispatch transcripts show the scroll action failing because its element detached; isolated reruns passed. The basis still pauses for one second and then scrolls without observing the handoff.

## Suggested fix

Synchronize the journey with completion of the first-screen handoff before scrolling the earlier-rows button, keeping the existing paging assertions and timeout unchanged.

<details>
<summary>Contributing locations</summary>

- some-service `apps/dag-ui-e2e/read-latency/wire-and-errors.spec.ts`: uses a fixed pause before acting on a button the handoff can replace.
- some-service `apps/dag-ui-e2e/read-latency/wire-and-errors.spec.ts`: scrolls during the replacement window.

</details>

### some-service — `some-service`

Synchronize the journey with completion of the first-screen handoff before scrolling the earlier-rows button, keeping the existing paging assertions and timeout unchanged. The public interface stays unchanged; the behavior above changes.

## Evidence

Two dispatch transcripts show the scroll action failing because its element detached; isolated reruns passed. The basis still pauses for one second and then scrolls without observing the handoff.
