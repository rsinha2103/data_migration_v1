# Data_Migration_Concept - product documentation

| | |
|---|---|
| Product | Data_Migration_Concept (`dmc`) - an open-source data migration workbench |
| Version documented | 0.1.0 (repository `rsinha2103/data_migration_v1`, branch `main`) |
| Written | 2026-11-10, against commit `09d3c21` and later |
| Copyright | capt_conv, Apache-2.0 |
| Audience | the product owner and testers (screen-by-screen testing), and the team that will turn the demo into a production-ready product |

## How to use this documentation

The documentation is split into chapters so that each can be read, tested and updated on its own.

| # | Chapter | Read it when you want to ... |
|---|---|---|
| 01 | [Overview and concepts](01-overview-and-concepts.md) | understand what the product is, its vocabulary, the data model and the safety model |
| 02 | [Installation and running](02-installation-and-running.md) | install, start the UI/CLI, choose a working database |
| 03a | [UI screens: projects, flow, proposals, sources, structures](03a-ui-screens-project-to-structures.md) | S01-S08: point at a screen and know every control, message and expected result (with test cases) |
| 03b | [UI screens: mapping, preview, specification import](03b-ui-mapping-screens.md) | S09-S11: the mapping page (joins, row sets, bulk tools, grid, Excel spec), the preview and the import |
| 03c | [UI screens: XREF, checks, loads, reconciliation, data, results, SQL, history, files](03c-ui-screens-rules-data-admin.md) | S12-S23 |
| 04 | [Project files reference](04-project-files-reference.md) | know every setting of `project.yaml`, `object.yaml`, mapping, checks, reconciliation and XREF files |
| 05 | [Engine reference](05-engine-reference.md) | know exactly what each step (extract, transform, check, load, reconcile, profile, report) does |
| 06 | [Command-line reference](06-cli-reference.md) | use or script the `dmc` commands |
| 07 | [Proposals reference](07-proposals-reference.md) | know every suggestion the product can make and the rule behind it |
| 08 | [Test guide](08-test-guide.md) | run a thorough end-to-end test with prepared sample data and expected results |
| 09 | [Traceability and production readiness](09-traceability-and-production-readiness.md) | map every feature to code and automated tests, see the message catalogue, limits and the open items for a production product |

## Conventions used in the chapters

- **Screen IDs** `S01` to `S23` identify the screens of the web interface (the map of all screens and their tabs is in chapter 03a section 0.2). Test cases are named `TC-S09-03` (screen 09, case 3). Cross-cutting test cases use other prefixes (`TC-E2E-`, `TC-CLI-`, `TC-SEC-`).
- **"Flash message"** is the coloured box shown on the next page after an action. Green = success, red = problem. Its title is quoted exactly as the product shows it.
- **Paths** such as `objects/supplier/object.yaml` are relative to the project folder. `src/...` paths are relative to the repository.
- **Names** that become table names (objects, sources, structures, target tables, check names) are *identifiers*: they start with a letter and contain only letters, digits and `_`.
- Text in `monospace` is something you type or an exact value you will see.
- "Not implemented" or "Limitation" is stated explicitly wherever the product does not (yet) do something a reader might expect. Chapter 09 collects them.

## The product in one page

1. A **project** is a folder (kept in git). It holds **objects** (supplier, material, ...). An object has **sources** (files or database tables), **structures** (target layouts, each with a field mapping or custom SQL), **XREF** value lists, **checks**, **reconciliations** and **load destinations**.
2. A migration is a pipeline of steps: **Extract** (sources to staging tables) -> **Transform** (generated SQL builds target tables) -> **Check** (validate) -> **Load** (write files/tables, only if no check blocks) -> **Reconcile** (prove nothing was lost) -> **Profile** (describe the data) -> **Report** (Excel/HTML/JSON).
3. Everything runs in a **working database** (SQLite for the UI by default, any SQLAlchemy URL otherwise). Every step is recorded in a **run log**.
4. The product **proposes** mappings, value lists, checks, reconciliations and loads - it **never applies them automatically**. Only what the user confirms is run.
5. The web UI (`dmc ui`) and the CLI (`dmc ...`) use the same engine. Both edit and run the same project files.

## Change history of this document

| Date | Change |
|---|---|
| 2026-11-10 | First complete edition. The end-to-end scenario TC-E2E-02 and the demo numbers in TC-E2E-01 were replayed against the product when written; the screen-level cases were derived from the code and templates and should be confirmed by hand on first use (record any difference and correct this document) |
