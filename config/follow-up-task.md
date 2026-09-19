## What

Verify the follow-up drafts run `@RUN@` left behind, group what stands by root cause, and
put one verified ticket per root cause on the `@BOARD@` board. Where another run already
has an open issue for the same root cause, add this run's evidence to it as a comment
instead of filing a duplicate.

## Why

During a run, workers, the monitor, the pacemaker and the manager draft what they noticed
outside their own scope as unverified follow-ups, and nobody has checked them. This
dispatch checks them. A draft is worth a ticket only once its claim holds in the tree it
names. Every session's tickets accumulate on one board, so a root cause already filed
there gets this run's evidence rather than a second issue.

## Where everything is

- **Where to run commands.** Your own working directory is the agent graph's scratch
  directory, which configures no plan source. Run every `@PLAN_STORE@`, `onepipeline` and
  `onevcs` command from the checkout that launched you, `@CHECKOUT@` (`cd` there first).
  That checkout's configuration is what names the `@BOARD@` board and every other source a
  command here reads.
- **The plan store.** `@PLAN_STORE@` is the plan-store program every command below names,
  spelled in full. Run it exactly as written and never as a bare `onetaskgraph`: a bare
  name is answered by whatever your search path offers first, which on two real runs was
  another release — one filed its tickets as issues of the wrong repository, and one had
  every sound ticket refused by a validator reading an older record schema.
- **The drafts.** Run `@RUN@`'s drafts are local Markdown tasks in the `drafts` plan
  source, under `@DRAFTS_ROOT@/tasks/@RUN@/drafts/`. From that checkout, list them with
  `@PLAN_STORE@ task list --source drafts --project @RUN@ --json`, and read one with
  `@PLAN_STORE@ task show <qualified draft id> --json`.
- **Each draft's transcript.** A draft's `orchestrator.follow-up-draft` record carries a
  `transcript` command, `onepipeline transcript …` or `onepipeline monitor …`. Run it from
  that checkout to read the turns the draft was written from. A manager's draft carries
  none.
- **Other repositories' trees.** Read them in the registered checkouts `onevcs repos`
  lists. In the checkout of a draft's repository, run `git fetch origin`, then read
  `origin/<base>` with `git show origin/<base>:<path>` and
  `git grep <pattern> origin/<base>`. Never check out, commit to, or otherwise modify any
  checkout, and never clone. A repository with no registered checkout cannot be verified.
- **The board.** `@BOARD@` is the plan source verified tickets are copied onto. Read it with
  `@PLAN_STORE@ task list --source @BOARD@ --json` and
  `@PLAN_STORE@ task show <id> --json`, and comment with
  `@PLAN_STORE@ task comment add|list|edit|delete`.

## What each board status means

@STATUS_VOCABULARY@
## What to do, in order

1. **Record the basis first.** Before verifying anything, fetch `origin` in the registered
   checkout of every repository a draft names, and record `git rev-parse origin/<base>`.
   That commit is the basis every claim about that repository is verified at, and it is
   what the ticket's `basis` records.
2. **Verify each draft against that basis.** Read what the draft claims, its transcript and
   the tree, and decide whether the claim holds at the basis commit.
3. **Drop a draft you cannot verify, or one the basis already fixes.** Keep the id of every
   dropped draft and the reason it was dropped.
4. **Report what should have been surfaced live.** A draft that reads as something that
   should have been raised during the run — a decision fork, a constraint that could not
   be met, something the manager needed to act on then — is a finding in its own right.
   Report it, naming the draft, its author and why it was blocking, rather than filing it
   quietly as a ticket.
5. **Group what stands by root cause**, and write one ticket per root cause, to the shape
   under "The verified ticket" below.
6. **Validate each ticket, then delete the drafts it consumed.** Run
   `@VALIDATE@ <path of the ticket>` and correct the ticket until that command reports it
   sound. Then delete the draft files that ticket consumed, under
   `@DRAFTS_ROOT@/tasks/@RUN@/drafts/`.
7. **Search the board for the same root cause** among its open items: first by the
   `orchestrator.follow-up` metadata's `root_cause` and `repository`, then by titles and
   text (`@PLAN_STORE@ task list --source @BOARD@ --search <text> --json`). Both read the
   whole board, whichever repository an item's issue lives in, so narrow neither to a
   repository. An item at `Deferred` is open: no agent picks it up to work on, but it is
   searched like any other open item and still takes this run's evidence.
8. **Write each ticket as if the board's accepted fixes were already in.** List the
   board's accepted items — those at @ACCEPTED_STATUSES@ — with
   `@PLAN_STORE@ task list --source @BOARD@ @ACCEPTED_FILTER@ --json`, every page, the
   whole board, whichever repository an item's issue lives in: an accepted fix in another
   repository can change a ticket here. Read each item's `## Suggested fix`. This is **not**
   the search of the step before: an accepted item carrying the same root cause as a
   ticket is that step's — this run's evidence goes to it as a comment, and this step does
   not touch that ticket. This step is for accepted tickets of *other* root causes whose
   fixes bear on a ticket this run will copy. For each such ticket, decide against each
   accepted fix as if it were in:
   - **unchanged** — the fix does not bear on it: nothing changes;
   - **evaporates** — the fix removes this root cause too: the ticket is not filed. Delete
     its file and the drafts it consumed, and report those drafts as dropped, naming the
     accepted item's URL. On a re-dispatch where the board already holds this run's own
     item for it, withdraw that item instead, under the withdrawal rules of "The verified
     ticket", with the reason in its text naming the accepted item;
   - **shrinks** — the fix removes part of the impact or narrows where the root cause
     bites: write `## Impact` (its prose and its severity lines) and, where the scope
     narrows, `## Root cause` to what remains;
   - **needs a different fix** — the fix the evidence would otherwise support conflicts
     with, duplicates or is superseded by the accepted one: `## Suggested fix` states what
     remains right once the accepted fix is in, and the fix it would otherwise have
     proposed goes under `## Rejected fixes` with the accepted item as the reason.

   <!-- llmlint: ignore-block[changed_behavior_has_e2e] Whether a `Done` fix has reached the basis is the agent's reading of a tree, which the suite never scripts a verdict for: it doubles the agent's turn, so a journey could only assert what its own script chose. What the tooling holds for a `Done` item — that the accepted listing selects it and `board-status` admits it as a far end — is driven in `tests/test_follow_up_tickets.py` over every accepted status. -->
   A `Done` item's fix is assumed only where it has not reached the basis recorded in
   step 1 — read the tree; where it has, the verification at the basis already accounts
   for it and nothing changes. A `Proposal` or `Deferred` item's fix is never assumed; a
   clearly related one may be mentioned as related, as "The verified ticket" states.
   <!-- llmlint: ignore-end[changed_behavior_has_e2e] -->

   For every fate but unchanged, add the item's `depends_on` entry, say in the text where
   and how its fix changed the ticket with the item's URL, then validate the ticket again. On a
   re-dispatch, re-derive all of this from the board as it now is, exactly as a first pass
   does: an accepted ticket may have appeared, moved or been un-accepted since the last
   pass, so entries are added and removed and the ticket's claims re-derived to match, and
   this run's own item is edited by copying its ticket again.
9. **Decide each ticket's status from the board, before every copy.** Run
   `@BOARD_STATUS@ --board @BOARD@ <path of the ticket>`, adding `--withdraw` for a ticket
   this run withdraws, and write the word it prints as the ticket's `status`, then validate
   the ticket again. When it refuses, copy nothing for that ticket and keep what it printed
   for your report.
10. **Put each ticket on the board.** Where no open item carries the root cause, or the
    item that does is this run's own, copy the ticket as "The verified ticket" states. When
    the store refuses that copy, copy nothing more for that ticket and keep what it printed
    for your report. Where an
    open item for it was created by another run, copy nothing: add this run's one comment
    to that item, or edit the comment this run already left there, under "Ownership on the
    board" below.
11. **Report** every issue you created or updated with its URL (its location where the
    board reports no URL), every dropped draft with its reason, every ticket dropped or
    withdrawn under an accepted ticket with that ticket's URL, every ticket the board
    refused a status for with what `board-status` printed, every ticket the store refused
    to copy with the refusal it printed, and every finding that should have been surfaced
    live.

## The verified ticket

@TICKET_CONTRACT@
## Ownership on the board

@COMMENT_CONTRACT@
@REDISPATCH@
@FEEDBACK@
