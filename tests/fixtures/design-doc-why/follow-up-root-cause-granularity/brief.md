Plan project: authoring:follow-up-root-cause-granularity

Repository: github.com/nickderobertis/ai-orchestrator (this repository; self-dispatch rule
applies). Write the plan into the `authoring` source.

## What

Change what a follow-up run means by a **root cause**, so that two reports of one problem
become one ticket — inside one run when it groups its drafts, and across runs when it
searches the `followups` board before filing.

Today a root cause is, in effect, **one code location**: the thing a run's own evidence
pointed at. Two runs (or one run with two drafts) that hit the same underlying gap from
different files each file a ticket, because each pins the cause on its own file. The
change is to define a root cause as **the broken invariant or missing guarantee** — the rule
the system should hold and does not — with **every code location that contributes to it**
listed under it, and to make the duplicate search and the grouping step work at that level.

Three parts, all in what this host tells the follow-up agent and the tooling that holds it
(`templates/follow-up-task.md.j2`, its includes under `templates/follow-up-task/`,
`orchestrator/follow_up_tickets.py` and its validators, and the tests over them):

1. **The definition.** A root cause is stated as the invariant or guarantee that is missing
   or broken, in words that do not depend on which file a run happened to see. A ticket
   names every contributing location (possibly several, possibly in several repositories)
   under it. The bar for one root cause versus several: **one root cause when a single change
   to the rule removes every instance, even if that change touches several places**; two
   when they need independent fixes that each stand alone. This must not degrade into
   catch-all tickets ("the follow-up tooling is awkward"): each ticket still carries exactly
   one concrete suggested fix, which the existing format already allows to span several
   units and repositories.
2. **Grouping within a run** (today's step 5, "Group what stands by root cause") applies
   that definition, so a run's own drafts that share an invariant become one ticket.
3. **The board search before filing** (today's step 7) looks for the same root cause at that
   level: text searches are phrased in the words of the symptom and the invariant, not only
   the failing file or function, because the file is exactly what differs between two
   reports of one cause. A match against an open item — a `Proposal` included, as today's
   same-root-cause path already allows — takes this run's evidence as a comment (and, where
   the new evidence shows the cause is broader than the item states, widens the item's
   stated root cause and contributing locations rather than filing a sibling). Decide what
   becomes of today's exact-match `root_cause` slug search, which in practice almost never
   matches across runs because each run invents its slug.

## Why

The user, reviewing the `followups` board, keeps finding duplicates:

> "duplicates from follow up agent - It seems to be not doing good enough search for
> existing issues before filing a new one. I have recently encountered multiple duplicates."

and, on the diagnosis:

> "I wonder if the problem is actually more structural like are we defining root cause to
> always be a single code location? maybe if root cause could multiple things that work
> together to cause the issue that would resolve the contention?"

Every duplicate costs the user a review of a ticket that adds nothing, splits one problem's
evidence and priority across two items (so neither reaches the occurrence count that raises
its priority), and risks two plans fixing one problem twice, or fixing half of it.

## The concrete examples

Both from 2026-10-08, flagged by the user in comments on the board.

- **#1634 "Plan checks miss destination CI title policy"** (root cause slug
  `publication-title-policy-missing-ci-validation`, located in
  `orchestrator/publication_guard.py`) duplicates **#1332 "planner guidance omits the
  breaking title for a UI engine relink"** (slug `planner-omits-engine-break-title`, located
  in `personas/planner.yaml`). #1332's suggested fix, rewritten on 2026-10-06, is already
  general: read the target repository's contribution and release policy before choosing a
  node title. Every plausible text search ("node title", "commit-msg", "Conventional Commit",
  "pr-title", "title") returns both today, so the search very likely found #1332 — and the
  agent filed anyway because the two named different files. The shared invariant is "a
  lifecycle node's title is never checked against the destination's title and release
  policy before dispatch", with the planner guidance and the plan check as two contributing
  locations. #1332 was still a `Proposal` at the time.
- **#1637 "Follow-up verification cannot select one draft in a live run"**
  (`scripts/follow-ups.sh`) and **#1638 "Comment selection filters ownership after reading
  unrelated items"** (`orchestrator/follow_up_comments.py`) were filed by the **same** run 8
  seconds apart, from three manager drafts written in one sitting. The shared invariant is
  "the follow-up recipes can only act on a whole run or the whole board, never one named
  item". The run's grouping step split them by file. (#1638 has since been fixed on `main` by
  `3414832a`; under one ticket that fix would have been scoped to cover both.)
- Earlier pairs a manager cleanup closed on 2026-10-02, each filed days apart and each the
  same defect in different words: onepipeline #650/#320, #522/#581, #638/#622; onetaskgraph
  #3040/#1141.

## What the manager read (so the planner can confirm, not take on trust)

- The `## Root cause` placeholder in the ticket example
  (`orchestrator/follow_up_tickets.py`, `_HEADING_GUIDANCE`): "a simple explanation of the
  root cause, naming the paths inside the repository where it lives" — a single-home
  framing.
- The ticket record's single `repository`, described in the template as "the repository its
  root cause lives in".
- The root cause is one kebab-case slug that names the ticket's file and is the exact-match
  key of the metadata search; it also appears in the evidence-comment marker and the
  disposition account.
- Step 7's text-search guidance: "once per distinct question about its failing function,
  file, command or message".
- Step 7's accepted-fixes rule: "A `Proposal` or `Deferred` item's fix is never assumed".
  The manager's reading is that the definition change routes the examples above through the
  existing same-root-cause path (which already applies to open `Proposal` items) without
  touching this rule; confirm or refute that.
- The verifying runs' transcripts were on other hosts (`shulgin`, `U-17UN402ICR95C`), so
  which searches the agents actually ran is inferred, not read. Tickets record no rejected
  near-matches.

## Constraints

- AGENTS.md's follow-up rules stand unless this work changes them, and AGENTS.md must agree
  with the result: the board is searched, never listed; every query goes through
  `python -m orchestrator.follow_up_tickets board-items`; one query per distinct question,
  answers reused; a bound ticket makes no duplicate search; status rules and who may move an
  item are unchanged.
- Existing tickets on the board, their record schema and their slugs keep working: a later
  run still finds, binds and comments on a ticket filed under today's shape. If the record
  schema moves, older records are read and brought forward the way this module already
  handles schema changes.
- One ticket, one concrete suggested fix — unchanged.
- No repository-specific rule in a general instruction (the user has said this before about
  `personas/planner.yaml`; it applies here too).
- This repository is a configuration layer over published libraries: if the change needs a
  plan-store capability `onetaskgraph` lacks (for example a search it cannot narrow by),
  surface it as a gap rather than filling it here.
- No cleanup of the existing duplicates (#1634, #1637) in this plan — the user will decide
  that after the run.

## Suggested implementation (a suggestion only)

Likely one node, since the definition, the grouping step, the search step and the validator
all change together; split only if a real seam appears. Consider: rewording the
`## Root cause` guidance to "the invariant or guarantee that is missing, then each
contributing location"; giving the search step a required first query phrased as the
invariant/symptom; having each filed ticket's disposition (or the ticket) record the
near-matches the search returned and why each was judged a different root cause, so a
duplicate can be diagnosed later without the transcript; and either dropping the slug
metadata search or keeping it only as a cheap first probe. Whether a validator can hold any
of this deterministically, and what stays the agent's judgment, is the planner's call.

## Acceptance criteria

- The plan's nodes, taken together, make a follow-up dispatch define a root cause as the
  broken invariant or missing guarantee with every contributing location listed under it,
  and state the one-root-cause-versus-several bar so that both pairs in "The concrete
  examples" would each be one ticket while independent fixes stay separate tickets.
- The plan makes the within-run grouping step and the pre-filing board search both apply
  that definition, with search phrasing that does not depend only on the failing file, and
  decides the fate of the exact-match slug search.
- The plan keeps tickets filed under today's shape findable, bindable and commentable by
  later runs, and keeps every AGENTS.md follow-up rule named under Constraints, leaving
  AGENTS.md agreeing with the new behaviour.
- Each node's criteria name the tests over the template and tooling it touches.
- Every fork the brief leaves open is decided in the plan or asked of the manager, and every
  exception to a constraint is escalated rather than settled silently.
