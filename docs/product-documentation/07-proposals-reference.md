# 07 - Proposals reference

The proposals engine (`src/data_migration_concept/proposals/`) answers: *"what could be added to this object?"* It is a set of **pure functions** over a snapshot (`ObjectFacts`) - it **never changes the project**. The UI (S05) shows the results; an accepted proposal is applied by `ui/proposals_service.py` through the same validated edit functions as manual edits.

## 1. Principles

1. **Proposed, never applied automatically.** Opening S05 changes no file.
2. **Applied by id, recomputed on the server.** A proposal id is a stable hash of `kind | structure | key` (12 hex characters). When you press Apply the server recomputes all proposals and applies those whose ids you ticked; an id that is no longer proposed is reported as "no longer available".
3. **Test-run on your data.** Where possible a proposed check is run against the staged values first. If the data already breaks it, it is proposed as **warn** with a red "Heads-up" note (it will not block a load unless you raise it).
4. **Each is validated.** Every proposal is applied through `apply_changes`: if it would add a project problem it is **refused**, files are restored, and the other proposals still go through.
5. **Dismissable.** Dismissed ids are stored in `objects/<object>/proposals_dismissed.json` and can be brought back.
6. **Sorted** by kind (mapping, xref, check, recon, load), then confidence (`high` before `medium`), then structure and title.

Facts the engine uses (built by `proposals_service.build_facts`): structures with their fields and rules, source columns, **profiles of staged tables** (first 100,000 rows per table), distinct values per column (up to 2,000 per column), existing checks/recons/XREFs/loads, confirmed structure-based rules. Without extracted data only the proposals that need no data are made.

## 2. Origin and confidence

| Badge | Meaning |
|---|---|
| origin `target structure` | derived from the structure definition (flags, types, keys) |
| origin `library` | from the built-in library (`catalog.py`, `countries.py`): standard value lists and field-name based formats |
| origin `your data` | from the staged data (values, layouts, ranges) |
| `high` confidence | exact/standard basis |
| `medium` confidence | heuristic or a data-based guess; review before applying |

## 3. Field mappings (`mapping_proposals`)

For each mapping-based structure with known source columns, for every **not mapped** field (never for custom-SQL structures), in this order:

| Basis | Rule | Confidence |
|---|---|---|
| **Exact name** | exactly one source column has the same name ignoring case, spaces, `_`, `-` | high |
| **Synonym** | no exact match and exactly one column is a known legacy name for the field (table below) | medium |
| **Similar spelling** | no exact/synonym match, the target name does **not** end in a digit (so `SMTP_ADDR_2` never matches `SMTP_ADDR`), and exactly one column has a name with similarity >= 0.86 | medium |
| **Joined source** | none of the above matched in the main source and exactly one joined-source column matches by name or synonym (proposed as `source.COLUMN`) | medium |
| **Value-based** | the field is a country/currency/unit/language field (by name) not ending in a digit, and no field of that kind is already mapped, and **exactly one** source column has >= 90% of its distinct values recognised as that kind (the column has at most 300 distinct values and max length <= 30; a tie gives no proposal) | medium, origin *your data* |

Payload: `MOVE <column>`. A note is added when the column is already used by another field. Applying a mapping proposal skips a field that has meanwhile been mapped.

**Synonym table** (target field -> legacy names, all compared squashed to lower case): `LIFNR` -> vendor, vendorid, vendornumber, vendorno, supplier, supplierid, supplierno, suppliernumber; `NAMEFIRST` -> name1, name, vendorname, suppliername; `COUNTRY` -> land1, land, ctry, cntry, countrycode; `CITY1` -> ort01, city, town; `POSTCODE1` -> pstlz, postcode, postalcode, zip, zipcode; `STREET` -> stras, street1, streetname, addressline1; `TELNRLONG` -> telf1, phone, phonenumber, tel, telephone; `FAXNRLONG` -> telfx, fax, faxnumber; `SMTPADDR` -> email, emailaddress, mail; `LANGUCORR` -> spras, language, lang; `REGION` -> regio, state, province; `SORTL` -> matchcode, searchterm.

## 4. Value lists (`xref_proposals`)

A proposal per **kind of coded value** (`country`, `currency`, `uom`, `language`) when at least one *MOVE* field with a matching name exists and no list of that name exists yet.

| Kind | Field-name pattern (case-insensitive, whole name) | Recognises | Target value |
|---|---|---|---|
| country | `COUNTRY, LAND1, LAND, CTRY, CNTRY, BANKS, POBOX_CTRY, NATION, *_COUNTRY` | ISO 3166 alpha-2, alpha-3, English names, common spellings (e.g. UK) | alpha-2 code |
| currency | `WAERS, CURRENCY, CURR, CUKY, *_CURRENCY, *_WAERS, WAERS_*` | ISO 4217 codes (+ `EURO`) | code |
| uom | `MEINS, UOM, UNIT, BASE_UOM, *_UOM, MSEHI, MEINH, BSTME` | table of common spellings (EA/PCS/ST -> PC, KGS -> KG, LTR -> L, ...) | common ISO/UN unit code (**check against the target system's units**) |
| language | `SPRAS, LANGU, LANG, LANGUAGE, LANGU_CORR, LANG_CORR_PERS, LANGU_ISO, *_LANGU` | E/EN/ENG/ENGLISH, D/DE/GER/GERMAN, F/FR, S/ES, I/IT, P/PT, NL, PL, SV ... | 2-letter ISO 639-1 |

- **With extracted data**: the list holds the values actually found; values that cannot be resolved are **left out** and named in a note ("Not recognised, so left out: ... The transform will stop and list them until you add them yourself."). Confidence `high` if all values were recognised, else `medium`; origin *your data*.
- **Without data**: the list holds **all standard values** (origin *library*, medium) with a note to run Extract.
- Applying: writes `xref/<kind>.csv` (refused if it exists) and switches the listed *MOVE* fields (same field and source column) to `XREF` with policy `error`.

## 5. The target structure's own rules (`target_rule_proposals`)

One proposal per kind that is **not yet confirmed**: *Mandatory fields must be filled*, *Values must fit the field length*, *Values must match the field's data type*, *Key fields must be unique*.

- Applies to the non-NOT_MIGRATED fields flagged for that kind (length: has a length; data type: not string; key: flagged key).
- **Test run** on plain MOVE fields: mandatory = empty count; length = values longer than the length; data type = values failing the type validator; key = non-empty rows minus distinct values. Combined keys are not test-run.
- Mandatory fields that are **not mapped yet** are counted as would-be failures.
- If anything would fail: proposed as `warn` with "Heads-up: ... It is proposed as a warning so it does not block your load; make it an error on the Checks tab once the data is clean." (medium). Otherwise `error` (high).
- Applying confirms the kind with that severity in `checks.yaml` -> `from_mapping`.

## 6. Checks (`check_proposals`)

Per mapping-based structure and active (not NOT_MIGRATED) field. A check is not proposed if an equivalent one (same structure, field, type, or same name) already exists. All names look like `<structure>_<field>_<short>`.

### 6.1 Library formats (by field name; origin *library*)

| Key | Field-name pattern | Type / setting | Default severity | Why |
|---|---|---|---|---|
| `country_format` | country names (see section 4) | PATTERN `[A-Z]{2}` | error | ISO 3166 codes are two capital letters |
| `currency_format` | currency names | PATTERN `[A-Z]{3}` | error | ISO 4217 |
| `email_format` | `SMTP_ADDR*, E_?MAIL*, EMAIL*, MAIL_ADDR*` | PATTERN `[^@\s]+@[^@\s]+\.[^@\s]+` | warn | an e-mail needs an @ and a domain |
| `iban_format` | `IBAN*` | PATTERN `[A-Z]{2}[0-9]{2}[A-Z0-9]{11,30}` | warn | IBAN layout |
| `phone_format` | `TEL*, MOBILE*, FAX*, PHONE*` | PATTERN `[+0-9 ()/.\-]+` | warn (medium) | digits, spaces and `+ ( ) / . -` |
| `company_code_format` | `BUKRS` | PATTERN `[A-Za-z0-9]{4}` | warn (medium) | four letters or digits |

Fields of the same rule are **grouped into one proposal**. Each field is test-run on its staged source column; if the data already breaks the rule, that entry becomes `warn` with a Heads-up note, and the proposal's confidence drops to medium. (A country-format check proposed as warn when the data holds names such as `Germany`.)

### 6.2 From your data (origin *your data*, medium)

For plain MOVE fields with a profile, and not already covered by a library rule and not a coded-value field:

| Proposal | Condition | Check (severity) |
|---|---|---|
| **Allowed values** "only allow the N values found" | >= 10 rows; between 2 and `min(12, rows/2)` distinct values | ALLOWED_VALUES with the values (warn) |
| **Keep the layout** | >= 3 filled values, **every** filled value has the same shape (<= 40 characters) and more than one distinct value | PATTERN built from the shape (`9`->`\d`, `A`->`[A-Z]`, `a`->`[a-z]`, other characters escaped; runs as `{n}`) (warn) |
| **Warn if it starts to be left empty** | field not mandatory, >= 5 rows, **no** empty value | NULL_RATE `max_pct: 5` (warn); fields of one structure are **grouped** into one proposal |
| **No negative values** | integer/decimal field whose minimum is >= 0 | RANGE `min: 0` (warn) |
| **Values should be unique** | no key flagged/proposed yet in the structure, >= 5 rows, every row has a different non-empty value | UNIQUE (warn); only one per structure |

## 7. Reconciliations (`recon_proposals`)

For each mapping-based structure:

| Proposal | Condition |
|---|---|
| **Row count** `<structure>_row_count` | always (for structures with row sets, titled "as many rows as all its row sets together") |
| **Keys** `<structure>_keys` | the structure has key fields and each is a MOVE of an **own-source** column (not a joined column). Source key = the source columns, target key = the key fields |
| **Values** `<structure>_values` (FIELDS auto) | the key proposal exists and at least one non-key field is an own-source MOVE |
| **Totals** `<structure>_total_<field>` (sum) | up to **3** integer/decimal fields that are own-source MOVEs |

**Structures with row sets only get the row-count proposal** (key/value/total recons read the main source only). A proposal is skipped when the same type/field is already reconciled or the name exists.

## 8. Load destinations (`load_proposals`)

One proposal per structure without a destination: a **CSV file** `output/<structure>.csv` (origin *library*, high). Nothing is loaded until a destination exists.

## 9. Gaps box

Not a proposal: S05 lists, per structure, the mandatory fields that are still *(not mapped)*, because they would fail the mandatory rule and block the load once it is confirmed as an error.

## 10. Test checklist for proposals

| ID | Scenario | Expected |
|---|---|---|
| TC-PRO-01 | Field `LIFNR` unmapped, source column `LIFNR` | "Fill LIFNR from LIFNR" (high) |
| TC-PRO-02 | Field `LIFNR`, source column `VENDOR` | medium, "common name ... in legacy systems" |
| TC-PRO-03 | Two source columns match one field exactly | no proposal (ambiguous) |
| TC-PRO-04 | Field `SMTP_ADDR_2` with a column `SMTP_ADDR` | no fuzzy proposal |
| TC-PRO-05 | Field `COUNTRY` unmapped; column `LAND_X` holding ISO codes only | value-based mapping proposal (medium, your data) |
| TC-PRO-06 | Country values `DE, UK, Germany, XX` | list with DE, UK->GB, Germany->DE; note "Not recognised, so left out: XX" |
| TC-PRO-07 | Before Extract, a country field exists | list with all standard codes + "No data has been extracted yet" note |
| TC-PRO-08 | Country data containing names | country-format check proposed as warn + Heads-up |
| TC-PRO-09 | Mandatory field with empty values in the data | mandatory rule proposed as warn + Heads-up with counts |
| TC-PRO-10 | A 12-row column with 3 distinct values | allowed-values proposal (warn) |
| TC-PRO-11 | A column where every value looks like `0000100001` | layout proposal `\d{10}` |
| TC-PRO-12 | Apply a proposal that would invalidate the project (e.g. check on a removed field) | refused, others applied |
| TC-PRO-13 | Dismiss, reload, restore | hidden then back |
| TC-PRO-14 | Structure with a row set | only the row-count recon proposed |
| TC-PRO-15 | Compare project files before and after merely opening S05 | identical |
