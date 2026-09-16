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

#: Accessors rather than second copies of the contract: what each names is the helper's
#: own constant, so a helper that stopped defining one fails the read below rather than
#: answering wrongly. The source is held beside the variable because the helper composes
#: the second out of the first — a reader that spelled the source itself could name a
#: source this checkout does not plan into.
NAME_HOLDER = "PLAN_AUTHORING_ROOT_ENV"
SOURCE_HOLDER = "PLAN_AUTHORING_SOURCE"


@functools.cache
def _held(holder: str) -> str:
    read = subprocess.run(  # noqa: S603 - this repository's own helper, read as its callers do
        # noqa: S607 - bash from the search path, because that is the interpreter every
        # recipe sourcing this helper runs it under; an absolute one here would read a
        # shell the callers never use.
        ["bash", "-c", f'source "{HELPER}"; printf "%s" "${holder}"'],  # noqa: S607
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if read.returncode != 0 or not read.stdout.strip():
        raise AssertionError(
            f"{HELPER} did not answer with the {holder} it composes; it is the one source of "
            f"that name, so nothing here can stand in for it:\n{read.stdout}{read.stderr}"
        )
    return read.stdout.strip()


def name() -> str:
    """The environment variable a planning launch exports its plan-authoring root under."""
    return _held(NAME_HOLDER)


def source() -> str:
    """The plan source that root belongs to, as this checkout's store configures it."""
    return _held(SOURCE_HOLDER)
