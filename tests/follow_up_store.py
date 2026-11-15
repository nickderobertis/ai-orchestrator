"""A follow-up store of a test's own, so nothing a journey drafts, files or answers lands here.

onefollowup reads its store from `ONEFOLLOWUP_STORE` — an `onetaskgraph.yaml`, or the
directory holding one — and keeps its working files, `.onefollowup/`, beside it. The
follow-up recipes create their plans in this checkout's `follow-up-runs` source. So a
journey points all three under its own directory: a store whose `drafts` source is a
local folder and whose `followups` destination is another, standing in for the board;
the `drafts` root at the plan store's environment layer, so a process reading this
checkout's configuration agrees with onefollowup on where drafts are; and the
`follow-up-runs` root, so the plans the recipes create are the journey's own.
"""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

import follow_up_variables
import yaml

from orchestrator.root import REPO_ROOT

#: This checkout's own store configuration.
CHECKOUT_STORE = yaml.safe_load((REPO_ROOT / "onetaskgraph.yaml").read_text(encoding="utf-8"))

#: The plan source the follow-up recipes create their plans in, at the store's environment
#: layer.
RUNS_ROOT = "ONETASKGRAPH_SOURCES__FOLLOW_UP_RUNS__CONFIG__ROOT"

#: The source standing in for the board. It is a name of its own rather than `followups`,
#: which `onefollowup.yaml` names, so the suite's settings pointing `followups` at an empty
#: loopback board stay in force for every process that reads this checkout's configuration,
#: and nothing a journey runs can reach a real board; onefollowup is pointed at it through
#: its own environment layer, `ONEFOLLOWUP_DESTINATION`.
DESTINATION = "stand-in-followups"
DESTINATION_ENV = "ONEFOLLOWUP_DESTINATION"


class Store(NamedTuple):
    """One journey's follow-up store: its directory, the drafts and board folders, and the
    environment pointing every party at them."""

    directory: Path
    drafts: Path
    board: Path
    plans: Path
    environment: dict[str, str]

    def over(self, inherited: dict[str, str]) -> dict[str, str]:
        """``inherited`` with every party pointed at this store."""
        return inherited | self.environment

    @property
    def work(self) -> Path:
        """onefollowup's working files, beside the store's `onetaskgraph.yaml`."""
        return self.directory / ".onefollowup"


def isolated(root: Path) -> Store:
    """A store under ``root`` whose destination is a local folder standing in for the board."""
    directory = root / "follow-up-store"
    drafts, board, plans = directory / ".follow-ups", directory / "board", root / "follow-up-runs"
    for folder in (drafts, board, plans):
        folder.mkdir(parents=True, exist_ok=True)
    (directory / "onetaskgraph.yaml").write_text(
        yaml.safe_dump(
            {
                # This checkout's boundary policy, so every write is classified and checked
                # as the checkout's own store would classify and check it.
                "write_policy": CHECKOUT_STORE["write_policy"],
                "default_sources": [follow_up_variables.source()],
                "sources": {
                    # The checkout's drafts source with its root here, so a draft is
                    # classified as the checkout's own declaration classifies it.
                    follow_up_variables.source(): CHECKOUT_STORE["sources"][
                        follow_up_variables.source()
                    ]
                    | {"config": {"root": ".follow-ups"}},
                    DESTINATION: {"plugin": "local-md", "config": {"root": "board"}},
                    # The board `onefollowup.yaml` names, as this checkout declares it, so the
                    # settings pointing it at the suite's empty loopback board complete it here
                    # as they do in the checkout's own configuration. Nothing writes to it.
                    "followups": CHECKOUT_STORE["sources"]["followups"],
                },
            }
        ),
        encoding="utf-8",
    )
    environment = {
        follow_up_variables.ONEFOLLOWUP_STORE: str(directory),
        follow_up_variables.root_name(): str(drafts),
        follow_up_variables.plugin_name(): "local-md",
        RUNS_ROOT: str(plans),
        DESTINATION_ENV: DESTINATION,
    }
    return Store(directory, drafts, board, plans, environment)
