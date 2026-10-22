import pytest

from data_migration_concept.config import ConfigError, load_project


def test_example_project_loads(example_project):
    project = load_project(example_project)
    obj = project.objects["supplier"]
    assert project.config.working_connection == "working"
    assert [s.name for s in obj.config.structures] == ["general"]
    assert len(obj.mappings["general"].fields) == 6


def test_missing_project_file(tmp_path):
    with pytest.raises(ConfigError, match="project.yaml: file not found"):
        load_project(tmp_path)


def test_missing_xref_and_source_file_are_reported_together(project_copy):
    obj_dir = project_copy / "objects" / "supplier"
    (obj_dir / "xref" / "country.csv").unlink()
    (obj_dir / "data" / "lfa1.csv").unlink()
    with pytest.raises(ConfigError) as exc:
        load_project(project_copy)
    text = str(exc.value)
    assert "file 'data/lfa1.csv' not found" in text
    assert "xref file 'xref/country.csv' not found" in text
    assert len(exc.value.errors) == 2


def test_unknown_source_and_connection_are_reported(project_copy):
    obj_file = project_copy / "objects" / "supplier" / "object.yaml"
    content = obj_file.read_text(encoding="utf-8")
    content = content.replace("source: lfa1", "source: nope")
    content = content.replace(
        "sources:\n",
        "sources:\n  - kind: sql\n    name: legacy\n    connection: ghost\n    table: t\n",
    )
    obj_file.write_text(content, encoding="utf-8")
    with pytest.raises(ConfigError) as exc:
        load_project(project_copy)
    text = str(exc.value)
    assert "unknown source 'nope'" in text
    assert "unknown connection 'ghost'" in text


def test_schema_errors_name_file_and_field(project_copy):
    mapping = project_copy / "objects" / "supplier" / "mappings" / "general.yaml"
    mapping.write_text(
        "fields:\n  - target_field: A\n    rule: { type: MAGIC }\n", encoding="utf-8"
    )
    with pytest.raises(ConfigError) as exc:
        load_project(project_copy)
    assert "mappings" in str(exc.value) and "general.yaml" in str(exc.value)


def test_invalid_yaml_is_reported(project_copy):
    (project_copy / "project.yaml").write_text("name: [unclosed\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="invalid YAML"):
        load_project(project_copy)
