# `tests/ask_seam`

The host-tool journeys over the seam a dispatched agent asks its manager through: the
real `scripts/ask-manager.sh` shim, the installed `onemessagebus ask` it hands every
question to under `config/onemessagebus.yaml`, and the real launches that decide what a dispatch is
given to ask with and which bus configuration its run records.

- **These journeys prove this host's wiring, never the bus's behaviour.** What a question
  does once it is on the queue — its correlation, its wait, a timeout that carries no
  ruling, a listener re-armed rather than a question re-asked — is proven by the
  `onemessagebus` release's own suite. A journey here asks whether a real launch's
  dispatch, a real run's channel and the manager's own recipes reach that behaviour, and
  reads a channel only through the bus's verbs or `just channel-next`, never its files.

- **One target, `ask-seam:test`, keyed on `askSeamWorkspace`**: what these journeys
  drive and read, minus the prose they never open and the e2e helpers they never import —
  named one by one rather than as a directory, so a journey added beside them does not
  silently start invalidating this tier. Every journey here spends a real launch, which
  is why nothing wider may key it and why there is no second, whole-workspace target.
