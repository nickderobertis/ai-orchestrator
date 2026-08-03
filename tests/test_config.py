"""Unit tests for config loading and the base ⊕ persona merge."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from orchestrator.config import ConfigError, build_effective_config, load_mapping, load_yaml

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


def test_run_only_instructions_are_appended_once() -> None:
    cfg = build_effective_config(_base(), _persona(), extra_instructions="CI IS AUTHORITATIVE")
    assert cfg["system_prompt"] == ("SHARED PREAMBLE\n\nROLE INSTRUCTIONS\n\nCI IS AUTHORITATIVE")
    assert cfg["system_prompt"].count("CI IS AUTHORITATIVE") == 1


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


def test_load_mapping_merges_json_surrogate_pairs(tmp_path) -> None:
    """A `.json` ledger file is read with JSON escape semantics, not YAML's."""
    written = tmp_path / "details.json"
    # Exactly what `json.dump` (ensure_ascii=True, the default every ledger writer
    # here uses) emits for one emoji, so this fixture is the real on-disk shape.
    assert json.dumps({"detail": "diff 😀 done"}) == '{"detail": "diff \\ud83d\\ude00 done"}'
    written.write_text('{"detail": "diff \\ud83d\\ude00 done"}', encoding="utf-8")

    # PyYAML — what this used to be read with — leaves that pair as two lone
    # surrogates instead, which is the whole reason the dispatch exists.
    assert load_mapping(written) == {"detail": "diff 😀 done"}


def test_load_mapping_keeps_yaml_semantics_for_yaml(tmp_path) -> None:
    p = tmp_path / "persona.yaml"
    p.write_text("agent:\n  name: backend\n", encoding="utf-8")
    assert load_mapping(p) == {"agent": {"name": "backend"}}


def test_load_mapping_rejects_a_non_mapping_json_document(tmp_path) -> None:
    p = tmp_path / "list.json"
    p.write_text('["a", "b"]', encoding="utf-8")
    with pytest.raises(ConfigError, match="must be a JSON mapping"):
        load_mapping(p)


def test_load_mapping_falls_back_for_a_json_file_json_cannot_parse(tmp_path) -> None:
    """A ledger file JSON rejects stays exactly as readable as it was before."""
    truncated = tmp_path / "status.json"
    truncated.write_text("", encoding="utf-8")
    assert load_mapping(truncated) == {}

    unparseable = tmp_path / "torn.json"
    unparseable.write_text('{"status": "runn', encoding="utf-8")
    with pytest.raises(ConfigError, match="invalid YAML"):
        load_mapping(unparseable)
