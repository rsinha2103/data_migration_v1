"""A structure filled from several source tables (joins)."""

from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from data_migration_concept.config import ConfigError, load_project
from data_migration_concept.extract import ExtractError, extract_object
from data_migration_concept.recon import run_recons
from data_migration_concept.runlog import RunLog
from data_migration_concept.transform import TransformError, generate_object, transform_object

GOLDEN = Path(__file__).parent / "golden"
ADDR = (
    "LIFNR,STRAS,ORT01,LAND\n"
    "0000100001,Hafenstr. 1,Hamburg,DE\n"
    "0000100002,High St 5,Manchester,UK\n"
    "0000100003,Main St 9,Austin,US\n"
)  # supplier 0000100004 has no address row


@pytest.fixture
def working():
    engine = create_engine("sqlite://")
    log = RunLog(engine)
    log.ensure_schema()
    return engine, log


def obj_dir(project: Path) -> Path:
    return project / "objects" / "supplier"


def edit(project: Path, rel: str, old: str, new: str):
    path = obj_dir(project) / rel
    content = path.read_text(encoding="utf-8")
    assert old in content, f"{old!r} not in {rel}"
    path.write_text(content.replace(old, new), encoding="utf-8")


def add_join(project: Path, *, kind="left", duplicates="error", addr=ADDR, fields=True):
    """Join an address source to the supplier structure."""
    (obj_dir(project) / "data" / "addr.csv").write_text(addr, encoding="utf-8")
    edit(
        project,
        "object.yaml",
        "    format: csv\nstructures:",
        "    format: csv\n  - kind: file\n    name: addr\n    path: data/addr.csv\n    format: csv\n"
        "structures:",
    )
    edit(
        project,
        "object.yaml",
        "    source: lfa1\n    mapping: mappings/general.yaml\n",
        "    source: lfa1\n    joins:\n      - source: addr\n        type: " + kind + "\n"
        "        on_duplicates: " + duplicates + "\n        keys: [{main: LIFNR, other: LIFNR}]\n"
        "    mapping: mappings/general.yaml\n",
    )
    if fields:
        edit(
            project,
            "mappings/general.yaml",
            "  - target_field: SEARCH_TERM",
            "  - target_field: STREET\n    rule: { type: MOVE, source_field: addr.STRAS }\n"
            "  - target_field: CITY\n"
            "    rule: { type: CONCAT, source_fields: [addr.ORT01, LAND1], separator: ', ' }\n"
            "  - target_field: SEARCH_TERM",
        )


def run(project: Path, engine, log):
    loaded = load_project(project)
    run_id = log.start_run("p", "supplier")
    extract_object(loaded, "supplier", engine, log, run_id)
    return (
        loaded,
        run_id,
        transform_object(loaded, "supplier", engine, log, run_id, write_build=False),
    )


def rows(engine, sql: str):
    with engine.connect() as conn:
        return conn.execute(text(sql)).all()


# --- the result ---------------------------------------------------------------------------


def test_columns_of_the_joined_source_fill_the_target(project_copy, working):
    engine, log = working
    add_join(project_copy)
    _, _, results = run(project_copy, engine, log)
    assert (results[0].rows_in, results[0].rows_out) == (4, 4)  # a left join keeps every row
    got = rows(
        engine, 'select "PARTNER","STREET","CITY" from tgt_bp_general order by "_dmc_row_no"'
    )
    assert got == [
        ("0000100001", "Hafenstr. 1", "Hamburg, DE"),
        ("0000100002", "High St 5", "Manchester, UK"),
        ("0000100003", "Main St 9", "Austin, US"),
        ("0000100004", None, ", DE"),  # no address row: empty, the CONCAT treats it as blank
    ]


def test_inner_join_drops_rows_without_a_match(project_copy, working):
    engine, log = working
    add_join(project_copy, kind="inner")
    _, _, results = run(project_copy, engine, log)
    assert (results[0].rows_in, results[0].rows_out) == (4, 3)
    assert "0000100004" not in [r[0] for r in rows(engine, 'select "PARTNER" from tgt_bp_general')]


def test_the_generated_sql_is_plain_and_matches_the_golden_file(project_copy):
    add_join(project_copy)
    project = load_project(project_copy)
    sql = generate_object(project, project.objects["supplier"])[0].render()
    assert sql == (GOLDEN / "supplier_join.sql").read_text(encoding="utf-8")


def test_lineage_still_points_at_the_main_source(project_copy, working):
    engine, log = working
    add_join(project_copy)
    run(project_copy, engine, log)
    assert [r[0] for r in rows(engine, 'select "_dmc_row_no" from tgt_bp_general order by 1')] == [
        1,
        2,
        3,
        4,
    ]


def test_xref_works_on_a_joined_column(project_copy, working):
    engine, log = working
    add_join(project_copy)
    edit(
        project_copy,
        "mappings/general.yaml",
        "  - target_field: SEARCH_TERM",
        "  - target_field: ADDR_COUNTRY\n"
        "    rule: { type: XREF, source_field: addr.LAND, xref: country, on_unmapped: error }\n"
        "  - target_field: SEARCH_TERM",
    )
    run(project_copy, engine, log)
    got = dict(rows(engine, 'select "PARTNER", "ADDR_COUNTRY" from tgt_bp_general'))
    assert got["0000100002"] == "GB"  # UK translated; the supplier without an address stays empty


def test_unmapped_xref_values_in_a_joined_source_are_named(project_copy, working):
    engine, log = working
    add_join(project_copy, addr=ADDR.replace("UK", "ZZ"))
    edit(
        project_copy,
        "mappings/general.yaml",
        "  - target_field: SEARCH_TERM",
        "  - target_field: ADDR_COUNTRY\n"
        "    rule: { type: XREF, source_field: addr.LAND, xref: country }\n"
        "  - target_field: SEARCH_TERM",
    )
    with pytest.raises(TransformError, match="no entry in xref 'country' for 'ZZ'"):
        run(project_copy, engine, log)


# --- safety -------------------------------------------------------------------------------


def test_a_key_that_repeats_would_multiply_rows_so_the_transform_stops(project_copy, working):
    engine, log = working
    add_join(project_copy, addr=ADDR + "0000100001,Second St 2,Hamburg,DE\n")
    with pytest.raises(TransformError, match="would multiply rows.*0000100001 appears 2 times"):
        run(project_copy, engine, log)


def test_duplicates_can_be_resolved_by_taking_the_first_row(project_copy, working):
    engine, log = working
    add_join(project_copy, duplicates="first", addr=ADDR + "0000100001,Second St 2,Hamburg,DE\n")
    _, _, results = run(project_copy, engine, log)
    assert results[0].rows_out == 4  # still one row per supplier
    got = dict(rows(engine, 'select "PARTNER","STREET" from tgt_bp_general'))
    assert got["0000100001"] == "Hafenstr. 1"  # the first address row


def test_the_joined_source_must_be_extracted(project_copy, working):
    engine, log = working
    add_join(project_copy)
    loaded = load_project(project_copy)
    run_id = log.start_run("p", "supplier")
    # only the main source is staged, as if Extract had been run for it alone
    from data_migration_concept.extract.readers import read_file
    from data_migration_concept.extract.staging import write_staging

    main = loaded.objects["supplier"].config.sources[0]
    write_staging(
        engine,
        "stg_supplier_lfa1",
        read_file(main, obj_dir(project_copy)),
        run_id,
        source_name="lfa1",
    )
    with pytest.raises(
        TransformError, match="staging table 'stg_supplier_addr'.*run extract first"
    ):
        transform_object(loaded, "supplier", engine, log, run_id, write_build=False)


def test_extract_checks_the_columns_of_both_sources(project_copy, working):
    engine, log = working
    add_join(project_copy, addr=ADDR.replace("STRAS", "STREET"))
    loaded = load_project(project_copy)
    with pytest.raises(ExtractError, match="missing source column.*addr.STRAS"):
        extract_object(loaded, "supplier", engine, log, log.start_run("p", "supplier"))


def test_extract_checks_the_join_keys(project_copy, working):
    engine, log = working
    add_join(project_copy, addr=ADDR.replace("LIFNR", "VENDOR", 1))
    loaded = load_project(project_copy)
    with pytest.raises(ExtractError, match="join with 'addr': missing key column.*LIFNR"):
        extract_object(loaded, "supplier", engine, log, log.start_run("p", "supplier"))


def test_reconciliation_compares_only_what_the_main_source_holds(project_copy, working):
    engine, log = working
    add_join(project_copy)
    loaded, _, _ = run(project_copy, engine, log)
    results = run_recons(loaded, "supplier", engine, log, log.start_run("p", "supplier"))
    assert all(r.status.value == "PASS" for r in results), [(r.name, r.message) for r in results]


# --- configuration ------------------------------------------------------------------------


def test_bad_join_definitions_are_reported_when_the_project_loads(project_copy):
    add_join(project_copy, fields=False)
    edit(project_copy, "object.yaml", "      - source: addr\n", "      - source: nowhere\n")
    with pytest.raises(ConfigError, match="joined source 'nowhere' does not exist"):
        load_project(project_copy)


def test_a_structure_cannot_join_its_own_source_or_use_a_reserved_name(project_copy):
    add_join(project_copy, fields=False)
    edit(project_copy, "object.yaml", "      - source: addr\n", "      - source: lfa1\n")
    with pytest.raises(ConfigError, match="own source and cannot be joined"):
        load_project(project_copy)


def test_source_names_that_clash_with_generated_sql_are_refused(project_copy):
    add_join(project_copy, fields=False)
    for name in ("s", "x1"):
        text_ = (obj_dir(project_copy) / "object.yaml").read_text(encoding="utf-8")
        (obj_dir(project_copy) / "object.yaml").write_text(
            text_.replace("source: addr", f"source: {name}").replace("name: addr", f"name: {name}"),
            encoding="utf-8",
        )
        with pytest.raises(ConfigError, match="reserved for generated SQL"):
            load_project(project_copy)
        text_ = (obj_dir(project_copy) / "object.yaml").read_text(encoding="utf-8")
        (obj_dir(project_copy) / "object.yaml").write_text(
            text_.replace(f"source: {name}", "source: addr").replace(f"name: {name}", "name: addr"),
            encoding="utf-8",
        )


def test_a_join_needs_a_key_and_a_mapping(project_copy):
    add_join(project_copy, fields=False)
    edit(project_copy, "object.yaml", "keys: [{main: LIFNR, other: LIFNR}]", "keys: []")
    with pytest.raises(ConfigError, match="at least 1 item"):
        load_project(project_copy)
