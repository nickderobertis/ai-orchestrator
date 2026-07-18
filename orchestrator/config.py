"""Load and merge onejudge configs: base ⊕ persona → one effective config.

The base (`config/onejudge.base.yaml`) carries settings common to every subtask;
a persona (`personas/<name>.yaml` or `personas/<repo>/<name>.yaml`) carries only
the role-specific delta. Merging
them produces the effective onejudge config that `dispatch` runs. The task itself
is never merged in — it is passed to onejudge over the CLI (`--task`).
"""

# llmlint: ignore-file[modern_domain_modeling] onejudge configs are open-ended YAML
# merged and re-serialized as dicts (matching history.py / status.py convention); a
# typed model would fight the arbitrary base⊕persona merge this layer exists to do.

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
    max_turns: int | None = None,
    done_when: str | None = None,
    extra_instructions: str | None = None,
) -> dict[str, Any]:
    """Merge a persona delta over the base into an effective onejudge config.

    Personas author in ai-orchestrator's own stable vocabulary — the role goes in
    `agent.instructions` — and this adapter translates that to onejudge's wire
    schema: it emits a single `system_prompt` (the base's shared preamble first,
    then the persona's role) and drops the internal `agent` block. Keeping the
    persona vocabulary decoupled from onejudge's field names is deliberate: a schema
    change on onejudge's side (e.g. its `agent` → `skill`/`system_prompt` migration)
    is absorbed here, in one place, instead of rippling through every persona file.

    Merge rules:
      * `system_prompt` — the base's shared preamble (`agent.instructions`) with the
        persona's role instructions appended after it (both kept), not replaced.
      * `user` — persona keys override base keys (so a persona brings its required
        `persona` and may override `done_when` / `max_turns`).
      * `evals` — persona replaces base if present.
      * `session` — the `session` argument overrides the base default.
      * `task` — deliberately dropped; it is passed over the CLI, never merged.

    CLI overrides (`session`, `max_turns`, `done_when`) win over both files. The run
    directory is selected by `dispatch` (onejudge runs the agent in its own cwd), so
    it is not part of the config.
    """
    cfg = copy.deepcopy(base)
    cfg.pop("task", None)

    base_agent = cfg.pop("agent", {}) or {}
    persona_agent = persona.get("agent", {}) or {}
    preamble = str(base_agent.get("instructions", "")).rstrip()
    role = str(persona_agent.get("instructions", "")).strip()
    parts = [part for part in (preamble, role, (extra_instructions or "").strip()) if part]
    combined = "\n\n".join(parts)
    if combined:
        cfg["system_prompt"] = combined

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
