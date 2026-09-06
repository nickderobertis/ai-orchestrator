# The plan-review refusals this host has paid for

`just review-plan` spends one judged turn per task and answers with a verdict. Until
this change that verdict carried **one** finding — `config/plan-review-verdict.schema.json`
declared `passes` and `reason`, and `REVIEW_PROMPT` asked for "one sentence saying why"
— so a reviewer that could see three defects was contractually able to report one. The
author corrected that one, came back, and paid again. Eleven such rounds over two plans
sit behind this document, two of them spent oscillating between opposite wrong answers
on a single criterion that one pass listing both objections would have closed.

The verdict now carries a list of findings, each naming the criterion it is about and
why that criterion is refused, and the schema admits a refusal only with at least one
finding and a pass only with none. That is the change. This document is the evidence it
was designed against: every refusal this host has recorded, classified.

## The corpus

[`plan-review-refusals.json`](plan-review-refusals.json) beside this file holds it. It
is captured **whole rather than sampled**, and its own header states where it came from,
what was selected, and the cutoff:

* **Source.** The `oneharness` history this host records for every review turn —
  `oneharness.plan-review.toml` sets `history = true`, so each turn's prompt and answer
  land under `$XDG_STATE_HOME/oneharness/history/`. That is the only place a refusal
  survives: `orchestrator/plan_review.py` records a **pass** onto a plan's task and
  nothing at all otherwise, by design, so a plan's own records hold no refusal to read.
* **Selection.** Every recorded plan-review turn of this repository's checkouts whose
  answer parsed as a verdict and whose `passes` was false:

  | Measure | Count |
  | --- | ---: |
  | verdicts_recorded | 721 |
  | passing_verdicts | 354 |
  | refusals | 367 |
  | distinct_tasks | 86 |
  | plans | 13 |
* **Cutoff.** 2026-09-05, the last review turn recorded before this change was
  dispatched.
* **Base.** `8b695645`, the commit the widening started from. `already-caught` and
  `newly-caught` are decided by running `orchestrator/criteria_guard.py` from that
  commit and from the finished tree over each refusal's own reviewed task, so both are
  re-takeable rather than asserted.

The eleven rounds the change was commissioned over are named nowhere as eleven
individual refusals — the task that commissioned it counts them and names the two plans
they fell in. So the corpus is deliberately the **superset**: every refusal this host
recorded, which contains those eleven along with everything else, rather than a
reconstruction of which eleven they were.

Each refusal carries the reviewer's own recorded reason, the plan and task it was about,
a **shape**, and a **classification**. The classification is what this change is
answerable for and it is decided by running the detectors rather than by reading:

| Classification | How it is decided | Count |
| --- | --- | ---: |
| `already-caught` | `orchestrator/criteria_guard.py` refused those criteria on the commit this dispatch started from, so the judged turn need not have been spent at all | 15 |
| `newly-caught` | The detectors this change widens refuse it on the finished tree and did not refuse it on that base | 19 |
| `left-to-the-judge` | No detector refuses it, and its shape names the false refusal a detector for it would risk | 333 |

The **shape** is assigned by a stated rule over the refusal's own recorded reason, and
the reason is stored beside it so the assignment can be re-read rather than taken on
trust. Shapes are supporting detail; nothing above rests on them except which
false-refusal risk a `left-to-the-judge` entry is named against.

## What the two detectors were widened against

The task this work came from names two detectors — the one for a criterion depending on
state outside the worker's own dispatch, and the one for a criterion prescribing a
procedure in place of the property it stands for. Both were widened, and both widenings
are bounded by the same measurement: **no task that passed review is newly refused**.
The passing verdicts counted above were re-checked against the widened detectors, which
refuse none of the tasks behind them.

* **`PROCEDURE` — red before green.** These are the `procedure` (17 newly caught)
  entries, all of one demand: that each assertion be *observed failing for the intended
  reason before it passes*. It is good
  practice, and `config/dispatch-appendix.md` tells a worker to do it; as a **criterion**
  it asks for a development step the finished tree cannot carry, so a judge either takes
  the worker's word for it or fails work that did it. This host has paid the second — the
  appendix records a node failed because its own criteria required its assertions be
  observed failing. The idiom appears in **no** verdict that passed: every plan that
  eventually cleared review had dropped it.
* **`OUT_OF_DISPATCH` — a publication with a word in the way.** These are the
  `outside-dispatch` (2 newly caught) entries, whose criteria said `are both published at
  that version` and `the release carrying it is actually published`. `is published` was already on that list; those two differ from it
  by an adverb and by the number of the subject. The entries are patterns rather than
  phrases now, and the publication one tolerates up to two intervening words.

Both widenings are driven in `tests/test_criteria_guard.py`, over criteria taken from
the corpus and beside sound criteria of the same shape that pass before and after.

## What was deliberately left to the judge, and why

Each entry names its shape, and each shape in the corpus header names the false refusal a
detector for it would risk. The four that carry most of the weight:

* **`goal-not-proved` (115 left).** *The criteria could all be satisfied while the goal is
  missed.* Deciding it needs a model of the goal. The nearest deterministic form refuses
  a criterion that permits an alternative outcome — the shape of every sound conditional
  criterion this repository writes.
* **`outside-dispatch` (62 left).** What the remainder rests on is a live external
  service, a nondeterministic tier, or another node's landing, and none of those is
  lexically distinct from a property of the finished tree. `the judged lint over this
  diff is green` is a criterion this repository's own nodes state and pass review with;
  `is merged` would refuse `Nothing is merged, pushed, or opened`, which the corpus
  carries on a refused *and* a passed version of one task.
* **`procedure` (65 left).** Past the red-before-green idiom the shape is ordinary
  prose. The general form keys on `is run` / `has been run`, which appears in 22 tasks
  that passed review and 23 that were refused.
* **`perishable` (35 left).** `VERSION_LITERAL` already refuses the spelled-out number.
  Refusing what is left — `the newest release it could take` — would refuse the exact
  criterion `tests/test_criteria_guard.py` holds up as the *correct* replacement for a
  version literal.

Every one of those is the trade `orchestrator/criteria_guard.py` already states: this
check refuses a plan outright, so it is written to **miss** a criterion that names its
subject in prose rather than to refuse a sound one, because a false refusal blocks
correct work and gets worked around.

## What this change costs an operator

`config/plan-review-verdict.schema.json` is one of the files hashed into every review
key, so **every review record this host holds is invalidated by this change**. A record
is invalidated rather than lost: the task's content is untouched, and what no longer
matches is the bar the record was granted under. `just check-plan <source:project>` will
refuse each such task for carrying no review record for what it currently says, and
`just review-plan <source:project>` re-reviews them — one judged turn per task, picking
up where a previous run stopped, since it re-reviews only what carries no record.

Two things follow. A plan held on the `plans` board cannot be cleared here at all, since
a record is an entry of the task's own Markdown document and a board has none — so a
board plan is re-reviewed by drafting it in the `authoring` source and copying it over. And the invalidation is the mechanism working:
it is the same rule that invalidates a record when `personas/planner.yaml` moves, and it
is why this node was sequenced after every other change to this repository in its plan.
