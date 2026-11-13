> **This is a sample of the proposed follow-up ticket structure, not a ticket**: its content is copied from issue #2499 so the shape can be judged on real text, and it will be closed as not planned once the structure is confirmed.

## Root cause

A judge may challenge a manager ruling but cannot emit the recognized decline while running read-only. Its disagreement can appear only as prose in a verdict, leaving the manager without the typed challenge the run is supposed to surface.

## Impact

**Who is affected.** Developers and managers delivering changes through onepipeline.

**What it costs them.** Read-only judges cannot use the command-based decline path; the host currently relies on verdict prose that does not become a typed challenge.

**What it costs the product owner.** This adds diagnosis and repeated work to delivery in the affected situation; no data loss or measured extra monthly spend is established.

<details>
<summary>Cost of the workaround</summary>

- Applying it: Have the judge explain the departure in its verdict and have the manager read and handle it manually. This requires manual intervention per occurrence.
- Side effects on the outcome: none once completed; delivery is delayed.
- Discovery: the draft required diagnosis; the failure does not directly prescribe this workaround.

</details>

- Severity: high
- Workaround: Have the judge explain the departure in its verdict and have the manager read and handle it manually.
- Severity with the workaround: medium
- Priority estimate: high (severity with the workaround medium; fires consistently; 1 occurrence; raised one level because it fires consistently)

## Examples

Read-only judges cannot use the command-based decline path; the host currently relies on verdict prose that does not become a typed challenge.

## Suggested fix

Add an optional structured manager-ruling decline to the judge verdict and have the engine validate its reference and emit the existing typed challenge on the judge’s behalf.

<details>
<summary>Contributing locations</summary>

- onepipeline `src/ruling.rs`: resolves manager rulings for the write-based decline path.
- onepipeline `src/verbs.rs`: accepts declines only through a channel-writing verb.
- onejudge `crates/onejudge/src/provider.rs:381`: owns the verdict contract without a structured ruling-decline field.

</details>

### Workflow — `onepipeline`

Judge verdict gains an optional declined_manager_ruling object with ruling reference and reason. The engine refuses user rulings and unknown references and emits one existing declined-ruling surface for a valid manager ruling.

### Verdict contract — `onejudge`

Judge verdict gains an optional declined_manager_ruling object with ruling reference and reason. The engine refuses user rulings and unknown references and emits one existing declined-ruling surface for a valid manager ruling.

## Evidence

A read-only judge in node `aio-adopt-ruling-provenance` disagreed with a manager ruling and could say so only as prose in its verdict, so no typed decline reached the manager. The contributing behavior was verified in the trees on this item's Basis field against that occurrence.
