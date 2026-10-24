"""An organization's login is not a private term once one of its public repositories is known.

A host whose routes send an organization's work to that organization's tracker keeps its
private repositories private by rule in the host overlay `just repos-apply` installs. The
pinned `onevcs boundary check --destination public` then refuses each private repository's
bare name and its full `owner/name`, and it would refuse the owner's login on its own as well
— unless the registry knows a public repository of that owner, which is what keeps a login from
being a term (onevcs's `boundary/derive.rs`). So the host also registers one of the owner's
public repositories, declared public by rule so the host is never asked, and the bare owner
word passes while every private name is still refused.

These journeys drive the real pinned `onevcs` over a synthetic organization, `hiddenco`,
whose rules and registrations live in each test's own `ONEVCS_HOME`
(`tests/boundary_home.py`), never the host's.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import boundary_home

#: The synthetic organization's private repository and one public repository of it.
PRIVATE = "github.com/hiddenco/quietharbor"
PUBLIC = "github.com/hiddenco/openwidget"

#: The three kinds of text the boundary is asked about.
OWNER_WORD = "Filed for hiddenco by the follow-up run."
BARE_NAME = "Filed against quietharbor by the follow-up run."
FULL_NAME = "Filed against hiddenco/quietharbor by the follow-up run."


def _check(home: Path, text: str) -> subprocess.CompletedProcess[str]:
    """The pinned `onevcs boundary check --destination public` over ``text`` under ``home``."""
    return subprocess.run(
        [str(boundary_home.ONEVCS), "boundary", "check", "--destination", "public"]
        + ["--input", "-"],
        input=json.dumps({"destination": "public", "text": [text]}),
        env=boundary_home.environment(None, home, transports=False),
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )


def _verdict(done: subprocess.CompletedProcess[str]) -> str:
    assert done.returncode in (0, 1), done.stdout + done.stderr
    verdict = json.loads(done.stdout.splitlines()[0])["verdict"]
    assert isinstance(verdict, str)
    return verdict


def test_a_known_public_repository_frees_the_owner_word_and_no_private_name(
    tmp_path: Path,
) -> None:
    """With a public repository of the owner registered, only the owner word passes."""
    home = boundary_home.registered_home(tmp_path, public=[PUBLIC], private=[PRIVATE])

    assert _verdict(_check(home, OWNER_WORD)) == "pass"
    assert _verdict(_check(home, BARE_NAME)) == "refuse"
    assert _verdict(_check(home, FULL_NAME)) == "refuse"


def test_an_owner_of_only_private_repositories_is_itself_a_term(tmp_path: Path) -> None:
    """Without that public repository the owner word is refused too, which is why the host
    registers one."""
    home = boundary_home.registered_home(tmp_path, public=[], private=[PRIVATE])

    assert _verdict(_check(home, OWNER_WORD)) == "refuse"
    assert _verdict(_check(home, BARE_NAME)) == "refuse"
