# The design document a plan is read as

The template a dispatched `design-doc` writer follows, and the one place its shape is
stated. `personas/design-doc.yaml` names this path rather than carrying a second copy:
one question gets one answer, and a role that restated the shape would be a second
answer for a writer to follow when the two drifted. Change the shape here.

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
