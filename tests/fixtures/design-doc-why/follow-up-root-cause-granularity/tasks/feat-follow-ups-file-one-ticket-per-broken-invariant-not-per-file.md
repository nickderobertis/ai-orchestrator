---
title: 'feat(follow-ups): file one ticket per broken invariant, not per file'
status: todo
project: follow-up-root-cause-granularity
metadata:
  onepipeline.execution_checkout: ai-orchestrator-isolated
  onepipeline.id: define-root-cause-as-invariant
  onepipeline.persona: engineer
  "onetaskgraph.template": {"answers_digest":"sha256:fe66e03f8ee0ed2f0a15be815831cb76da8872cb795b8a5351886351635fbd3c","body_digest":"sha256:f27db49e6ebe52e4c6878a94735c9a2150b91437ad3ffd309b0aea011f737e77","digest":"sha256:ffdea433a9a5ef8e32c5c0d93ff980b893c7e9d12a0bfd949900264a82ad2b9d","template":"onepipeline:plan-task"}
  orchestrator.budgets: []
  "orchestrator.plan-review": {"by":"review-plan","key":"660c33da3ee86bdb1058bffa1db99efa5766c4ef1431b67b44023c6e84588b24","reviewed_at":"2026-10-09T11:50:23.507264+00:00"}
  "onetaskgraph.copies": {"plans":"plans:I_kwDOTXCWqs8AAAABWFjSnw"}
repositories:
- github.com/nickderobertis/ai-orchestrator
---
## What

Change what a follow-up run means by a **root cause**, so that two reports of one problem become one ticket — inside one run when it groups its drafts, and across runs when it searches the `followups` board before filing. The change lives in `templates/follow-up-task.md.j2` (initial mode's "What to do, in order" steps 5, 7 and 9, "The account of every draft", "The verified ticket", and the budget-overrun bullets of "Every landed change against its budgets"), its includes `templates/follow-up-task/ownership.md.j2` and `templates/follow-up-task/older-schema.md.j2`, `orchestrator/follow_up_tickets.py` (the record schema, `HEADINGS`, `_HEADING_GUIDANCE`, the validators, the `board-items` help text), the tests over them, and the prose that restates these rules: AGENTS.md's "Follow-ups: drafted while a run works, verified once it ends", `docs/orchestration.md`'s follow-up sections, and `personas/follow-up.yaml`'s system prompt.

**1. The definition.** A root cause is the **invariant or guarantee that is missing or broken** — the rule the system should hold and does not — stated in words that do not depend on which file a run happened to see, with **every code location that contributes to it** listed under it (possibly several, possibly in several repositories). The bar, stated beside the `## Root cause` guidance and in step 5: **one root cause when a single change to the rule removes every instance, even if that change touches several places; two when they need independent fixes that each stand alone.** The invariant must be specific enough that one concrete suggested fix removes it, so the bar refuses a catch-all ("the follow-up tooling is awkward"); one ticket, one concrete suggested fix is unchanged, and that fix may already span several units and repositories. The ticket's title is the invariant in one line, and its slug names the invariant rather than a file. The record's `repository` stops being described as "the repository its root cause lives in" (a root cause may span several) and is described by what it already is: the repository its issue is filed in, the one its fix changes when there is one. State all of this in general terms: no issue, repository or file from this task's examples appears in the rendered task or the module's guidance strings.

**2. Grouping within a run.** Step 5 applies the definition: a run's own drafts whose evidence points at different files but shares one invariant become one ticket, its contributing locations listing each.

**3. The board search before filing.** For an unbound ticket, step 7's first query is a `--search` phrased in the words of the invariant and its symptom — what a report of the problem from any file would carry — then one per distinct further question about the symptom, the command or the message, and the failing file or function last, because the file is exactly what differs between two reports of one cause. **The agent's `--metadata orchestrator.follow-up/root_cause=<slug>` query is dropped for ordinary tickets** (each run invents its slug, so it almost never matches across runs) **and kept for budget-overrun tickets**, whose slug `<repository name>-<budget id>-over-<cause>` is derived deterministically. The tooling's own internal slug lookups — `_evidence_carrier`'s query for an older account naming no carrier — stay as they are. Every other search rule stands: a bound ticket makes no duplicate search; `board-status` alone asks by origin; each distinct question once, answers reused; nothing lists the board; the per-ticket request count is unchanged (one query swapped for one). An open item — at any open status, `Proposal` and `Deferred` included, as today's same-root-cause path already allows — whose stated root cause meets the bar is **the same root cause even when its location and slug differ**, and takes this run's evidence as a comment rather than a sibling ticket.

**4. Adopting the matched item's slug.** Today "the same root cause" is enforced as slug equality: the evidence-comment marker carries a `root_cause`, `check-dispositions --board`'s carrier check (`filed_board_problems` → `_evidence_carrier`) requires the carrier item's record `root_cause` to equal the local ticket's, and the occurrence recount (`counts_as_occurrence`) counts a comment only when its marker's `root_cause` equals the issue's. The template never says so, so an agent that matched an item by text under its own invented slug could not route evidence there without a refusal, and filed instead. The task now states that **a ticket whose root cause is an open item's takes that item's `root_cause` slug** — its file name and its record's `root_cause` — so the marker, the account and the recount agree with no change to the tooling's keys.

**5. Widening without taking ownership.** No run rewrites another run's item (the ownership rule stands). Where this run's evidence shows the cause broader than another run's item states, its evidence comment's `Bears on the ticket:` paragraph becomes required and states only the widened invariant and each added contributing location. A run widens its **own** item by rewriting its ticket and copying it again.

**6. The stored ticket shape moves to schema 10.**
- `## Root cause` is held to two labelled parts, in this order, the way `## Impact` is held to its parts: `**Invariant.**` (the missing or broken guarantee, in file-independent words) then `**Contributing locations.**` (one or more bullets, each naming a repository and a path).
- A new **last** section, `## Duplicate search`, after `## Owning runs`, terse because the user reads every ticket: one line naming the text queries run, then one line per open item the searches returned that was judged a different root cause — a markdown link to it whose text is its title, never a bare URL, and why one change would not remove both — or `none`. It never restates a matched item's content.
- Schemas 7, 8 and 9 stay readable and sound exactly as today, without either addition; `board-status`, `re-estimate`, `copy` and the `--board` checks keep reading them. The older-schema rule (`templates/follow-up-task/older-schema.md.j2`) brings a ticket of this run forward to schema 10 when the run rewrites it — its `## Root cause` written as the two parts from the evidence it already carries, and its `## Duplicate search` stating that no search was recorded when it was filed where this dispatch ran none for it (a bound ticket runs none) — and another run's item keeps its schema and its body, as today.
- A new **optional** section, `## Related tickets`, placed directly before `## Duplicate search`. Most tickets omit it, and it is absent — no empty heading, no `none` — when it has no entries. Each entry is one bullet and nothing more: a markdown link to the other ticket whose text is that ticket's title — never a bare or plain-text URL — marked `dependency` or `related`, and exactly **one sentence** saying why it is related to and/or different from this ticket — for a `dependency`, what this ticket needs from it. No longer description, and never a restatement of the other ticket's content. It lists **every dependency the ticket records** — each `depends_on` entry on an accepted ticket, by its URL, marked `dependency` — and each issue that contributes to the same broader problem but is an independent fix (it did not meet the one-change-removes-every-instance bar), marked `related`, at whatever status. Today's rule that "a `Proposal` or `Deferred` item for a clearly related root cause may be named as related, by URL, with no `depends_on` entry" moves into this section: such an item is a `related` entry and nowhere else, still with no dependency entry and no change to the ticket's claims. The rule that the text states where and how an accepted fix changed the ticket (in `## Impact`, `## Root cause`, `## Suggested fix` or `## Rejected fixes`) stands beside the section's `dependency` entry. The section differs from `## Duplicate search`: that one is the diagnostic record of near-matches judged a different root cause; this one is related work a reader should know about, so an item may appear in both. At schema 10, `board-status`'s existing dependency resolution requires each entry's URL on a `dependency` bullet of `## Related tickets` (today it requires the URL anywhere in the body, which stays the rule for schemas 7-9), and `validate` refuses a present section that is empty, carries `none`, or has a bullet without a markdown link to the ticket, with a bare or plain-text URL, without one of the two marks, or carrying more than the link, its mark and one sentence (more than one sentence, or a second line or paragraph under the bullet). Bringing a ticket of this run forward to schema 10 writes the section from its existing dependency entries and related mentions.
- `ticket_section_headings()` derives the evidence-comment heading refusal from `HEADINGS`, so `Duplicate search` and `Related tickets` are refused in an evidence comment; keep it derived, adding the optional heading the way `REJECTED_FIXES` is added.

## Why

The user, reviewing the `followups` board, keeps finding duplicates: "duplicates from follow up agent - It seems to be not doing good enough search for existing issues before filing a new one. I have recently encountered multiple duplicates." On the diagnosis: "I wonder if the problem is actually more structural like are we defining root cause to always be a single code location? maybe if root cause could multiple things that work together to cause the issue that would resolve the contention?"

Every duplicate costs the user a review of a ticket that adds nothing, splits one problem's evidence and priority across two items (so neither reaches the occurrence count that raises its priority), and risks two plans fixing one problem twice, or fixing half of it.

## Acceptance criteria

- The initial-mode task rendered from `templates/follow-up-task.md.j2`, and the example ticket's `## Root cause` guidance in `orchestrator/follow_up_tickets.py`, define a root cause as the invariant or guarantee that is missing or broken, in words independent of the file a run saw, with every contributing location listed under it (possibly several, possibly in several repositories); state the bar that it is one root cause when a single change to the rule removes every instance, even across several places, and two when they need independent fixes that each stand alone; state that the invariant is specific enough that one concrete suggested fix removes it, so no catch-all ticket stands; keep one ticket, one concrete suggested fix; make the title the invariant in one line and the slug name the invariant rather than a file; and no longer describe the record's `repository` as the one place a root cause lives. None of this text names an issue, repository or file from this task's examples.
- Step 5 of the rendered initial task groups a run's own drafts by that definition, so drafts whose evidence points at different files but shares one invariant become one ticket listing each contributing location.
- For an unbound ticket, step 7 of the rendered initial task makes its first `board-items --search` query one phrased in the words of the invariant and its symptom, then one per distinct further question about the symptom, command or message, with the failing file or function last; spells no `--metadata orchestrator.follow-up/root_cause=` query for an ordinary ticket; and still spells that slug query in the budget-overrun bullets, whose slug is derived deterministically. Every other search rule renders as before: a bound ticket makes no duplicate search, only `board-status` asks by origin, each distinct question is asked once and answers are reused, and nothing lists the board. The `board-items` subcommand's description and help agree with this.
- The rendered initial task states that an open item at any open status, `Proposal` and `Deferred` included, whose stated root cause meets the bar is the same root cause even when its location and slug differ, and takes this run's evidence as a comment rather than a new ticket; that this run's ticket for it then takes that item's `root_cause` slug as its file name and record `root_cause`; that where the evidence shows the cause broader, the evidence comment's `Bears on the ticket:` paragraph is required and states only the widened invariant and each added contributing location; that no run edits another run's item; and the rule that a `Proposal` or `Deferred` item's fix is never assumed is unchanged.
- `check-dispositions --board`, driven over a real `local-md` board, accepts this run's evidence comment on another run's open `Proposal` item whose ticket was filed under a different slug and names a different contributing location, when this run's local ticket has taken that item's slug and the disposition's `detail` names the carrier; `re-estimate` on that item counts the comment as an occurrence; and the same check still refuses, naming it, a comment from a local ticket that kept its own different slug. `COMMENT_MARKER`, `COMMENT_OPENING`, `REPLY_MARKER`, the reply openings and the record's existing keys are byte-identical to before.
- `SCHEMA` is 10 and 9 is among `PRIOR_SCHEMAS`. `validate` refuses a schema-10 ticket, naming what is wrong, whose `## Root cause` is not `**Invariant.**` followed by `**Contributing locations.**` with at least one bullet, or which lacks a non-empty `## Duplicate search` as its last section; it accepts a schema-10 ticket of that shape, and accepts schema-7, 8 and 9 tickets without either addition exactly as before. The example ticket the task renders carries the new shape, with `## Duplicate search`'s guidance asking for one line naming the queries run and one line per open item judged a different root cause (a markdown link to it whose text is its title, never a bare URL, and why one change would not remove both) or `none`, and never a restatement of a matched item. An evidence comment carrying `## Duplicate search` as a level-2 heading is refused by the `--board` checks through the existing derived heading list.
- A follow-up ticket has an optional `## Related tickets` section, placed directly before `## Duplicate search` and absent from the rendered ticket — no heading, no `none` — when it has no entries, listing by URL each independent issue contributing to the same broader problem, marked `related`, and every dependency the ticket records, marked `dependency`, each entry exactly a markdown link to that ticket whose text is the ticket's title (never a bare or plain-text URL), its mark, and one sentence saying why it is related to or different from this ticket (for a dependency, what this ticket needs from it), and nothing more — no longer description and no restatement of the other ticket's content. The rendered initial task and the example ticket state this and move today's "a clearly related `Proposal` or `Deferred` item may be named as related, by URL" rule into the section (still with no `depends_on` entry). `validate` accepts a schema-10 ticket without the section and one with a well-formed section, and refuses, naming it, a present section that is empty, says `none`, or has a bullet lacking a markdown link to the ticket, carrying a bare or plain-text URL, lacking a `related`/`dependency` mark, or carrying more than one sentence beside them; over a real `local-md` board, `board-status` refuses a schema-10 ticket whose `depends_on` entry's URL is not on a `dependency` bullet of the section and accepts it once it is, while schema-7-9 tickets keep today's anywhere-in-the-body rule; and an evidence comment carrying `## Related tickets` as a level-2 heading is refused by the `--board` checks.
- Tickets filed under today's shape keep working: over a real `local-md` board, a schema-9 and a schema-7 item filed by another run are found by an invariant-phrased `--search`, take this run's evidence comment, pass `check-dispositions --board`, and are re-estimated by `re-estimate`, keeping their schema; and a ticket of this run at schema 9 brought forward by the rule `templates/follow-up-task/older-schema.md.j2` renders into both modes validates at schema 10 after `board-status`.
- `tests/plan_tooling/test_follow_ups_recipe_e2e.py`'s journey over the task's board search (today `test_the_task_searches_the_board_by_root_cause_and_that_query_selects_exactly_its_items`) holds the new search: the task a real dispatch of the shipped graph is handed spells the invariant-first `--search` for an ordinary ticket and the slug query only for overruns, every `board-items` it spells still names a narrowing flag, and the scripted turn's invariant-phrased search answers the earlier run's item filed under a different slug.
- AGENTS.md agrees with the new behaviour and keeps every other follow-up rule: its "The board is searched, never listed" paragraph says the agent asks first by the invariant's words and once per further distinct text question, and by root-cause slug only for a budget overrun; it states the root-cause definition and bar where it describes grouping or the same-root-cause path; and its rules that the board is searched and never listed, that every query goes through `python -m orchestrator.follow_up_tickets board-items`, that a bound ticket makes no duplicate search, that a run owns only the issues it created, and the status and who-moves-an-item rules are unchanged. `docs/orchestration.md`'s follow-up sections and `personas/follow-up.yaml`'s system prompt agree with it.
- Tests hold all of the above, replacing assertions that pinned the old wording: `tests/test_follow_up_tickets.py`, `tests/test_follow_up_ticket_docs.py`, `tests/test_follow_up_priority.py`, `tests/test_follow_up_comments.py`, `tests/test_follow_up_agent_arrangement.py`, `tests/plan_tooling/test_follow_ups_recipe_e2e.py`, `tests/plan_tooling/test_follow_ups_comment_answers_e2e.py`, `tests/plan_tooling/test_follow_ups_answer_comments_recipe_e2e.py` and `tests/plan_tooling/test_linear_routed_follow_ups_e2e.py` pass over the finished tree; every line the change adds under `orchestrator/` is covered by them; ruff's format check and lint and mypy pass over `orchestrator/` and `tests/`; and the judged lint (llmlint) over the branch's diff against its base reports no finding.
- Every claim the dispatch's report makes about the change and its evidence is true of the tree as it finally stands; a check result it reports was taken over that final tree, not over an earlier state a later edit invalidated.

## Additional info

**Decisions already taken, which this task does not reopen.** The manager ruled: (1) ownership stands — no run rewrites another run's item; a broader cause goes in the evidence comment's `Bears on the ticket:` paragraph, required in that case, stating only the widened invariant and each added location; a run widens its own item by copying again. (2) The near-match record is on the board, as the ticket's last section, terse: one line of queries, one line per item judged distinct (URL and why one change would not remove both), or `none`; no restatement of matched items. It is on the board because the local disposition account stays on the verifying host, and the transcripts behind the duplicates below were on other hosts. (3) `## Root cause` is held to `**Invariant.**` then `**Contributing locations.**` at schema 10; schemas 7-9 stay sound and are brought forward when a run rewrites its own ticket; the one-change-removes-every-instance bar sits next to that guidance. (4) The agent's slug metadata query is dropped for ordinary tickets and kept for budget overruns; the tooling's internal slug lookups stay. (5) No new budget. (6) A later addition from the user: the optional `## Related tickets` section described under What, distinct from `## Duplicate search`, absorbing today's "clearly related item may be named as related" rule and listing every recorded dependency. (7) The user's refinement: each entry is exactly the other ticket's link plus one sentence on why it is related to and/or different from this ticket — for a dependency, what this ticket needs from it. Cleaning up the existing duplicates is out of scope: this task writes nothing to any board.

**The examples, as reading material only** (never quoted in the template or the module). #1634 "Plan checks miss destination CI title policy" (slug `publication-title-policy-missing-ci-validation`, located in `orchestrator/publication_guard.py`) duplicates #1332 "planner guidance omits the breaking title for a UI engine relink" (slug `planner-omits-engine-break-title`, `personas/planner.yaml`): one invariant — a lifecycle node's title is never checked against the destination's title and release policy before dispatch — with two contributing locations; #1332 was a `Proposal`. #1637 (`scripts/follow-ups.sh`) and #1638 (`orchestrator/follow_up_comments.py`) were filed by one run 8 seconds apart from three drafts: one invariant — the follow-up recipes can act only on a whole run or the whole board, never one named item. Use pairs of this shape for the test fixtures, generated on a real `local-md` board through the module's own render, never read from the live board.

**Stored shapes that must not move.** `COMMENT_MARKER`, `COMMENT_OPENING`, `REPLY_MARKER` and the reply openings are read back off comments already on both boards, and the record's existing keys are read off every item there, so they stay byte for byte. The schema bump follows the module's own pattern (the comment above `SCHEMA` records each schema's addition; `STRUCTURE_AT`-style gating holds a structure only from the schema that introduced it). If you find you need a departure from any agreed shape — another record key, a different section name, a change to the marker or the gathering's routing — surface it to the manager with `$ORCHESTRATOR_ASK_MANAGER` and keep building against the shape as agreed: a shared shape is never changed unilaterally.

**Where things are.** In `templates/follow-up-task.md.j2`: step 5 around line 292, step 7 around 307-364 (the slug query is spelled at 314), step 9 around 375, "The account of every draft" around 406, "The verified ticket" around 449 (the `repository` description at 458, the title at 472, the same-root-cause dependency rule at 560), the budget-overrun bullets around 628-650. In `orchestrator/follow_up_tickets.py`: `SCHEMA` and its history at 97-137, `RECORD_KEYS` around 430, `HEADINGS` at 508, `_HEADING_GUIDANCE` at 5170, `counts_as_occurrence` at 3156, `filed_board_problems` and `_evidence_carrier` at 3723-3810, the `board-items` parser at 5792, the `Impact` labelled-part check around 1669 as the pattern for `## Root cause`'s parts. AGENTS.md's follow-up section starts at line 949 (the search paragraph at 1063). Tests pinning the old search wording include `tests/plan_tooling/test_follow_ups_recipe_e2e.py` around 2783 and 3420-3470.

**Testing the judged part.** Whether two reports share an invariant is the agent's judgment, which no journey can script a verdict for; follow the template's existing `<!-- llmlint: ignore-block[changed_behavior_has_e2e] … -->` pattern for that prose, naming the tests that hold what the tooling decides (the rendered task carrying the rule, the slug adoption accepted and the old shape refused over a real board).

**Cadence.** Iterate with the targeted pytest modules and `just lint-llm-diff <base>`; running the pieces separately is fine, and do not run the repository's whole gate, which the merge path runs on publication.

## Additional info

### Operational notes for this host

**Run only the checks that exercise what you changed.** The tests that cover the code
you touched, the lint that reads your diff — and nothing wider. The repository's own
automation runs the full bar downstream on the change this work becomes: a `pre-push`
hook where that repository publishes locally, the host's required checks where it
publishes remotely. Neither is waiting for you to have run it first, so running it here
spends twenty to forty minutes of this dispatch learning what the merge path reports
anyway. That is measured rather than estimated: one node in this workstream spent about
84 minutes on three rounds of everything its repository could run, to reach lint findings
a diff-scoped run reports in two.

**Pick those checks from your change, and take the narrowest scope each one supports.**
One test module over the whole suite; the lint over your diff over the lint over the
tree. A repository's own fast deterministic tier is a fine choice when it is what
exercises your change — its `just --list` says which recipe that is — and a poor one when
you reached for it because it was the widest thing available. When a check you ran
reports something, fix that and run **that check** again; nothing here asks you to re-run
its neighbours to confirm, and a wider tier cannot re-check a finding it never made.

**A dispatch that changed nothing tracked owes none of them.** When your deliverable is a
document, a report, or an answer, there is no changed code for those checks to exercise
and you are complete without them — decide that from what the repository shows, never
because running them is slow or inconvenient. `git status` reporting nothing to commit is
then a correct and complete outcome, not a problem to solve by writing a file nobody
asked for.

**A publication check runs downstream, so no worker can run it and none is judged on it.**
The host's required checks and a repository's `pre-push` gate run on the change this
work becomes, after this dispatch settles: downstream publication checks are not
runnable by a worker, and a dispatch is never held to one having passed. That holds on a
retry too. A `checks-failed` retry — a re-dispatch onto the same branch carrying the
merge path's refusal and `onevcs`'s evidence — is judged on its task's stated local
acceptance checks: the repair of what the refusal named, proven by the checks the task
names, and never by the downstream check passing again, which can only happen once the
retry has settled. One retry that fixed and locally verified the failing test its
refusal named was failed for not re-running the required check, which left finished
work unpublished and its dependents skipped until a manager published it by hand.

**Never signal a process you did not identify by PID, and a PID you got from a pattern is
still a pattern kill.** Several managers share this host, and their dispatches, drivers,
publications and test servers run under the same few binary names — `just`, `node`,
`python`, `onepipeline-api` — so a kill by name knows nothing about *whose* work it
matched. The distinction is the tool rather than the pattern: `pgrep` and `ps` **read**,
and either spelling of them is fine, `pgrep -x` included; `pkill` **signals**, and it is
never the answer here, in any spelling. `-x` is the trap, because it reads as the careful
one — `pkill -f 'just gate'` at least needs a narrowing pattern, while `pkill -x just`
takes every `just` on the machine. Reading the table first and piping the pids into `kill`
is no way around it: that is the same pattern kill with more steps.

Two workers reached that place from different directions on 2026-08-24, while another
manager's dispatch had been live over an hour. One ran `pkill -TERM -x just`. The other
ran `ps -eo pid,args | grep 'onepipeline-api serve' | awk '{print $1}' | while read pid;
do kill "$pid"; done` — which obeys the letter of every paragraph below and does the same
damage, because `just dag-ui` and the e2e suites both spawn that server and a pytest run's
were alive at that moment.

**The one process you may signal is one you started yourself, so capture its PID as you
start it:**

    cmd & MYPID=$!
    kill "$MYPID"

Anything you did not start belongs to somebody. Read what it is, report what you found,
and leave it running.

**A wait written against a full command line never ends, because one of those command
lines is yours.** That is a different defect from the one above — it wedges your own
dispatch rather than somebody else's — and it is why `pgrep -f` cannot be waited on.
`pgrep -f` matches its pattern against every process's whole argv and excludes exactly one
process — itself — never the shell that invoked it. So any pattern naming the thing you are
waiting for is, by construction, a substring of the command line of the shell doing the
waiting: `until ! pgrep -f "just gate"` matches its own loop and never exits, and so does
the same loop written against `llmlint-judge.sh`, `scripts/fetch-corpus.sh`, or
`publish-branch`. That is a defect in the polling mechanism rather than a fact about gates,
so it applies to every wait you write.

That has wedged four workers here, and then two more workers and the manager itself in one
night: seven instances of one defect. A crozier corpus audit waited in `until ! pgrep -f
'scripts/fetch-corpus.sh'` with no fetch running at all — the only two matches were its own
polling shell and the manager's diagnostic shell — and waited until it was interrupted. A
`check-plan-bar-conflict` dispatch waited on `! pgrep -f "llmlint-judge.sh"`, whose negation
could never become true, and was cancelled and killed; its replacement then reached for
`pkill -f` against three patterns, which is not a wait at all but the same self-match with a
signal attached, and is refused above. And the manager, an hour after instructing a worker
about this, guarded a launch with `pgrep -f "publish-branch" && echo ABORT || launch`, whose
pattern was a literal substring of the launching shell's own command line: it aborted
unconditionally and then reported a publish as RUNNING that did not exist. That last one is
the evidence that matters most — a rule its own author reproduces an hour after issuing it
is a rule whose wording is the problem rather than its reader.

**Wait with a sentinel file.** Background the whole command you are waiting on — one
command, so that what you waited on is what you meant to run — and poll for a file only
that invocation can write:

    ( just test > /tmp/check.$$.log 2>&1; echo $? > /tmp/check.$$.exit ) &
    until [ -f /tmp/check.$$.exit ]; do sleep 20; done

**A process must finish inside the turn that started it.** Backgrounding is how you wait
on a slow command *within* one turn, which is why the launch and the wait sit together in
that example — both halves belong to the same turn. What does not survive is a background
process left running when the turn ends: nothing carries it across, and the launch gives
you no sign of that, because it succeeded and the turn then ended normally. The loss
shows up a turn later as a sentinel that never appeared and a recipe terminated by
signal, with the whole run to pay for again. It bites hardest where it is most tempting,
because a worker reaches for backgrounding exactly when a command is slow enough to
threaten the turn — here the judged lint tier and a whole test tier, which are the two
runs a node most needs to have made. So run a slow check in the foreground, or background
it and wait out its sentinel before your turn ends; never end a turn intending to read a
result on the next one.

**To ask whether a process is running at all, match the executable, not the command line.**
`pgrep -x onepipeline` matches process *names* exactly — the binary, not its arguments — so
the shell doing the asking, whose own name is `bash`, cannot match it however the pattern
is spelled. Where the binary name is not distinctive enough to identify what you mean
(`just`, `node` and `python` run everything), read the process table instead and drop the
one self-match that form has:

    ps -eo pid,args | grep -v grep | grep 'lint-llm-diff'

Both of those are **reads**, which is what makes `-x` safe here and forbidden the moment
the tool signals. The replacement for the worker wedged above reached for `pkill -f "just
gate"`, `pkill -f "just check"` and `pkill -f "nx run"` while another manager's run was
live. Read to learn what is running, report what you found, and signal nothing you did not
start.

**One sentinel and one log per invocation, and look before you launch one.** That pair is a
shape rather than two literal paths. Several dispatches run on this host at once and they
share `/tmp`, so a second worker writing `/tmp/check.log` writes into the first one's — and
`until [ -f /tmp/check.exit ]` returns immediately on somebody else's exit file, reporting
their result as yours. Name both after the invocation (`/tmp/check.$$.log`, or the node
id), and, before starting it, check whether its sentinel path is already in use by another
invocation; if so, choose a different invocation-specific pair. This path-ownership check
does not prohibit concurrent checks or judged tiers. Three workers collided on shared names
today and not one of the three failures read as what it was: two
deterministic-tier runs deadlocked against the same cargo target-directory lock, so both
logs sat unchanged and neither progressed — about fifteen minutes lost and three
interruptions to a live turn; two whole-e2e runs wrote to one log, and stopping the
duplicate left four `SIGTERM` entries in it that read exactly like real test failures; two
judged-tier runs raced and wasted a roll, which was harmless only by luck. Read a log that
has stopped growing as two commands blocking each other before you read it as one command
working.

<!-- llmlint: ignore[contracts_have_one_source_or_a_drift_gate] The source of this contract is the engine that exports the variable, and it lands in the same run as this paragraph: there is no released copy to reconcile against yet, and this task is explicitly told not to go looking for one in an installed engine. `tests/test_dispatch_appendix.py` holds these four properties and no more, so the day the engine side is adopted the drift gate has an exact set to reconcile. -->
**Write the files this dispatch needs under `ONEPIPELINE_NODE_SCRATCH_DIR`, rather than
under a `/tmp` path you invented.** It is an absolute path to a directory that exists and
is writable when the dispatch starts, is unique to this dispatch, and is not removed while
it runs.

**Root-owned files after a container run.** The pre-push visual guard captures in Docker
as root and can leave `.nx/` and `dist/` unwritable, failing the *next* check with `NX
Permission denied (os error 13)`. There is no passwordless sudo; repair from inside a
container:

    docker run --rm -v "$PWD":/w -w /w mcr.microsoft.com/playwright:v1.61.1-noble \
      chown -R "$(id -u):$(id -g)" /w

**If a test fails in a way that makes no sense, check the host before the test.** On
2026-08-19 two publication attempts against the same already-green branch failed on two
unrelated tests — a 5-second timeout, then a browser remote that never came up — and both
were written up as flakes. The host was at 197G/197G, and the next attempt said so
outright with `No space left on device`. A full disk does not announce itself to a test
runner; it announces itself as an unrelated test failing. One command rules it out:

    df -h /home .

**If a rule looks wrong or misapplied, say so with evidence rather than editing it.**

**One suppression policy, and this is the whole of it.** A site-scoped `ignore` directive
is permitted where the rule is genuinely misapplied at that site **and** the directive
carries a substantive reason saying why. What is forbidden is silencing a finding you
have not answered so that a count moves. What you owe an account of is **every
suppression your own diff writes — added, moved or rewritten — and every one already
standing over a line that diff changes** — each with its site and its reason, in your
completion report, so the manager reads what you left rather than discovering it. Both
halves are decided from the diff alone: a directive line inside it, or a changed line
inside the region a directive covers. A file-scoped directive is outside both unless your
diff writes the directive itself — its region is every line of the file, so counting it
would make this file's own header a debt of every change that edits a word of it.
Suppressions elsewhere in the tree are not yours to inventory: this repository carries several hundred standing directives, and
a list of them would be longer than the report is read under while saying nothing about
the change under review.

That scope is stated because the sentence it replaces read two ways at once: its first
clause reached every directive anywhere in the repository, while its second — *so the
manager reads what you left* — was about this dispatch's own work, in the same breath. A
node was failed on the wider reading for not accounting for suppressions it had never
touched, on a tree whose every target had just run from cold and come back green; it cost
a third dispatch of a run's final node. The reading above is the one the purpose clause
and the scale both support, and it is written so that a worker complying with it and a
judge reading it adversarially land in the same place. It says which lines rather than which sites for the same
reason: *site* was itself readable two ways, and the first worker to apply the sentence
could not decide whether the file-scoped directives at the head of `AGENTS.md` were
owed by a change that edits one of its paragraphs. Apply that same test to whatever
replaces it: read it as a worker trying to comply and as a judge looking for a way to
refuse, and if the two can land in different places it is not one source yet.

`AGENTS.md` states no suppression policy of its own and points here, because this file is
what your judge reads beside your task: while that document said it twice, one
dispatch added four site-scoped ignores each carrying a substantive reason and was failed
for *"the task's categorical instruction never to suppress a rule"* — work that was
correct under one of this repository's own readings and refused under the other.

**`--no-verify` is not an acceptable response to a slow or inconvenient hook.** The hooks
are this repository's enforcement point — there is no CI — so bypassing one commits work
that nothing has checked, and nothing you run afterwards catches it: a check runs over
the finished tree, by which point a bypassed commit is simply history. Two workers
today reached for `git commit --no-verify`, both on a commit they had judged mechanical,
and one of the two was a *merge* commit — a conflict resolution, which is the single most
likely commit in a dispatch to be wrong. No check caught either one; the monitor caught
both. If a hook is slow, wait for it. If it refuses, it is telling you something about the
commit in front of you, and the answer is to fix the commit.

**Commit a coherent working piece the moment it works.** A dirty worktree does not
survive this dispatch: what is uncommitted when your last turn ends is what nothing
recovers — not a retry, not a recovery verb, not a manager reading the branch. So each
piece is its own commit as you finish it, rather than one commit held back to the end.
*The moment it works* is the moment you have seen it work: the checks that piece needs
are run before it is committed rather than after, so that each commit is one you have
evidence for.

**Ask rather than stop.** When you cannot proceed without a decision that is not yours —
a frozen contract you would have to amend, a goal that reads two ways, a constraint you
would have to relax — put the question to your manager over `$ORCHESTRATOR_ASK_MANAGER`,
which every dispatch carries, and go on working on whatever does not depend on the answer.
Ending your turns with the work undone and the question unasked is the one response
nothing can recover: a worker that reasoned about a frozen contract correctly, declined to
amend it unilaterally, named two options for its owner, and then stopped for three turns
lost about fifteen minutes of correct work and settled reporting `ahead of main: 0
commit(s)`.

**Draft a follow-up that can wait; never draft one that cannot.** Work you notice that
is outside your subtask — a bug beside the code you touched, a missing test or script, a
stale document, an improvement to this harness or to the context agents are given — is a
follow-up, and doing it is not yours. Record each one the moment you notice it by piping
its body into `"$ORCHESTRATOR_FOLLOW_UP_DRAFT"`, whose `--help` names the flags and the four
headings that body carries; the command stamps where it came from. Do not collect them into
a list at the end of your last message instead, because nothing reads that list. A draft is
unverified — a follow-up agent checks every one after the run — and it is not your
completion report, which still says what you did. Drafting is only for what can wait:
anything blocking, whether a decision fork, a constraint you cannot meet, or something your
manager should act on now, goes over `$ORCHESTRATOR_ASK_MANAGER` immediately, as the
paragraph above says, and is never drafted in its place.

**A GitHub rate-limit refusal that `gh api rate_limit` disagrees with is the secondary
limiter.** The primary limit is the one that endpoint reports and the one a wait answers.
The secondary limiter is reported by nothing, polled by nothing, and not waited out by
polling — every further attempt extends it. So when a `gh` call is refused for a rate
limit while `gh api rate_limit` still shows budget, stop making that call: deliver the
result another way, name in your report what was refused and what you tried, and leave the
retry to a person.

**Publication goes through the harness.** The session branch reaches its remote only
through `onevcs publish "$ONEVCS_SESSION"`, and its change request is lifted, landed or
kept for the user's review by the lifecycle after you settle. No `git push` of the session branch, no `gh pr create`
for it, no `gh pr ready`, no `gh pr merge`. Finish the branch, commit everything, leave
the tree clean, and report. Landing it is explicitly **not** yours to perform and **not**
part of your acceptance criteria. Two things are yours, and each only when this task's
own `## Additional info` — its own words above these notes — says so:

- **Only when the task's `## Additional info` says the change request may be published
  early**: `onevcs publish "$ONEVCS_SESSION" --draft [--title T] [--body-file PATH]` opens
  the session's change request as a draft, and a later `publish --draft` pushes new
  commits onto that same change request and never opens a second. Commit everything
  first — a dirty tree is committed for you, under a provenance you did not choose.
  `onevcs change describe "$ONEVCS_SESSION" --body-file PATH` replaces its description
  and `onevcs change show "$ONEVCS_SESSION"` reads it back. The description you leave is
  what the drafter finishes from, so start it with what only you know — the evidence and
  where it is. Never mark the draft ready yourself; after you finish, the lifecycle lifts
  it or keeps it as a draft for the user's review according to the repository's policy.
  `ONEVCS_SESSION` is in every lifecycle dispatch's environment.
- **Only when the task's `## Additional info` authorizes a throwaway demonstration change
  request**: after the draft is open, cut a branch from the session branch, commit the
  demonstration on it, push it with `git push -u origin <branch>`, open it with
  `gh pr create --draft --base <session branch> --head <branch> --title "DO NOT MERGE: …"`,
  capture the evidence — `gh pr view <n> --comments`, `gh pr checks <n>`, and `gh api`
  **reads** of that change request's own comments, checks and reviews, never a write to
  anything — then close it with `gh pr close <n> --delete-branch`, return to the session
  branch, and delete the local branch — a local branch left behind makes the session's
  close refuse. Its base is always the session branch, never the repository's base; it
  is never marked ready; it is closed before you finish.

Everything else a push or a change-request verb could do is still not yours.

**A branch you resume may already be on its remote, and a published branch only grows.**
A retry — a `checks-failed` one among them — re-dispatches onto the same branch, which an
earlier publication may already have pushed. So check before you touch its history: `git
fetch` and `git log origin/<branch>`, or the published commit your task's context names.
The repair of a branch already on its remote is new commits on top of the remote's
commit — never an amend, a rebase, a squash, a reset or a force-push of commits already
there. One retry that fixed both refusals its merge path named by amending the two commits
already pushed was refused `non-fast-forward` when it published, and settled
`push-rejected` with the correct tree stranded on the host.

**Every claim you make about the finished work is true of the tree as it finally
stands.** That is the property you are held to, and it says nothing about where your
report sits. This conversation does not end when you report — your supervisor keeps
asking, and answering well means running things — so a report required to be this
dispatch's literal final output is one no correct worker can give. What is required
instead is that nothing you have said about the work is untrue of the tree by the time you
stop.

When work follows your report, say in that same turn what changed and what you re-ran. A
correct delta satisfies this exactly as fully as restating the whole report does. Silence
does not satisfy it at all. Your last substantive turn should leave a reader able to say
what the finished tree contains and what was verified about it, whether that comes from
one report or from a report plus the deltas after it.

**A claim about a check, a test, a lint run, or a commit that was not run or was not made
is false, and fails on its merits.** A report asserting a check passed on a commit where
no such run occurred is that case, and one node of this host's history was correctly
failed for it. So do not carry a claim forward across a change that could have invalidated
it — re-run what the change could have broken, or say which claims you have not re-checked.

**Evidence deliberately about an earlier or induced state is not a false claim.** Proving
that an assertion can fail means making it fail, reading the message, and reverting;
proving that a message used to say nothing means quoting the run from before the change.
A citation of a run taken before a change, or of a failure induced on purpose as evidence,
is correct evidence — provided the citation says which it is. One node of this host was
failed for exactly that: its own criteria required its assertions be observed failing, and
the resulting-tree property above was read as forbidding the citation that requirement
produces.

**A commit that cannot affect a check leaves that check's evidence standing.** *Could have
invalidated it* is a condition, and a commit that fails it does not reach the claim — so a
run taken before a later commit is still evidence for the tree after it, provided the
report names that commit and says why it is inert for that check. What decides it is what
the check reads, never what kind of change the commit is. A comment-only or
documentation-only commit to content a check does not read is inert for that check; the
same comment is not inert for a check that reads comments, which a judged lint does, so
that one is re-run or named as not re-checked. A commit touching anything a check reads is
not inert for it, however small. Two dispatches of one node, about 45 minutes each, were
refused over nothing but a comment-only final commit after their cited check runs, with no
change in whether either tree was correct, and the manager had to send *re-run after every
commit* three separate times to get past it: read as absolute, the conditional rule above
is one no worker can meet, because every commit comes after the last run.

The ordering demand this replaces — that the report come after everything else, and that
anything found later be repaired and the whole report written again — is **withdrawn**. It
failed six of fourteen nodes in one workstream, each with complete committed work, a green
deterministic tier, and no acceptance criterion found unmet, and two of those carried an
escalated warning about it in their own task and failed anyway. A bar finished work
cannot clear teaches everyone to route around the thing that enforces quality; the
property above is what that demand was serving, and it is what survives.

### State the bar in `## Acceptance criteria`, not only here

A judge reads this node's `## Acceptance criteria` and its own review bar. Where the
criteria are silent about something the bar demands, it imports the demand and applies its
own reading of it — and finished, gate-green work has been failed twice that way: once for
running the gate's parts separately, and once for never having *"provided a final verified
completion report"*, a demand in neither the task nor the shared completion clause.

So every demand this node will be held to is stated as a criterion of its own, including
the two the bar makes of every implementation dispatch:

- the behavior this node adds is **proven end to end** by a test or journey that drives the
  real interface, rather than by inspection;
- every claim the dispatch makes about the finished work — what it verified, and the
  evidence for it — is **true of the tree as it finally stands**.

Criteria state properties of the finished tree; the commands that produce them belong in
this section. Whether the criteria answer every demand their own bar — or this appendix —
makes of them is read by `just review-plan <source:project>`, which spends a judged turn
on it and judges it by meaning: criteria that state a demand in their own words answer it,
and no criterion is refused for failing to use a particular phrase. Nothing reads it
deterministically any more, because the phrase matching that did refused wordings the same
review had just asked for.


<!-- onetaskgraph:template-answers
acceptance_criteria:
- The initial-mode task rendered from `templates/follow-up-task.md.j2`, and the example ticket's `## Root cause` guidance in `orchestrator/follow_up_tickets.py`, define a root cause as the invariant or guarantee that is missing or broken, in words independent of the file a run saw, with every contributing location listed under it (possibly several, possibly in several repositories); state the bar that it is one root cause when a single change to the rule removes every instance, even across several places, and two when they need independent fixes that each stand alone; state that the invariant is specific enough that one concrete suggested fix removes it, so no catch-all ticket stands; keep one ticket, one concrete suggested fix; make the title the invariant in one line and the slug name the invariant rather than a file; and no longer describe the record's `repository` as the one place a root cause lives. None of this text names an issue, repository or file from this task's examples.
- Step 5 of the rendered initial task groups a run's own drafts by that definition, so drafts whose evidence points at different files but shares one invariant become one ticket listing each contributing location.
- 'For an unbound ticket, step 7 of the rendered initial task makes its first `board-items --search` query one phrased in the words of the invariant and its symptom, then one per distinct further question about the symptom, command or message, with the failing file or function last; spells no `--metadata orchestrator.follow-up/root_cause=` query for an ordinary ticket; and still spells that slug query in the budget-overrun bullets, whose slug is derived deterministically. Every other search rule renders as before: a bound ticket makes no duplicate search, only `board-status` asks by origin, each distinct question is asked once and answers are reused, and nothing lists the board. The `board-items` subcommand''s description and help agree with this.'
- The rendered initial task states that an open item at any open status, `Proposal` and `Deferred` included, whose stated root cause meets the bar is the same root cause even when its location and slug differ, and takes this run's evidence as a comment rather than a new ticket; that this run's ticket for it then takes that item's `root_cause` slug as its file name and record `root_cause`; that where the evidence shows the cause broader, the evidence comment's `Bears on the ticket:` paragraph is required and states only the widened invariant and each added contributing location; that no run edits another run's item; and the rule that a `Proposal` or `Deferred` item's fix is never assumed is unchanged.
- '`check-dispositions --board`, driven over a real `local-md` board, accepts this run''s evidence comment on another run''s open `Proposal` item whose ticket was filed under a different slug and names a different contributing location, when this run''s local ticket has taken that item''s slug and the disposition''s `detail` names the carrier; `re-estimate` on that item counts the comment as an occurrence; and the same check still refuses, naming it, a comment from a local ticket that kept its own different slug. `COMMENT_MARKER`, `COMMENT_OPENING`, `REPLY_MARKER`, the reply openings and the record''s existing keys are byte-identical to before.'
- '`SCHEMA` is 10 and 9 is among `PRIOR_SCHEMAS`. `validate` refuses a schema-10 ticket, naming what is wrong, whose `## Root cause` is not `**Invariant.**` followed by `**Contributing locations.**` with at least one bullet, or which lacks a non-empty `## Duplicate search` as its last section; it accepts a schema-10 ticket of that shape, and accepts schema-7, 8 and 9 tickets without either addition exactly as before. The example ticket the task renders carries the new shape, with `## Duplicate search`''s guidance asking for one line naming the queries run and one line per open item judged a different root cause (a markdown link to it whose text is its title, never a bare URL, and why one change would not remove both) or `none`, and never a restatement of a matched item. An evidence comment carrying `## Duplicate search` as a level-2 heading is refused by the `--board` checks through the existing derived heading list.'
- A follow-up ticket has an optional `## Related tickets` section, placed directly before `## Duplicate search` and absent from the rendered ticket — no heading, no `none` — when it has no entries, listing by URL each independent issue contributing to the same broader problem, marked `related`, and every dependency the ticket records, marked `dependency`, each entry exactly a markdown link to that ticket whose text is the ticket's title (never a bare or plain-text URL), its mark, and one sentence saying why it is related to or different from this ticket (for a dependency, what this ticket needs from it), and nothing more — no longer description and no restatement of the other ticket's content. The rendered initial task and the example ticket state this and move today's "a clearly related `Proposal` or `Deferred` item may be named as related, by URL" rule into the section (still with no `depends_on` entry). `validate` accepts a schema-10 ticket without the section and one with a well-formed section, and refuses, naming it, a present section that is empty, says `none`, or has a bullet lacking a markdown link to the ticket, carrying a bare or plain-text URL, lacking a `related`/`dependency` mark, or carrying more than one sentence beside them; over a real `local-md` board, `board-status` refuses a schema-10 ticket whose `depends_on` entry's URL is not on a `dependency` bullet of the section and accepts it once it is, while schema-7-9 tickets keep today's anywhere-in-the-body rule; and an evidence comment carrying `## Related tickets` as a level-2 heading is refused by the `--board` checks.
- 'Tickets filed under today''s shape keep working: over a real `local-md` board, a schema-9 and a schema-7 item filed by another run are found by an invariant-phrased `--search`, take this run''s evidence comment, pass `check-dispositions --board`, and are re-estimated by `re-estimate`, keeping their schema; and a ticket of this run at schema 9 brought forward by the rule `templates/follow-up-task/older-schema.md.j2` renders into both modes validates at schema 10 after `board-status`.'
- '`tests/plan_tooling/test_follow_ups_recipe_e2e.py`''s journey over the task''s board search (today `test_the_task_searches_the_board_by_root_cause_and_that_query_selects_exactly_its_items`) holds the new search: the task a real dispatch of the shipped graph is handed spells the invariant-first `--search` for an ordinary ticket and the slug query only for overruns, every `board-items` it spells still names a narrowing flag, and the scripted turn''s invariant-phrased search answers the earlier run''s item filed under a different slug.'
- 'AGENTS.md agrees with the new behaviour and keeps every other follow-up rule: its "The board is searched, never listed" paragraph says the agent asks first by the invariant''s words and once per further distinct text question, and by root-cause slug only for a budget overrun; it states the root-cause definition and bar where it describes grouping or the same-root-cause path; and its rules that the board is searched and never listed, that every query goes through `python -m orchestrator.follow_up_tickets board-items`, that a bound ticket makes no duplicate search, that a run owns only the issues it created, and the status and who-moves-an-item rules are unchanged. `docs/orchestration.md`''s follow-up sections and `personas/follow-up.yaml`''s system prompt agree with it.'
- 'Tests hold all of the above, replacing assertions that pinned the old wording: `tests/test_follow_up_tickets.py`, `tests/test_follow_up_ticket_docs.py`, `tests/test_follow_up_priority.py`, `tests/test_follow_up_comments.py`, `tests/test_follow_up_agent_arrangement.py`, `tests/plan_tooling/test_follow_ups_recipe_e2e.py`, `tests/plan_tooling/test_follow_ups_comment_answers_e2e.py`, `tests/plan_tooling/test_follow_ups_answer_comments_recipe_e2e.py` and `tests/plan_tooling/test_linear_routed_follow_ups_e2e.py` pass over the finished tree; every line the change adds under `orchestrator/` is covered by them; ruff''s format check and lint and mypy pass over `orchestrator/` and `tests/`; and the judged lint (llmlint) over the branch''s diff against its base reports no finding.'
- Every claim the dispatch's report makes about the change and its evidence is true of the tree as it finally stands; a check result it reports was taken over that final tree, not over an earlier state a later edit invalidated.
additional_info: |-
  **Decisions already taken, which this task does not reopen.** The manager ruled: (1) ownership stands — no run rewrites another run's item; a broader cause goes in the evidence comment's `Bears on the ticket:` paragraph, required in that case, stating only the widened invariant and each added location; a run widens its own item by copying again. (2) The near-match record is on the board, as the ticket's last section, terse: one line of queries, one line per item judged distinct (URL and why one change would not remove both), or `none`; no restatement of matched items. It is on the board because the local disposition account stays on the verifying host, and the transcripts behind the duplicates below were on other hosts. (3) `## Root cause` is held to `**Invariant.**` then `**Contributing locations.**` at schema 10; schemas 7-9 stay sound and are brought forward when a run rewrites its own ticket; the one-change-removes-every-instance bar sits next to that guidance. (4) The agent's slug metadata query is dropped for ordinary tickets and kept for budget overruns; the tooling's internal slug lookups stay. (5) No new budget. (6) A later addition from the user: the optional `## Related tickets` section described under What, distinct from `## Duplicate search`, absorbing today's "clearly related item may be named as related" rule and listing every recorded dependency. (7) The user's refinement: each entry is exactly the other ticket's link plus one sentence on why it is related to and/or different from this ticket — for a dependency, what this ticket needs from it. Cleaning up the existing duplicates is out of scope: this task writes nothing to any board.

  **The examples, as reading material only** (never quoted in the template or the module). #1634 "Plan checks miss destination CI title policy" (slug `publication-title-policy-missing-ci-validation`, located in `orchestrator/publication_guard.py`) duplicates #1332 "planner guidance omits the breaking title for a UI engine relink" (slug `planner-omits-engine-break-title`, `personas/planner.yaml`): one invariant — a lifecycle node's title is never checked against the destination's title and release policy before dispatch — with two contributing locations; #1332 was a `Proposal`. #1637 (`scripts/follow-ups.sh`) and #1638 (`orchestrator/follow_up_comments.py`) were filed by one run 8 seconds apart from three drafts: one invariant — the follow-up recipes can act only on a whole run or the whole board, never one named item. Use pairs of this shape for the test fixtures, generated on a real `local-md` board through the module's own render, never read from the live board.

  **Stored shapes that must not move.** `COMMENT_MARKER`, `COMMENT_OPENING`, `REPLY_MARKER` and the reply openings are read back off comments already on both boards, and the record's existing keys are read off every item there, so they stay byte for byte. The schema bump follows the module's own pattern (the comment above `SCHEMA` records each schema's addition; `STRUCTURE_AT`-style gating holds a structure only from the schema that introduced it). If you find you need a departure from any agreed shape — another record key, a different section name, a change to the marker or the gathering's routing — surface it to the manager with `$ORCHESTRATOR_ASK_MANAGER` and keep building against the shape as agreed: a shared shape is never changed unilaterally.

  **Where things are.** In `templates/follow-up-task.md.j2`: step 5 around line 292, step 7 around 307-364 (the slug query is spelled at 314), step 9 around 375, "The account of every draft" around 406, "The verified ticket" around 449 (the `repository` description at 458, the title at 472, the same-root-cause dependency rule at 560), the budget-overrun bullets around 628-650. In `orchestrator/follow_up_tickets.py`: `SCHEMA` and its history at 97-137, `RECORD_KEYS` around 430, `HEADINGS` at 508, `_HEADING_GUIDANCE` at 5170, `counts_as_occurrence` at 3156, `filed_board_problems` and `_evidence_carrier` at 3723-3810, the `board-items` parser at 5792, the `Impact` labelled-part check around 1669 as the pattern for `## Root cause`'s parts. AGENTS.md's follow-up section starts at line 949 (the search paragraph at 1063). Tests pinning the old search wording include `tests/plan_tooling/test_follow_ups_recipe_e2e.py` around 2783 and 3420-3470.

  **Testing the judged part.** Whether two reports share an invariant is the agent's judgment, which no journey can script a verdict for; follow the template's existing `<!-- llmlint: ignore-block[changed_behavior_has_e2e] … -->` pattern for that prose, naming the tests that hold what the tooling decides (the rendered task carrying the rule, the slug adoption accepted and the old shape refused over a real board).

  **Cadence.** Iterate with the targeted pytest modules and `just lint-llm-diff <base>`; running the pieces separately is fine, and do not run the repository's whole gate, which the merge path runs on publication.
budgets: []
spikes: []
what: |-
  Change what a follow-up run means by a **root cause**, so that two reports of one problem become one ticket — inside one run when it groups its drafts, and across runs when it searches the `followups` board before filing. The change lives in `templates/follow-up-task.md.j2` (initial mode's "What to do, in order" steps 5, 7 and 9, "The account of every draft", "The verified ticket", and the budget-overrun bullets of "Every landed change against its budgets"), its includes `templates/follow-up-task/ownership.md.j2` and `templates/follow-up-task/older-schema.md.j2`, `orchestrator/follow_up_tickets.py` (the record schema, `HEADINGS`, `_HEADING_GUIDANCE`, the validators, the `board-items` help text), the tests over them, and the prose that restates these rules: AGENTS.md's "Follow-ups: drafted while a run works, verified once it ends", `docs/orchestration.md`'s follow-up sections, and `personas/follow-up.yaml`'s system prompt.

  **1. The definition.** A root cause is the **invariant or guarantee that is missing or broken** — the rule the system should hold and does not — stated in words that do not depend on which file a run happened to see, with **every code location that contributes to it** listed under it (possibly several, possibly in several repositories). The bar, stated beside the `## Root cause` guidance and in step 5: **one root cause when a single change to the rule removes every instance, even if that change touches several places; two when they need independent fixes that each stand alone.** The invariant must be specific enough that one concrete suggested fix removes it, so the bar refuses a catch-all ("the follow-up tooling is awkward"); one ticket, one concrete suggested fix is unchanged, and that fix may already span several units and repositories. The ticket's title is the invariant in one line, and its slug names the invariant rather than a file. The record's `repository` stops being described as "the repository its root cause lives in" (a root cause may span several) and is described by what it already is: the repository its issue is filed in, the one its fix changes when there is one. State all of this in general terms: no issue, repository or file from this task's examples appears in the rendered task or the module's guidance strings.

  **2. Grouping within a run.** Step 5 applies the definition: a run's own drafts whose evidence points at different files but shares one invariant become one ticket, its contributing locations listing each.

  **3. The board search before filing.** For an unbound ticket, step 7's first query is a `--search` phrased in the words of the invariant and its symptom — what a report of the problem from any file would carry — then one per distinct further question about the symptom, the command or the message, and the failing file or function last, because the file is exactly what differs between two reports of one cause. **The agent's `--metadata orchestrator.follow-up/root_cause=<slug>` query is dropped for ordinary tickets** (each run invents its slug, so it almost never matches across runs) **and kept for budget-overrun tickets**, whose slug `<repository name>-<budget id>-over-<cause>` is derived deterministically. The tooling's own internal slug lookups — `_evidence_carrier`'s query for an older account naming no carrier — stay as they are. Every other search rule stands: a bound ticket makes no duplicate search; `board-status` alone asks by origin; each distinct question once, answers reused; nothing lists the board; the per-ticket request count is unchanged (one query swapped for one). An open item — at any open status, `Proposal` and `Deferred` included, as today's same-root-cause path already allows — whose stated root cause meets the bar is **the same root cause even when its location and slug differ**, and takes this run's evidence as a comment rather than a sibling ticket.

  **4. Adopting the matched item's slug.** Today "the same root cause" is enforced as slug equality: the evidence-comment marker carries a `root_cause`, `check-dispositions --board`'s carrier check (`filed_board_problems` → `_evidence_carrier`) requires the carrier item's record `root_cause` to equal the local ticket's, and the occurrence recount (`counts_as_occurrence`) counts a comment only when its marker's `root_cause` equals the issue's. The template never says so, so an agent that matched an item by text under its own invented slug could not route evidence there without a refusal, and filed instead. The task now states that **a ticket whose root cause is an open item's takes that item's `root_cause` slug** — its file name and its record's `root_cause` — so the marker, the account and the recount agree with no change to the tooling's keys.

  **5. Widening without taking ownership.** No run rewrites another run's item (the ownership rule stands). Where this run's evidence shows the cause broader than another run's item states, its evidence comment's `Bears on the ticket:` paragraph becomes required and states only the widened invariant and each added contributing location. A run widens its **own** item by rewriting its ticket and copying it again.

  **6. The stored ticket shape moves to schema 10.**
  - `## Root cause` is held to two labelled parts, in this order, the way `## Impact` is held to its parts: `**Invariant.**` (the missing or broken guarantee, in file-independent words) then `**Contributing locations.**` (one or more bullets, each naming a repository and a path).
  - A new **last** section, `## Duplicate search`, after `## Owning runs`, terse because the user reads every ticket: one line naming the text queries run, then one line per open item the searches returned that was judged a different root cause — a markdown link to it whose text is its title, never a bare URL, and why one change would not remove both — or `none`. It never restates a matched item's content.
  - Schemas 7, 8 and 9 stay readable and sound exactly as today, without either addition; `board-status`, `re-estimate`, `copy` and the `--board` checks keep reading them. The older-schema rule (`templates/follow-up-task/older-schema.md.j2`) brings a ticket of this run forward to schema 10 when the run rewrites it — its `## Root cause` written as the two parts from the evidence it already carries, and its `## Duplicate search` stating that no search was recorded when it was filed where this dispatch ran none for it (a bound ticket runs none) — and another run's item keeps its schema and its body, as today.
  - A new **optional** section, `## Related tickets`, placed directly before `## Duplicate search`. Most tickets omit it, and it is absent — no empty heading, no `none` — when it has no entries. Each entry is one bullet and nothing more: a markdown link to the other ticket whose text is that ticket's title — never a bare or plain-text URL — marked `dependency` or `related`, and exactly **one sentence** saying why it is related to and/or different from this ticket — for a `dependency`, what this ticket needs from it. No longer description, and never a restatement of the other ticket's content. It lists **every dependency the ticket records** — each `depends_on` entry on an accepted ticket, by its URL, marked `dependency` — and each issue that contributes to the same broader problem but is an independent fix (it did not meet the one-change-removes-every-instance bar), marked `related`, at whatever status. Today's rule that "a `Proposal` or `Deferred` item for a clearly related root cause may be named as related, by URL, with no `depends_on` entry" moves into this section: such an item is a `related` entry and nowhere else, still with no dependency entry and no change to the ticket's claims. The rule that the text states where and how an accepted fix changed the ticket (in `## Impact`, `## Root cause`, `## Suggested fix` or `## Rejected fixes`) stands beside the section's `dependency` entry. The section differs from `## Duplicate search`: that one is the diagnostic record of near-matches judged a different root cause; this one is related work a reader should know about, so an item may appear in both. At schema 10, `board-status`'s existing dependency resolution requires each entry's URL on a `dependency` bullet of `## Related tickets` (today it requires the URL anywhere in the body, which stays the rule for schemas 7-9), and `validate` refuses a present section that is empty, carries `none`, or has a bullet without a markdown link to the ticket, with a bare or plain-text URL, without one of the two marks, or carrying more than the link, its mark and one sentence (more than one sentence, or a second line or paragraph under the bullet). Bringing a ticket of this run forward to schema 10 writes the section from its existing dependency entries and related mentions.
  - `ticket_section_headings()` derives the evidence-comment heading refusal from `HEADINGS`, so `Duplicate search` and `Related tickets` are refused in an evidence comment; keep it derived, adding the optional heading the way `REJECTED_FIXES` is added.
why: |-
  The user, reviewing the `followups` board, keeps finding duplicates: "duplicates from follow up agent - It seems to be not doing good enough search for existing issues before filing a new one. I have recently encountered multiple duplicates." On the diagnosis: "I wonder if the problem is actually more structural like are we defining root cause to always be a single code location? maybe if root cause could multiple things that work together to cause the issue that would resolve the contention?"

  Every duplicate costs the user a review of a ticket that adds nothing, splits one problem's evidence and priority across two items (so neither reaches the occurrence count that raises its priority), and risks two plans fixing one problem twice, or fixing half of it.
-->
