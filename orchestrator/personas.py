"""Persona authoring and validation.

A persona is a delta over the base config (see `personas/README.md`). This module
scaffolds a new persona from the template and validates that every persona file
matches the delta contract — the deterministic, offline gate wired into
`just check`. It never calls onejudge.
"""

# llmlint: ignore-file[modern_domain_modeling] this module validates open-ended
# persona/config YAML as dicts at the trust boundary (the same convention as
# config.py / history.py); a typed model would presuppose the very structure the
# validation exists to check.

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path
from typing import Any

from . import BASE_CONFIG, PERSONA_DIR, TEMPLATE
from .config import ConfigError, build_effective_config, load_yaml

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")

# The persona delta contract. Anything outside this is a hard error: task comes
# over the CLI, and provider / session / the shared preamble come from the base.
ALLOWED_TOP = {"agent", "user", "evals"}
ALLOWED_AGENT = {"name", "instructions"}
ALLOWED_USER = {"persona", "done_when", "max_turns"}


def persona_files(persona_dir: Path) -> list[Path]:
    """Return recursive persona files, excluding underscore-prefixed paths."""
    return sorted(
        path
        for path in persona_dir.rglob("*.yaml")
        if not any(part.startswith("_") for part in path.relative_to(persona_dir).parts)
    )


def persona_path(name: str, persona_dir: Path = PERSONA_DIR) -> Path:
    """Resolve a slash-qualified persona name safely beneath ``persona_dir``."""
    parts = name.split("/")
    if not name or any(not NAME_RE.fullmatch(part) for part in parts):
        raise ValueError(
            f"invalid persona name {name!r}: use slash-separated lowercase letters, "
            "digits, and hyphens"
        )
    root = persona_dir.resolve()
    target = (persona_dir.joinpath(*parts).with_suffix(".yaml")).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"invalid persona name {name!r}: path escapes persona directory") from exc
    return target


def persona_name(path: Path, persona_dir: Path) -> str:
    """Return the slash-qualified catalog name for a discovered persona path."""
    return path.relative_to(persona_dir).with_suffix("").as_posix()


def validate_persona(data: dict[str, Any]) -> list[str]:
    """Validate one loaded persona mapping; return a list of error messages."""
    errors: list[str] = []

    unknown_top = sorted(set(data) - ALLOWED_TOP)
    if unknown_top:
        errors.append(
            f"unknown top-level key(s): {', '.join(unknown_top)} "
            f"(allowed: {', '.join(sorted(ALLOWED_TOP))})"
        )

    agent = data.get("agent")
    if not isinstance(agent, dict):
        errors.append("missing 'agent' mapping")
    else:
        unknown = sorted(set(agent) - ALLOWED_AGENT)
        if unknown:
            errors.append(f"unknown agent key(s): {', '.join(unknown)}")
        instr = agent.get("instructions")
        if not isinstance(instr, str) or not instr.strip():
            errors.append("agent.instructions is required and must be a non-empty string")

    user = data.get("user")
    if not isinstance(user, dict):
        errors.append("missing 'user' mapping")
    else:
        unknown = sorted(set(user) - ALLOWED_USER)
        if unknown:
            errors.append(f"unknown user key(s): {', '.join(unknown)}")
        persona = user.get("persona")
        if not isinstance(persona, str) or not persona.strip():
            errors.append("user.persona is required and must be a non-empty string")
        if "done_when" in user and not isinstance(user["done_when"], str):
            errors.append("user.done_when must be a string")
        if "max_turns" in user:
            mt = user["max_turns"]
            if not isinstance(mt, int) or isinstance(mt, bool) or mt <= 0:
                errors.append("user.max_turns must be a positive integer")

    if "evals" in data and not isinstance(data["evals"], list):
        errors.append("evals must be a list")

    return errors


def validate_all(persona_dir: Path, base_path: Path) -> dict[str, list[str]]:
    """Validate every persona and confirm each merges to a complete config.

    Returns a mapping of persona name to its (possibly empty) error list. A base
    that is missing a shared default (e.g. `user.done_when`) surfaces here, since
    it would leave every merged config incomplete.
    """
    try:
        base = load_yaml(base_path)
    except ConfigError as exc:
        return {"<base>": [str(exc)]}

    results: dict[str, list[str]] = {}
    for path in persona_files(persona_dir):
        try:
            data = load_yaml(path)
        except ConfigError as exc:
            results[persona_name(path, persona_dir)] = [str(exc)]
            continue
        errors = validate_persona(data)
        if not errors:
            merged = build_effective_config(base, data)
            if not merged.get("system_prompt"):
                errors.append(
                    "merged config is missing system_prompt (check the base config's "
                    "agent.instructions preamble)"
                )
            for field in ("user.persona", "user.done_when", "user.max_turns"):
                key = field.split(".")[1]
                if not merged.get("user", {}).get(key):
                    errors.append(f"merged config is missing {field} (check the base config)")
        results[persona_name(path, persona_dir)] = errors
    return results


def new_persona(name: str, *, persona_dir: Path = PERSONA_DIR, force: bool = False) -> Path:
    """Scaffold a slash-qualified persona from the template; return its path."""
    target = persona_path(name, persona_dir)
    if target.exists() and not force:
        raise FileExistsError(f"{target} already exists (pass --force to overwrite)")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(TEMPLATE, target)
    return target


def main_new(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scaffold a new persona from the template.")
    parser.add_argument("name", help="persona name (slash-separated lowercase segments)")
    parser.add_argument("--force", action="store_true", help="overwrite an existing persona")
    parser.add_argument("--persona-dir", type=Path, default=PERSONA_DIR)
    args = parser.parse_args(argv)
    try:
        target = new_persona(args.name, persona_dir=args.persona_dir, force=args.force)
    except (ValueError, FileExistsError, OSError) as exc:
        print(f"new-persona: {exc}", file=sys.stderr)
        return 1
    print(
        f"Created {target} — fill in agent.instructions and user.persona, "
        "then run 'just validate-personas'."
    )
    return 0


def main_validate(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate every persona against the delta contract."
    )
    parser.add_argument("--persona-dir", type=Path, default=PERSONA_DIR)
    parser.add_argument("--base", type=Path, default=BASE_CONFIG)
    args = parser.parse_args(argv)

    results = validate_all(args.persona_dir, args.base)
    invalid = {name: errs for name, errs in results.items() if errs}
    if invalid:
        for name, errs in invalid.items():
            for err in errs:
                print(f"{name}: {err}", file=sys.stderr)
        print(f"validate-personas: {len(invalid)} persona(s) invalid", file=sys.stderr)
        return 1
    print(f"validate-personas: {len(results)} persona(s) OK")
    return 0
