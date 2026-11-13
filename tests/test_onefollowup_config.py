"""The one file this host configures its follow-up loop with, held to exactly what it states.

onefollowup carries the loop — drafts, verification, tickets, comment answers — and this
repository states only its store, destination, budget inputs and review sink, in the root
`onefollowup.yaml`. Everything else is onefollowup's shipped default, so a key added here is
a decision about triage this host has deliberately not taken. Read twice: as the file says
it, and as the installed `onefollowup config show --sources` resolves it.
"""

from __future__ import annotations

import json
import os
import subprocess
from typing import Any

import yaml

from orchestrator.root import REPO_ROOT

CONFIG = REPO_ROOT / "onefollowup.yaml"
ONEFOLLOWUP = REPO_ROOT / ".venv" / "bin" / "onefollowup"

#: The file, whole: the keys and values this host states and no others.
STATED: dict[str, Any] = {
    "destination": "followups",
    "drafts_source": "drafts",
    "hooks": {
        "batch_preparing": ["scripts/follow-up-budget-inputs.sh"],
        "batch_settled": ["onepipeline", "human-items", "raise", "--phase", "follow-up-review"],
    },
}


def test_the_file_states_exactly_the_store_destination_inputs_and_sink() -> None:
    stated = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))

    assert stated == STATED
    assert "review_sink_command" not in stated and "inputs_command" not in stated


def test_onefollowup_resolves_those_keys_from_the_file_and_every_other_from_its_default() -> None:
    environment = {
        name: value for name, value in os.environ.items() if not name.startswith("ONEFOLLOWUP_")
    }
    shown = subprocess.run(  # noqa: S603 - the pinned onefollowup
        [str(ONEFOLLOWUP), "config", "show", "--sources"],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert shown.returncode == 0, shown.stderr
    document = json.loads(shown.stdout)
    sources = document["sources"]

    from_file = {key for key, source in sources.items() if source["layer"] == "file"}
    assert from_file == set(STATED)
    assert {sources[key]["name"] for key in from_file} == {str(CONFIG)}
    assert {source["layer"] for key, source in sources.items() if key not in STATED} == {"default"}
    for key, value in STATED.items():
        assert document["config"][key] == value
