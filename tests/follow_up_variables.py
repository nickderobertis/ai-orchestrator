"""The names a test clears or points so that follow-up drafts land in its own store.

A draft is written by `onefollowup draft` into the source `onefollowup.yaml` names as its
`drafts_source`, through the plan store, whose environment layer spells a source's root and
plugin as `ONETASKGRAPH_SOURCES__<SOURCE>__CONFIG__ROOT` and `..__PLUGIN`. A launch exports
the drafting command as `ORCHESTRATOR_FOLLOW_UP_DRAFT`, and onefollowup reads its own
configuration file and store from `ONEFOLLOWUP_CONFIG` and `ONEFOLLOWUP_STORE`. The source
is read from the configuration file rather than spelled, so a test cannot point a source
this checkout does not draft into.
"""

from __future__ import annotations

import functools

import yaml

from orchestrator.root import REPO_ROOT

CONFIG = REPO_ROOT / "onefollowup.yaml"

#: The variable a launch exports the drafting command's path under.
COMMAND = "ORCHESTRATOR_FOLLOW_UP_DRAFT"

#: The variables onefollowup reads its configuration file and its store from.
ONEFOLLOWUP_CONFIG = "ONEFOLLOWUP_CONFIG"
ONEFOLLOWUP_STORE = "ONEFOLLOWUP_STORE"


@functools.cache
def source() -> str:
    """The plan source a draft is stored in, as `onefollowup.yaml` names it."""
    stated = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    return str(stated["drafts_source"])


def _setting(path: str) -> str:
    return f"ONETASKGRAPH_SOURCES__{source().upper().replace('-', '_')}__{path}"


def root_name() -> str:
    """The variable the drafts source's root is set under at the store's environment layer."""
    return _setting("CONFIG__ROOT")


def plugin_name() -> str:
    """The variable the drafts source's plugin is set under at the same layer."""
    return _setting("PLUGIN")


def command_name() -> str:
    """The variable the drafting command's path is exported under."""
    return COMMAND


def all_names() -> tuple[str, str, str]:
    """The three a launch's environment may carry, for a journey that clears them."""
    return root_name(), plugin_name(), command_name()


def onefollowup_names() -> tuple[str, str]:
    """The two onefollowup reads its configuration and store from."""
    return ONEFOLLOWUP_CONFIG, ONEFOLLOWUP_STORE
