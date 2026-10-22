import pytest
from pydantic import ValidationError

from data_migration_concept.config.models import (
    MappingFile,
    ObjectConfig,
    ProjectConfig,
    SqlSource,
    StructureConfig,
    XrefRule,
)


def test_project_requires_defined_working_connection():
    with pytest.raises(ValidationError, match="working_connection"):
        ProjectConfig(name="p", working_connection="x", connections={"a": {"url_env": "A"}})


def test_unknown_keys_are_rejected():
    with pytest.raises(ValidationError):
        ProjectConfig(name="p", working_connection="a", connections={"a": {"url_env": "A"}}, typo=1)


def test_xref_default_policy_needs_default_value():
    with pytest.raises(ValidationError, match="default"):
        XrefRule(type="XREF", source_field="A", xref="x", on_unmapped="default")
    assert XrefRule(type="XREF", source_field="A", xref="x", on_unmapped="default", default="?")


def test_mapping_rejects_duplicate_targets_and_unknown_rule_types():
    move = {"type": "MOVE", "source_field": "A"}
    with pytest.raises(ValidationError, match="duplicate target_field"):
        MappingFile(
            fields=[{"target_field": "T", "rule": move}, {"target_field": "T", "rule": move}]
        )
    with pytest.raises(ValidationError):
        MappingFile(fields=[{"target_field": "T", "rule": {"type": "MAGIC"}}])


def test_concat_needs_two_fields_and_substring_start_is_one_based():
    with pytest.raises(ValidationError):
        MappingFile(
            fields=[{"target_field": "T", "rule": {"type": "CONCAT", "source_fields": ["A"]}}]
        )
    with pytest.raises(ValidationError):
        MappingFile(
            fields=[
                {
                    "target_field": "T",
                    "rule": {"type": "SUBSTRING", "source_field": "A", "start": 0, "length": 2},
                }
            ]
        )


def test_sql_source_needs_exactly_one_of_table_or_query():
    with pytest.raises(ValidationError):
        SqlSource(kind="sql", name="s", connection="c")
    with pytest.raises(ValidationError):
        SqlSource(kind="sql", name="s", connection="c", table="t", query="select 1")


def test_structure_needs_exactly_one_of_mapping_or_custom_sql():
    with pytest.raises(ValidationError):
        StructureConfig(name="s", target_table="t", source="src")
    with pytest.raises(ValidationError):
        StructureConfig(
            name="s", target_table="t", source="src", mapping="m.yaml", custom_sql="a.sql"
        )


def test_object_rejects_duplicate_target_tables():
    st = {"target_table": "t", "source": "f", "mapping": "m.yaml"}
    with pytest.raises(ValidationError, match="duplicate target_table"):
        ObjectConfig(
            name="o",
            sources=[{"kind": "file", "name": "f", "path": "a.csv", "format": "csv"}],
            structures=[{"name": "a", **st}, {"name": "b", **st}],
        )
