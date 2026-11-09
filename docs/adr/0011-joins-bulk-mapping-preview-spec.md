# ADR 0011: Joined sources, bulk mapping, preview and the mapping specification

Status: accepted.

## Context
Testing with real SAP data showed four gaps on the structures and mapping screen: one target structure often needs several source tables; mapping hundreds of fields one by one is slow; the effect of a rule is only visible after a full run; and business owners review a mapping in Excel, not in a web page.

## Decision
**Joins.** A structure may join other sources of the same object (`joins:` in `object.yaml`). The alias is the source name, the structure's own source is `s`; rules refer to joined columns as `source.COLUMN`. A join has `keys` (pairs `main` / `other`; the name is not `on`, because YAML 1.1 reads `on` as a boolean), a `type` (`left` keeps every main row, `inner` only matching ones) and `on_duplicates` (`error` or `first`). A join must never multiply rows silently: with `error` the transform stops and names the repeated keys; with `first` the lowest `_dmc_row_no` per key is used. A source's columns are validated at extract time (join keys and qualified references). Reconciliation reads the structure's own source only, so `FIELDS auto` and proposed key/total reconciliations skip qualified references. Proposals profile a qualified column in its own source (`StructureFacts.locate`).

**Row sets (UNION ALL).** A structure may have `unions:`, each a row set with its own main `source`, `joins` and `overrides` (target field -> rule). The generator builds one SELECT per row set from the same mapping (`effective_rule` picks the override) and combines them with `UNION ALL` into the one target table. Validation: row-set names are unique and not `main`; sources, joins and XREF files must exist; overrides must name mapped fields; a field is `NOT_MIGRATED` in every row set or none, so every SELECT has the same columns. Extract validates each row set's columns; the transform reads and counts every row set's staging table; the COUNT reconciliation sums them; proposals offer only the COUNT reconciliation for such a structure. `_dmc_row_no` is per source, so it is not unique across row sets.

**Bulk mapping.** The mapping page gets row selection with "set this rule for the selected fields", "copy the first rule to the others" and "set review", plus sorting and a review filter. These act on the page only; nothing is saved until "Save mapping", so a mistake is undone by reloading.

**Preview.** `transform.preview.preview_structure` runs the same plan as the transform (`plan_structure`) for the first N rows, and shows the target value with the source values underneath, flags mandatory-empty and too-long values, and lists the XREF values without an entry together with the policy that would apply. It writes nothing except what a transform also does first: it (re)loads the XREF tables from their files.

**Mapping specification.** The mapping exports to Excel (`Mapping`, `How to read`, one sheet per XREF list used) with a plain-words "Logic" column; the reviewer edits Rule, Source, Setting 1-3, Note and Review. Importing (Excel or a simple CSV with Target field / Rule / Source) never applies anything directly: it shows every difference, ignored fields and unusable rows, and writes only after confirmation, through `apply_changes`, so a change that breaks the project is refused. With row sets, each gets a sheet `Rows <name>` (the effective rule per field and an "Own rule" marker); the import diffs every sheet against what that sheet showed (the override, else the main rule as it was), so a change made only on `Mapping` is not mistaken for a row-set change, and the result is stored as overrides (rules equal to the main rule are dropped) together with the main mapping in one validated write. `FieldMapping` gained `note` and `review` (`""`, `approved`, `changes`); they are ignored by the generator.

## Consequences
- A structure with joins needs every joined source extracted first.
- Importing a rule that refers to a missing XREF list is refused at confirmation by the project validation, not at preview.
- The spreadsheet is a view of the mapping, not a second source of truth: the YAML files stay authoritative.
