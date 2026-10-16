// Acceleration layer: column contracts, scheduled-table bodies, the DT registry,
// DDL builders, and the aggregate read statements. Port of the DT sections of
// utils/queries.py and the DDL builders in utils/config.py.
import type { IsoDate } from "@/lib/view-state";
import { addDays } from "@/lib/dates";
import {
  DEP_COWORK_SERVICE_MIX,
  DEP_SEARCH_BATCH,
  DEP_SEARCH_DAILY,
  DEP_SEARCH_SERVING,
  SURFACE_AGENTS,
  SURFACE_AI_FUNCTIONS,
  SURFACE_COCO,
  SURFACE_COWORK,
  SURFACE_MODEL_RATES,
  SURFACE_WAREHOUSE_ATTRIBUTION,
} from "@/lib/plans";
import { TASK_RETENTION_DAYS, type Settings, taskWarehouse } from "@/lib/settings";
import {
  AU,
  COCO_USAGE_VIEW,
  NONE_LABEL,
  TOKEN_TYPES,
  TOKEN_TYPE_COLUMNS,
  UNKNOWN,
  type Statement,
  fqn,
  quoteIdentifier,
  surfaceToSuffix,
  utcDay,
} from "@/lib/sql/core";
import { AI_FN_TOKENS, coworkServiceMixParts, modelRatesBodySql } from "@/lib/sql/facts";
import { warehouseBodySqlAttributionOnly, warehouseBodySqlFull } from "@/lib/sql/warehouse";

// Explicit column contracts for every DT-eligible surface, in SELECT order.
export const DT_COLUMNS: Readonly<Record<string, readonly string[]>> = {
  [SURFACE_COCO]: [
    "SUB_SURFACE",
    "USAGE_DATE",
    "USER_NAME",
    "MODEL_NAME",
    "ROLE_NAME",
    "INFERENCE_REGION",
    "CALLS",
    "TOKENS",
    "CREDITS",
    "TOK_INPUT",
    "TOK_OUTPUT",
    "TOK_CACHE_READ_INPUT",
    "TOK_CACHE_WRITE_INPUT",
  ],
  [SURFACE_AI_FUNCTIONS]: [
    "USAGE_DATE",
    "USER_NAME",
    "FUNCTION_NAME",
    "MODEL_NAME",
    "QUERY_TAG",
    "CALLS",
    "TOKENS",
    "CREDITS",
    "INCOMPLETE_CALLS",
  ],
  [SURFACE_AGENTS]: [
    "USAGE_DATE",
    "USER_NAME",
    "AGENT_NAME",
    "AGENT_FQN",
    "AGENT_TAG_LABELS",
    "CALLS",
    "TOKENS",
    "CREDITS",
    "ROOT_REQUESTS",
    "CHILD_REQUESTS",
  ],
  [SURFACE_COWORK]: [
    "USAGE_DATE",
    "USER_NAME",
    "COWORK_NAME",
    "AGENT_FQN",
    "AGENT_NAME",
    "ROLE_NAME",
    "AGENT_TAG_LABELS",
    "CALLS",
    "TOKENS",
    "CREDITS",
    "SQL_QUERY_CREDITS",
    "AI_FUNCTIONS_CREDITS",
    "ROOT_REQUESTS",
    "CHILD_REQUESTS",
  ],
  [DEP_COWORK_SERVICE_MIX]: [
    "USAGE_DATE",
    "USER_NAME",
    "SERVICE_TYPE",
    "MODEL_NAME",
    "CALLS",
    "TOKENS",
    "CREDITS",
    "TOK_INPUT",
    "TOK_OUTPUT",
    "TOK_CACHE_READ_INPUT",
    "TOK_CACHE_WRITE_INPUT",
  ],
  [DEP_SEARCH_DAILY]: [
    "USAGE_DATE",
    "SERVICE_NAME",
    "CONSUMPTION_TYPE",
    "MODEL_NAME",
    "CALLS",
    "TOKENS",
    "CREDITS",
  ],
  [DEP_SEARCH_SERVING]: ["USAGE_DATE", "USAGE_HOUR", "SERVICE_NAME", "CALLS", "CREDITS"],
  [DEP_SEARCH_BATCH]: [
    "QUERY_ID",
    "USAGE_DATE",
    "SERVICE_NAME",
    "CONSUMPTION_TYPE",
    "MODEL_NAME",
    "TOKENS",
    "CREDITS",
  ],
  [SURFACE_WAREHOUSE_ATTRIBUTION]: [
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
  ],
  [SURFACE_MODEL_RATES]: ["MODEL_NAME", "R_INPUT", "R_OUTPUT", "R_CACHE_READ", "R_CACHE_WRITE"],
};

// ---------------------------------------------------------------------------
// Scheduled-table bodies: the windowed SELECT without its date predicate.
// ---------------------------------------------------------------------------

const tokCol = (t: string) => TOKEN_TYPE_COLUMNS[t].toLowerCase();

export function cocoBodySql(): string {
  const tokenSums = TOKEN_TYPES.map((t) => `    SUM(NVL(tg:${t}::NUMBER, 0)) AS ${tokCol(t)}`).join(
    ",\n",
  );
  return `
WITH coco_granular AS (
    SELECT
        LOWER(h.INTERFACE) AS sub_surface,
        ${utcDay("h.USAGE_TIME")} AS usage_date,
        NVL(h.USER_NAME, '${UNKNOWN}') AS user_name,
        NVL(h.METADATA:role_name::STRING, '${UNKNOWN}') AS role_name,
        NVL(h.METADATA:inference_region::STRING, '${UNKNOWN}') AS inference_region,
        token_model.key::STRING AS model_name,
        token_model.value AS tg,
        credit_model.value AS cg
    FROM ${AU}.${COCO_USAGE_VIEW} h,
         LATERAL FLATTEN(INPUT => h.TOKENS_GRANULAR) token_model,
         LATERAL FLATTEN(INPUT => h.CREDITS_GRANULAR) credit_model
    WHERE token_model.key = credit_model.key
)
SELECT
    sub_surface,
    usage_date,
    user_name,
    model_name,
    role_name,
    inference_region,
    COUNT(*)     AS calls,
    SUM(NVL(tg:input::NUMBER, 0) + NVL(tg:output::NUMBER, 0) +
        NVL(tg:cache_read_input::NUMBER, 0) + NVL(tg:cache_write_input::NUMBER, 0)) AS tokens,
    SUM(NVL(cg:input::FLOAT, 0) + NVL(cg:output::FLOAT, 0) +
        NVL(cg:cache_read_input::FLOAT, 0) + NVL(cg:cache_write_input::FLOAT, 0)) AS credits,
${tokenSums}
FROM coco_granular
GROUP BY ALL
`;
}

export function aiFnBodySql(): string {
  return `
SELECT
    ${utcDay("a.START_TIME")}                                     AS usage_date,
    NVL(u.NAME, '${UNKNOWN}')                                      AS user_name,
    NVL(a.FUNCTION_NAME, '${UNKNOWN}')                             AS function_name,
    NVL(a.MODEL_NAME, '${UNKNOWN}')                                AS model_name,
    NVL(NULLIF(a.QUERY_TAG, ''), '${NONE_LABEL}')                  AS query_tag,
    COUNT(*)                                                       AS calls,
    SUM(${AI_FN_TOKENS.trim()})                                   AS tokens,
    SUM(NVL(a.CREDITS, 0))                                        AS credits,
    SUM(CASE WHEN a.IS_COMPLETED = FALSE THEN 1 ELSE 0 END)       AS incomplete_calls
FROM ${AU}.CORTEX_AI_FUNCTIONS_USAGE_HISTORY a
LEFT JOIN ${AU}.USERS u ON u.USER_ID = a.USER_ID
GROUP BY ALL
`;
}

export function agentBodySql(): string {
  return `
SELECT
    ${utcDay("START_TIME")}                            AS usage_date,
    NVL(USER_NAME, '${UNKNOWN}')                        AS user_name,
    NVL(AGENT_NAME, '${UNKNOWN}')                       AS agent_name,
    NVL(AGENT_DATABASE_NAME, '${UNKNOWN}') || '.' ||
    NVL(AGENT_SCHEMA_NAME, '${UNKNOWN}')   || '.' ||
    NVL(AGENT_NAME, '${UNKNOWN}')                       AS agent_fqn,
    NVL(ARRAY_TO_STRING(
        TRANSFORM(AGENT_TAGS, t -> t:tag_name::STRING || ' = ' || t:tag_value::STRING),
        '|'
    ), '')                                            AS agent_tag_labels,
    COUNT(*)                                           AS calls,
    SUM(NVL(TOKENS, 0))                               AS tokens,
    SUM(NVL(TOKEN_CREDITS, 0))                         AS credits,
    SUM(CASE WHEN PARENT_REQUEST_ID IS NULL     THEN 1 ELSE 0 END) AS root_requests,
    SUM(CASE WHEN PARENT_REQUEST_ID IS NOT NULL THEN 1 ELSE 0 END) AS child_requests
FROM ${AU}.CORTEX_AGENT_USAGE_HISTORY
GROUP BY ALL
`;
}

export function coworkBodySql(): string {
  return `
SELECT
    ${utcDay("START_TIME")}                              AS usage_date,
    NVL(USER_NAME, '${UNKNOWN}')                          AS user_name,
    NVL(SNOWFLAKE_COWORK_NAME, '${UNKNOWN}')               AS cowork_name,
    NVL(AGENT_DATABASE_NAME, '${UNKNOWN}') || '.' ||
    NVL(AGENT_SCHEMA_NAME, '${UNKNOWN}')   || '.' ||
    NVL(AGENT_NAME, '${UNKNOWN}')                         AS agent_fqn,
    NVL(AGENT_NAME, '${NONE_LABEL}')                      AS agent_name,
    NVL(METADATA:role_name::STRING, '${UNKNOWN}')         AS role_name,
    NVL(ARRAY_TO_STRING(
        TRANSFORM(AGENT_TAGS, t -> t:tag_name::STRING || ' = ' || t:tag_value::STRING),
        '|'
    ), '')                                              AS agent_tag_labels,
    COUNT(*)                                             AS calls,
    SUM(NVL(TOKENS, 0))                                 AS tokens,
    SUM(NVL(TOKEN_CREDITS, 0))                           AS credits,
    SUM(NVL(METADATA:sql_query_credits::FLOAT, 0))       AS sql_query_credits,
    SUM(NVL(METADATA:ai_functions_credits::FLOAT, 0))    AS ai_functions_credits,
    SUM(CASE WHEN PARENT_REQUEST_ID IS NULL     THEN 1 ELSE 0 END) AS root_requests,
    SUM(CASE WHEN PARENT_REQUEST_ID IS NOT NULL THEN 1 ELSE 0 END) AS child_requests
FROM ${AU}.SNOWFLAKE_COWORK_USAGE_HISTORY
GROUP BY ALL
`;
}

export function coworkServiceMixBodySql(): string {
  const p = coworkServiceMixParts();
  return `
WITH tokens AS (
    SELECT
        ${utcDay("h.START_TIME")} AS usage_date,
        NVL(h.USER_NAME, '${UNKNOWN}') AS user_name,
        rq.key   AS request_id,
        s.key    AS service_type,
        m.key    AS model_name,
${p.tokenSums}
    FROM ${AU}.SNOWFLAKE_COWORK_USAGE_HISTORY h,
         LATERAL FLATTEN(input => h.TOKENS_GRANULAR) e,
         LATERAL FLATTEN(input => e.value) rq,
         LATERAL FLATTEN(input => rq.value) s,
         LATERAL FLATTEN(input => s.value) m
    WHERE s.key <> 'start_time'
    GROUP BY ALL
),
credits AS (
    SELECT
        rq.key AS request_id,
        s.key  AS service_type,
        cm.key AS model_name,
        SUM(${p.creditSums}) AS credits
    FROM ${AU}.SNOWFLAKE_COWORK_USAGE_HISTORY h,
         LATERAL FLATTEN(input => h.CREDITS_GRANULAR) e,
         LATERAL FLATTEN(input => e.value) rq,
         LATERAL FLATTEN(input => rq.value) s,
         LATERAL FLATTEN(input => s.value) cm
    WHERE s.key <> 'start_time'
    GROUP BY ALL
)
SELECT
    t.usage_date,
    t.user_name,
    t.service_type,
    t.model_name,
    COUNT(*)                     AS calls,
    SUM(${p.totalTokens}) AS tokens,
    SUM(NVL(c.credits, 0))       AS credits,
${p.outerTokenSums}
FROM tokens t
LEFT JOIN credits c
       ON c.request_id = t.request_id
      AND c.service_type = t.service_type
      AND c.model_name = t.model_name
GROUP BY ALL
`;
}

export function searchDailyBodySql(): string {
  return `
SELECT
    ${utcDay("USAGE_DATE")}                              AS usage_date,
    NVL(SERVICE_NAME, '${UNKNOWN}')                       AS service_name,
    NVL(CONSUMPTION_TYPE, '${UNKNOWN}')                   AS consumption_type,
    NVL(MODEL_NAME, '${NONE_LABEL}')                      AS model_name,
    COUNT(*)                                             AS calls,
    SUM(TRY_CAST(TOKENS AS NUMBER))                      AS tokens,
    SUM(NVL(CREDITS, 0))                                 AS credits
FROM ${AU}.CORTEX_SEARCH_DAILY_USAGE_HISTORY
GROUP BY ALL
`;
}

export function searchServingBodySql(): string {
  return `
SELECT
    ${utcDay("START_TIME")}                                          AS usage_date,
    DATE_TRUNC('hour', CONVERT_TIMEZONE('UTC', START_TIME))          AS usage_hour,
    NVL(SERVICE_NAME, '${UNKNOWN}')                                    AS service_name,
    COUNT(*)                                                          AS calls,
    SUM(NVL(CREDITS, 0))                                              AS credits
FROM ${AU}.CORTEX_SEARCH_SERVING_USAGE_HISTORY
GROUP BY ALL
`;
}

export function searchBatchBodySql(): string {
  return `
SELECT
    QUERY_ID,
    ${utcDay("START_TIME")}                              AS usage_date,
    NVL(SERVICE_NAME, '${UNKNOWN}')                       AS service_name,
    NVL(CONSUMPTION_TYPE, '${UNKNOWN}')                   AS consumption_type,
    NVL(MODEL_NAME, '${NONE_LABEL}')                      AS model_name,
    SUM(TRY_CAST(TOKENS AS NUMBER))                     AS tokens,
    SUM(NVL(CREDITS_USED, 0))                           AS credits
FROM ${AU}.CORTEX_SEARCH_BATCH_QUERY_USAGE_HISTORY
GROUP BY ALL
`;
}

// ---------------------------------------------------------------------------
// Registry
// ---------------------------------------------------------------------------

export interface DtBodySpec {
  readonly surfaceKey: string;
  readonly body: () => string;
  readonly columns: readonly string[];
  readonly clusterColumn: string | null;
  readonly applyOuterRetention: boolean;
  readonly degradedFlagKey?: string;
}

function spec(
  surfaceKey: string,
  body: () => string,
  overrides: Partial<DtBodySpec> = {},
): DtBodySpec {
  return {
    surfaceKey,
    body,
    columns: DT_COLUMNS[surfaceKey],
    clusterColumn: "USAGE_DATE",
    applyOuterRetention: true,
    ...overrides,
  };
}

// The registry of DT-eligible surfaces. Cortex Inference API is deliberately absent.
export const DT_BODY_REGISTRY: Readonly<Record<string, DtBodySpec>> = {
  [SURFACE_COCO]: spec(SURFACE_COCO, cocoBodySql),
  [SURFACE_AI_FUNCTIONS]: spec(SURFACE_AI_FUNCTIONS, aiFnBodySql),
  [SURFACE_AGENTS]: spec(SURFACE_AGENTS, agentBodySql),
  [SURFACE_COWORK]: spec(SURFACE_COWORK, coworkBodySql),
  [DEP_COWORK_SERVICE_MIX]: spec(DEP_COWORK_SERVICE_MIX, coworkServiceMixBodySql),
  [DEP_SEARCH_DAILY]: spec(DEP_SEARCH_DAILY, searchDailyBodySql),
  [DEP_SEARCH_SERVING]: spec(DEP_SEARCH_SERVING, searchServingBodySql),
  [DEP_SEARCH_BATCH]: spec(DEP_SEARCH_BATCH, searchBatchBodySql),
  [SURFACE_WAREHOUSE_ATTRIBUTION]: spec(SURFACE_WAREHOUSE_ATTRIBUTION, warehouseBodySqlFull, {
    applyOuterRetention: false,
    degradedFlagKey: "wh_degraded",
  }),
  [SURFACE_MODEL_RATES]: spec(SURFACE_MODEL_RATES, modelRatesBodySql, {
    clusterColumn: null,
    applyOuterRetention: false,
  }),
};

export const DT_SURFACES = Object.keys(DT_BODY_REGISTRY);

export { warehouseBodySqlAttributionOnly };

// ---------------------------------------------------------------------------
// Object names and reads
// ---------------------------------------------------------------------------

export function aggregateTableName(surfaceKey: string, prefix: string): string {
  return `${prefix}_${surfaceToSuffix(surfaceKey)}`;
}

export function refreshTaskName(surfaceKey: string, prefix: string): string {
  return `${prefix}_TASK_${surfaceToSuffix(surfaceKey)}`;
}

export function aggregateTableFqn(surfaceKey: string, settings: Settings): string {
  return fqn(
    settings.database,
    settings.schema,
    aggregateTableName(surfaceKey, settings.objectPrefix),
  );
}

export function refreshTaskFqn(surfaceKey: string, settings: Settings): string {
  return fqn(
    settings.database,
    settings.schema,
    refreshTaskName(surfaceKey, settings.objectPrefix),
  );
}

// SELECT against a scheduled table for the window, half-open on USAGE_DATE.
export function dtReadSql(
  surfaceKey: string,
  start: IsoDate,
  end: IsoDate,
  settings: Settings,
): Statement {
  const cols = DT_COLUMNS[surfaceKey].join(", ");
  const sql = `SELECT ${cols} FROM ${aggregateTableFqn(surfaceKey, settings)} WHERE USAGE_DATE >= ?::DATE AND USAGE_DATE < ?::DATE`;
  return { sql, params: [start, addDays(end, 1)] };
}

// Explicit-column read of the global Model Rates table.
export function modelRatesDtSql(settings: Settings): Statement {
  const table = `${settings.objectPrefix}_${SURFACE_MODEL_RATES.replaceAll(" ", "_")}`;
  const columns = DT_COLUMNS[SURFACE_MODEL_RATES].join(", ");
  return {
    sql: `SELECT ${columns} FROM ${fqn(settings.database, settings.schema, table)}`,
    params: [],
  };
}

// ---------------------------------------------------------------------------
// DDL builders (utils/config.py)
// ---------------------------------------------------------------------------

export function createRefreshWarehouseSql(name: string, size: string): string {
  return (
    `CREATE WAREHOUSE IF NOT EXISTS ${quoteIdentifier(name)} ` +
    `WAREHOUSE_SIZE = '${size.toUpperCase()}' ` +
    "AUTO_SUSPEND = 60 AUTO_RESUME = TRUE INITIALLY_SUSPENDED = TRUE"
  );
}

export function resizeRefreshWarehouseSql(name: string, size: string): string {
  return `ALTER WAREHOUSE ${quoteIdentifier(name)} SET WAREHOUSE_SIZE = '${size.toUpperCase()}'`;
}

// CREATE TABLE with the declared column contract, initially empty.
export function createAggregateTableSql(s: DtBodySpec, settings: Settings): string {
  const table = aggregateTableFqn(s.surfaceKey, settings);
  const colList = s.columns.length ? s.columns.join(", ") : "*";
  const clusterBy = s.clusterColumn ? `\n  CLUSTER BY (${s.clusterColumn})` : "";
  return (
    `CREATE TABLE IF NOT EXISTS ${table}\n` +
    `${clusterBy}\n` +
    `  AS SELECT ${colList} FROM (${s.body()}) _init\n` +
    "  WHERE 1 = 0"
  );
}

// CREATE OR REPLACE TASK running the nightly INSERT OVERWRITE at 01:00 UTC.
export function createRefreshTaskSql(
  s: DtBodySpec,
  settings: Settings,
  lagDays: number = TASK_RETENTION_DAYS,
): string {
  const table = aggregateTableFqn(s.surfaceKey, settings);
  const task = refreshTaskFqn(s.surfaceKey, settings);
  const wh = quoteIdentifier(taskWarehouse(settings));
  const colList = s.columns.length ? s.columns.join(", ") : "*";
  const retention = s.applyOuterRetention
    ? `\nWHERE USAGE_DATE >= DATEADD('day', -${lagDays}, CONVERT_TIMEZONE('UTC', CURRENT_TIMESTAMP())::DATE)`
    : "";
  return (
    `CREATE OR REPLACE TASK ${task}\n` +
    `  WAREHOUSE = ${wh}\n` +
    "  SCHEDULE = 'USING CRON 0 1 * * * UTC'\n" +
    "  AS\n" +
    `INSERT OVERWRITE INTO ${table}\n` +
    `SELECT ${colList} FROM (${s.body()}) _body\n` +
    retention
  );
}

export const suspendTaskSql = (taskFqn: string) => `ALTER TASK ${taskFqn} SUSPEND`;
export const resumeTaskSql = (taskFqn: string) => `ALTER TASK ${taskFqn} RESUME`;
export const executeTaskSql = (taskFqn: string) => `EXECUTE TASK ${taskFqn}`;
export const dropObjectSql = (objectFqn: string, objectType = "TABLE") =>
  `DROP ${objectType} IF EXISTS ${objectFqn}`;
export const suspendWarehouseSql = (whFqn: string) => `ALTER WAREHOUSE ${whFqn} SUSPEND`;
export const resumeWarehouseSql = (whFqn: string) => `ALTER WAREHOUSE ${whFqn} RESUME`;

export function accelerationTableSql(surfaceKey: string, settings: Settings): string {
  return createAggregateTableSql(DT_BODY_REGISTRY[surfaceKey], settings);
}

export function accelerationTaskSql(surfaceKey: string, settings: Settings): string {
  return createRefreshTaskSql(DT_BODY_REGISTRY[surfaceKey], settings);
}

// Labelled lifecycle statements for one surface; "Drop resource" is documentation only.
export function accelerationLifecycleSql(
  surfaceKey: string,
  settings: Settings,
): Record<string, string> {
  const task = refreshTaskFqn(surfaceKey, settings);
  return {
    "Create task warehouse": createRefreshWarehouseSql(
      taskWarehouse(settings),
      settings.warehouseSize,
    ),
    "Create aggregate table": accelerationTableSql(surfaceKey, settings),
    "Create refresh task": accelerationTaskSql(surfaceKey, settings),
    "Enable schedule": resumeTaskSql(task),
    "Run now": executeTaskSql(task),
    "Pause schedule": suspendTaskSql(task),
    "Drop resource": dropObjectSql(task, "TASK"),
  };
}
