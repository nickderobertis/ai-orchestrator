## What

Let whoever launches a run, meaning one execution of a plan, tag it with short labels like `nightly` and filter run lists by them.

## Why

The user starts a dozen runs a day and cannot find this week's crozier runs without opening each. They want labels set once at launch and shown wherever runs are listed.

## Architecture

The engine, `onepipeline`, runs plans. It checks tags at launch and stores them in each run's record, which every run list reads, so it ships first.

### Run ledger — `onepipeline` (`src/ledger.rs`)

The ledger keeps each run's permanent record, which gains its tags.

#### Launch record

**Reversibility: high cost** — older engines on other people's machines read launch records, and all would need updating.

Each run's `launch.json`, written once at launch, gains a `tags` list.

```diff
 {
   "run_id": "run-20260930-crozier-fern",
   "plan": "plans:crozier-fern-parity-close",
   "started_at": "2026-09-30T15:40:36Z",
+  "tags": ["crozier", "nightly"],
   "engine_version": "0.42.0"
 }
```

**Reversible:**

- **Older runs.** A record written before this change reads as a run with no tags.
- **Tags are fixed.** Nothing changes a run's tags after launch, including resuming a stopped run, called adopting it.

### Command line — `onepipeline` (`src/cli.rs`)

The command line starts and lists runs.

#### Tag format

**Reversibility: high cost** — tightening it would invalidate tags stored on other people's machines.

A tag is 1 to 32 lowercase letters, digits or hyphens, starting with a letter; a run has at most eight.

```text
tag   := [a-z][a-z0-9-]{0,31}
tags  := at most 8 distinct tags per run
```

#### `start --tag` and `runs --tag`

**Reversibility: high cost** — other people's scripts will pass `--tag`; renaming it, or making `runs --tag` match any tag, silently breaks them.

`start` takes one `--tag` per tag; `runs --tag` lists only runs carrying every tag named.

```diff
 Usage: onepipeline start [OPTIONS] <PLAN>

 Options:
       --run-id <ID>        The run's id; generated when omitted
+      --tag <TAG>          Label the run; repeatable, at most 8. Fixed for the run's life
       --detach             Return once the driver is running
```

```diff
 Usage: onepipeline runs [OPTIONS]

 Options:
       --mine               Only runs this session launched
+      --tag <TAG>          Only runs carrying this tag; repeated, a run must carry every one
       --json               Print one JSON object per run
```

**Reversible:**

- **Bad tags are refused early.** A malformed tag is refused, naming it, before anything is stored.

### Read API — `onepipeline-ui` (`onepipeline-api`)

The read API is the server behind the browser view.

**Reversible:**

- **Tags in the run list.** `GET /runs` returns each run's `tags`, an empty list when it has none.
- **Tag filter.** `GET /runs` takes a repeatable `tag` parameter, and a run must carry every tag given.

### Browser view — `onepipeline-ui` (`web/`)

The browser view is the web page listing runs.

**Reversible:**

- **Tag chips.** Each run in the list shows its tags.
- **Shareable filter.** A tag filter above the list is kept in the page address, so a filtered list can be shared as a link.

### Launch recipe — `ai-orchestrator` (`justfile`)

The launch recipe is how this repository starts runs.

**Reversible:**

- **Pass-through.** `just orchestrate --tag` hands each tag to `onepipeline start` unchanged.

## Acceptance criteria

- A run launched with `--tag crozier --tag nightly` shows both tags in every run list; filtering by both finds it.
- A malformed tag is refused, creating no run.
- Older runs show no tags, and adopted runs keep theirs.
- `just orchestrate <plan> --tag nightly` launches a run tagged `nightly`.

## Planned tasks

| Task | Unit | What it delivers | Depends on | Where it lives |
| --- | --- | --- | --- | --- |
| feat(ledger): store run tags | Run ledger | `tags` in `launch.json` | none | https://github.com/example/onepipeline/issues/101 |
| feat(cli): add --tag | Command line | Both flags and the check | feat(ledger): store run tags | https://github.com/example/onepipeline/issues/102 |
| feat(api): filter by tag | Read API | The `GET /runs` change | feat(cli): add --tag | https://github.com/example/onepipeline-ui/issues/55 |
| feat(ui): filter by tag | Browser view | Tags and the filter | feat(api): filter by tag | https://github.com/example/onepipeline-ui/issues/56 |
| feat(recipe): pass --tag through | Launch recipe | The pass-through | feat(cli): add --tag | https://github.com/example/ai-orchestrator/issues/900 |
