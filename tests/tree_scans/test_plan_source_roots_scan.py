"""The half of `tests/test_plan_source_roots.py` that scans the whole tree.

It sits in the `tree-scans` project.

These grep every tracked file for the store's test Linear key, so what they read is every
file git tracks and they carry `reads_docs`, which routes them to that project's whole-
workspace `test-docs` target; the tests of that module that read none of it stay there, in
`orchestrator:test`, and those reading a registered checkout in
`orchestrator:test-checkouts`.
"""

from __future__ import annotations

import subprocess

import pytest
from test_plan_source_roots import RESERVED_LINEAR_KEY, _linear_schema

from orchestrator.root import REPO_ROOT


@pytest.mark.reads_docs
def test_no_tracked_file_names_the_store_s_test_linear_key() -> None:
    """Nothing here names the key onetaskgraph's own live lane reads.

    A host's organization Linear key is named for that organization — the example layer's
    is `HIDDENCO_LINEAR_API_KEY`. A tracked file naming the bare
    default — in a config, a mask, an `.env` template — is one edit away from a source or a
    dispatch reading the test workspace's key in place of the production one, or handing the
    production one to a lane that expects the test one.
    """
    properties = _linear_schema()["properties"]
    assert isinstance(properties, dict)
    assert properties["api_key_env"]["default"] == RESERVED_LINEAR_KEY, properties["api_key_env"]
    named = subprocess.run(
        ["git", "grep", "--line-number", "--word-regexp", "--fixed-strings", RESERVED_LINEAR_KEY],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert named.returncode == 1, (
        f"tracked files name {RESERVED_LINEAR_KEY}, onetaskgraph's test key:\n"
        f"{named.stdout}{named.stderr}"
    )
