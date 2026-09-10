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
* **`perishable` (35 left).** A deterministic rule refused the spelled-out number when
  this was measured. Refusing what is left — `the newest release it could take` — would
  refuse the exact criterion `tests/test_criteria_guard.py` held up as the *correct*
  replacement for a version literal. The whole shape is the judge's now; see [Two of
  these detectors have since moved to the judged tier](#two-of-these-detectors-have-since-moved-to-the-judged-tier).

Every one of those is the trade `orchestrator/criteria_guard.py` already states: this
check refuses a plan outright, so it is written to **miss** a criterion that names its
subject in prose rather than to refuse a sound one, because a false refusal blocks
correct work and gets worked around.

## Two of these detectors have since moved to the judged tier

Everything above is dated to its own cutoff, and two of the detectors it counts against
are no longer in `orchestrator/criteria_guard.py` at all: the **version literal**, and
the **phrase matching** that refused criteria for being silent about a demand their own
bar makes. Read an `already-caught` entry refused by either of those as one the judged
turn now owns again — the classification records what the detectors did on the base
commit this corpus was captured against, and nothing re-decides it.

They moved because the two tiers were refusing each other's required wording rather than
adding up, which cost three judged rounds on real plans. One review prescribed its own
remedy — pin the immutable version — and the deterministic rule then refused it outright.
Another refused a criterion for pinning a spelling in place of a property while the
deterministic rule refused the same task for lacking a literal phrase the criterion stated
across three sentences without using those words; and because a review record is keyed on
the task's own authored content, inserting words to satisfy a matcher invalidated the
record and bought another judged turn.

**A third rule was named for the same move and turns out not to exist.** The case for
moving these described the deterministic tier as also refusing a *superlative* — a
criterion pinning "the newest", "the best", "the most recent" — and it never did: the
example given was the judged tier refusing a superlative and the deterministic tier
refusing the immutable version offered in its place, which is the version-literal rule
above wearing a second description. Nothing was relocated for it, nothing was deleted, and
this paragraph is here so the next reader does not go looking for the rule that moved.
`perishable` is the shape both halves of that example fall in.

The line the split now follows is whether the rule needs judgment. *Whether a criterion's
truth turns on an event outside the dispatch* does not, and stays deterministic. *Whether
a number is the right number*, and *whether the criteria answer a demand by meaning
rather than by phrase*, do, and one verdict can hold both considerations at once where two
tiers could only compound. A third question joined them for the same reason: whether a
node whose criteria describe work that changes no repository file declares
`expects_no_diff`, which is a reading of prose.

**One shape moved the other way**, out of `outside-dispatch`'s left-to-the-judge pile and
into the deterministic list: a criterion asserting that somebody else's **released
artifact** exists, or that it carries a named change. That is not a fact about the finished
tree under any wording, and establishing it means going and reading another repository. One
such criterion required a pin to name a plan-store release carrying two fixes that no
release archive can carry; the worker correctly determined it could not be satisfied, and
the node was killed and settled by hand while the rest of its work was complete and landed.
It is bounded the way every widening above is — `tests/criteria_examples.py`'s
`RELEASED_ELSEWHERE_IN_PROSE` holds the sound criteria of the same shape, including the
corresponding-content correction the refusal itself recommends — and it is labelled
coverage rather than corpus evidence, because the corpus stores a fragment per refusal and
never the criterion it came from.

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

## The shape this document had no answer for, and now does

The corpus above classifies `outside-dispatch` as a shape left to the judge, because what a
criterion rests on "is a live external service, a nondeterministic tier, or another node's
landing, and none of those is lexically distinct from a property of the finished tree." That
is right about detection. What it did not record is that for one class of node the judge had
**no acceptable answer to give**, and refused every wording in turn.

The class is a node whose deliverable *is itself* an assertion about something outside the
tree: an inventory of another system's settings, a pinned copy of a published contract, a
fixture mirroring a service's shape. `config/merge-path-checks.json` — this repository's own
inventory of the checks each merge path requires — is exactly one.

Asked for criteria over such a node, the reviewer refused all three available shapes:

| shape | the refusal |
| --- | --- |
| consult the outside source during the dispatch | *"depends on a live GitHub response that the dispatch does not control and that may be unavailable or change during the work"* |
| capture and report that consultation | *"requires a particular evidence-gathering and reporting procedure ('read', 'quoted', and 'reports') in place of a dispatch-controlled property"* |
| describe only the finished file | *"can be satisfied with an invented, obsolete, or incomplete check set, so it does not prove the task's stated goal"* |

Those three are mutually exclusive and exhaustive. Each is individually defensible; together
they admit nothing. Four planner dispatches and thirteen judged review turns went into one
plan before it was recognised as a trap rather than as bad wording — and rounds three and
four contradicted each other outright, the third asking for "proof anchored to captured
authoritative evidence" and the fourth refusing that capture as a procedure.

The bar has since been given a **corresponding-content** shape that this class of node can
satisfy. What it requires is stated once, where the bar is stated; this record is about the
refusals that made it necessary.

### The second half of the same trap

The same reviewer refused *"the checks that exercise this change are green over the finished
tree … and the judged lint over this diff"*, for making *"a nondeterministic paid external
judge part of the node's acceptance bar."*

That contradicted the very bar it was applying, which requires a node that changes code to
show the lint over its diff green — and it contradicted the section above, which records
`the judged lint over this diff is green` as "a criterion this repository's own nodes state
and pass review with". Every node that changes code was exposed to it. The bar has since
been corrected on that point too.

### How this change was landed, and why that is recorded here

Not through a dispatched plan. The plan written to make this repair was refused by the bar it
was repairing — three criteria, then one, in three shapes, ending with the reviewer asking
for "dispatch-controlled contract-level proof of the review behavior" after having refused
exactly that. A plan whose subject is the reviewer needs criteria about reviewer behaviour,
and no criterion about reviewer behaviour passed. With no escape hatch by design, and tracked
files reachable only through a dispatched plan, this host could not plan a change to its own
review bar.

The operator waived the self-dispatch rule for this one change. It was authored in an
isolated worktree, never the canonical checkout, and published through the ordinary merge
path so the `pre-push` gate verified it like anything else. **The waiver records a deadlock
rather than setting a precedent**: what it set aside is that every tracked change is reviewed
at plan level, and what made it safe to set aside once is that the change is small, its
wording was specified in an approved design document beforehand, and the merge path verified
it unchanged.

**What is deliberately not claimed here.** Nothing above asserts how the reviewer behaves
*after* this change. That is a live judged tier, and the honest way to learn it is to run
`just review-plan` against a plan carrying the corresponding-content shape and read what
comes back. The open questions about the reviewer's demandingness — whether it contradicts
itself across rounds, whether the loop should have a convergence bound, and whether its
judgment agrees with the two documents above when tested directly — are untouched by this
change and remain follow-up work.
