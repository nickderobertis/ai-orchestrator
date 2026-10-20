"""The plan store's two credential names, read off the tracked masks rather than restated.

Every `oneharness.*.toml` masks the board token and an organization's production Linear key
from its chain. The Linear key's environment-variable name is the one place a tracked file
may carry that organization's name, and the masks are where it is stated: this module reads
it from the variants' own `unset_env` in `oneharness.dispatch.toml`, so a test holding a
mask, a resolved record or a refusal to it names the variable without spelling it.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

#: The board credential every board source here names as its `token_env`.
BOARD_TOKEN = "GH_PROJECTS_TOKEN"

#: The spelling a resolved record and a test fixture write in place of the Linear key's name.
LINEAR_KEY_PLACEHOLDER = "<organization-linear-key>"


def _masked() -> set[str]:
    """Every name any variant's `unset_env` in `oneharness.dispatch.toml` lists."""
    document = tomllib.loads((REPO_ROOT / "oneharness.dispatch.toml").read_text(encoding="utf-8"))
    names: set[str] = set()
    pending: list[object] = [document]
    while pending:
        table = pending.pop()
        if isinstance(table, dict):
            for key, value in table.items():
                if key == "unset_env" and isinstance(value, list):
                    names.update(name for name in value if isinstance(name, str))
                else:
                    pending.append(value)
    return names


def organization_linear_key() -> str:
    """The masked Linear key's variable name: the one mask ending in `_LINEAR_API_KEY`."""
    keys = [name for name in _masked() if name.endswith("_LINEAR_API_KEY")]
    if len(keys) != 1:
        raise AssertionError(f"oneharness.dispatch.toml masks {len(keys)} Linear keys, not one")
    return keys[0]


#: Both credentials, in the order every mask lists them.
PLAN_STORE_CREDENTIALS = (BOARD_TOKEN, organization_linear_key())


def with_placeholder(text: str) -> str:
    """``text`` with the Linear key's name written as `LINEAR_KEY_PLACEHOLDER`."""
    return text.replace(organization_linear_key(), LINEAR_KEY_PLACEHOLDER)


def from_placeholder(text: str) -> str:
    """``text`` with `LINEAR_KEY_PLACEHOLDER` written as the Linear key's name."""
    return text.replace(LINEAR_KEY_PLACEHOLDER, organization_linear_key())
