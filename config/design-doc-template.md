# The design document a plan is read as

The template a dispatched `design-doc` writer follows, and the one place its shape is
stated. `personas/design-doc.yaml` names this path rather than carrying a second copy:
one question gets one answer, and a role that restated the shape would be a second
answer for a writer to follow when the two drifted. Change the shape here.

**One property below is lent to the dispatch's own task.** `scripts/finish-plan.sh` copies the
block between the `composed-into-the-dispatch` markers verbatim into the design-doc node's
acceptance criteria, so the requirement a plan across repositories turns on is in the text
that dispatch is judged against rather than only behind a pointer to this file. This file
is still the one source — those are its bytes, not a second copy of them — and a launch
that cannot find the pair of markers refuses by name rather than composing nothing. Write
the block so it reads as a criterion standing on its own: it is quoted somewhere this
file's own layout is not there to be referred to. It is also the only place that property
is written: the skeleton below describes each section without restating what the list
above judges it on, so a property said twice here is a property that can drift here.

**Changing this file leaves every approved design document unapproved.** `just
approve-design` keys its record on a digest of the document's own content *and* of this
file, so a plan already approved is refused at launch until somebody records the approval
again. That is the gate working — an approval is of a document read against the bar in
force when it was read, and moving the bar is exactly what nobody has read it against —
so a change here is paid for in re-approvals rather than routed around.

**Who it is for decides everything below.** The reader is a technical product manager
with no depth of knowledge in this domain. They read this instead of the plan, and they
have to be able to form an opinion from it: accept a contract, argue with an
architecture, or say the acceptance criteria do not describe what they asked for. A
document only its author's peers can read has failed at the one job it has, however
accurate it is.

## What the document is judged on

- **Simple language. Terse, pithy, precise. No jargon.** Prefer the short word and the
  short sentence. A term of art that cannot be avoided is defined once, in passing, in
  the words that reader already has.
- **It excludes implementation detail that can easily be changed later.** A function
  name, a file layout, a library choice with a dozen equivalents, the order two steps
  run in — none of that survives contact with the work, and each line of it is a line
  the reader has to skip to reach something they can act on.
- **It highlights the components that substantially affect the plan and may be harder to
  change later.** The decisions that are expensive to reverse are what the reader is
  actually being asked about: a stored shape, a published interface, a boundary between
  two pieces of code, a dependency taken on somebody else's release. Say what each one
  commits to and what reversing it would cost.
<!-- composed-into-the-dispatch -->
- **Where the plan spans more than one repository, the architecture names the repository
  each piece lives in.** A reader who does not already hold that mapping cannot tell which
  change lands where, which is the one thing the architecture exists to let them picture.
  The planned-tasks table cannot carry it — that table's columns are fixed and its
  location column is the task record's own — so the architecture is where it goes or it is
  nowhere. A plan inside one repository says nothing, because there is nothing to tell
  apart.
<!-- end composed-into-the-dispatch -->
- **It links out to the tasks and issues where the detail lives rather than restating
  them.** Restating a task's acceptance criteria here creates a second copy that goes
  stale the first time the task is amended. The planned-tasks table is where the linking
  happens.
- **Each row of that table points at its task using whatever the plan store reports that
  task's location to be.** Ask the store; never compose a location by hand. The pointer
  reads as a **link** where the task lives on a website, and as a **path** where it is a
  file on this machine — those are the two forms a plan store here reports, and which
  one a row gets is decided by the store's answer rather than by the writer.

## The shape

Six sections, in this order, and no others. Nothing else is a heading of the document.

```markdown
## What

What is being built, in one short passage.

## Why

The motivation, in the user's own terms — what they said they wanted and why. Not the
handoff, and not a restatement of What.

## Architecture

The shape of the thing: the pieces, and how they fit. Enough for the reader to picture
it and to see where the expensive decisions sit.

## Contracts

The agreements the plan cuts at, each stated so the reader could accept or reject it —
what each party owes, and what the other may assume. One per paragraph or list item.

## Acceptance criteria

What has to be true when the whole plan is done. Properties of the finished thing, not
the steps taken to reach it.

## Planned tasks

| Task | What it delivers | Depends on | Where it lives |
| --- | --- | --- | --- |
| <task title> | <one line> | <task titles, or none> | <link, or path> |
```
