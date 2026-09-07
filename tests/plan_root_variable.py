"""The name a planning launch exports its plan-authoring root under, read from its source.

`scripts/plan-root-env.sh` composes that name and that value, and is the only place
either is composed: `onetaskgraph.yaml` roots the `authoring` source relatively, so a
launch that resolved it in two places could export two directories and the plan a
dispatched planner authors would simply land where nothing looks for it.

So no test spells the name. This reads it out of the helper, by sourcing the helper and
printing the constant it holds — which is the same read `scripts/plan.sh` makes, one
step short of the resolution. Deliberately short of it: a caller who only needs the name
should not need a resolvable plan store to learn it, and every journey that varies the
environment the resolution runs under lives in `tests/plan_tooling/test_plan_root_env.py`.
"""

from __future__ import annotations

import functools
import subprocess

from orchestrator.root import REPO_ROOT

HELPER = REPO_ROOT / "scripts" / "plan-root-env.sh"

#: An accessor rather than a second copy of the contract: what it names is the helper's
#: own constant, so a helper that stopped defining it fails the read below rather than
#: answering wrongly.
NAME_HOLDER = "PLAN_AUTHORING_ROOT_ENV"


@functools.cache
def name() -> str:
    """The environment variable a planning launch exports its plan-authoring root under."""
    read = subprocess.run(  # noqa: S603 - this repository's own helper, read as its callers do
        ["bash", "-c", f'source "{HELPER}"; printf "%s" "${NAME_HOLDER}"'],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if read.returncode != 0 or not read.stdout.strip():
        raise AssertionError(
            f"{HELPER} did not answer with the name it composes; it is the one source of "
            f"that name, so nothing here can stand in for it:\n{read.stdout}{read.stderr}"
        )
    return read.stdout.strip()
