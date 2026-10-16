// Cross-surface warehouse compute attribution. Port of the warehouse section of
// utils/queries.py: QUERY_ATTRIBUTION_HISTORY and QUERY_METERING_HISTORY are
// unioned into one row per QUERY_ID and restricted to Cortex-attributable rows.
import type { IsoDate } from "@/lib/view-state";
import {
  SURFACE_AGENTS,
  SURFACE_AI_FUNCTIONS,
  SURFACE_COCO,
  SURFACE_COWORK,
  SURFACE_SEARCH,
} from "@/lib/plans";
import {
  AU,
  COCO_APP_NAMES,
  TIER_PER_REQUEST,
  TIER_SURFACE,
  type Statement,
  utcDay,
  utcWindow,
  windowParams,
} from "@/lib/sql/core";

export const WAREHOUSE_FACT_COLUMNS = [
  "QUERY_ID",
  "QUERY_TAG",
  "WAREHOUSE_NAME",
  "SURFACE",
  "TIER",
  "REQUEST_ID",
  "SOURCE_VIEW",
  "COMPUTE_CREDITS",
  "CLOUD_SERVICES_CREDITS",
  "WAREHOUSE_CREDITS",
  "USAGE_DATE",
] as const;

const cocoApps = COCO_APP_NAMES.map((app) => `'${app}'`).join(", ");
const isCocoTag = `TRY_PARSE_JSON(c.QUERY_TAG):app::STRING IN (${cocoApps})`;

// Zero-row availability probe for QUERY_METERING_HISTORY.
export function meteringProbeSql(): Statement {
  return { sql: `SELECT 1 FROM ${AU}.QUERY_METERING_HISTORY LIMIT 0`, params: [] };
}

// QUERY_ID intersection count between the two source views (advisory check).
export function meteringAttributionOverlapSql(start: IsoDate, end: IsoDate): Statement {
  const sql = `
SELECT COUNT(*) AS OVERLAP_COUNT
FROM (SELECT DISTINCT QUERY_ID FROM ${AU}.QUERY_ATTRIBUTION_HISTORY WHERE ${utcWindow("START_TIME")}) a
JOIN (SELECT DISTINCT QUERY_ID FROM ${AU}.QUERY_METERING_HISTORY WHERE ${utcWindow("QUERY_START_TIME")}) m
  ON a.QUERY_ID = m.QUERY_ID
`;
  return { sql, params: [...windowParams(start, end), ...windowParams(start, end)] };
}

// The classification CTE shared by the windowed statement and both scheduled bodies.
function classifiedCte(source: string): string {
  return `classified AS (
    SELECT
        c.QUERY_ID,
        c.QUERY_TAG,
        c.WAREHOUSE_NAME,
        c.SOURCE_VIEW,
        c.COMPUTE_CREDITS,
        c.CLOUD_SERVICES_CREDITS,
        c.COMPUTE_CREDITS + c.CLOUD_SERVICES_CREDITS AS WAREHOUSE_CREDITS,
        c.USAGE_DATE,
        CASE
            WHEN c.QUERY_TAG LIKE 'cortex-agent%'             THEN '${SURFACE_AGENTS}'
            WHEN c.QUERY_TAG LIKE 'snowflake-intelligence%'   THEN '${SURFACE_COWORK}'
            WHEN ${isCocoTag}                                THEN '${SURFACE_COCO}'
            WHEN si.QUERY_ID IS NOT NULL                      THEN '${SURFACE_SEARCH}'
            WHEN af.QUERY_ID IS NOT NULL                      THEN '${SURFACE_AI_FUNCTIONS}'
        END AS SURFACE,
        CASE
            WHEN c.QUERY_TAG LIKE 'cortex-agent-%'                       THEN '${TIER_PER_REQUEST}'
            WHEN c.QUERY_TAG LIKE 'cortex-agent'                         THEN '${TIER_SURFACE}'
            WHEN c.QUERY_TAG LIKE 'snowflake-intelligence-%'             THEN '${TIER_PER_REQUEST}'
            WHEN c.QUERY_TAG LIKE 'snowflake-intelligence'               THEN '${TIER_SURFACE}'
            WHEN ${isCocoTag}
                 AND TRY_PARSE_JSON(c.QUERY_TAG):request_id::STRING IS NOT NULL THEN '${TIER_PER_REQUEST}'
            WHEN ${isCocoTag}                                           THEN '${TIER_SURFACE}'
            WHEN si.QUERY_ID IS NOT NULL OR af.QUERY_ID IS NOT NULL      THEN '${TIER_PER_REQUEST}'
        END AS TIER,
        CASE
            WHEN c.QUERY_TAG LIKE 'cortex-agent-%'           THEN SUBSTR(c.QUERY_TAG, 14)
            WHEN c.QUERY_TAG LIKE 'snowflake-intelligence-%' THEN SUBSTR(c.QUERY_TAG, 24)
            WHEN ${isCocoTag}                                THEN TRY_PARSE_JSON(c.QUERY_TAG):request_id::STRING
            WHEN si.QUERY_ID IS NOT NULL OR af.QUERY_ID IS NOT NULL THEN c.QUERY_ID
        END AS REQUEST_ID
    FROM ${source} c
    LEFT JOIN search_ids si ON si.QUERY_ID = c.QUERY_ID
    LEFT JOIN aif_ids af ON af.QUERY_ID = c.QUERY_ID
)`;
}

const FINAL_SELECT = `
SELECT
    QUERY_ID, QUERY_TAG, WAREHOUSE_NAME, SURFACE, TIER, REQUEST_ID, SOURCE_VIEW,
    COMPUTE_CREDITS, CLOUD_SERVICES_CREDITS, WAREHOUSE_CREDITS, USAGE_DATE
FROM classified
WHERE SURFACE IS NOT NULL
`;

function warehouseCreditFactsSqlImpl(
  start: IsoDate,
  end: IsoDate,
  includeMetering: boolean,
): Statement {
  const params: string[] = [...windowParams(start, end)];
  const ctes: string[] = [
    `attr AS (
    SELECT
        QUERY_ID,
        ANY_VALUE(QUERY_TAG)      AS QUERY_TAG,
        ANY_VALUE(WAREHOUSE_NAME) AS WAREHOUSE_NAME,
        SUM(NVL(CREDITS_ATTRIBUTED_COMPUTE, 0)) AS COMPUTE_CREDITS,
        0::FLOAT                  AS CLOUD_SERVICES_CREDITS,
        'ATTRIBUTION'             AS SOURCE_VIEW,
        ANY_VALUE(${utcDay("START_TIME")}) AS USAGE_DATE
    FROM ${AU}.QUERY_ATTRIBUTION_HISTORY
    WHERE ${utcWindow("START_TIME")}
    GROUP BY QUERY_ID
)`,
  ];
  let combinedSource = "attr";
  if (includeMetering) {
    params.push(...windowParams(start, end));
    ctes.push(`meter AS (
    SELECT
        QUERY_ID,
        ANY_VALUE(QUERY_TAG)      AS QUERY_TAG,
        ANY_VALUE(WAREHOUSE_NAME) AS WAREHOUSE_NAME,
        SUM(NVL(CREDITS_USED_COMPUTE, 0))         AS COMPUTE_CREDITS,
        SUM(NVL(CREDITS_USED_CLOUD_SERVICES, 0))::FLOAT  AS CLOUD_SERVICES_CREDITS,
        'METERING'                AS SOURCE_VIEW,
        ANY_VALUE(${utcDay("QUERY_START_TIME")}) AS USAGE_DATE
    FROM ${AU}.QUERY_METERING_HISTORY
    WHERE ${utcWindow("QUERY_START_TIME")}
      AND WAREHOUSE_NAME IS NOT NULL
    GROUP BY QUERY_ID
)`);
    ctes.push(`combined AS (
    SELECT * FROM attr
    UNION ALL
    SELECT m.* FROM meter m
    WHERE NOT EXISTS (SELECT 1 FROM attr a WHERE a.QUERY_ID = m.QUERY_ID)
)`);
    combinedSource = "combined";
  }
  ctes.push(`search_ids AS (
    SELECT DISTINCT QUERY_ID FROM ${AU}.CORTEX_SEARCH_BATCH_QUERY_USAGE_HISTORY
)`);
  ctes.push(`aif_ids AS (
    SELECT DISTINCT QUERY_ID FROM ${AU}.CORTEX_AI_FUNCTIONS_USAGE_HISTORY
)`);
  ctes.push(classifiedCte(combinedSource));
  return { sql: "WITH " + ctes.join(",\n") + FINAL_SELECT, params };
}

// Full two-path statement: attribution UNION metering.
export function warehouseCreditFactsSql(start: IsoDate, end: IsoDate): Statement {
  return warehouseCreditFactsSqlImpl(start, end, true);
}

// Attribution-only fallback for when metering is inaccessible.
export function warehouseCreditFactsAttributionOnlySql(start: IsoDate, end: IsoDate): Statement {
  return warehouseCreditFactsSqlImpl(start, end, false);
}

const BODY_ATTR = `attr AS (
    SELECT
        QUERY_ID,
        ANY_VALUE(QUERY_TAG)      AS QUERY_TAG,
        ANY_VALUE(WAREHOUSE_NAME) AS WAREHOUSE_NAME,
        SUM(NVL(CREDITS_ATTRIBUTED_COMPUTE, 0)) AS COMPUTE_CREDITS,
        0::FLOAT                  AS CLOUD_SERVICES_CREDITS,
        'ATTRIBUTION'             AS SOURCE_VIEW,
        ANY_VALUE(${utcDay("START_TIME")}) AS USAGE_DATE
    FROM ${AU}.QUERY_ATTRIBUTION_HISTORY
    GROUP BY QUERY_ID
)`;

const BODY_IDS = `search_ids AS (
    SELECT DISTINCT QUERY_ID FROM ${AU}.CORTEX_SEARCH_BATCH_QUERY_USAGE_HISTORY
    WHERE START_TIME >= DATEADD('day', -365, CONVERT_TIMEZONE('UTC', CURRENT_TIMESTAMP()))
),
aif_ids AS (
    SELECT DISTINCT QUERY_ID FROM ${AU}.CORTEX_AI_FUNCTIONS_USAGE_HISTORY
)`;

// Scheduled-table body: full (attribution plus metering), all history.
export function warehouseBodySqlFull(): string {
  return `
WITH ${BODY_ATTR},
meter AS (
    SELECT
        QUERY_ID,
        ANY_VALUE(QUERY_TAG)      AS QUERY_TAG,
        ANY_VALUE(WAREHOUSE_NAME) AS WAREHOUSE_NAME,
        SUM(NVL(CREDITS_USED_COMPUTE, 0))         AS COMPUTE_CREDITS,
        SUM(NVL(CREDITS_USED_CLOUD_SERVICES, 0))::FLOAT  AS CLOUD_SERVICES_CREDITS,
        'METERING'                AS SOURCE_VIEW,
        ANY_VALUE(${utcDay("QUERY_START_TIME")}) AS USAGE_DATE
    FROM ${AU}.QUERY_METERING_HISTORY
    WHERE WAREHOUSE_NAME IS NOT NULL
    GROUP BY QUERY_ID
),
combined AS (
    SELECT * FROM attr
    UNION ALL
    SELECT m.* FROM meter m
    WHERE NOT EXISTS (SELECT 1 FROM attr a WHERE a.QUERY_ID = m.QUERY_ID)
),
${BODY_IDS},
${classifiedCte("combined")}${FINAL_SELECT}`;
}

// Scheduled-table body: attribution only, no metering CTE.
export function warehouseBodySqlAttributionOnly(): string {
  return `
WITH ${BODY_ATTR},
${BODY_IDS},
${classifiedCte("attr")}${FINAL_SELECT}`;
}

export function warehouseBodyForVariant(variant: "full" | "attribution_only"): string {
  return variant === "full" ? warehouseBodySqlFull() : warehouseBodySqlAttributionOnly();
}
