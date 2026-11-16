<!-- The body the owning run's verifying agent authors for onetaskgraph#4039
     (tests/fixtures/follow-ups/onetaskgraph-4039.md), as the shipped task teaches: each collapsed
     block's content back in its plain place, no estimate line, which onefollowup writes, and no
     `- Severity:`, `- Workaround:` or `- Severity with the workaround:` line, whose values are the
     record's `impact` alone. From onefollowup v0.7.0's
     crates/onefollowup/tests/fixtures/board/onetaskgraph-4039.authored.md, less those lines. -->
## Root cause

A metadata write rereads the board’s field definitions whenever a configured projection is empty, even when that projected value did not change. Tickets without transcripts therefore pay an extra shared API request on every later metadata update.

## Impact

**Who is affected.** Follow-up automation updating records with absent projected values.

**What it costs them.** One additional board-fields request per affected metadata write.

**What it costs the product owner.** Repeated maintenance consumes shared API allowance and risks delaying board workflows; the evidence does not measure an outage or spending increase.

**Cost of the workaround.**
- Applying it: Retain draft material through subsequent maintenance; manager-only tickets still have no transcript.
- Side effects on the outcome: No work is lost; verification or delivery is delayed.
- Discovery: The extra request is visible only in request telemetry, so investigation is needed.

## Examples

The transcript records an extra board-fields read when transcripts were absent, and no such read with the drafts present.

## Suggested fix

Compare old and new rendered projection values before resolving field definitions. When all projections for the changed metadata key are unchanged, write the metadata body without reading or writing board fields.

**Contributing locations.**
- onetaskgraph `crates/onetaskgraph-github-projects/src/lib.rs`: Resolves projection board fields before determining whether any projected value moved.
- onetaskgraph `crates/onetaskgraph-github-projects/src/lib.rs`: Requires every projected field to be present on the item to avoid a board-field read.

### Unchanged empty metadata projections must not reread board fields — `onetaskgraph`

Compare old and new rendered projection values before resolving field definitions. When all projections for the changed metadata key are unchanged, write the metadata body without reading or writing board fields. This changes behavior without adding a public interface.

## Evidence

The transcript records an extra board-fields read when transcripts were absent, and no such read with the drafts present. The basis resolves the board unconditionally for a projected metadata key, then falls back to a board read when an empty projection is not carried by the item.
