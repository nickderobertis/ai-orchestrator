---
title: Follow-up root-cause granularity
project: follow-up-root-cause-granularity
metadata:
  onetaskgraph.template:
    answers_digest: sha256:fd2857993c0edde72b67ce0ce6cea8fa5da741e6494704e779a7733082cc9850
    body_digest: sha256:c309affa3617980926c941fb0645ad3eb7e3510d2a32466d3307f7067aca74a1
    digest: sha256:3066b7ea6ceb04f5ce64b4394bce4a241ee2bd7960ad6d081d06776d129f26b6
    template: onepipeline:design-doc
  "onetaskgraph.copies": {"plans":"plans:I_kwDOTXCWqs8AAAABWFkJfQ"}
---
## What

Make follow-up runs file one ticket per broken rule or missing guarantee, gathering every contributing location under it. Reports from different files, repositories or runs become one problem when one change to the rule would resolve them all; independently fixable problems stay separate. Tickets show how duplicates were searched for and which other tickets their work relates to.

## Why

The user reports: “duplicates from follow up agent - It seems to be not doing good enough search for existing issues before filing a new one. I have recently encountered multiple duplicates.” They ask: “I wonder if the problem is actually more structural like are we defining root cause to always be a single code location? maybe if root cause could multiple things that work together to cause the issue that would resolve the contention?”

Duplicates waste review time, split evidence and priority, and risk fixing the same problem twice or only half of it.

## Architecture

The follow-up instructions guide the agent’s judgment about causes, grouping and search; the ticket tooling checks the resulting structure and evidence in numbered stored ticket formats, called schemas (schema 10 is the new version; schemas 7–9 are older versions). One coordinated change updates those instructions, their supporting documentation, the stored ticket format and its validators, while retaining the `root_cause` slug—the short identifier a run makes up for a root cause, which also names the ticket file. Existing tickets can receive evidence even in Proposal or Deferred, board statuses for tickets nobody has accepted yet. Tests exercise the real local Markdown board and dispatched instructions; deciding whether two reports truly share a cause remains agent judgment.

### Follow-up workflow — `ai-orchestrator`

This workflow turns drafts from completed runs into verified tickets or evidence on existing tickets. The change covers templates/follow-up-task.md.j2 and its ownership and older-schema includes, orchestrator/follow_up_tickets.py and its validators, the follow-up tests, AGENTS.md, docs/orchestration.md and personas/follow-up.yaml.

#### Schema 10 ticket bodies, with older tickets still readable

**Reversibility: high cost** — Board items persist beyond the writing run and are read by older installed tools and other hosts; undoing their format would require coordinating those readers and rewriting stored items.

Schema 10 separates the broken guarantee from its contributing locations and leaves a terse search record on the board, where reviewers can read it without the originating host’s transcript. The following diff shows the changed body sections; intervening sections remain unchanged.

```diff
 ## Root cause
-<cause stated in free-form prose>
+**Invariant.** <the missing or broken guarantee, independent of file names>
+**Contributing locations.**
+- <repository>: <path>
+[one bullet for every contributing location]

 [existing intervening sections unchanged]
 ## Owning runs
 [existing content unchanged]
+
+## Related tickets
+- [<ticket title>](<ticket URL>) — dependency: <one sentence stating what this ticket needs from it>
+- [<ticket title>](<ticket URL>) — related: <one sentence stating why it is related or different>
+
+## Duplicate search
+<one line naming the text queries run>
+[<near-match title>](<ticket URL>) — <why one change would not remove both>
+[one line per open item judged distinct, or none]
```

The two Root cause labels must appear in that order, with at least one location bullet naming a repository and path. Duplicate search is mandatory, nonempty and last; it never repeats a matched item’s content. Related tickets is optional, immediately before Duplicate search, and omitted entirely when empty: no empty heading or `none`. Each entry is exactly a Markdown link using the other ticket’s title, a `dependency` or `related` mark, and one sentence; bare URLs, extra sentences and continuation lines are refused.

Every recorded `depends_on` entry on an accepted ticket must have its URL on a `dependency` bullet, checked by `board-status`; the body still explains where and how that accepted fix changes this ticket. Independent issues contributing to the broader problem are `related` at any status; a Proposal or Deferred item adds no dependency and its fix is never assumed. A near-match may appear in both sections because search history and related work serve different purposes.

Schemas 7–9 remain valid without these additions, retain their anywhere-in-the-body dependency-URL rule, and remain searchable, copyable, commentable and re-estimable. Rewriting this run’s older ticket upgrades it to schema 10 using its existing evidence, dependencies and related mentions; when no search was run, Duplicate search says no search was recorded when filed. Other runs’ bodies and schemas remain untouched. `COMMENT_MARKER`, `COMMENT_OPENING`, `REPLY_MARKER`, reply openings and existing record keys remain byte-identical; the derived forbidden-heading list also rejects Duplicate search and Related tickets in evidence comments.

**Reversible:**

- **Cause and grouping.** Step 5 and `## Root cause` define one cause by whether one concrete rule change removes every instance, retain one suggested fix per ticket, reject catch-all causes, and make the title and `root_cause` slug name the invariant rather than a file.
- **Repository meaning.** The record’s `repository` identifies where the issue is filed, and the repository its fix changes when there is one, rather than claiming the cause occupies only one repository.
- **Search order.** For an unbound ordinary ticket, step 7 replaces `board-items --metadata orchestrator.follow-up/root_cause=<slug>` with invariant-and-symptom `--search`, asks each further symptom, command or message question once, and searches by file or function last.
- **Search boundaries.** Budget overruns retain their deterministic `<repository name>-<budget id>-over-<cause>` slug query, internal slug lookups stay intact, bound tickets make no duplicate search, only `board-status` asks by origin, answers are reused and every query narrows the board without listing it or increasing per-ticket request count.
- **Matched identity.** A match at any open status, including Proposal and Deferred, makes the local ticket adopt the existing item’s `root_cause` in both filename and record so its evidence marker, disposition carrier check and occurrence recount agree, even when the contributing locations differ.
- **Widening and ownership.** An evidence comment on another run’s broader cause requires `Bears on the ticket:` to state only the widened invariant and added locations; this run may widen its own item by rewriting and copying it again, while ownership, status and who-moves-an-item rules stay unchanged.
- **Scope.** The implementation updates `board-items` help and the instructions consistently, adds no budget, and neither cleans up existing duplicates nor writes to live boards.

## Acceptance criteria

- Rendered follow-up instructions and ticket guidance apply the same file-independent cause definition to grouping and search, preserve one concrete fix per ticket, and contain no issue, repository or file from the motivating examples.
- The shipped graph’s dispatch supplies invariant-first narrowing searches for ordinary tickets and slug queries only for budget overruns; a scripted search finds an older item with a different slug on a real local Markdown board.
- On that board, adopting an open Proposal item’s slug lets check-dispositions --board accept evidence from another location and re-estimate count it as an occurrence; keeping the different local slug is still refused with an explanation.
- Validation accepts the specified schema-10 shape and rejects missing or misordered root-cause parts, missing or nonfinal Duplicate search, malformed Related tickets, and dependency URLs missing from dependency bullets; evidence comments cannot carry either new ticket heading.
- Schema-7, 8 and 9 tickets remain readable under their existing rules; real-board journeys show schema-7 and 9 items can be found, receive evidence, pass disposition checks and be re-estimated without rewriting them, while this run’s schema-9 ticket upgrades and validates at schema 10 under both task modes.
- The nine follow-up test modules named in the implementation task pass, cover every added orchestrator line, and preserve the comment markers, openings and record keys; Ruff formatting and lint, mypy, and judged lint over the branch diff pass, with reported evidence valid for the finished tree.

## Planned tasks

| Task | Unit | What it delivers | Depends on | Where it lives |
| --- | --- | --- | --- | --- |
| feat(follow-ups): file one ticket per broken invariant, not per file | Follow-up workflow | Invariant-based grouping and search, schema-10 tickets, compatibility and real-board regression coverage | none | /home/nick.guest/ai-orchestrator/.plans/tasks/feat-follow-ups-file-one-ticket-per-broken-invariant-not-per-file.md |


<!-- onetaskgraph:template-answers
acceptance_criteria:
- Rendered follow-up instructions and ticket guidance apply the same file-independent cause definition to grouping and search, preserve one concrete fix per ticket, and contain no issue, repository or file from the motivating examples.
- The shipped graph’s dispatch supplies invariant-first narrowing searches for ordinary tickets and slug queries only for budget overruns; a scripted search finds an older item with a different slug on a real local Markdown board.
- On that board, adopting an open Proposal item’s slug lets check-dispositions --board accept evidence from another location and re-estimate count it as an occurrence; keeping the different local slug is still refused with an explanation.
- Validation accepts the specified schema-10 shape and rejects missing or misordered root-cause parts, missing or nonfinal Duplicate search, malformed Related tickets, and dependency URLs missing from dependency bullets; evidence comments cannot carry either new ticket heading.
- Schema-7, 8 and 9 tickets remain readable under their existing rules; real-board journeys show schema-7 and 9 items can be found, receive evidence, pass disposition checks and be re-estimated without rewriting them, while this run’s schema-9 ticket upgrades and validates at schema 10 under both task modes.
- The nine follow-up test modules named in the implementation task pass, cover every added orchestrator line, and preserve the comment markers, openings and record keys; Ruff formatting and lint, mypy, and judged lint over the branch diff pass, with reported evidence valid for the finished tree.
architecture: The follow-up instructions guide the agent’s judgment about causes, grouping and search; the ticket tooling checks the resulting structure and evidence in numbered stored ticket formats, called schemas (schema 10 is the new version; schemas 7–9 are older versions). One coordinated change updates those instructions, their supporting documentation, the stored ticket format and its validators, while retaining the `root_cause` slug—the short identifier a run makes up for a root cause, which also names the ticket file. Existing tickets can receive evidence even in Proposal or Deferred, board statuses for tickets nobody has accepted yet. Tests exercise the real local Markdown board and dispatched instructions; deciding whether two reports truly share a cause remains agent judgment.
budgets: []
plan_budgets:
  checklist:
  - budget: ''
    concern: latency
    in_scope: true
    not_applicable: 'n/a because nobody waits on a follow-up verification dispatch interactively: it runs after a run settles, and this plan adds no step to it, only rephrases one search per ticket and two short sections the agent writes.'
    summary: Follow-up verification runs after settlement with nobody waiting; this plan adds no step.
  - budget: ''
    concern: quota and rate-limit headroom
    in_scope: true
    not_applicable: 'n/a because the plan is request-neutral: each unbound ticket''s slug metadata query is replaced by one invariant-phrased text query, and budget-overrun tickets keep their one slug query. The count is the agent''s judgment, which no gate exercises since the suite doubles the paid model.'
    summary: 'Request-neutral: one slug query per ticket swapped for one invariant text query; agent judgment no gate exercises.'
  - budget: ''
    concern: how the work scales with data
    in_scope: true
    not_applicable: 'n/a because nothing lists the board: searches stay narrowing queries whose cost grows with tickets per run, not with board size, exactly as today.'
    summary: Searches stay narrowing queries; cost grows with tickets per run, never with board size.
  - budget: ''
    concern: spend
    in_scope: true
    not_applicable: 'n/a because no dispatch is added and fewer tickets are filed: merging duplicates removes review work rather than adding paid turns.'
    summary: No new dispatch; fewer duplicate tickets means less review, not more spend.
  - budget: ''
    concern: resource use
    in_scope: false
    not_applicable: n/a because the change is template prose and string checks over ticket bodies already in memory; no process, disk or memory use changes.
    summary: Template prose and string checks over bodies already in memory.
  overview: |-
    Change what a follow-up run means by a **root cause**, so two reports of one problem become one ticket — inside one run when it groups its drafts, and across runs when it searches the `followups` board before filing. One node, in this repository: `templates/follow-up-task.md.j2` and its includes, `orchestrator/follow_up_tickets.py` and its validators, the tests over them, and the prose that restates the follow-up rules (AGENTS.md, `docs/orchestration.md`, `personas/follow-up.yaml`).

    1. **The definition.** A root cause is the invariant or guarantee that is missing or broken, stated in words that do not depend on which file a run happened to see, with every contributing location listed under it. One root cause when a single change to the rule removes every instance, even across several places and repositories; two when they need independent fixes that each stand alone. Each ticket still carries exactly one concrete suggested fix.
    2. **Grouping within a run** applies that definition, so a run's own drafts sharing an invariant become one ticket.
    3. **The board search before filing** starts with a text query phrased in the words of the invariant and its symptom, file and function last. The agent's exact-match `root_cause` slug query is dropped for ordinary tickets and kept only for budget-overrun tickets, whose slug is derived deterministically. A ticket whose root cause is an open item's takes that item's `root_cause` slug, because the evidence-comment marker, the disposition account's carrier check and the occurrence recount all key on slug equality — the structural reason a matched item was filed beside rather than commented on.
    4. **The stored ticket shape moves to schema 10**: `## Root cause` is held to two labelled parts, `**Invariant.**` then `**Contributing locations.**`, and a terse last section, `## Duplicate search`, records the queries run and each open item judged a different root cause, so a duplicate can be diagnosed from the board without the verifying host's transcript. An optional `## Related tickets` section, absent when empty, lists by URL every dependency the ticket records (marked `dependency`) and each independent issue contributing to the same broader problem (marked `related`), each entry only the link and one sentence on why it is related or different, absorbing today's "clearly related item may be named" rule. Schemas 7-9 stay readable and are brought forward when a run rewrites its own ticket.

    Ownership is unchanged: no run rewrites another run's item; where new evidence shows the cause broader, its evidence comment's `Bears on the ticket:` paragraph states the widened invariant and each added location. The existing duplicates (#1634, #1637) are not cleaned up here.
  realistic_data:
  - artifact: tests/test_follow_up_tickets.py and tests/plan_tooling/test_follow_ups_recipe_e2e.py fixtures
    choice: generator
    data: Follow-up tickets and board items under the current and older record schemas, and two reports of one root cause filed under different slugs
    reason: 'The suite already builds tickets and board items on a real local-md board through the module''s own render and the store; the #1332/#1634-shaped pair (one invariant, two files, two slugs) is generated the same way rather than copied from the live board, which no test may read.'
    summary: 'Generated on a real local-md board by the module''s own render; the duplicate pair is modelled on #1332/#1634, never read from the live board.'
  repo_wide_effects: []
  schema_version: 2
  sizing: A follow-up verification dispatch on this host handles about 1-10 drafts and files 1-5 tickets, each unbound ticket asking the followups board (about 1,650 issues) one root-cause question and 1-3 text questions. This plan swaps one question for another per ticket, so board requests per ticket stay the same.
  spike_findings: []
  ten_x: 'At 10x (20-50 tickets in one dispatch) the first thing the product owner notices is one dispatch spending about 100-200 board searches on the GitHub token every session shares, approaching GitHub''s search rate limits and its secondary limiter. This plan does not change that cost: it swaps the slug question for an invariant-phrased text question one for one. No budget covers it, because the query count is the agent''s judgment, which no gate exercises (the suite doubles the paid model), and a standalone measurement would spend a paid dispatch and live board quota each time.'
  ten_x_summary: At 50 tickets in one dispatch, about 100-200 board searches run back to back on the shared token and meet GitHub's search rate limits; unchanged in kind by this plan, unbudgeted.
  workload: 'Realistic: one initial-mode follow-up dispatch per settled run; 1-10 drafts per run (the brief''s #1637/#1638 pair came from three drafts written in one sitting), grouped into 1-5 tickets; the followups board holds about 1,650 issues (#1638 was filed 2026-10-08). Today each unbound ticket asks `board-items` once by `--metadata orchestrator.follow-up/root_cause=<slug>` and 1-3 times by `--search`; after this plan it asks once by an invariant-phrased `--search` and 1-3 more by `--search` (budget-overrun tickets keep their one deterministic slug query), so the per-ticket count is unchanged. A ticket body grows by two labelled lines in `## Root cause` and a `## Duplicate search` section of one query line plus one line per near-match, typically 1-4 lines. 10x: 50-100 drafts and 20-50 tickets in one dispatch.'
planned_tasks:
- delivers: Invariant-based grouping and search, schema-10 tickets, compatibility and real-board regression coverage
  depends_on: none
  location: /home/nick.guest/ai-orchestrator/.plans/tasks/feat-follow-ups-file-one-ticket-per-broken-invariant-not-per-file.md
  task: 'feat(follow-ups): file one ticket per broken invariant, not per file'
  unit: Follow-up workflow
predates_budgets: ''
units:
- decisions:
  - content:
    - text: Schema 10 separates the broken guarantee from its contributing locations and leaves a terse search record on the board, where reviewers can read it without the originating host’s transcript. The following diff shows the changed body sections; intervening sections remain unchanged.
      type: prose
    - language: diff
      text: |2-
         ## Root cause
        -<cause stated in free-form prose>
        +**Invariant.** <the missing or broken guarantee, independent of file names>
        +**Contributing locations.**
        +- <repository>: <path>
        +[one bullet for every contributing location]

         [existing intervening sections unchanged]
         ## Owning runs
         [existing content unchanged]
        +
        +## Related tickets
        +- [<ticket title>](<ticket URL>) — dependency: <one sentence stating what this ticket needs from it>
        +- [<ticket title>](<ticket URL>) — related: <one sentence stating why it is related or different>
        +
        +## Duplicate search
        +<one line naming the text queries run>
        +[<near-match title>](<ticket URL>) — <why one change would not remove both>
        +[one line per open item judged distinct, or none]
      type: code
    - text: 'The two Root cause labels must appear in that order, with at least one location bullet naming a repository and path. Duplicate search is mandatory, nonempty and last; it never repeats a matched item’s content. Related tickets is optional, immediately before Duplicate search, and omitted entirely when empty: no empty heading or `none`. Each entry is exactly a Markdown link using the other ticket’s title, a `dependency` or `related` mark, and one sentence; bare URLs, extra sentences and continuation lines are refused.'
      type: prose
    - text: Every recorded `depends_on` entry on an accepted ticket must have its URL on a `dependency` bullet, checked by `board-status`; the body still explains where and how that accepted fix changes this ticket. Independent issues contributing to the broader problem are `related` at any status; a Proposal or Deferred item adds no dependency and its fix is never assumed. A near-match may appear in both sections because search history and related work serve different purposes.
      type: prose
    - text: Schemas 7–9 remain valid without these additions, retain their anywhere-in-the-body dependency-URL rule, and remain searchable, copyable, commentable and re-estimable. Rewriting this run’s older ticket upgrades it to schema 10 using its existing evidence, dependencies and related mentions; when no search was run, Duplicate search says no search was recorded when filed. Other runs’ bodies and schemas remain untouched. `COMMENT_MARKER`, `COMMENT_OPENING`, `REPLY_MARKER`, reply openings and existing record keys remain byte-identical; the derived forbidden-heading list also rejects Duplicate search and Related tickets in evidence comments.
      type: prose
    justification: Board items persist beyond the writing run and are read by older installed tools and other hosts; undoing their format would require coordinating those readers and rewriting stored items.
    name: Schema 10 ticket bodies, with older tickets still readable
  name: Follow-up workflow
  part: ''
  repository: ai-orchestrator
  reversible:
  - text: Step 5 and `## Root cause` define one cause by whether one concrete rule change removes every instance, retain one suggested fix per ticket, reject catch-all causes, and make the title and `root_cause` slug name the invariant rather than a file.
    title: Cause and grouping
  - text: The record’s `repository` identifies where the issue is filed, and the repository its fix changes when there is one, rather than claiming the cause occupies only one repository.
    title: Repository meaning
  - text: For an unbound ordinary ticket, step 7 replaces `board-items --metadata orchestrator.follow-up/root_cause=<slug>` with invariant-and-symptom `--search`, asks each further symptom, command or message question once, and searches by file or function last.
    title: Search order
  - text: Budget overruns retain their deterministic `<repository name>-<budget id>-over-<cause>` slug query, internal slug lookups stay intact, bound tickets make no duplicate search, only `board-status` asks by origin, answers are reused and every query narrows the board without listing it or increasing per-ticket request count.
    title: Search boundaries
  - text: A match at any open status, including Proposal and Deferred, makes the local ticket adopt the existing item’s `root_cause` in both filename and record so its evidence marker, disposition carrier check and occurrence recount agree, even when the contributing locations differ.
    title: Matched identity
  - text: An evidence comment on another run’s broader cause requires `Bears on the ticket:` to state only the widened invariant and added locations; this run may widen its own item by rewriting and copying it again, while ownership, status and who-moves-an-item rules stay unchanged.
    title: Widening and ownership
  - text: The implementation updates `board-items` help and the instructions consistently, adds no budget, and neither cleans up existing duplicates nor writes to live boards.
    title: Scope
  summary: This workflow turns drafts from completed runs into verified tickets or evidence on existing tickets. The change covers templates/follow-up-task.md.j2 and its ownership and older-schema includes, orchestrator/follow_up_tickets.py and its validators, the follow-up tests, AGENTS.md, docs/orchestration.md and personas/follow-up.yaml.
visual_changes: []
what: Make follow-up runs file one ticket per broken rule or missing guarantee, gathering every contributing location under it. Reports from different files, repositories or runs become one problem when one change to the rule would resolve them all; independently fixable problems stay separate. Tickets show how duplicates were searched for and which other tickets their work relates to.
why: |-
  The user reports: “duplicates from follow up agent - It seems to be not doing good enough search for existing issues before filing a new one. I have recently encountered multiple duplicates.” They ask: “I wonder if the problem is actually more structural like are we defining root cause to always be a single code location? maybe if root cause could multiple things that work together to cause the issue that would resolve the contention?”

  Duplicates waste review time, split evidence and priority, and risk fixing the same problem twice or only half of it.
-->
