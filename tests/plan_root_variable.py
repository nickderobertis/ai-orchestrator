"""The name a launch exports this checkout's plan-authoring root under.

The `authoring` source `onetaskgraph.yaml` configures is rooted relatively, so a launch
that writes or reads it pins its root through the plan store's own environment layer,
`ONETASKGRAPH_SOURCES__<SOURCE>__CONFIG__ROOT` beside `..__PLUGIN`. Composed here from that
layer's convention, so no journey spells the names itself.
"""

from __future__ import annotations

#: The plan source a plan is drafted in, as `onetaskgraph.yaml` names it.
SOURCE = "authoring"


def source() -> str:
    """The plan source the authoring root belongs to."""
    return SOURCE


def _setting(key: str) -> str:
    return f"ONETASKGRAPH_SOURCES__{SOURCE.upper()}__{key}"


def name() -> str:
    """The environment variable the plan-authoring root is exported under."""
    return _setting("CONFIG__ROOT")


def plugin_name() -> str:
    """The environment variable that source's plugin is exported under, beside its root."""
    return _setting("PLUGIN")
