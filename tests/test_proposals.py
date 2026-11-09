"""The proposal engine: pure functions over a snapshot of an object. Nothing is applied here."""

import re

import pytest
from pydantic import TypeAdapter

from data_migration_concept.config.models import CheckConfig, ChecksFile, Load, ReconConfig
from data_migration_concept.profile.profile import ColumnProfile
from data_migration_concept.proposals import FieldFacts, ObjectFacts, StructureFacts, propose
from data_migration_concept.proposals.catalog import (
    DOMAINS,
    LANGUAGES,
    UOM,
    domain_for_field,
)
from data_migration_concept.proposals.countries import ALPHA2, resolve_country
from data_migration_concept.proposals.engine import (
    _impact,
    _regex_from_shape,
    check_proposals,
    load_proposals,
    mapping_proposals,
    recon_proposals,
    target_rule_proposals,
    xref_proposals,
)

MOVE = lambda col: {"type": "MOVE", "source_field": col}  # noqa: E731
UNMAPPED = {"type": "UNMAPPED"}


def profile(column, rows, empty=0, distinct=None, low=None, shapes=(), top=()):
    return ColumnProfile(
        column,
        rows,
        empty,
        distinct if distinct is not None else rows - empty,
        low,
        None,
        1,
        10,
        list(top),
        list(shapes),
    )


def facts(fields, *, source="src", **kwargs):
    return ObjectFacts(structures=[StructureFacts("general", source, fields)], **kwargs)


def by_title(proposals):
    return {p.title: p for p in proposals}


# --- the library ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "code"),
    [
        ("DE", "DE"), ("de", "DE"), (" us ", "US"), ("DEU", "DE"), ("Germany", "DE"),
        ("united kingdom", "GB"), ("UK", "GB"), ("U.S.A.", "US"), ("Holland", "NL"),
        ("Cote d'Ivoire", "CI"), ("XX", None), ("", None), ("Atlantis", None),
    ],
)  # fmt: skip
def test_country_resolver(value, code):
    assert resolve_country(value) == code


def test_country_table_is_complete_and_consistent():
    assert len(ALPHA2) == 249
    assert all(len(c) == 2 and c.isupper() for c in ALPHA2)
    from data_migration_concept.proposals.countries import ALPHA3

    assert len(ALPHA3) == 249 and set(ALPHA3.values()) == set(ALPHA2)


def test_other_domains_resolve_and_recognise_field_names():
    by_key = {d.key: d for d in DOMAINS}
    assert (
        by_key["currency"].resolve("eur") == "EUR" and by_key["currency"].resolve("EURO") == "EUR"
    )
    assert by_key["currency"].resolve("XXX") is None
    assert by_key["uom"].resolve("each") == "PC" and by_key["uom"].resolve("zzz") is None
    assert by_key["language"].resolve("german") == "DE"
    for name, key in [("LAND1", "country"), ("BANKS", "country"), ("WAERS", "currency"),
                      ("MEINS", "uom"), ("SPRAS", "language"), ("NAME1", None)]:  # fmt: skip
        found = domain_for_field(name)
        assert (found.key if found else None) == key
    assert len(UOM) > 30 and len(LANGUAGES) > 20


# --- mapping ----------------------------------------------------------------------------


def test_mapping_exact_fuzzy_ambiguous_and_already_mapped():
    f = facts(
        [
            FieldFacts("PARTNER_ID", UNMAPPED),
            FieldFacts("Name", UNMAPPED),
            FieldFacts("COUNTRYY", UNMAPPED),
            FieldFacts("CITY", UNMAPPED),
            FieldFacts("STREET", UNMAPPED),
            FieldFacts("DONE", MOVE("done")),
        ],
        source_columns={"src": ["partner id", "NAME", "COUNTRY", "City", "CITY", "ZIP", "done"]},
    )
    found = by_title(mapping_proposals(f))
    assert set(found) == {"Fill PARTNER_ID from partner id", "Fill Name from NAME",
                          "Fill COUNTRYY from COUNTRY"}  # fmt: skip
    assert found["Fill PARTNER_ID from partner id"].confidence == "high"
    assert found["Fill COUNTRYY from COUNTRY"].confidence == "medium"  # near match only
    # CITY is ambiguous (City and CITY), STREET has no column, DONE is already mapped
    payload = found["Fill Name from NAME"].payload
    assert payload == {"field": "Name", "rule": {"type": "MOVE", "source_field": "NAME"}}


def test_mapping_notes_a_column_that_is_already_used():
    f = facts(
        [FieldFacts("A", MOVE("X")), FieldFacts("X", UNMAPPED)],
        source_columns={"src": ["X"]},
    )
    (only,) = mapping_proposals(f)
    assert "already used" in only.notes[0]


def test_no_mapping_proposals_without_source_columns_or_for_custom_sql():
    assert mapping_proposals(facts([FieldFacts("A", UNMAPPED)])) == []
    custom = ObjectFacts(
        structures=[StructureFacts("g", "src", [], custom_sql=True)],
        source_columns={"src": ["A"]},
    )
    assert mapping_proposals(custom) == []


# --- value lists ------------------------------------------------------------------------


def test_country_list_from_data_leaves_out_unrecognised_values():
    data = {("src", "LAND"): ["DE", "UK", "Germany", "XX", " "]}
    f = facts([FieldFacts("COUNTRY", MOVE("LAND"))], distinct=lambda s, c: data.get((s, c)))
    (x,) = xref_proposals(f)
    assert x.origin == "your data" and x.confidence == "medium"
    assert x.payload["rows"] == [("DE", "DE"), ("UK", "GB"), ("Germany", "DE")]
    assert x.payload["xref"] == "country"
    assert x.payload["fields"] == [
        {"structure": "general", "field": "COUNTRY", "source_field": "LAND"}
    ]
    assert any("Not recognised" in n and "XX" in n for n in x.notes)


def test_clean_data_gives_a_high_confidence_list_covering_several_fields():
    f = ObjectFacts(
        structures=[
            StructureFacts("general", "a", [FieldFacts("COUNTRY", MOVE("C1"))]),
            StructureFacts("bank", "b", [FieldFacts("BANKS", MOVE("C2"))]),
        ],
        distinct=lambda s, c: ["DE", "US"] if c == "C1" else ["US", "FR"],
    )
    (x,) = xref_proposals(f)
    assert x.confidence == "high" and [r[0] for r in x.payload["rows"]] == ["DE", "US", "FR"]
    assert (
        len(x.payload["fields"]) == 2 and "general.COUNTRY" in x.title and "bank.BANKS" in x.title
    )


def test_without_data_the_whole_standard_list_is_offered():
    f = facts([FieldFacts("WAERS", MOVE("CUR"))])
    (x,) = xref_proposals(f)
    assert x.origin == "library" and ("EUR", "EUR") in x.payload["rows"]
    assert any("No data has been extracted" in n for n in x.notes)


def test_no_list_proposed_when_it_exists_or_the_field_is_not_a_plain_move():
    moved = [FieldFacts("COUNTRY", MOVE("LAND"))]
    assert xref_proposals(facts(moved, xrefs={"country"})) == []
    already = [FieldFacts("COUNTRY", {"type": "XREF", "source_field": "LAND", "xref": "x"})]
    assert xref_proposals(facts(already)) == []
    assert xref_proposals(facts([FieldFacts("COUNTRY", UNMAPPED)])) == []
    assert xref_proposals(facts([FieldFacts("NAME1", MOVE("N"))])) == []


# --- checks -----------------------------------------------------------------------------


def test_checks_from_the_field_name_library():
    f = facts([FieldFacts("COUNTRY", MOVE("C")), FieldFacts("SMTP_ADDR", MOVE("M")),
               FieldFacts("IBAN", UNMAPPED), FieldFacts("NAME1", MOVE("N"))])  # fmt: skip
    proposals = check_proposals(f)
    found = {e["name"]: e for p in proposals for e in p.payload["entries"]}
    assert set(found) == {"general_COUNTRY_country_format", "general_SMTP_ADDR_email_format",
                          "general_IBAN_iban_format"}  # fmt: skip
    country = found["general_COUNTRY_country_format"]
    assert country["type"] == "PATTERN" and country["pattern"] == "[A-Z]{2}"
    assert "severity" not in country  # error is the default
    assert found["general_SMTP_ADDR_email_format"]["severity"] == "warn"
    assert all(p.origin == "library" for p in proposals)


def test_existing_checks_are_not_proposed_again():
    f = facts(
        [FieldFacts("COUNTRY", MOVE("C"))],
        checks=[{"name": "mine", "structure": "general", "type": "PATTERN", "field": "COUNTRY"}],
    )
    assert check_proposals(f) == []


def test_checks_from_the_data():
    f = facts(
        [
            FieldFacts("STATUS", MOVE("ST")),
            FieldFacts("CODE", MOVE("CD")),
            FieldFacts("NOTE", MOVE("NT")),
            FieldFacts("AMOUNT", MOVE("AM"), data_type="decimal"),
            FieldFacts("REF", MOVE("RF")),
        ],
        profiles={"src": {
            "ST": profile("ST", 20, distinct=3, shapes=[("AAAA", 10), ("AAA", 10)]),
            "CD": profile("CD", 20, distinct=20, shapes=[("9999999999", 20)]),
            "NT": profile("NT", 20, empty=5, distinct=15, shapes=[("Aa", 3)]),
            "AM": profile("AM", 20, distinct=18, low="0.5", shapes=[("9.9", 10), ("99.9", 10)]),
            "RF": profile("RF", 20, distinct=20, shapes=[("AA99", 10), ("AAA9", 10)]),
        }},
        distinct=lambda s, c: ["ACTIVE", "CLOSED", "OPEN"] if c == "ST" else None,
    )  # fmt: skip
    found = {e["name"]: e for p in check_proposals(f) for e in p.payload["entries"]}
    assert found["general_STATUS_allowed"]["values"] == ["ACTIVE", "CLOSED", "OPEN"]
    assert found["general_CODE_format"]["pattern"] == r"\d{10}"
    assert found["general_AMOUNT_not_negative"]["type"] == "RANGE"
    assert "general_NOTE_filled" not in found  # 25% empty: no "always filled" claim
    assert found["general_STATUS_filled"]["type"] == "NULL_RATE"
    uniques = [n for n in found if n.endswith("_unique")]
    assert len(uniques) == 1  # one uniqueness suggestion per structure, not one per column
    assert all(v.get("severity") == "warn" for v in found.values())  # data-based ones only warn


def test_no_unique_suggestion_when_a_key_is_already_marked():
    f = facts(
        [FieldFacts("ID", MOVE("I"), key=True), FieldFacts("CODE", MOVE("C"))],
        profiles={"src": {"C": profile("C", 20, distinct=20)}},
    )
    assert not [p for p in check_proposals(f) if p.payload["entries"][0]["type"] == "UNIQUE"]


def test_regex_from_shape_is_valid_and_matches_the_values_it_came_from():
    for shape, good, bad in [("9999-AA", "1234-AB", "12-AB"), ("AAa", "ABc", "abc"),
                             ("9", "7", "77"), ("A.9", "X.1", "XX1")]:  # fmt: skip
        pattern = _regex_from_shape(shape)
        assert re.fullmatch(pattern, good) and not re.fullmatch(pattern, bad)
    assert _regex_from_shape("9999999999") == r"\d{10}"


# --- reconciliations and loads ----------------------------------------------------------


def test_recon_proposals_use_keys_and_totals_when_they_make_sense():
    f = facts(
        [
            FieldFacts("PARTNER", MOVE("LIFNR"), key=True),
            FieldFacts("NAME", MOVE("NAME1")),
            FieldFacts("AMOUNT", MOVE("AMT"), data_type="decimal"),
            FieldFacts("LATER", UNMAPPED),
        ]
    )
    found = {e["name"]: e for p in recon_proposals(f) for e in p.payload["entries"]}
    assert set(found) == {"general_row_count", "general_keys", "general_values",
                          "general_total_AMOUNT"}  # fmt: skip
    assert found["general_keys"]["source_key"] == ["LIFNR"]
    assert found["general_keys"]["target_key"] == ["PARTNER"]
    assert found["general_values"]["fields"] == "auto"
    assert found["general_total_AMOUNT"]["source_field"] == "AMT"


def test_recon_without_keys_only_proposes_counts_and_totals():
    f = facts([FieldFacts("A", MOVE("a")), FieldFacts("B", UNMAPPED)])
    assert {e["type"] for p in recon_proposals(f) for e in p.payload["entries"]} == {"COUNT"}


def test_existing_recons_and_loads_are_not_proposed_again():
    f = facts(
        [FieldFacts("A", MOVE("a"))],
        recons=[{"name": "x", "structure": "general", "type": "COUNT"}],
        loads=[{"kind": "file", "structure": "general", "path": "output/g.csv", "format": "csv"}],
    )
    assert recon_proposals(f) == [] and load_proposals(f) == []
    assert [p.payload["path"] for p in load_proposals(facts([FieldFacts("A", UNMAPPED)]))] == [
        "output/general.csv"
    ]


# --- the whole -------------------------------------------------------------------------


def test_everything_proposed_is_something_the_project_would_accept():
    f = facts(
        [
            FieldFacts("COUNTRY", MOVE("LAND")),
            FieldFacts("PARTNER", MOVE("LIFNR"), key=True),
            FieldFacts("AMOUNT", MOVE("AMT"), data_type="decimal"),
            FieldFacts("SMTP_ADDR", MOVE("MAIL")),
            FieldFacts("TODO", UNMAPPED),
        ],
        source_columns={"src": ["LAND", "LIFNR", "AMT", "MAIL", "TODO"]},
    )
    proposals = propose(f)
    kinds = {p.kind for p in proposals}
    assert kinds == {"mapping", "xref", "check", "recon", "load"}
    for p in proposals:
        if p.kind == "check" and "from_mapping" in p.payload:
            ChecksFile.model_validate({"from_mapping": p.payload["from_mapping"]})
        elif p.kind == "check":
            for entry in p.payload["entries"]:
                TypeAdapter(CheckConfig).validate_python(entry)
        elif p.kind == "recon":
            for entry in p.payload["entries"]:
                TypeAdapter(ReconConfig).validate_python(entry)
        elif p.kind == "load":
            TypeAdapter(Load).validate_python(p.payload)


def test_ids_are_stable_unique_and_dismissed_ones_are_left_out():
    f = facts([FieldFacts("COUNTRY", MOVE("LAND")), FieldFacts("PARTNER", MOVE("L"), key=True)])
    first, second = propose(f), propose(f)
    assert [p.id for p in first] == [p.id for p in second]
    assert len({p.id for p in first}) == len(first)
    gone = first[0].id
    assert gone not in {p.id for p in propose(f, dismissed={gone})}


def test_proposals_are_ordered_by_kind_then_confidence():
    f = facts(
        [FieldFacts("COUNTRY", MOVE("LAND")), FieldFacts("X", UNMAPPED)],
        source_columns={"src": ["LAND", "X"]},
    )
    order = [p.kind for p in propose(f)]
    assert order == sorted(order, key=["mapping", "xref", "check", "recon", "load"].index)


def test_proposing_never_changes_the_facts():
    fields = [FieldFacts("COUNTRY", MOVE("LAND")), FieldFacts("TODO", UNMAPPED)]
    f = facts(fields, source_columns={"src": ["LAND", "TODO"]})
    before = [(x.name, dict(x.rule)) for x in fields]
    propose(f)
    assert [(x.name, dict(x.rule)) for x in fields] == before


# --- grouping, legacy names and values --------------------------------------------------


def test_library_checks_for_several_fields_come_as_one_proposal():
    f = facts(
        [FieldFacts("TELNR_LONG", MOVE("a")), FieldFacts("TELNR_LONG_2", UNMAPPED),
         FieldFacts("MOBILE_LONG", UNMAPPED), FieldFacts("COUNTRY", UNMAPPED)]
    )  # fmt: skip
    proposals = check_proposals(f)
    phone = next(p for p in proposals if "Phone format" in p.title)
    assert "3 fields" in phone.title
    assert [e["field"] for e in phone.payload["entries"]] == [
        "TELNR_LONG",
        "TELNR_LONG_2",
        "MOBILE_LONG",
    ]
    assert len({e["name"] for e in phone.payload["entries"]}) == 3  # one check per field
    assert len(proposals) == 2  # phones together, country on its own


def test_group_skips_fields_that_already_have_a_check():
    f = facts(
        [FieldFacts("TELNR_LONG", MOVE("a")), FieldFacts("TELNR_LONG_2", MOVE("b"))],
        checks=[{"name": "own", "structure": "general", "type": "PATTERN", "field": "TELNR_LONG"}],
    )
    (only,) = check_proposals(f)
    assert [e["field"] for e in only.payload["entries"]] == ["TELNR_LONG_2"]


def test_numbered_copies_are_not_mapped_by_similar_spelling():
    f = facts(
        [FieldFacts("SMTP_ADDR", UNMAPPED), FieldFacts("SMTP_ADDR_2", UNMAPPED),
         FieldFacts("SMTP_ADDR_3", UNMAPPED)],
        source_columns={"src": ["SMTP_ADDR"]},
    )  # fmt: skip
    assert [p.title for p in mapping_proposals(f)] == ["Fill SMTP_ADDR from SMTP_ADDR"]


def test_known_legacy_names_are_proposed_with_medium_confidence():
    f = facts(
        [FieldFacts("COUNTRY", UNMAPPED), FieldFacts("CITY1", UNMAPPED),
         FieldFacts("POST_CODE1", UNMAPPED), FieldFacts("LIFNR", UNMAPPED)],
        source_columns={"src": ["LAND1", "ORT01", "PSTLZ", "LIFNR"]},
    )  # fmt: skip
    found = {p.payload["field"]: p for p in mapping_proposals(f)}
    assert found["LIFNR"].confidence == "high"
    for target, column in [("COUNTRY", "LAND1"), ("CITY1", "ORT01"), ("POST_CODE1", "PSTLZ")]:
        assert found[target].confidence == "medium"
        assert found[target].payload["rule"]["source_field"] == column
        assert "common name" in found[target].why


def test_a_column_is_found_by_its_values_when_its_name_gives_no_hint():
    f = facts(
        [FieldFacts("COUNTRY", UNMAPPED), FieldFacts("POBOX_CTRY", UNMAPPED)],
        source_columns={"src": ["ABC", "XYZ"]},
        profiles={
            "src": {"ABC": profile("ABC", 20, distinct=4), "XYZ": profile("XYZ", 20, distinct=15)}
        },
        distinct=lambda s, c: {"ABC": ["DE", "UK", "US", "France"], "XYZ": ["a1", "b2", "c3"]}[c],
    )
    found = mapping_proposals(f)
    assert [p.payload["field"] for p in found] == ["COUNTRY"]  # one field per kind of value
    assert found[0].payload["rule"]["source_field"] == "ABC"
    assert found[0].origin == "your data" and "look like country" in found[0].why


def test_value_based_mapping_needs_a_clear_winner():
    both = lambda s, c: ["DE", "UK", "US"]  # noqa: E731
    f = facts(
        [FieldFacts("COUNTRY", UNMAPPED)],
        source_columns={"src": ["A", "B"]},
        profiles={"src": {"A": profile("A", 10, distinct=3), "B": profile("B", 10, distinct=3)}},
        distinct=both,
    )
    assert mapping_proposals(f) == []  # two equally good columns: no guess
    mostly_wrong = facts(
        [FieldFacts("COUNTRY", UNMAPPED)],
        source_columns={"src": ["A"]},
        profiles={"src": {"A": profile("A", 10, distinct=4)}},
        distinct=lambda s, c: ["DE", "UK", "zz", "yy"],
    )
    assert mapping_proposals(mostly_wrong) == []


def test_always_filled_fields_are_one_grouped_suggestion():
    f = facts(
        [FieldFacts("A", MOVE("a")), FieldFacts("B", MOVE("b")), FieldFacts("C", MOVE("c"))],
        profiles={
            "src": {"a": profile("a", 10), "b": profile("b", 10), "c": profile("c", 10, empty=4)}
        },
    )
    (only,) = (
        p for p in check_proposals(f) if "filled in every row" in p.title or "left empty" in p.title
    )
    assert [e["field"] for e in only.payload["entries"]] == ["A", "B"]  # C has gaps in the data
    assert "2 fields" in only.title


def test_value_based_mapping_stays_quiet_once_a_field_of_that_kind_is_mapped():
    f = facts(
        [FieldFacts("COUNTRY", MOVE("LAND")), FieldFacts("POBOX_CTRY", UNMAPPED)],
        source_columns={"src": ["LAND", "ABC"]},
        profiles={"src": {"ABC": profile("ABC", 20, distinct=3)}},
        distinct=lambda s, c: ["DE", "UK", "US"],
    )
    assert mapping_proposals(f) == []


def test_language_fields_are_recognised_by_name_not_by_a_loose_prefix():
    assert domain_for_field("SPRAS") and domain_for_field("LANGU_CORR")
    assert domain_for_field("LANGU_NOTE") is None and domain_for_field("NOTE_ADDRESS") is None


# --- a test run of every proposed check on the data as it is today ----------------------


def counts_for(**columns):
    return lambda s, c: columns.get(c)


def test_a_check_the_data_already_breaks_is_proposed_as_a_warning_with_a_heads_up():
    f = facts(
        [FieldFacts("COUNTRY", MOVE("LAND")), FieldFacts("WAERS", MOVE("CUR"))],
        profiles={
            "src": {"LAND": profile("LAND", 40, distinct=4), "CUR": profile("CUR", 40, distinct=2)}
        },
        value_counts=counts_for(
            LAND=[("DE", 20), ("Germany", 6), ("UK", 12), ("XX", 2)],
            CUR=[("EUR", 30), ("USD", 10)],
        ),
    )
    (country,) = (
        p
        for p in check_proposals(f)
        if p.origin == "library" and p.payload["entries"][0]["field"] == "COUNTRY"
    )
    entry = country.payload["entries"][0]
    assert entry["severity"] == "warn"  # would flag 'Germany' today, so it must not block the load
    (note,) = country.notes
    assert note.startswith("Heads-up") and "6 of 40 rows" in note and "Germany" in note
    assert country.confidence == "medium" and "warn" in country.summary
    (currency,) = (
        p
        for p in check_proposals(f)
        if p.origin == "library" and p.payload["entries"][0]["field"] == "WAERS"
    )
    assert "severity" not in currency.payload["entries"][0]  # clean data keeps the strict default
    assert currency.notes == [] and currency.confidence == "high"


def test_impact_for_each_kind_of_check():
    st = StructureFacts("general", "src", [])
    field = FieldFacts("X", MOVE("C"))
    f = facts(
        [field],
        profiles={"src": {"C": profile("C", 10, empty=3, distinct=4)}},
        value_counts=counts_for(C=[("1", 4), ("5", 2), ("abc", 1)]),
    )
    pattern = _impact(f, st, field, {"type": "PATTERN", "field": "X", "pattern": r"\d"})
    assert (pattern.failing, pattern.total, pattern.samples) == (1, 10, ["abc"])
    allowed = _impact(f, st, field, {"type": "ALLOWED_VALUES", "field": "X", "values": ["1", "5"]})
    assert allowed.failing == 1 and allowed.samples == ["abc"]
    rng = _impact(f, st, field, {"type": "RANGE", "field": "X", "min": 2, "max": 9})
    assert (rng.failing, sorted(rng.samples)) == (5, ["1", "abc"])  # 4 below the minimum + 1 text
    assert _impact(f, st, field, {"type": "NULL_RATE", "field": "X", "max_pct": 10}).failing == 3
    assert _impact(f, st, field, {"type": "NULL_RATE", "field": "X", "max_pct": 50}).failing == 0
    assert _impact(f, st, field, {"type": "UNIQUE", "fields": ["X"]}) is None


def test_no_impact_is_claimed_without_data_or_for_fields_that_are_not_copied():
    st = StructureFacts("general", "src", [])
    entry = {"type": "PATTERN", "field": "X", "pattern": "a"}
    assert _impact(facts([]), st, FieldFacts("X", MOVE("C")), entry) is None  # nothing staged
    staged = facts(
        [], profiles={"src": {"C": profile("C", 5)}}, value_counts=counts_for(C=[("b", 5)])
    )
    assert _impact(staged, st, FieldFacts("X", UNMAPPED), entry) is None  # not a plain MOVE
    assert _impact(staged, st, FieldFacts("X", MOVE("C")), entry).failing == 5


# --- the target structure's own rules are proposed, never run on their own ---------------


def rule_facts(**kwargs):
    fields = [
        FieldFacts("ID", MOVE("I"), mandatory=True, key=True, length=4),
        FieldFacts("NAME", MOVE("N"), mandatory=True, length=20),
        FieldFacts("BORN", MOVE("B"), data_type="date"),
        FieldFacts("TODO", UNMAPPED, mandatory=True),
    ]
    return facts(fields, **kwargs)


def test_structure_rules_are_proposed_per_kind_and_none_is_confirmed_by_default():
    found = {p.payload["from_mapping"].popitem()[0]: p for p in target_rule_proposals(rule_facts())}
    assert set(found) == {"mandatory", "length", "data_type", "key"}
    assert "3 field(s)" in found["mandatory"].title  # ID, NAME, TODO
    assert "general.ID" in found["length"].title and "general.NAME" in found["length"].title
    assert found["key"].title.startswith("Key fields must be unique") and "ID" in found["key"].title
    assert all(p.origin == "target structure" and p.kind == "check" for p in found.values())


def test_confirmed_kinds_and_structures_without_such_fields_are_not_proposed_again():
    assert {
        p.payload["from_mapping"].popitem()[0]
        for p in target_rule_proposals(
            rule_facts(from_mapping={"mandatory": "error", "key": "warn"})
        )
    } == {"length", "data_type"}
    plain = facts([FieldFacts("A", MOVE("a"))])
    assert target_rule_proposals(plain) == []
    custom = ObjectFacts(structures=[StructureFacts("g", "src", [], custom_sql=True)])
    assert target_rule_proposals(custom) == []


def test_structure_rules_the_data_already_breaks_are_proposed_as_warnings_with_the_reason():
    profiles = {"src": {
        "I": profile("I", 10, distinct=8),                 # two duplicate ids
        "N": profile("N", 10, empty=2, distinct=7),         # two empty names
        "B": profile("B", 10, distinct=10),
    }}  # fmt: skip
    counts = {
        "N": [("short", 6), ("a name that is far too long", 2)],
        "B": [("2026-01-01", 9), ("01.01.2026", 1)],
    }
    f = rule_facts(profiles=profiles, value_counts=lambda s, c: counts.get(c))
    found = {next(iter(p.payload["from_mapping"])): p for p in target_rule_proposals(f)}
    mandatory = found["mandatory"]
    assert mandatory.payload["from_mapping"] == {"mandatory": "warn"}
    assert any("general.NAME: 2 of 10 rows" in n for n in mandatory.notes)
    assert any(
        "1 mandatory field(s) are not mapped yet (general.TODO)" in n for n in mandatory.notes
    )
    assert found["length"].payload["from_mapping"] == {"length": "warn"}
    assert "far too long" in " ".join(found["length"].notes)
    assert found["data_type"].payload["from_mapping"] == {"data_type": "warn"}
    assert "01.01.2026" in " ".join(found["data_type"].notes)
    assert found["key"].payload["from_mapping"] == {"key": "warn"}  # 8 distinct of 10


def test_clean_data_keeps_the_strict_default():
    profiles = {"src": {"I": profile("I", 10), "N": profile("N", 10), "B": profile("B", 10)}}
    counts = {"N": [("ok", 10)], "B": [("2026-01-01", 10)]}
    f = facts(
        [
            FieldFacts("NAME", MOVE("N"), mandatory=True, length=5),
            FieldFacts("BORN", MOVE("B"), data_type="date"),
        ],
        profiles=profiles,
        value_counts=lambda s, c: counts.get(c),
    )
    found = target_rule_proposals(f)
    assert {next(iter(p.payload["from_mapping"].values())) for p in found} == {"error"}
    assert all(p.notes == [] and p.confidence == "high" for p in found)
