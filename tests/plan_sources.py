"""The plan sources `onetaskgraph.yaml` configures, and the roots they claim.

Read with a reader for this document's own shape rather than with a YAML parser,
because this repository installs none: the configuration is one flat `sources:`
mapping of two-space-indented names, each carrying a `plugin` and a `config` block.
The reader refuses a document it cannot account for — a source with no plugin, a name
`default_sources` claims that no `sources:` entry defines — so a restructured file
fails here rather than silently reporting no sources and passing every check built on
it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath

#: A source's name, its `plugin`, and one `config` setting, at the three indents this
#: document spells them at. Anchored so a deeper key of some future nested block
#: cannot be read as one of these.
_SOURCE = re.compile(r"^  ([A-Za-z0-9_.-]+):$")
_PLUGIN = re.compile(r"^    plugin:\s*(\S+)\s*$")
_CONFIG = re.compile(r"^    config:$")
_SETTING = re.compile(r"^      ([A-Za-z0-9_.-]+):\s*(.+?)\s*$")
_DEFAULTS = re.compile(r"^default_sources:\s*\[(.*)\]\s*$", re.MULTILINE)


@dataclass(frozen=True)
class PlanSource:
    """One configured source: what plugin serves it and what its `config` block says."""

    name: str
    plugin: str
    settings: dict[str, str]

    @property
    def root(self) -> str | None:
        """The directory this source stores its records under, when it has one."""
        return self.settings.get("root")


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def read_plan_sources(text: str) -> dict[str, PlanSource]:
    """Every source the document defines, by name."""
    sources: dict[str, PlanSource] = {}
    name: str | None = None
    plugin: str | None = None
    settings: dict[str, str] = {}
    in_config = False

    def _close() -> None:
        if name is None:
            return
        if plugin is None:
            raise ValueError(f"onetaskgraph.yaml's source {name!r} names no plugin")
        sources[name] = PlanSource(name=name, plugin=plugin, settings=dict(settings))

    for line in text.splitlines():
        opened = _SOURCE.match(line)
        if opened is not None:
            _close()
            name, plugin, settings, in_config = opened.group(1), None, {}, False
            continue
        if name is None:
            continue
        named_plugin = _PLUGIN.match(line)
        if named_plugin is not None:
            plugin, in_config = named_plugin.group(1), False
            continue
        if _CONFIG.match(line) is not None:
            in_config = True
            continue
        setting = _SETTING.match(line)
        if in_config and setting is not None:
            settings[setting.group(1)] = _unquote(setting.group(2))
            continue
        if not line.startswith("    "):
            _close()
            name, plugin, settings, in_config = None, None, {}, False
    _close()
    return sources


def read_default_sources(text: str) -> list[str]:
    """The sources that answer when a command names none."""
    named = _DEFAULTS.search(text)
    if named is None:
        raise ValueError("onetaskgraph.yaml names no `default_sources`")
    return [entry.strip() for entry in named.group(1).split(",") if entry.strip()]


def _normalized(root: str) -> str:
    """One spelling per directory, so `.plans`, `./.plans` and `.plans/` compare equal."""
    return str(PurePosixPath(root))


def shared_roots(sources: dict[str, PlanSource]) -> dict[str, list[str]]:
    """Each root more than one source claims, with the sources claiming it.

    Sharing a root is what nothing else catches: two `local-md` sources over one
    directory each answer with every project in it, so a listing across the default
    sources reports each plan once per source and neither copy is wrong.
    """
    claimed: dict[str, list[str]] = {}
    for source in sources.values():
        if source.root is not None:
            claimed.setdefault(_normalized(source.root), []).append(source.name)
    return {root: names for root, names in claimed.items() if len(names) > 1}
