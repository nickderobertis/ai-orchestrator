"""The one file this host configures its follow-up loop with, held to exactly what it states.

onefollowup carries the loop — drafts, verification, tickets, comment answers — and this
repository states only its store, destination, budget inputs, review sink, the command
`onefollowup comments answer` launches each batch's feedback run with, the quota governor's
floor and grooming's clone directory, in the root `onefollowup.yaml`. Everything else is
onefollowup's shipped default, so a key added here is a decision about triage this host has
deliberately not taken. Read twice: as the file says it, and as the installed `onefollowup
config show --sources` resolves it.

The review sink hands the engine the context file onefollowup writes, which names an owner,
so onefollowup refuses every verb until `hooks.owner.session` is set. That is the launching
manager's session, which a launch and the drafting command export at onefollowup's
environment layer, `ONEFOLLOWUP_HOOKS__OWNER__SESSION`, rather than anything tracked.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import yaml

from orchestrator.root import REPO_ROOT

CONFIG = REPO_ROOT / "onefollowup.yaml"
ONEFOLLOWUP = REPO_ROOT / ".venv" / "bin" / "onefollowup"

#: The file, whole: the keys and values this host states and no others.
STATED: dict[str, object] = {
    "destination": "followups",
    "drafts_source": "drafts",
    "hooks": {
        "batch_preparing": ["scripts/follow-up-budget-inputs.sh"],
        "batch_settled": [
            "onepipeline",
            "human-items",
            "raise",
            "--phase",
            "follow-up-review",
            "--context",
            "{context_file}",
        ],
    },
    "comments": {"launch_command": ["just", "follow-ups-answer-batch", "{batch}", "{feedback}"]},
    "governor": {"floor_percent": 20},
    "groom": {"clones": ".onefollowup/groom/clones"},
}

#: The blocks the file states some keys of, each of whose other keys is the shipped default.
BLOCKS = ("governor", "groom")

#: Where every caller states the owner the review sink's context names, and a session.
OWNER_ENV = "ONEFOLLOWUP_HOOKS__OWNER__SESSION"
SESSION = "e2e-manager-session"


def test_the_file_states_exactly_the_store_destination_inputs_sink_and_launch() -> None:
    stated = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))

    assert stated == STATED
    assert "review_sink_command" not in stated and "inputs_command" not in stated


def _shown(environment: dict[str, str], *config: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - the pinned onefollowup
        [str(ONEFOLLOWUP), "config", "show", "--sources", *config],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )


def test_onefollowup_resolves_those_keys_from_the_file_and_every_other_from_its_default() -> None:
    """The file's keys from the file and the owner from the environment; the rest default."""
    environment = {
        name: value for name, value in os.environ.items() if not name.startswith("ONEFOLLOWUP_")
    }
    shown = _shown(environment | {OWNER_ENV: SESSION})
    assert shown.returncode == 0, shown.stderr
    document = json.loads(shown.stdout)
    sources = document["sources"]

    # `hooks` is reported at the last layer that set any of it, the owner's.
    assert sources["hooks"] == {"layer": "env", "name": OWNER_ENV}, sources["hooks"]
    from_file = {key for key, source in sources.items() if source["layer"] == "file"}
    assert from_file == set(STATED) - {"hooks"}
    assert {sources[key]["name"] for key in from_file} == {str(CONFIG)}
    assert {source["layer"] for key, source in sources.items() if key not in STATED} == {"default"}
    for key, value in STATED.items():
        if key not in ("hooks", *BLOCKS):
            assert document["config"][key] == value
    hooks = STATED["hooks"]
    assert isinstance(hooks, dict)
    assert document["config"]["hooks"] == hooks | {"owner": {"session": SESSION}}

    # With no owner stated, onefollowup refuses the file rather than running a sink whose
    # context names nobody.
    refused = _shown(environment)
    assert refused.returncode == 2, refused.stdout
    assert "hooks.owner.session" in refused.stderr, refused.stderr


def test_the_governor_and_groom_blocks_move_only_the_keys_the_file_states(tmp_path: Path) -> None:
    """Each block resolves to the shipped default with the file's keys over it."""
    environment = {
        name: value for name, value in os.environ.items() if not name.startswith("ONEFOLLOWUP_")
    } | {OWNER_ENV: SESSION}
    shown = _shown(environment)
    assert shown.returncode == 0, shown.stderr
    empty = tmp_path / "onefollowup.yaml"
    empty.write_text("{}\n", encoding="utf-8")
    shipped = _shown(environment, "--config", str(empty))
    assert shipped.returncode == 0, shipped.stderr
    for block in BLOCKS:
        stated = STATED[block]
        assert isinstance(stated, dict)
        resolved = json.loads(shown.stdout)["config"][block]
        default = json.loads(shipped.stdout)["config"][block]
        assert resolved == default | stated, (block, resolved, default)
