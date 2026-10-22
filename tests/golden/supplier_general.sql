DROP TABLE IF EXISTS "tgt_bp_general";

CREATE TABLE "tgt_bp_general" AS
SELECT
  s."_dmc_row_no" AS "_dmc_row_no",
  s."_dmc_run_id" AS "_dmc_run_id",
  s."LIFNR" AS "PARTNER",
  'SUPL' AS "BU_GROUP",
  s."NAME1" AS "NAME_ORG1",
  x1."target_value" AS "COUNTRY",
  SUBSTR(CAST(s."NAME1" AS TEXT), 1, 10) AS "SEARCH_TERM",
  COALESCE(CAST(s."LIFNR" AS TEXT), '') || '-' || COALESCE(CAST(s."NAME1" AS TEXT), '') AS "LEGACY_REF"
FROM "stg_supplier_lfa1" AS s
LEFT JOIN "xref_supplier_country" AS x1
  ON CAST(s."LAND1" AS TEXT) = x1."legacy_value";
