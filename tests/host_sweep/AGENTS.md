# `tests/host_sweep`

- **Never meet this host's sweep lock.** Every journey names an `XDG_CACHE_HOME` and state
  roots of its own, and waits out each detached job it starts by the pid it captured; it
  signals nothing.
- **Stay apart from `tests/session_setup/`.** That project re-provisions the real
  environment, so widening its key to reach the `justfile` or the pins these journeys copy
  makes every such edit pay for a provisioning; a file these journeys start reading joins
  this project's key instead.
