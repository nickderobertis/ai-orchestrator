"""A real plan-store fixture passes under the invoking user's layer that would refuse it.

`tests/plan_store_user_layer.py` is the rule, and the incident behind it: a host's own
plan-store user layer refused what a fixture's store did. This runs a real fixture of this
tier — `test_follow_up_comment_version_e2e.py`, which writes, decides and copies follow-up
tickets onto a loopback board through the locked store — as a pytest session of its own
loading this suite's conftest, under a configuration home holding a declaration the pinned
store refuses on every load: a private source of the host's naming a plugin this pin does not
know. The sandbox's other properties are held in
`tests/e2e/suite_helpers/test_plan_store_user_layer_e2e.py`.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import plan_store_user_layer
import pytest
from published_tools import ONETASKGRAPH_BIN
from waits import timeout as e2e_timeout

from orchestrator.root import REPO_ROOT

#: A host declaration the pinned store refuses on every load: a private source of the host's
#: whose plugin this pin does not know, as a layer written for a later store release names one.
#: The incident's own declaration, `authoring` private, is the tracked document's now, and a
#: layer stating a key the tracked document already states is overridden by it, so a layer
#: refusing a fixture has to declare something the tracked document does not.
HOSTILE_LAYER = (
    "sources:\n"
    "  hostprivate:\n"
    "    plugin: jira\n"
    "    visibility: private\n"
    "    config:\n"
    "      root: /nonexistent/host-private-store\n"
)
#: The fixture run whole: every case of it copies a ticket onto the loopback board.
FIXTURE = Path(__file__).with_name("test_follow_up_comment_version_e2e.py")


@pytest.fixture(autouse=True)
def _source_settings_restored(monkeypatch: pytest.MonkeyPatch) -> None:
    """Undo, after the test, the source settings `isolate()` unsets in this process."""
    for name in [
        name for name in os.environ if name.startswith(plan_store_user_layer.SOURCE_SETTING_PREFIX)
    ]:
        monkeypatch.delenv(name)


def test_a_fixtures_copy_is_untouched_by_a_user_layer_that_would_refuse_it(
    tmp_path: Path,
) -> None:
    """A fixture copying onto a board passes under the user layer that refused it on the host."""
    config_home = tmp_path / "config"
    layer = (
        config_home / plan_store_user_layer.STORE_DIRECTORY / plan_store_user_layer.USER_DOCUMENT
    )
    layer.parent.mkdir(parents=True)
    layer.write_text(HOSTILE_LAYER, encoding="utf-8")
    # This process's environment as a user whose configuration home is ``config_home``: without
    # the suite's record of the host's home or its coverage hand-off, so the session started
    # under it takes ``config_home`` for the invoking user's, as a target would.
    environment = {
        key: value
        for key, value in os.environ.items()
        if key != plan_store_user_layer.HOST_CONFIG_HOME_ENV
        and not key.startswith(("COV_CORE", "PYTEST_XDIST"))
    }
    environment[plan_store_user_layer.CONFIG_HOME_ENV] = str(config_home)
    refused = subprocess.run(
        [str(ONETASKGRAPH_BIN), "config", "show", "--json"],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(60),
        check=False,
    )
    assert refused.returncode != 0 and "jira" in refused.stderr, (
        f"the store read the layer at {layer} without refusing, so this proves nothing: "
        f"{refused.stdout}{refused.stderr}"
    )

    ran = subprocess.run(
        [sys.executable, "-m", "pytest", str(FIXTURE), "-p", "no:cacheprovider", "--no-cov", "-q"],
        cwd=REPO_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=e2e_timeout(600),
        check=False,
    )

    assert ran.returncode == 0, (
        f"a plan-store user layer at the invoking user's {layer} reached a fixture of this "
        f"suite:\n{ran.stdout[-6000:]}\n{ran.stderr[-2000:]}"
    )
    assert layer.read_text(encoding="utf-8") == HOSTILE_LAYER
