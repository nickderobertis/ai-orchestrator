"""Load and merge onejudge configs: base ⊕ persona → one effective config.

The base (`config/onejudge.base.yaml`) carries settings common to every subtask;
a persona (`personas/<name>.yaml`) carries only the role-specific delta. Merging
them produces the effective onejudge config that `dispatch` runs. The task itself
is never merged in — it is passed to onejudge over the CLI (`--task`).
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml


class ConfigError(Exception):
    """A config file is missing, unreadable, or not a YAML mapping."""


def load_yaml(path: str | Path) -> dict[str, Any]:
    """Load a YAML file, requiring a top-level mapping.

    Raises ConfigError (not a raw YAML/OS error) so callers get one actionable
    failure type at this trust boundary.
    """
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read config {p}: {exc}") from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {p}: {exc}") from exc
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ConfigError(f"{p} must be a YAML mapping, got {type(data).__name__}")
    return data


def build_effective_config(
    base: dict[str, Any],
    persona: dict[str, Any],
    *,
    session: str | None = None,
    project_dir: str | None = None,
    max_turns: int | None = None,
    done_when: str | None = None,
) -> dict[str, Any]:
    """Merge a persona delta over the base into an effective onejudge config.

    Merge rules:
      * `agent.instructions` — the persona's role instructions are appended after
        the base's shared preamble (both kept), not replaced.
      * `agent.name` / `agent.dir` — persona (or `project_dir`) overrides base.
      * `user` — persona keys override base keys (so a persona brings its required
        `persona` and may override `done_when` / `max_turns`).
      * `evals` — persona replaces base if present.
      * `session` — the `session` argument overrides the base default.
      * `task` — deliberately dropped; it is passed over the CLI, never merged.

    CLI overrides (`session`, `project_dir`, `max_turns`, `done_when`) win over
    both files.
    """
    cfg = copy.deepcopy(base)
    cfg.pop("task", None)

    agent = cfg.setdefault("agent", {})
    persona_agent = persona.get("agent", {}) or {}
    preamble = str(agent.get("instructions", "")).rstrip()
    role = str(persona_agent.get("instructions", "")).strip()
    if preamble and role:
        agent["instructions"] = f"{preamble}\n\n{role}"
    elif role:
        agent["instructions"] = role
    if persona_agent.get("name"):
        agent["name"] = persona_agent["name"]
    if project_dir is not None:
        agent["dir"] = project_dir

    user = cfg.setdefault("user", {})
    user.update(persona.get("user", {}) or {})
    if max_turns is not None:
        user["max_turns"] = max_turns
    if done_when is not None:
        user["done_when"] = done_when

    if "evals" in persona:
        cfg["evals"] = persona["evals"]
    if session is not None:
        cfg["session"] = session

    return cfg
