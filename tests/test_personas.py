"""Unit tests for persona validation and scaffolding."""

from __future__ import annotations

import pytest

from orchestrator import BASE_CONFIG, PERSONA_DIR
from orchestrator.personas import (
    main_new,
    main_validate,
    new_persona,
    persona_files,
    persona_name,
    persona_path,
    validate_all,
    validate_persona,
)


def test_shipped_personas_all_valid() -> None:
    results = validate_all(PERSONA_DIR, BASE_CONFIG)
    assert results, "expected at least one persona"
    bad = {name: errs for name, errs in results.items() if errs}
    assert not bad, f"shipped personas should validate: {bad}"


def test_template_is_excluded() -> None:
    names = {p.stem for p in persona_files(PERSONA_DIR)}
    assert "_template" not in names
    assert "planner" in names


@pytest.mark.reads_docs
def test_readme_catalog_matches_discovered_personas() -> None:
    readme = (PERSONA_DIR / "README.md").read_text(encoding="utf-8")
    catalog = readme.split("## Catalog\n", 1)[1].split("\n## ", 1)[0]
    documented = {line.split("`", 2)[1] for line in catalog.splitlines() if line.startswith("| `")}
    discovered = {persona_name(path, PERSONA_DIR) for path in persona_files(PERSONA_DIR)}

    assert not discovered - documented, (
        f"personas missing from README catalog: {sorted(discovered - documented)}"
    )
    assert not documented - discovered, (
        f"README catalog entries without persona files: {sorted(documented - discovered)}"
    )


def test_persona_files_discovers_subdirectories_and_skips_private_paths(tmp_path) -> None:
    (tmp_path / "repo").mkdir()
    (tmp_path / "repo" / "specialist.yaml").touch()
    (tmp_path / "repo" / "_draft.yaml").touch()
    (tmp_path / "_private").mkdir()
    (tmp_path / "_private" / "hidden.yaml").touch()

    assert persona_files(tmp_path) == [tmp_path / "repo" / "specialist.yaml"]


def test_persona_files_skips_symlink_that_escapes_catalog(tmp_path) -> None:
    persona_dir = tmp_path / "personas"
    persona_dir.mkdir()
    external = tmp_path / "external.yaml"
    external.touch()
    (persona_dir / "escaped.yaml").symlink_to(external)

    assert persona_files(persona_dir) == []


def test_valid_persona_has_no_errors() -> None:
    data = {"agent": {"instructions": "do x"}, "user": {"persona": "review x"}}
    assert validate_persona(data) == []


def test_missing_required_fields() -> None:
    errors = validate_persona({"agent": {}, "user": {}})
    assert any("agent.instructions" in e for e in errors)
    assert any("user.persona" in e for e in errors)


def test_unknown_top_level_key_rejected() -> None:
    data = {"agent": {"instructions": "x"}, "user": {"persona": "p"}, "task": "nope"}
    errors = validate_persona(data)
    assert any("unknown top-level key" in e for e in errors)


def test_non_mapping_agent_and_user_rejected() -> None:
    errors = validate_persona({"agent": "x", "user": "y"})
    assert any("missing 'agent'" in e for e in errors)
    assert any("missing 'user'" in e for e in errors)


def test_done_when_must_be_string() -> None:
    data = {"agent": {"instructions": "x"}, "user": {"persona": "p", "done_when": 5}}
    assert any("done_when" in e for e in validate_persona(data))


def test_unknown_nested_keys_rejected() -> None:
    data = {"agent": {"instructions": "x", "dir": "."}, "user": {"persona": "p", "foo": 1}}
    errors = validate_persona(data)
    assert any("unknown agent key" in e for e in errors)
    assert any("unknown user key" in e for e in errors)


@pytest.mark.parametrize("bad", [0, -1, True, "6", 1.5])
def test_bad_max_turns_rejected(bad: object) -> None:
    data = {"agent": {"instructions": "x"}, "user": {"persona": "p", "max_turns": bad}}
    assert any("max_turns" in e for e in validate_persona(data))


def test_evals_must_be_list() -> None:
    data = {"agent": {"instructions": "x"}, "user": {"persona": "p"}, "evals": {"a": 1}}
    assert any("evals" in e for e in validate_persona(data))


def test_validate_all_surfaces_bad_base(tmp_path) -> None:
    base = tmp_path / "base.yaml"
    base.write_text("agent:\n  name: x\n", encoding="utf-8")  # no user.done_when/max_turns
    persona_dir = tmp_path / "personas"
    persona_dir.mkdir()
    (persona_dir / "p.yaml").write_text(
        "agent:\n  instructions: x\nuser:\n  persona: p\n", encoding="utf-8"
    )
    results = validate_all(persona_dir, base)
    assert any("missing user.done_when" in e for errs in results.values() for e in errs)


def test_new_persona_scaffolds_from_template(tmp_path) -> None:
    target = new_persona("my-role", persona_dir=tmp_path)
    assert target.exists()
    assert "agent" in target.read_text(encoding="utf-8")


def test_new_persona_scaffolds_in_subdirectory(tmp_path) -> None:
    target = new_persona("repo/my-role", persona_dir=tmp_path)
    assert target == (tmp_path / "repo" / "my-role.yaml").resolve()
    assert target.is_file()


def test_validate_all_uses_subdir_qualified_names(tmp_path) -> None:
    new_persona("repo/my-role", persona_dir=tmp_path)
    results = validate_all(tmp_path, BASE_CONFIG)
    assert list(results) == ["repo/my-role"]
    assert not results["repo/my-role"]


@pytest.mark.parametrize(
    "name", ["../escape", "repo/../escape", "/absolute", "repo//name", "repo\\name"]
)
def test_persona_path_rejects_traversal_and_malformed_names(tmp_path, name) -> None:
    with pytest.raises(ValueError, match="invalid persona name"):
        persona_path(name, tmp_path)


def test_new_persona_rejects_bad_name(tmp_path) -> None:
    with pytest.raises(ValueError, match="invalid persona name"):
        new_persona("Bad Name", persona_dir=tmp_path)


def test_new_persona_refuses_overwrite(tmp_path) -> None:
    new_persona("dup", persona_dir=tmp_path)
    with pytest.raises(FileExistsError):
        new_persona("dup", persona_dir=tmp_path)
    # force overwrites
    assert new_persona("dup", persona_dir=tmp_path, force=True).exists()


def test_main_new_and_validate_clis(tmp_path, capsys) -> None:
    rc = main_new(["repo/role-a", "--persona-dir", str(tmp_path)])
    assert rc == 0
    # A freshly scaffolded persona still has template placeholders but valid shape.
    rc = main_validate(["--persona-dir", str(tmp_path), "--base", str(BASE_CONFIG)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "OK" in out


def test_main_validate_fails_on_bad_persona(tmp_path, capsys) -> None:
    (tmp_path / "broken.yaml").write_text("agent: {}\nuser: {}\n", encoding="utf-8")
    rc = main_validate(["--persona-dir", str(tmp_path), "--base", str(BASE_CONFIG)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "broken" in err


def test_main_new_rejects_bad_name(tmp_path, capsys) -> None:
    rc = main_new(["Bad Name", "--persona-dir", str(tmp_path)])
    assert rc == 1
    assert "invalid persona name" in capsys.readouterr().err


def test_main_new_reports_os_error(tmp_path, capsys) -> None:
    not_a_directory = tmp_path / "file"
    not_a_directory.touch()
    rc = main_new(["role", "--persona-dir", str(not_a_directory)])
    assert rc == 1
    assert "new-persona:" in capsys.readouterr().err


def test_validate_all_reports_unreadable_base(tmp_path) -> None:
    results = validate_all(tmp_path, tmp_path / "no-base.yaml")
    assert "<base>" in results and results["<base>"]


def test_validate_all_reports_unparseable_persona(tmp_path) -> None:
    (tmp_path / "bad.yaml").write_text("key: [unclosed\n", encoding="utf-8")
    results = validate_all(tmp_path, BASE_CONFIG)
    assert results["bad"], "an unparseable persona should surface an error"
