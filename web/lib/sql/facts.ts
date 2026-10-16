// Fact builders: one statement per surface, filtered only by date, plus the
// window-independent model-rate and user-tag statements.
// Port of utils/queries.py. Statement text is copied verbatim, including
// whitespace, so the golden parity test can compare byte for byte.
import type { IsoDate } from "@/lib/view-state";
import { addDays } from "@/lib/dates";
import {
  AU,
  COCO_USAGE_VIEW,
  NONE_LABEL,
  RATES_WINDOW_DAYS,
  TOKEN_TYPES,
  TOKEN_TYPE_COLUMNS,
  UNKNOWN,
  type Param,
  type Statement,
  utcDay,
  utcWindow,
  windowParams,
} from "@/lib/sql/core";

const tokCol = (t: string) => TOKEN_TYPE_COLUMNS[t].toLowerCase();

// ---------------------------------------------------------------------------
// Model credit rates
// ---------------------------------------------------------------------------

export interface ModelRate {
  readonly MODEL_NAME: string;
  readonly R_INPUT: number | null;
  readonly R_OUTPUT: number | null;
  readonly R_CACHE_READ: number | null;
  readonly R_CACHE_WRITE: number | null;
}

// Per-model, per-token-type credit rates observed in CoCo history over a bounded
// window. The lower bound is a date literal from Snowflake's UTC today so the
// statement text is stable for the whole UTC day and stays result-cache eligible.
export function modelRatesSql(today: IsoDate): Statement {
  const since = addDays(today, -RATES_WINDOW_DAYS);
  const sql = `
WITH coco_granular AS (
    SELECT
        token_model.value AS tg,
        credit_model.value AS cg,
        token_model.key::STRING AS model_name
    FROM ${AU}.${COCO_USAGE_VIEW} h,
         LATERAL FLATTEN(INPUT => h.TOKENS_GRANULAR) token_model,
         LATERAL FLATTEN(INPUT => h.CREDITS_GRANULAR) credit_model
    WHERE h.USAGE_TIME >= ?::TIMESTAMP_TZ
      AND token_model.key = credit_model.key
)
SELECT
    model_name,
    SUM(cg:input::FLOAT)             / NULLIF(SUM(tg:input::NUMBER), 0)             AS r_input,
    SUM(cg:output::FLOAT)            / NULLIF(SUM(tg:output::NUMBER), 0)            AS r_output,
    SUM(cg:cache_read_input::FLOAT)  / NULLIF(SUM(tg:cache_read_input::NUMBER), 0)  AS r_cache_read,
    SUM(cg:cache_write_input::FLOAT) / NULLIF(SUM(tg:cache_write_input::NUMBER), 0) AS r_cache_write
FROM coco_granular
WHERE model_name IS NOT NULL
GROUP BY model_name
`;
  return { sql, params: [`${since} 00:00:00 +0000`] };
}

// Parameterless Model Rates aggregate body with a 90-day UTC cutoff.
export function modelRatesBodySql(): string {
  return `
WITH coco_granular AS (
    SELECT
        token_model.value AS tg,
        credit_model.value AS cg,
        token_model.key::STRING AS model_name
    FROM ${AU}.${COCO_USAGE_VIEW} h,
         LATERAL FLATTEN(INPUT => h.TOKENS_GRANULAR) token_model,
         LATERAL FLATTEN(INPUT => h.CREDITS_GRANULAR) credit_model
    WHERE h.USAGE_TIME >= DATE_TRUNC(
        'day', DATEADD('day', -${RATES_WINDOW_DAYS}, CONVERT_TIMEZONE('UTC', CURRENT_TIMESTAMP()))
    )
      AND token_model.key = credit_model.key
)
SELECT
    model_name,
    SUM(cg:input::FLOAT)             / NULLIF(SUM(tg:input::NUMBER), 0)             AS r_input,
    SUM(cg:output::FLOAT)            / NULLIF(SUM(tg:output::NUMBER), 0)            AS r_output,
    SUM(cg:cache_read_input::FLOAT)  / NULLIF(SUM(tg:cache_read_input::NUMBER), 0)  AS r_cache_read,
    SUM(cg:cache_write_input::FLOAT) / NULLIF(SUM(tg:cache_write_input::NUMBER), 0) AS r_cache_write
FROM coco_granular
WHERE model_name IS NOT NULL
GROUP BY model_name
`;
}

// The model_rates CTE with every rate bound; models with no rates at all are dropped.
export function ratesCte(rates: readonly ModelRate[]): { sql: string; params: Param[] } {
  const params: Param[] = [];
  const rows: string[] = [];
  for (const rate of rates) {
    const values: Param[] = [rate.R_INPUT, rate.R_OUTPUT, rate.R_CACHE_READ, rate.R_CACHE_WRITE];
    if (values.every((v) => v === null)) continue;
    params.push(rate.MODEL_NAME, ...values);
    rows.push("(?, ?, ?, ?, ?)");
  }
  if (rows.length === 0) {
    return {
      sql: `
model_rates AS (
    SELECT NULL::STRING AS model_name, NULL::FLOAT AS r_input, NULL::FLOAT AS r_output,
           NULL::FLOAT AS r_cache_read, NULL::FLOAT AS r_cache_write
    WHERE 1 = 0
)`,
      params,
    };
  }
  return {
    sql: `
model_rates AS (
    SELECT
        column1::STRING AS model_name,
        column2::FLOAT  AS r_input,
        column3::FLOAT  AS r_output,
        column4::FLOAT  AS r_cache_read,
        column5::FLOAT  AS r_cache_write
    FROM VALUES
        ${rows.join(", ")}
)`,
    params,
  };
}

const REST_EST_CREDITS = `
      NVL(r.TOKENS_GRANULAR:input::NUMBER, 0)             * NVL(w.r_input, 0)
    + NVL(r.TOKENS_GRANULAR:output::NUMBER, 0)            * NVL(w.r_output, 0)
    + NVL(r.TOKENS_GRANULAR:cache_read_input::NUMBER, 0)  * NVL(w.r_cache_read, 0)
    + NVL(r.TOKENS_GRANULAR:cache_write_input::NUMBER, 0) * NVL(w.r_cache_write, 0)`;

export const AI_FN_TOKENS = `
    NVL(REDUCE(
        FILTER(a.METRICS, x -> x:key:unit::STRING = 'tokens'),
        0,
        (acc, x) -> acc + x:value::NUMBER
    ), 0)`;

// ---------------------------------------------------------------------------
// Fact builders
// ---------------------------------------------------------------------------

// CoCo: one row per delivery surface, day, user, model, role and inference region.
export function cocoFactsSql(start: IsoDate, end: IsoDate): Statement {
  const tokenSums = TOKEN_TYPES.map((t) => `    SUM(NVL(tg:${t}::NUMBER, 0)) AS ${tokCol(t)}`).join(
    ",\n",
  );
  const sql = `
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
    WHERE ${utcWindow("h.USAGE_TIME")}
      AND token_model.key = credit_model.key
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
  return { sql, params: windowParams(start, end) };
}

// Cortex AI Functions: credits from the view, tokens summed out of the METRICS array.
export function aiFnFactsSql(start: IsoDate, end: IsoDate): Statement {
  const sql = `
SELECT
    ${utcDay("a.START_TIME")}          AS usage_date,
    NVL(u.NAME, '${UNKNOWN}')                     AS user_name,
    NVL(a.FUNCTION_NAME, '${UNKNOWN}')            AS function_name,
    NVL(a.MODEL_NAME, '${UNKNOWN}')               AS model_name,
    NVL(NULLIF(a.QUERY_TAG, ''), '${NONE_LABEL}') AS query_tag,
    COUNT(*)                                    AS calls,
    SUM(${AI_FN_TOKENS.trim()}) AS tokens,
    SUM(NVL(a.CREDITS, 0))                      AS credits,
    SUM(CASE WHEN a.IS_COMPLETED = FALSE THEN 1 ELSE 0 END) AS incomplete_calls
FROM ${AU}.CORTEX_AI_FUNCTIONS_USAGE_HISTORY a
LEFT JOIN ${AU}.USERS u ON u.USER_ID = a.USER_ID
WHERE ${utcWindow("a.START_TIME")}
GROUP BY ALL
`;
  return { sql, params: windowParams(start, end) };
}

// Cortex Inference API with credits estimated from bound per-model rates.
export function restFactsSql(start: IsoDate, end: IsoDate, rates: readonly ModelRate[]): Statement {
  const cte = ratesCte(rates);
  const params: Param[] = [...cte.params, ...windowParams(start, end)];
  const sql = `WITH ${cte.sql}
SELECT
    ${utcDay("r.START_TIME")}                AS usage_date,
    NVL(u.NAME, '${UNKNOWN}')                           AS user_name,
    NVL(r.MODEL_NAME, '${UNKNOWN}')                     AS model_name,
    NVL(NULLIF(r.INFERENCE_REGION, ''), '${UNKNOWN}')   AS inference_region,
    NVL(NULLIF(r.QUERY_TAG, ''), '${NONE_LABEL}')       AS query_tag,
    COUNT(*)                                          AS calls,
    SUM(NVL(r.TOKENS, 0))                             AS tokens,
    SUM(${REST_EST_CREDITS.trim()}) AS credits,
    SUM(NVL(r.TOKENS_GRANULAR:input::NUMBER, 0))             AS tok_input,
    SUM(NVL(r.TOKENS_GRANULAR:output::NUMBER, 0))            AS tok_output,
    SUM(NVL(r.TOKENS_GRANULAR:cache_read_input::NUMBER, 0))  AS tok_cache_read_input,
    SUM(NVL(r.TOKENS_GRANULAR:cache_write_input::NUMBER, 0)) AS tok_cache_write_input,
    MAX(CASE WHEN w.model_name IS NULL THEN 1 ELSE 0 END)    AS is_unrated
FROM ${AU}.CORTEX_REST_API_USAGE_HISTORY r
LEFT JOIN ${AU}.USERS u ON u.USER_ID = r.USER_ID
LEFT JOIN model_rates w ON w.model_name = r.MODEL_NAME
WHERE ${utcWindow("r.START_TIME")}
GROUP BY ALL
`;
  return { sql, params };
}

// Cortex Agents: root versus child requests split on PARENT_REQUEST_ID.
export function agentFactsSql(start: IsoDate, end: IsoDate): Statement {
  const sql = `
SELECT
    ${utcDay("START_TIME")}            AS usage_date,
    NVL(USER_NAME, '${UNKNOWN}')                  AS user_name,
    NVL(AGENT_NAME, '${UNKNOWN}')                 AS agent_name,
    NVL(AGENT_DATABASE_NAME, '${UNKNOWN}') || '.' ||
    NVL(AGENT_SCHEMA_NAME, '${UNKNOWN}')   || '.' ||
    NVL(AGENT_NAME, '${UNKNOWN}')                 AS agent_fqn,
    NVL(ARRAY_TO_STRING(
        TRANSFORM(AGENT_TAGS, t -> t:tag_name::STRING || ' = ' || t:tag_value::STRING),
        '|'
    ), '')                                      AS agent_tag_labels,
    COUNT(*)                                    AS calls,
    SUM(NVL(TOKENS, 0))                         AS tokens,
    SUM(NVL(TOKEN_CREDITS, 0))                  AS credits,
    SUM(CASE WHEN PARENT_REQUEST_ID IS NULL     THEN 1 ELSE 0 END) AS root_requests,
    SUM(CASE WHEN PARENT_REQUEST_ID IS NOT NULL THEN 1 ELSE 0 END) AS child_requests
FROM ${AU}.CORTEX_AGENT_USAGE_HISTORY
WHERE ${utcWindow("START_TIME")}
GROUP BY ALL
`;
  return { sql, params: windowParams(start, end) };
}

// CoWork fact grain, keeping SQL and AI function credits as their own columns.
export function coworkFactsSql(start: IsoDate, end: IsoDate): Statement {
  const sql = `
SELECT
    ${utcDay("START_TIME")}                     AS usage_date,
    NVL(USER_NAME, '${UNKNOWN}')                  AS user_name,
    NVL(SNOWFLAKE_COWORK_NAME, '${UNKNOWN}')      AS cowork_name,
    NVL(AGENT_DATABASE_NAME, '${UNKNOWN}') || '.' ||
    NVL(AGENT_SCHEMA_NAME, '${UNKNOWN}')   || '.' ||
    NVL(AGENT_NAME, '${UNKNOWN}')                 AS agent_fqn,
    NVL(AGENT_NAME, '${UNKNOWN}')                 AS agent_name,
    NVL(METADATA:role_name::STRING, '${UNKNOWN}') AS role_name,
    NVL(ARRAY_TO_STRING(
        TRANSFORM(AGENT_TAGS, t -> t:tag_name::STRING || ' = ' || t:tag_value::STRING),
        '|'
    ), '')                                      AS agent_tag_labels,
    COUNT(*)                                    AS calls,
    SUM(NVL(TOKENS, 0))                         AS tokens,
    SUM(NVL(TOKEN_CREDITS, 0))                  AS credits,
    SUM(NVL(METADATA:sql_query_credits::FLOAT, 0))    AS sql_query_credits,
    SUM(NVL(METADATA:ai_functions_credits::FLOAT, 0)) AS ai_functions_credits,
    SUM(CASE WHEN PARENT_REQUEST_ID IS NULL     THEN 1 ELSE 0 END) AS root_requests,
    SUM(CASE WHEN PARENT_REQUEST_ID IS NOT NULL THEN 1 ELSE 0 END) AS child_requests
FROM ${AU}.SNOWFLAKE_COWORK_USAGE_HISTORY
WHERE ${utcWindow("START_TIME")}
GROUP BY ALL
`;
  return { sql, params: windowParams(start, end) };
}

// Shared pieces of the CoWork service-mix statement and its scheduled body.
export function coworkServiceMixParts() {
  return {
    tokenSums: TOKEN_TYPES.map((t) => `    SUM(NVL(m.value:${t}::NUMBER, 0)) AS ${tokCol(t)}`).join(
      ",\n",
    ),
    creditSums: TOKEN_TYPES.map((t) => `NVL(cm.value:${t}::FLOAT, 0)`).join(" + "),
    totalTokens: TOKEN_TYPES.map(tokCol).join(" + "),
    outerTokenSums: TOKEN_TYPES.map((t) => `    SUM(t.${tokCol(t)}) AS ${tokCol(t)}`).join(",\n"),
  };
}

// CoWork tokens and credits per service type and model; window bounds bound twice.
export function coworkServiceMixSql(start: IsoDate, end: IsoDate): Statement {
  const p = coworkServiceMixParts();
  const sql = `
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
    WHERE ${utcWindow("h.START_TIME")}
      AND s.key <> 'start_time'
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
    WHERE ${utcWindow("h.START_TIME")}
      AND s.key <> 'start_time'
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
  const window = windowParams(start, end);
  return { sql, params: [...window, ...window] };
}

// Cortex Search daily usage per service, consumption type and model.
export function searchDailyFactsSql(start: IsoDate, end: IsoDate): Statement {
  const sql = `
SELECT
    ${utcDay("USAGE_DATE")}                       AS usage_date,
    NVL(SERVICE_NAME, '${UNKNOWN}')                AS service_name,
    NVL(CONSUMPTION_TYPE, '${UNKNOWN}')             AS consumption_type,
    NVL(MODEL_NAME, '${NONE_LABEL}')                AS model_name,
    COUNT(*)                                       AS calls,
    SUM(TRY_CAST(TOKENS AS NUMBER))                AS tokens,
    SUM(NVL(CREDITS, 0))                            AS credits
FROM ${AU}.CORTEX_SEARCH_DAILY_USAGE_HISTORY
WHERE ${utcWindow("USAGE_DATE")}
GROUP BY ALL
`;
  return { sql, params: windowParams(start, end) };
}

// Cortex Search hourly serving credits per service.
export function searchServingFactsSql(start: IsoDate, end: IsoDate): Statement {
  const sql = `
SELECT
    ${utcDay("START_TIME")}                                          AS usage_date,
    DATE_TRUNC('hour', CONVERT_TIMEZONE('UTC', START_TIME))          AS usage_hour,
    NVL(SERVICE_NAME, '${UNKNOWN}')                                    AS service_name,
    COUNT(*)                                                          AS calls,
    SUM(NVL(CREDITS, 0))                                              AS credits
FROM ${AU}.CORTEX_SEARCH_SERVING_USAGE_HISTORY
WHERE ${utcWindow("START_TIME")}
GROUP BY ALL
`;
  return { sql, params: windowParams(start, end) };
}

// Cortex Search batch-query credits per QUERY_ID, service, consumption type and model.
export function searchBatchFactsSql(start: IsoDate, end: IsoDate): Statement {
  const sql = `
SELECT
    QUERY_ID,
    ${utcDay("START_TIME")}                       AS usage_date,
    NVL(SERVICE_NAME, '${UNKNOWN}')                AS service_name,
    NVL(CONSUMPTION_TYPE, '${UNKNOWN}')             AS consumption_type,
    NVL(MODEL_NAME, '${NONE_LABEL}')                AS model_name,
    SUM(TRY_CAST(TOKENS AS NUMBER))                AS tokens,
    SUM(NVL(CREDITS_USED, 0))                      AS credits
FROM ${AU}.CORTEX_SEARCH_BATCH_QUERY_USAGE_HISTORY
WHERE ${utcWindow("START_TIME")}
GROUP BY ALL
`;
  return { sql, params: windowParams(start, end) };
}

// Current USER-domain tag references; no parameters.
export function userTagsSql(): string {
  return `
SELECT
    TAG_NAME  AS tag_name,
    TAG_VALUE AS tag_value,
    TAG_NAME || ' = ' || TAG_VALUE AS tag_label,
    OBJECT_NAME AS user_name
FROM ${AU}.TAG_REFERENCES
WHERE DOMAIN = 'USER'
  AND TAG_VALUE IS NOT NULL
ORDER BY tag_name, tag_value, user_name
`;
}
