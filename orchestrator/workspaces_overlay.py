"""Compose the workspaces file a host installs from the tracked default and its own overlay.

`config/onevcs.workspaces.yml` sizes each identity's pool of warm worktree slots, and
the pool size is the one thing about it that is the host's rather than the repository's:
each host can afford a different amount of concurrent disk, and a tracked file edited
per host would be a per-host fork of one source. So the tracked file is the shared
default and never the last word. A host says what differs in a file **outside every
checkout** — ``${XDG_CONFIG_HOME:-$HOME/.config}/ai-orchestrator/workspaces.yml``, the
directory `claude-identities.env` lives in for the same reason — of the same schema and
version, partial: its ``default`` keys replace the tracked ``default``'s key by key, and
each of its ``rules`` entries replaces the tracked rule with an identical ``match`` or
is appended. `scripts/apply-repo-registry.sh` runs this before it validates and installs,
so what `onevcs` validates and what lands at ``$ONEVCS_HOME/workspaces.yml`` is the
composed document; with no host file the tracked file is installed byte for byte.

The host file is an external record, read at this boundary: a document that is not a
mapping, names a version other than the tracked one, or carries a key or a rule shape
the schema does not have is refused by name, before anything is composed. What the
composed document *means* — a ``pool: 0`` beside an ``overflow: 0``, a ``delete`` path
climbing out of the worktree — is `onevcs`'s to refuse, and the recipe asks it.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Final

import yaml

__all__ = ["HOST_FILE", "HOST_FILE_DIRECTORY", "OverlayRefused", "compose", "main"]

#: Where a host's overlay lives under its XDG configuration home, beside the Claude
#: identities file: the same directory, for the same reason — host state no checkout
#: should carry.
HOST_FILE_DIRECTORY: Final = "ai-orchestrator"
HOST_FILE: Final = "workspaces.yml"

#: The keys a workspaces document may carry at its top, and the keys a rule may carry
#: beside its `match` (`default` takes the same minus `match`), as `onevcs` declares the
#: shape: `WorkspacesFile`, `WorkspaceRule` and `WorkspaceDefault` in its `workspaces.rs`,
#: which `tests/test_engine_contracts.py` holds these two sets to at the adopted release.
TOP_LEVEL_KEYS: Final = frozenset({"version", "default", "rules"})
RULE_KEYS: Final = frozenset({"match", "pool", "overflow", "delete", "maintain"})


class OverlayRefused(ValueError):
    """The host file cannot be composed over the tracked one, and the message says why."""


def _mapping(value: object, what: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise OverlayRefused(f"{what} must be a mapping, not {type(value).__name__}")
    return {str(key): entry for key, entry in value.items()}


def _document(text: str, what: str) -> dict[str, object]:
    """One workspaces document as a mapping, or a refusal naming what is wrong with it."""
    try:
        loaded = yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise OverlayRefused(f"{what} is not valid YAML: {error}") from error
    document = _mapping(loaded, what)
    unknown = sorted(set(document) - TOP_LEVEL_KEYS)
    if unknown:
        raise OverlayRefused(
            f"{what} carries {unknown}, which a workspaces file has no key for; "
            f"it takes {sorted(TOP_LEVEL_KEYS)}"
        )
    return document


def _rules(document: dict[str, object], what: str) -> list[dict[str, object]]:
    """The `rules` list of a document, each entry checked to the shape a rule has."""
    listed = document.get("rules", [])
    if listed is None:
        listed = []
    if not isinstance(listed, list):
        raise OverlayRefused(f"{what}'s `rules` must be a list, not {type(listed).__name__}")
    rules: list[dict[str, object]] = []
    for index, entry in enumerate(listed, start=1):
        rule = _mapping(entry, f"{what}'s rule {index}")
        if "match" not in rule:
            raise OverlayRefused(f"{what}'s rule {index} names no `match`")
        unknown = sorted(set(rule) - RULE_KEYS)
        if unknown:
            raise OverlayRefused(
                f"{what}'s rule {index} carries {unknown}, which a rule has no key for"
            )
        rules.append(rule)
    return rules


def compose(tracked_text: str, host_text: str) -> dict[str, object]:
    """The tracked document with the host's overlay applied, as the mapping to install.

    ``default`` is merged key by key, the host's winning; each host rule replaces the
    tracked rule whose ``match`` is identical and is appended otherwise, keeping the
    tracked order so first-match-wins reads the same for every rule the host did not
    touch. The host file's ``version`` has to be the tracked one: a host written to a
    later schema would compose keys the installed `onevcs` cannot read.
    """
    tracked = _document(tracked_text, "the tracked workspaces file")
    host = _document(host_text, "the host workspaces file")
    if host.get("version", tracked.get("version")) != tracked.get("version"):
        raise OverlayRefused(
            f"the host workspaces file declares version {host['version']!r}, and the "
            f"tracked one {tracked.get('version')!r}; both have to be one schema"
        )
    default = {
        **_mapping(tracked.get("default", {}), "the tracked workspaces file's `default`"),
        **_mapping(host.get("default", {}), "the host workspaces file's `default`"),
    }
    unknown = sorted(set(default) - (RULE_KEYS - {"match"}))
    if unknown:
        raise OverlayRefused(f"`default` carries {unknown}, which it has no key for")
    rules = _rules(tracked, "the tracked workspaces file")
    for overlay in _rules(host, "the host workspaces file"):
        replaced = [index for index, rule in enumerate(rules) if rule["match"] == overlay["match"]]
        if replaced:
            rules[replaced[0]] = overlay
        else:
            rules.append(overlay)
    composed: dict[str, object] = {"version": tracked.get("version"), "default": default}
    composed["rules"] = rules
    return composed


def main(argv: list[str] | None = None) -> int:
    """`orchestrator-workspaces-overlay TRACKED HOST`: print the composed document.

    Exit 0 with the YAML on stdout; 2 with the refusal on stderr, naming the file and
    the key at fault, so the recipe stops before it validates or installs anything.
    """
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) != 2:
        print("usage: orchestrator-workspaces-overlay TRACKED_FILE HOST_FILE", file=sys.stderr)
        return 2
    tracked, host = (Path(argument) for argument in arguments)
    try:
        composed = compose(tracked.read_text(encoding="utf-8"), host.read_text(encoding="utf-8"))
    except OSError as error:
        print(
            f"workspaces-overlay: cannot read {error.filename}: {error.strerror}", file=sys.stderr
        )
        return 2
    except OverlayRefused as error:
        print(f"workspaces-overlay: {error}", file=sys.stderr)
        return 2
    sys.stdout.write(
        "# Composed by `just repos-apply` from the tracked config/onevcs.workspaces.yml\n"
        f"# overlaid by {host}; edit one of those, never this file.\n"
    )
    sys.stdout.write(yaml.safe_dump(composed, sort_keys=False, default_flow_style=None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
