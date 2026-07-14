"""Unit tests for config loading and the base ⊕ persona merge."""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.config import ConfigError, build_effective_config, load_yaml

REPO_ROOT = Path(__file__).parent.parent


def _base() -> dict:
    return {
        "provider": {"kind": "oneharness", "bin": "oneharness", "judge_config": "j.toml"},
        "agent": {"name": "base", "dir": ".", "instructions": "SHARED PREAMBLE"},
        "user": {"done_when": "base done", "max_turns": 12},
        "session": "orchestrated",
        "task": "should be dropped",
    }


def _persona() -> dict:
    return {
        "agent": {"name": "backend", "instructions": "ROLE INSTRUCTIONS"},
        "user": {"persona": "a tech lead", "max_turns": 6},
    }


def test_instructions_are_appended_not_replaced() -> None:
    cfg = build_effective_config(_base(), _persona())
    assert cfg["system_prompt"] == "SHARED PREAMBLE\n\nROLE INSTRUCTIONS"
    # The internal `agent` vocabulary is adapted away; onejudge never sees it.
    assert "agent" not in cfg


def test_base_instructions_require_incremental_commits() -> None:
    cfg = load_yaml(REPO_ROOT / "config" / "onejudge.base.yaml")
    assert "Commit as you go" in cfg["agent"]["instructions"]


def test_persona_overrides_user_keys() -> None:
    cfg = build_effective_config(_base(), _persona())
    assert cfg["user"]["persona"] == "a tech lead"
    assert cfg["user"]["max_turns"] == 6  # persona wins
    assert cfg["user"]["done_when"] == "base done"  # inherited from base


def test_task_is_never_merged() -> None:
    cfg = build_effective_config(_base(), _persona())
    assert "task" not in cfg


def test_cli_overrides_win_over_both() -> None:
    cfg = build_effective_config(
        _base(),
        _persona(),
        session="node-1",
        max_turns=20,
        done_when="cli done",
    )
    assert cfg["session"] == "node-1"
    assert cfg["user"]["max_turns"] == 20
    assert cfg["user"]["done_when"] == "cli done"


def test_persona_without_instructions_keeps_preamble() -> None:
    cfg = build_effective_config(_base(), {"agent": {}, "user": {"persona": "p"}})
    assert cfg["system_prompt"] == "SHARED PREAMBLE"


def test_persona_evals_replace_base() -> None:
    persona = _persona()
    persona["evals"] = [{"criterion": "c", "kind": "boolean"}]
    cfg = build_effective_config(_base(), persona)
    assert cfg["evals"] == [{"criterion": "c", "kind": "boolean"}]


def test_load_yaml_missing_file(tmp_path) -> None:
    with pytest.raises(ConfigError, match="cannot read"):
        load_yaml(tmp_path / "nope.yaml")


def test_load_yaml_non_mapping(tmp_path) -> None:
    p = tmp_path / "list.yaml"
    p.write_text("- a\n- b\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="must be a YAML mapping"):
        load_yaml(p)


def test_load_yaml_invalid(tmp_path) -> None:
    p = tmp_path / "bad.yaml"
    p.write_text("key: [unclosed\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="invalid YAML"):
        load_yaml(p)


def test_load_yaml_empty_is_empty_mapping(tmp_path) -> None:
    p = tmp_path / "empty.yaml"
    p.write_text("", encoding="utf-8")
    assert load_yaml(p) == {}
