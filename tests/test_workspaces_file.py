"""The workspaces file this host installs pools what it says, and maintains nothing itself.

`config/onevcs.workspaces.yml` is installed by `just repos-apply` as
`$ONEVCS_HOME/workspaces.yml`. Each repository declares its own pool maintenance in its
tracked `onevcs.toml`, so the one claim here about maintenance is an absence: no rule and
not `default:` names a `maintain`, because a host command would run in a repository whose
own declaration is missing, under a rule nobody declared for it.
`tests/e2e/publish_and_landing/test_worktree_pool_e2e.py` shows the pinned `onevcs`
resolving no maintenance from the composed file for a repository that declares none.
"""

from __future__ import annotations

import re
from typing import NamedTuple

from registered_checkouts import RepoIdentity

from orchestrator.root import REPO_ROOT

#: The tracked file, and where the recipe installs it.
TRACKED_WORKSPACES = REPO_ROOT / "config" / "onevcs.workspaces.yml"

#: The shared default for an identity no rule names: one warm slot per lender, and
#: sessions past it admitted without bound. A host that can afford more says so in its
#: own overlay, which `orchestrator/workspaces_overlay.py` composes onto this.
SHARED_POOL = 1
SHARED_OVERFLOW = "unlimited"
#: The one identity whose slots delete something on return — the innermost stage's log
#: — and what they delete.
THIS_REPOSITORY = "github.com/nickderobertis/ai-orchestrator"
DELETED_ON_RETURN = '[".logs/"]'

#: One rule of the file: its one-line `match:` flow mapping and the indented fields
#: under it, comments between them included, read from the text the way
#: `tests/test_repository_registration.py` reads the rules file, because the shape
#: asserted is the shape written.
RULE = re.compile(
    r"^  - match: \{host: (?P<host>[^,}]+), owner: (?P<owner>[^,}]+), name: (?P<name>[^,}]+)\}\n"
    r"(?P<fields>(?:^    \S.*\n)*)",
    re.MULTILINE,
)
RULE_FIELD = re.compile(r"^    (?P<key>[a-z_]+): (?P<value>.+)$", re.MULTILINE)
DEFAULT_FIELD = re.compile(r"^default:\n(?P<fields>(?:^  [a-z]+: .+\n)+)", re.MULTILINE)
#: Any line of the document below its header naming the key, however it is indented or
#: nested, so a `maintain` written in a shape the two readers above miss still counts.
MAINTAIN_KEY = re.compile(r"^\s*(?:- )?maintain:", re.MULTILINE)


class Rule(NamedTuple):
    """One rule as the file writes it."""

    identity: RepoIdentity
    fields: dict[str, str]


def rules() -> tuple[Rule, ...]:
    """Every rule of the tracked file, in the order it names them."""
    text = TRACKED_WORKSPACES.read_text(encoding="utf-8")
    read = tuple(
        Rule(
            identity=f"{found['host']}/{found['owner']}/{found['name']}",
            fields={field["key"]: field["value"] for field in RULE_FIELD.finditer(found["fields"])},
        )
        for found in RULE.finditer(text)
    )
    assert len(read) == text.count("- match:"), (
        "config/onevcs.workspaces.yml no longer writes every rule's match as a one-line "
        f"flow mapping, so only {len(read)} of {text.count('- match:')} rules were read back"
    )
    return read


def default() -> dict[str, str]:
    """The `default:` block, as key → value."""
    text = TRACKED_WORKSPACES.read_text(encoding="utf-8")
    found = DEFAULT_FIELD.search(text)
    assert found is not None, "config/onevcs.workspaces.yml declares no `default:` block"
    return {
        key: value
        for key, value in (line.strip().split(": ", 1) for line in found["fields"].splitlines())
    }


def test_the_shared_default_pools_one_slot_and_admits_every_session_past_it() -> None:
    """`pool: 1` and `overflow: unlimited` are the defaults every host starts from.

    One rather than more because the tracked file is the shared floor and a host's
    disk is the host's to size — its overlay raises the number, never this file; an
    unbounded overflow because the pool trades disk wear for speed and never blocks
    work behind it.
    """
    declared = default()

    assert declared.get("pool") == str(SHARED_POOL), declared
    assert declared.get("overflow") == SHARED_OVERFLOW, declared


def test_the_header_sends_a_host_to_its_own_overlay_for_its_pool_size() -> None:
    """The file says where a host sizes its pool, and that it is never here."""
    header = TRACKED_WORKSPACES.read_text(encoding="utf-8").split("\nversion:", 1)[0]

    assert "${XDG_CONFIG_HOME:-$HOME/.config}/ai-orchestrator/workspaces.yml" in header
    assert "A host sets its own pool size there, never by editing this file" in header


def test_this_repository_deletes_the_innermost_stages_log_on_every_return() -> None:
    """An `ai-orchestrator` slot returns without the `.logs/` its last session wrote.

    Every stage that captures output keeps it there, gitignored — so a return that
    cleans untracked files alone would hand the next session a log that reads as its
    own.
    """
    ours = [rule for rule in rules() if rule.identity == THIS_REPOSITORY]

    assert len(ours) == 1, f"the workspaces file names {THIS_REPOSITORY} {len(ours)} times"
    assert ours[0].fields.get("delete") == DELETED_ON_RETURN, ours[0].fields


def test_no_rule_is_empty() -> None:
    """A rule carrying nothing would match an identity and change nothing about it."""
    for rule in rules():
        assert rule.fields, f"{rule.identity}'s rule carries no field at all"


def test_this_host_names_no_maintenance_for_any_repository() -> None:
    """No tracked rule and not `default:` names a `maintain` command.

    Each repository declares its own pool maintenance in its tracked `onevcs.toml`, and
    onevcs runs a host `maintain` only for a repository that declares none. A host command
    here would therefore run, in exactly those repositories, a cleanup none of them
    declared — which is what this host chose not to do.
    """
    named = [rule.identity for rule in rules() if "maintain" in rule.fields]
    assert not named, f"config/onevcs.workspaces.yml maintains {named} with a host command"
    assert "maintain" not in default(), default()
    body = TRACKED_WORKSPACES.read_text(encoding="utf-8").split("\nversion:", 1)[1]
    assert not MAINTAIN_KEY.search(body), "config/onevcs.workspaces.yml names a `maintain`"
