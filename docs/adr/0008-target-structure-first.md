# ADR 0008: Define the target structure before mapping

Status: accepted

## Decision
- The target structure (field names, description, type, length, mandatory, key) is defined first; mapping then says how each target field is filled. Target fields no longer come from the source columns.
- An object is created from its source file only and may have no structures yet (`structures` may be empty). Transform stops with a clear message until one exists.
- A new rule `UNMAPPED` marks a target field that is defined but not mapped. It stays in the target table and the load file as an empty column, in template order. NOT_MIGRATED keeps its meaning (the field is left out). Mandatory/length/type/key checks still apply to unmapped fields, so a mandatory field left unmapped is flagged.
- A field can be flagged `key`. A structure with key fields gets a derived uniqueness check (`key.<structure>`).
- Ways in (UI): upload a template (SAP Migration Cockpit Excel XML, zip of CSV templates, Cockpit Excel, or a plain `.xlsx`/`.csv` with a column picker), paste a field list, or copy the source columns. A blank template can be downloaded. One file may hold many structures; the user picks which to create, in one validated step.
- Updating a structure from a newer template merges: new fields are added unmapped, descriptive attributes are updated, and mapping rules are never changed.
- Cockpit parsing is tolerant of SAP's inline markup (`<LS>`, `</>`) but refuses XML with a DTD/entities. Zips are read member by member without extracting; limits on size and member count apply. Test files are small invented look-alikes; real SAP templates are not stored in the repository.

## Consequences
Existing projects keep working (their fields are mapped rules). The mapping file is both the target definition and the mapping, which keeps one source of truth. Structures built from a single source only: joining several source tables into one structure is still done with custom SQL.
