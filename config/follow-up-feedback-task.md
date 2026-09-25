## What

Answer the board comments people wrote on run `@RUN@`'s follow-ups on the `@BOARD@` board.
The comments are quoted verbatim below, and they are the whole of this dispatch: act on
each one in the order it is quoted, post its one reply, and account for what you did.

Nothing else on the board or in this run's drafts is yours here. Do not inventory the
drafts, do not verify a claim, do not list the board, do not read its accepted items, and
do not touch a ticket, an issue or a comment that no comment below names.

## Why

This dispatch used to be composed from the full verification task, so a run answering two
comments spent the rest of its paid turn re-reading and re-copying every unrelated ticket
**after** its replies were already posted, and reached the provider's deadline with the
work it was dispatched for long done. The comments a person wrote are what is waiting on
an answer, so they are what this task carries.

## Where everything is

- **Where to run commands.** Your own working directory is the agent graph's scratch
  directory, which configures no plan source. Run every `@PLAN_STORE@` command from the
  checkout that launched you, `@CHECKOUT@` (`cd` there first). That checkout's
  configuration is what names the `@BOARD@` board.
- **The plan store.** `@PLAN_STORE@` is the plan-store program every command below names,
  spelled in full. Run it exactly as written and never as a bare `onetaskgraph`: a bare
  name is answered by whatever your search path offers first, which on two real runs was
  another release — one filed its tickets as issues of the wrong repository, and one had
  every sound ticket refused by a validator reading an older record schema.
- **The board.** Read the issue one comment sits on with
  `@PLAN_STORE@ task show <id> --json`, read its comments with
  `@PLAN_STORE@ task comment list <id>`, and add, edit or delete a comment with
  `@PLAN_STORE@ task comment add|edit|delete`. Read only the issues the comments below
  name, one at a time, and never list the board.
- **A ticket a comment asks you to change.** The ticket behind an issue is at
  `@DRAFTS_ROOT@/tasks/<its run>/tickets/<its root cause>.md`, which the issue's
  `@TICKET_METADATA_KEY@` record names. Only a ticket of an issue a comment below sits on
  is yours to change here, and only in what that comment asks for.

## What to do, in order

For each comment quoted below, in the order it is quoted:

1. **Act on it** under "Ownership on the board" below: perform whatever the comment calls
   for, within what this run owns, or nothing where nothing is called for. Where it asks
   for a change to the ticket behind the issue it sits on, and that issue is this run's,
   edit that ticket's file — changing what the comment asks for and leaving the rest of it
   exactly as it stands — then, **for that one ticket**:
   - run `@BOARD_STATUS@ --board @BOARD@ <path of the ticket>` and write the word it prints
     as the ticket's `status`, so a person's move of that item stands;
   - run `@VALIDATE@ <path of the ticket>` and correct the ticket until it reports it sound;
   - run `@COPY@ --board @BOARD@ <path of the ticket>`.

   When any of those refuses, copy nothing for that ticket, keep what it printed for your
   report and for the account, and go on to the next comment.
2. **Post its one reply**, naming the comment's id, as those rules state. Keep the id the
   store prints for it.
3. **Record it** in the account at `@RESPONSES@`, to the shape under "The account of every
   comment" below.

Then run `@CHECK_RESPONSES@` and correct the account until it reports it sound.

Finally, **report** each comment's URL beside what you did about it or why you did
nothing, and the reply you posted for it.

**Never change a board item's status**, and never touch an issue, a comment or a ticket no
comment below names: a person's move of an item to `Todo`, `Deferred` or `In Progress`
stands whenever they made it, and a ticket this run left unfinished is the next
verification dispatch's, not this one's.

## Ownership on the board

@COMMENT_CONTRACT@
## The account of every comment

@RESPONSE_CONTRACT@
## Acceptance criteria

- `@CHECK_RESPONSES@` reports the account at `@RESPONSES@` sound: every comment quoted
  below carries exactly one response, in the order it is quoted, naming the issue the
  comment is quoted on, and the board holds a reply of run `@RUN@` answering it. Run it
  last, after the final reply is posted and the final edit to that account, because a run
  of it from before either says nothing about what you leave.
- No issue, comment or ticket that no comment below names was created, edited, copied or
  closed by this dispatch.
- Every claim the report makes is true of the board as it finally stands.

## The comments to answer

Gathered into `@FEEDBACK_FILE@`, which is the name the account at `@RESPONSES@` records.

@FEEDBACK@
