-- ==========================================================================
--  Cortex AI Consumption Dashboard -- Query Acceleration Setup (sql/config.sql)
-- ==========================================================================
--
--  Prepare the acceleration layer WITHOUT using the Acceleration Settings page.
--  Run it on a FRESH install: `make acceleration:provision` (see
--  docs/OPERATIONS.md) refuses to run it once the config table holds settings.
--  Tables use CREATE IF NOT EXISTS, but tasks use CREATE OR REPLACE and the
--  settings MERGE overwrites existing values, so a re-run resets an installation.
--
--  What it creates (default names shown):
--    - COST_MGMT_DB.PUBLIC.CORTEX_DASHBOARD_CONFIG   (settings key-value table)
--    - COST_MGMT_WH                                  (task warehouse, XSMALL)
--    - 10 aggregate tables  COST_MGMT_<SURFACE>
--    - 10 scheduled tasks   COST_MGMT_TASK_<SURFACE>  (daily 01:00 UTC)
--
--  It also seeds CORTEX_DASHBOARD_CONFIG with enabled=true and an owned_objects
--  registry listing every object below, so the dashboard immediately reads from
--  the aggregate layer and Acceleration Settings can tear these down cleanly (no
--  "untracked objects" warning).
--
--  Requirements (executing role):
--    CREATE TABLE, CREATE TASK, CREATE WAREHOUSE on the target schema/account,
--    EXECUTE TASK on the account (to resume and run tasks), and read access to
--    SNOWFLAKE.ACCOUNT_USAGE.
--
--  Customizing names: the app defaults to database COST_MGMT_DB, schema PUBLIC,
--  prefix COST_MGMT, warehouse COST_MGMT_WH. To use different names, search and replace
--  them consistently throughout this file AND in the seeded config rows in
--  section 2.
-- ==========================================================================

USE SCHEMA "COST_MGMT_DB"."PUBLIC";

-- ==========================================================================
--  Section 1: Configuration table
-- ==========================================================================
CREATE TABLE IF NOT EXISTS "COST_MGMT_DB"."PUBLIC"."CORTEX_DASHBOARD_CONFIG" (
    KEY         VARCHAR NOT NULL,
    VALUE       VARCHAR,
    UPDATED_AT  TIMESTAMP_TZ DEFAULT CURRENT_TIMESTAMP(),
    PRIMARY KEY (KEY)
);
SELECT '[PASS] Configuration table ready' AS progress;

-- ==========================================================================
--  Section 2: Seed acceleration settings
--  Mirrors what Save in Acceleration Settings writes. owned_objects registers every
--  object this script creates so teardown and reconciliation stay accurate.
-- ==========================================================================
MERGE INTO "COST_MGMT_DB"."PUBLIC"."CORTEX_DASHBOARD_CONFIG" t
USING (
    SELECT COLUMN1 AS KEY, COLUMN2 AS VALUE FROM VALUES
        ('enabled', 'true'),
        ('database', 'COST_MGMT_DB'),
        ('schema', 'PUBLIC'),
        ('warehouse_name', 'COST_MGMT_WH'),
        ('warehouse_size', 'XSMALL'),
        ('object_prefix', 'COST_MGMT'),
        ('wh_degraded', 'false'),
        ('owned_objects', $$[{"fqn": "\"COST_MGMT_WH\"", "type": "WAREHOUSE"}, {"fqn": "\"COST_MGMT_DB\".\"PUBLIC\".\"COST_MGMT_COCO\"", "type": "TABLE"}, {"fqn": "\"COST_MGMT_DB\".\"PUBLIC\".\"COST_MGMT_TASK_COCO\"", "type": "TASK"}, {"fqn": "\"COST_MGMT_DB\".\"PUBLIC\".\"COST_MGMT_CORTEX_AI_FUNCTIONS\"", "type": "TABLE"}, {"fqn": "\"COST_MGMT_DB\".\"PUBLIC\".\"COST_MGMT_TASK_CORTEX_AI_FUNCTIONS\"", "type": "TASK"}, {"fqn": "\"COST_MGMT_DB\".\"PUBLIC\".\"COST_MGMT_CORTEX_AGENTS\"", "type": "TABLE"}, {"fqn": "\"COST_MGMT_DB\".\"PUBLIC\".\"COST_MGMT_TASK_CORTEX_AGENTS\"", "type": "TASK"}, {"fqn": "\"COST_MGMT_DB\".\"PUBLIC\".\"COST_MGMT_COWORK\"", "type": "TABLE"}, {"fqn": "\"COST_MGMT_DB\".\"PUBLIC\".\"COST_MGMT_TASK_COWORK\"", "type": "TASK"}, {"fqn": "\"COST_MGMT_DB\".\"PUBLIC\".\"COST_MGMT_COWORK_SERVICE_MIX\"", "type": "TABLE"}, {"fqn": "\"COST_MGMT_DB\".\"PUBLIC\".\"COST_MGMT_TASK_COWORK_SERVICE_MIX\"", "type": "TASK"}, {"fqn": "\"COST_MGMT_DB\".\"PUBLIC\".\"COST_MGMT_CORTEX_SEARCH_DAILY\"", "type": "TABLE"}, {"fqn": "\"COST_MGMT_DB\".\"PUBLIC\".\"COST_MGMT_TASK_CORTEX_SEARCH_DAILY\"", "type": "TASK"}, {"fqn": "\"COST_MGMT_DB\".\"PUBLIC\".\"COST_MGMT_CORTEX_SEARCH_SERVING\"", "type": "TABLE"}, {"fqn": "\"COST_MGMT_DB\".\"PUBLIC\".\"COST_MGMT_TASK_CORTEX_SEARCH_SERVING\"", "type": "TASK"}, {"fqn": "\"COST_MGMT_DB\".\"PUBLIC\".\"COST_MGMT_CORTEX_SEARCH_BATCH\"", "type": "TABLE"}, {"fqn": "\"COST_MGMT_DB\".\"PUBLIC\".\"COST_MGMT_TASK_CORTEX_SEARCH_BATCH\"", "type": "TASK"}, {"fqn": "\"COST_MGMT_DB\".\"PUBLIC\".\"COST_MGMT_WAREHOUSE_ATTRIBUTION\"", "type": "TABLE"}, {"fqn": "\"COST_MGMT_DB\".\"PUBLIC\".\"COST_MGMT_TASK_WAREHOUSE_ATTRIBUTION\"", "type": "TASK"}, {"fqn": "\"COST_MGMT_DB\".\"PUBLIC\".\"COST_MGMT_MODEL_RATES\"", "type": "TABLE"}, {"fqn": "\"COST_MGMT_DB\".\"PUBLIC\".\"COST_MGMT_TASK_MODEL_RATES\"", "type": "TASK"}]$$)
) s
ON t.KEY = s.KEY
WHEN MATCHED THEN UPDATE SET VALUE = s.VALUE, UPDATED_AT = CURRENT_TIMESTAMP()
WHEN NOT MATCHED THEN INSERT (KEY, VALUE) VALUES (s.KEY, s.VALUE);
SELECT '[PASS] Acceleration settings seeded' AS progress;

-- ==========================================================================
--  Section 3: Task warehouse
-- ==========================================================================
CREATE WAREHOUSE IF NOT EXISTS "COST_MGMT_WH" WAREHOUSE_SIZE = 'XSMALL' AUTO_SUSPEND = 60 AUTO_RESUME = TRUE INITIALLY_SUSPENDED = TRUE;
SELECT '[PASS] Task warehouse ready' AS progress;

-- ==========================================================================
--  Section 4: Aggregate tables and scheduled refresh tasks
-- ==========================================================================

-- CoCo
CREATE TABLE IF NOT EXISTS "COST_MGMT_DB"."PUBLIC"."COST_MGMT_COCO"

  CLUSTER BY (USAGE_DATE)
  AS SELECT SUB_SURFACE, USAGE_DATE, USER_NAME, MODEL_NAME, ROLE_NAME, INFERENCE_REGION, CALLS, TOKENS, CREDITS, TOK_INPUT, TOK_OUTPUT, TOK_CACHE_READ_INPUT, TOK_CACHE_WRITE_INPUT FROM (
WITH coco_granular AS (
    SELECT
        LOWER(h.INTERFACE) AS sub_surface,
        CONVERT_TIMEZONE('UTC', h.USAGE_TIME)::DATE AS usage_date,
        NVL(h.USER_NAME, '(unknown)') AS user_name,
        NVL(h.METADATA:role_name::STRING, '(unknown)') AS role_name,
        NVL(h.METADATA:inference_region::STRING, '(unknown)') AS inference_region,
        token_model.key::STRING AS model_name,
        token_model.value AS tg,
        credit_model.value AS cg
    FROM SNOWFLAKE.ACCOUNT_USAGE.SNOWFLAKE_COCO_USAGE_HISTORY h,
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
    SUM(NVL(tg:input::NUMBER, 0)) AS tok_input,
    SUM(NVL(tg:output::NUMBER, 0)) AS tok_output,
    SUM(NVL(tg:cache_read_input::NUMBER, 0)) AS tok_cache_read_input,
    SUM(NVL(tg:cache_write_input::NUMBER, 0)) AS tok_cache_write_input
FROM coco_granular
GROUP BY ALL
) _init
  WHERE 1 = 0;
CREATE OR REPLACE TASK "COST_MGMT_DB"."PUBLIC"."COST_MGMT_TASK_COCO"
  WAREHOUSE = "COST_MGMT_WH"
  SCHEDULE = 'USING CRON 0 1 * * * UTC'
  AS
INSERT OVERWRITE INTO "COST_MGMT_DB"."PUBLIC"."COST_MGMT_COCO"
SELECT SUB_SURFACE, USAGE_DATE, USER_NAME, MODEL_NAME, ROLE_NAME, INFERENCE_REGION, CALLS, TOKENS, CREDITS, TOK_INPUT, TOK_OUTPUT, TOK_CACHE_READ_INPUT, TOK_CACHE_WRITE_INPUT FROM (
WITH coco_granular AS (
    SELECT
        LOWER(h.INTERFACE) AS sub_surface,
        CONVERT_TIMEZONE('UTC', h.USAGE_TIME)::DATE AS usage_date,
        NVL(h.USER_NAME, '(unknown)') AS user_name,
        NVL(h.METADATA:role_name::STRING, '(unknown)') AS role_name,
        NVL(h.METADATA:inference_region::STRING, '(unknown)') AS inference_region,
        token_model.key::STRING AS model_name,
        token_model.value AS tg,
        credit_model.value AS cg
    FROM SNOWFLAKE.ACCOUNT_USAGE.SNOWFLAKE_COCO_USAGE_HISTORY h,
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
    SUM(NVL(tg:input::NUMBER, 0)) AS tok_input,
    SUM(NVL(tg:output::NUMBER, 0)) AS tok_output,
    SUM(NVL(tg:cache_read_input::NUMBER, 0)) AS tok_cache_read_input,
    SUM(NVL(tg:cache_write_input::NUMBER, 0)) AS tok_cache_write_input
FROM coco_granular
GROUP BY ALL
) _body

WHERE USAGE_DATE >= DATEADD('day', -365, CONVERT_TIMEZONE('UTC', CURRENT_TIMESTAMP())::DATE);
ALTER TASK "COST_MGMT_DB"."PUBLIC"."COST_MGMT_TASK_COCO" RESUME;
SELECT '[PASS] CoCo table + task created and scheduled' AS progress;

-- Cortex AI Functions
CREATE TABLE IF NOT EXISTS "COST_MGMT_DB"."PUBLIC"."COST_MGMT_CORTEX_AI_FUNCTIONS"

  CLUSTER BY (USAGE_DATE)
  AS SELECT USAGE_DATE, USER_NAME, FUNCTION_NAME, MODEL_NAME, QUERY_TAG, CALLS, TOKENS, CREDITS, INCOMPLETE_CALLS FROM (
SELECT
    CONVERT_TIMEZONE('UTC', a.START_TIME)::DATE                                     AS usage_date,
    NVL(u.NAME, '(unknown)')                                      AS user_name,
    NVL(a.FUNCTION_NAME, '(unknown)')                             AS function_name,
    NVL(a.MODEL_NAME, '(unknown)')                                AS model_name,
    NVL(NULLIF(a.QUERY_TAG, ''), '(none)')                  AS query_tag,
    COUNT(*)                                                       AS calls,
    SUM(NVL(REDUCE(
        FILTER(a.METRICS, x -> x:key:unit::STRING = 'tokens'),
        0,
        (acc, x) -> acc + x:value::NUMBER
    ), 0))                                   AS tokens,
    SUM(NVL(a.CREDITS, 0))                                        AS credits,
    SUM(CASE WHEN a.IS_COMPLETED = FALSE THEN 1 ELSE 0 END)       AS incomplete_calls
FROM SNOWFLAKE.ACCOUNT_USAGE.CORTEX_AI_FUNCTIONS_USAGE_HISTORY a
LEFT JOIN SNOWFLAKE.ACCOUNT_USAGE.USERS u ON u.USER_ID = a.USER_ID
GROUP BY ALL
) _init
  WHERE 1 = 0;
CREATE OR REPLACE TASK "COST_MGMT_DB"."PUBLIC"."COST_MGMT_TASK_CORTEX_AI_FUNCTIONS"
  WAREHOUSE = "COST_MGMT_WH"
  SCHEDULE = 'USING CRON 0 1 * * * UTC'
  AS
INSERT OVERWRITE INTO "COST_MGMT_DB"."PUBLIC"."COST_MGMT_CORTEX_AI_FUNCTIONS"
SELECT USAGE_DATE, USER_NAME, FUNCTION_NAME, MODEL_NAME, QUERY_TAG, CALLS, TOKENS, CREDITS, INCOMPLETE_CALLS FROM (
SELECT
    CONVERT_TIMEZONE('UTC', a.START_TIME)::DATE                                     AS usage_date,
    NVL(u.NAME, '(unknown)')                                      AS user_name,
    NVL(a.FUNCTION_NAME, '(unknown)')                             AS function_name,
    NVL(a.MODEL_NAME, '(unknown)')                                AS model_name,
    NVL(NULLIF(a.QUERY_TAG, ''), '(none)')                  AS query_tag,
    COUNT(*)                                                       AS calls,
    SUM(NVL(REDUCE(
        FILTER(a.METRICS, x -> x:key:unit::STRING = 'tokens'),
        0,
        (acc, x) -> acc + x:value::NUMBER
    ), 0))                                   AS tokens,
    SUM(NVL(a.CREDITS, 0))                                        AS credits,
    SUM(CASE WHEN a.IS_COMPLETED = FALSE THEN 1 ELSE 0 END)       AS incomplete_calls
FROM SNOWFLAKE.ACCOUNT_USAGE.CORTEX_AI_FUNCTIONS_USAGE_HISTORY a
LEFT JOIN SNOWFLAKE.ACCOUNT_USAGE.USERS u ON u.USER_ID = a.USER_ID
GROUP BY ALL
) _body

WHERE USAGE_DATE >= DATEADD('day', -365, CONVERT_TIMEZONE('UTC', CURRENT_TIMESTAMP())::DATE);
ALTER TASK "COST_MGMT_DB"."PUBLIC"."COST_MGMT_TASK_CORTEX_AI_FUNCTIONS" RESUME;
SELECT '[PASS] Cortex AI Functions table + task created and scheduled' AS progress;

-- Cortex Agents
CREATE TABLE IF NOT EXISTS "COST_MGMT_DB"."PUBLIC"."COST_MGMT_CORTEX_AGENTS"

  CLUSTER BY (USAGE_DATE)
  AS SELECT USAGE_DATE, USER_NAME, AGENT_NAME, AGENT_FQN, AGENT_TAG_LABELS, CALLS, TOKENS, CREDITS, ROOT_REQUESTS, CHILD_REQUESTS FROM (
SELECT
    CONVERT_TIMEZONE('UTC', START_TIME)::DATE                            AS usage_date,
    NVL(USER_NAME, '(unknown)')                        AS user_name,
    NVL(AGENT_NAME, '(unknown)')                       AS agent_name,
    NVL(AGENT_DATABASE_NAME, '(unknown)') || '.' ||
    NVL(AGENT_SCHEMA_NAME, '(unknown)')   || '.' ||
    NVL(AGENT_NAME, '(unknown)')                       AS agent_fqn,
    NVL(ARRAY_TO_STRING(
        TRANSFORM(AGENT_TAGS, t -> t:tag_name::STRING || ' = ' || t:tag_value::STRING),
        '|'
    ), '')                                            AS agent_tag_labels,
    COUNT(*)                                           AS calls,
    SUM(NVL(TOKENS, 0))                               AS tokens,
    SUM(NVL(TOKEN_CREDITS, 0))                         AS credits,
    SUM(CASE WHEN PARENT_REQUEST_ID IS NULL     THEN 1 ELSE 0 END) AS root_requests,
    SUM(CASE WHEN PARENT_REQUEST_ID IS NOT NULL THEN 1 ELSE 0 END) AS child_requests
FROM SNOWFLAKE.ACCOUNT_USAGE.CORTEX_AGENT_USAGE_HISTORY
GROUP BY ALL
) _init
  WHERE 1 = 0;
CREATE OR REPLACE TASK "COST_MGMT_DB"."PUBLIC"."COST_MGMT_TASK_CORTEX_AGENTS"
  WAREHOUSE = "COST_MGMT_WH"
  SCHEDULE = 'USING CRON 0 1 * * * UTC'
  AS
INSERT OVERWRITE INTO "COST_MGMT_DB"."PUBLIC"."COST_MGMT_CORTEX_AGENTS"
SELECT USAGE_DATE, USER_NAME, AGENT_NAME, AGENT_FQN, AGENT_TAG_LABELS, CALLS, TOKENS, CREDITS, ROOT_REQUESTS, CHILD_REQUESTS FROM (
SELECT
    CONVERT_TIMEZONE('UTC', START_TIME)::DATE                            AS usage_date,
    NVL(USER_NAME, '(unknown)')                        AS user_name,
    NVL(AGENT_NAME, '(unknown)')                       AS agent_name,
    NVL(AGENT_DATABASE_NAME, '(unknown)') || '.' ||
    NVL(AGENT_SCHEMA_NAME, '(unknown)')   || '.' ||
    NVL(AGENT_NAME, '(unknown)')                       AS agent_fqn,
    NVL(ARRAY_TO_STRING(
        TRANSFORM(AGENT_TAGS, t -> t:tag_name::STRING || ' = ' || t:tag_value::STRING),
        '|'
    ), '')                                            AS agent_tag_labels,
    COUNT(*)                                           AS calls,
    SUM(NVL(TOKENS, 0))                               AS tokens,
    SUM(NVL(TOKEN_CREDITS, 0))                         AS credits,
    SUM(CASE WHEN PARENT_REQUEST_ID IS NULL     THEN 1 ELSE 0 END) AS root_requests,
    SUM(CASE WHEN PARENT_REQUEST_ID IS NOT NULL THEN 1 ELSE 0 END) AS child_requests
FROM SNOWFLAKE.ACCOUNT_USAGE.CORTEX_AGENT_USAGE_HISTORY
GROUP BY ALL
) _body

WHERE USAGE_DATE >= DATEADD('day', -365, CONVERT_TIMEZONE('UTC', CURRENT_TIMESTAMP())::DATE);
ALTER TASK "COST_MGMT_DB"."PUBLIC"."COST_MGMT_TASK_CORTEX_AGENTS" RESUME;
SELECT '[PASS] Cortex Agents table + task created and scheduled' AS progress;

-- CoWork
CREATE TABLE IF NOT EXISTS "COST_MGMT_DB"."PUBLIC"."COST_MGMT_COWORK"

  CLUSTER BY (USAGE_DATE)
  AS SELECT USAGE_DATE, USER_NAME, COWORK_NAME, AGENT_FQN, AGENT_NAME, ROLE_NAME, AGENT_TAG_LABELS, CALLS, TOKENS, CREDITS, SQL_QUERY_CREDITS, AI_FUNCTIONS_CREDITS, ROOT_REQUESTS, CHILD_REQUESTS FROM (
SELECT
    CONVERT_TIMEZONE('UTC', START_TIME)::DATE                              AS usage_date,
    NVL(USER_NAME, '(unknown)')                          AS user_name,
    NVL(SNOWFLAKE_COWORK_NAME, '(unknown)')               AS cowork_name,
    NVL(AGENT_DATABASE_NAME, '(unknown)') || '.' ||
    NVL(AGENT_SCHEMA_NAME, '(unknown)')   || '.' ||
    NVL(AGENT_NAME, '(unknown)')                         AS agent_fqn,
    NVL(AGENT_NAME, '(none)')                      AS agent_name,
    NVL(METADATA:role_name::STRING, '(unknown)')         AS role_name,
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
FROM SNOWFLAKE.ACCOUNT_USAGE.SNOWFLAKE_COWORK_USAGE_HISTORY
GROUP BY ALL
) _init
  WHERE 1 = 0;
CREATE OR REPLACE TASK "COST_MGMT_DB"."PUBLIC"."COST_MGMT_TASK_COWORK"
  WAREHOUSE = "COST_MGMT_WH"
  SCHEDULE = 'USING CRON 0 1 * * * UTC'
  AS
INSERT OVERWRITE INTO "COST_MGMT_DB"."PUBLIC"."COST_MGMT_COWORK"
SELECT USAGE_DATE, USER_NAME, COWORK_NAME, AGENT_FQN, AGENT_NAME, ROLE_NAME, AGENT_TAG_LABELS, CALLS, TOKENS, CREDITS, SQL_QUERY_CREDITS, AI_FUNCTIONS_CREDITS, ROOT_REQUESTS, CHILD_REQUESTS FROM (
SELECT
    CONVERT_TIMEZONE('UTC', START_TIME)::DATE                              AS usage_date,
    NVL(USER_NAME, '(unknown)')                          AS user_name,
    NVL(SNOWFLAKE_COWORK_NAME, '(unknown)')               AS cowork_name,
    NVL(AGENT_DATABASE_NAME, '(unknown)') || '.' ||
    NVL(AGENT_SCHEMA_NAME, '(unknown)')   || '.' ||
    NVL(AGENT_NAME, '(unknown)')                         AS agent_fqn,
    NVL(AGENT_NAME, '(none)')                      AS agent_name,
    NVL(METADATA:role_name::STRING, '(unknown)')         AS role_name,
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
FROM SNOWFLAKE.ACCOUNT_USAGE.SNOWFLAKE_COWORK_USAGE_HISTORY
GROUP BY ALL
) _body

WHERE USAGE_DATE >= DATEADD('day', -365, CONVERT_TIMEZONE('UTC', CURRENT_TIMESTAMP())::DATE);
ALTER TASK "COST_MGMT_DB"."PUBLIC"."COST_MGMT_TASK_COWORK" RESUME;
SELECT '[PASS] CoWork table + task created and scheduled' AS progress;

-- CoWork — Service Mix
CREATE TABLE IF NOT EXISTS "COST_MGMT_DB"."PUBLIC"."COST_MGMT_COWORK_SERVICE_MIX"

  CLUSTER BY (USAGE_DATE)
  AS SELECT USAGE_DATE, USER_NAME, SERVICE_TYPE, MODEL_NAME, CALLS, TOKENS, CREDITS, TOK_INPUT, TOK_OUTPUT, TOK_CACHE_READ_INPUT, TOK_CACHE_WRITE_INPUT FROM (
WITH tokens AS (
    SELECT
        CONVERT_TIMEZONE('UTC', h.START_TIME)::DATE AS usage_date,
        NVL(h.USER_NAME, '(unknown)') AS user_name,
        rq.key   AS request_id,
        s.key    AS service_type,
        m.key    AS model_name,
    SUM(NVL(m.value:input::NUMBER, 0)) AS tok_input,
    SUM(NVL(m.value:output::NUMBER, 0)) AS tok_output,
    SUM(NVL(m.value:cache_read_input::NUMBER, 0)) AS tok_cache_read_input,
    SUM(NVL(m.value:cache_write_input::NUMBER, 0)) AS tok_cache_write_input
    FROM SNOWFLAKE.ACCOUNT_USAGE.SNOWFLAKE_COWORK_USAGE_HISTORY h,
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
        SUM(NVL(cm.value:input::FLOAT, 0) + NVL(cm.value:output::FLOAT, 0) + NVL(cm.value:cache_read_input::FLOAT, 0) + NVL(cm.value:cache_write_input::FLOAT, 0)) AS credits
    FROM SNOWFLAKE.ACCOUNT_USAGE.SNOWFLAKE_COWORK_USAGE_HISTORY h,
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
    SUM(tok_input + tok_output + tok_cache_read_input + tok_cache_write_input) AS tokens,
    SUM(NVL(c.credits, 0))       AS credits,
    SUM(t.tok_input) AS tok_input,
    SUM(t.tok_output) AS tok_output,
    SUM(t.tok_cache_read_input) AS tok_cache_read_input,
    SUM(t.tok_cache_write_input) AS tok_cache_write_input
FROM tokens t
LEFT JOIN credits c
       ON c.request_id = t.request_id
      AND c.service_type = t.service_type
      AND c.model_name = t.model_name
GROUP BY ALL
) _init
  WHERE 1 = 0;
CREATE OR REPLACE TASK "COST_MGMT_DB"."PUBLIC"."COST_MGMT_TASK_COWORK_SERVICE_MIX"
  WAREHOUSE = "COST_MGMT_WH"
  SCHEDULE = 'USING CRON 0 1 * * * UTC'
  AS
INSERT OVERWRITE INTO "COST_MGMT_DB"."PUBLIC"."COST_MGMT_COWORK_SERVICE_MIX"
SELECT USAGE_DATE, USER_NAME, SERVICE_TYPE, MODEL_NAME, CALLS, TOKENS, CREDITS, TOK_INPUT, TOK_OUTPUT, TOK_CACHE_READ_INPUT, TOK_CACHE_WRITE_INPUT FROM (
WITH tokens AS (
    SELECT
        CONVERT_TIMEZONE('UTC', h.START_TIME)::DATE AS usage_date,
        NVL(h.USER_NAME, '(unknown)') AS user_name,
        rq.key   AS request_id,
        s.key    AS service_type,
        m.key    AS model_name,
    SUM(NVL(m.value:input::NUMBER, 0)) AS tok_input,
    SUM(NVL(m.value:output::NUMBER, 0)) AS tok_output,
    SUM(NVL(m.value:cache_read_input::NUMBER, 0)) AS tok_cache_read_input,
    SUM(NVL(m.value:cache_write_input::NUMBER, 0)) AS tok_cache_write_input
    FROM SNOWFLAKE.ACCOUNT_USAGE.SNOWFLAKE_COWORK_USAGE_HISTORY h,
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
        SUM(NVL(cm.value:input::FLOAT, 0) + NVL(cm.value:output::FLOAT, 0) + NVL(cm.value:cache_read_input::FLOAT, 0) + NVL(cm.value:cache_write_input::FLOAT, 0)) AS credits
    FROM SNOWFLAKE.ACCOUNT_USAGE.SNOWFLAKE_COWORK_USAGE_HISTORY h,
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
    SUM(tok_input + tok_output + tok_cache_read_input + tok_cache_write_input) AS tokens,
    SUM(NVL(c.credits, 0))       AS credits,
    SUM(t.tok_input) AS tok_input,
    SUM(t.tok_output) AS tok_output,
    SUM(t.tok_cache_read_input) AS tok_cache_read_input,
    SUM(t.tok_cache_write_input) AS tok_cache_write_input
FROM tokens t
LEFT JOIN credits c
       ON c.request_id = t.request_id
      AND c.service_type = t.service_type
      AND c.model_name = t.model_name
GROUP BY ALL
) _body

WHERE USAGE_DATE >= DATEADD('day', -365, CONVERT_TIMEZONE('UTC', CURRENT_TIMESTAMP())::DATE);
ALTER TASK "COST_MGMT_DB"."PUBLIC"."COST_MGMT_TASK_COWORK_SERVICE_MIX" RESUME;
SELECT '[PASS] CoWork — Service Mix table + task created and scheduled' AS progress;

-- Cortex Search — Daily
CREATE TABLE IF NOT EXISTS "COST_MGMT_DB"."PUBLIC"."COST_MGMT_CORTEX_SEARCH_DAILY"

  CLUSTER BY (USAGE_DATE)
  AS SELECT USAGE_DATE, SERVICE_NAME, CONSUMPTION_TYPE, MODEL_NAME, CALLS, TOKENS, CREDITS FROM (
SELECT
    CONVERT_TIMEZONE('UTC', USAGE_DATE)::DATE                              AS usage_date,
    NVL(SERVICE_NAME, '(unknown)')                       AS service_name,
    NVL(CONSUMPTION_TYPE, '(unknown)')                   AS consumption_type,
    NVL(MODEL_NAME, '(none)')                      AS model_name,
    COUNT(*)                                             AS calls,
    SUM(TRY_CAST(TOKENS AS NUMBER))                      AS tokens,
    SUM(NVL(CREDITS, 0))                                 AS credits
FROM SNOWFLAKE.ACCOUNT_USAGE.CORTEX_SEARCH_DAILY_USAGE_HISTORY
GROUP BY ALL
) _init
  WHERE 1 = 0;
CREATE OR REPLACE TASK "COST_MGMT_DB"."PUBLIC"."COST_MGMT_TASK_CORTEX_SEARCH_DAILY"
  WAREHOUSE = "COST_MGMT_WH"
  SCHEDULE = 'USING CRON 0 1 * * * UTC'
  AS
INSERT OVERWRITE INTO "COST_MGMT_DB"."PUBLIC"."COST_MGMT_CORTEX_SEARCH_DAILY"
SELECT USAGE_DATE, SERVICE_NAME, CONSUMPTION_TYPE, MODEL_NAME, CALLS, TOKENS, CREDITS FROM (
SELECT
    CONVERT_TIMEZONE('UTC', USAGE_DATE)::DATE                              AS usage_date,
    NVL(SERVICE_NAME, '(unknown)')                       AS service_name,
    NVL(CONSUMPTION_TYPE, '(unknown)')                   AS consumption_type,
    NVL(MODEL_NAME, '(none)')                      AS model_name,
    COUNT(*)                                             AS calls,
    SUM(TRY_CAST(TOKENS AS NUMBER))                      AS tokens,
    SUM(NVL(CREDITS, 0))                                 AS credits
FROM SNOWFLAKE.ACCOUNT_USAGE.CORTEX_SEARCH_DAILY_USAGE_HISTORY
GROUP BY ALL
) _body

WHERE USAGE_DATE >= DATEADD('day', -365, CONVERT_TIMEZONE('UTC', CURRENT_TIMESTAMP())::DATE);
ALTER TASK "COST_MGMT_DB"."PUBLIC"."COST_MGMT_TASK_CORTEX_SEARCH_DAILY" RESUME;
SELECT '[PASS] Cortex Search — Daily table + task created and scheduled' AS progress;

-- Cortex Search — Serving
CREATE TABLE IF NOT EXISTS "COST_MGMT_DB"."PUBLIC"."COST_MGMT_CORTEX_SEARCH_SERVING"

  CLUSTER BY (USAGE_DATE)
  AS SELECT USAGE_DATE, USAGE_HOUR, SERVICE_NAME, CALLS, CREDITS FROM (
SELECT
    CONVERT_TIMEZONE('UTC', START_TIME)::DATE                                          AS usage_date,
    DATE_TRUNC('hour', CONVERT_TIMEZONE('UTC', START_TIME))          AS usage_hour,
    NVL(SERVICE_NAME, '(unknown)')                                    AS service_name,
    COUNT(*)                                                          AS calls,
    SUM(NVL(CREDITS, 0))                                              AS credits
FROM SNOWFLAKE.ACCOUNT_USAGE.CORTEX_SEARCH_SERVING_USAGE_HISTORY
GROUP BY ALL
) _init
  WHERE 1 = 0;
CREATE OR REPLACE TASK "COST_MGMT_DB"."PUBLIC"."COST_MGMT_TASK_CORTEX_SEARCH_SERVING"
  WAREHOUSE = "COST_MGMT_WH"
  SCHEDULE = 'USING CRON 0 1 * * * UTC'
  AS
INSERT OVERWRITE INTO "COST_MGMT_DB"."PUBLIC"."COST_MGMT_CORTEX_SEARCH_SERVING"
SELECT USAGE_DATE, USAGE_HOUR, SERVICE_NAME, CALLS, CREDITS FROM (
SELECT
    CONVERT_TIMEZONE('UTC', START_TIME)::DATE                                          AS usage_date,
    DATE_TRUNC('hour', CONVERT_TIMEZONE('UTC', START_TIME))          AS usage_hour,
    NVL(SERVICE_NAME, '(unknown)')                                    AS service_name,
    COUNT(*)                                                          AS calls,
    SUM(NVL(CREDITS, 0))                                              AS credits
FROM SNOWFLAKE.ACCOUNT_USAGE.CORTEX_SEARCH_SERVING_USAGE_HISTORY
GROUP BY ALL
) _body

WHERE USAGE_DATE >= DATEADD('day', -365, CONVERT_TIMEZONE('UTC', CURRENT_TIMESTAMP())::DATE);
ALTER TASK "COST_MGMT_DB"."PUBLIC"."COST_MGMT_TASK_CORTEX_SEARCH_SERVING" RESUME;
SELECT '[PASS] Cortex Search — Serving table + task created and scheduled' AS progress;

-- Cortex Search — Batch
CREATE TABLE IF NOT EXISTS "COST_MGMT_DB"."PUBLIC"."COST_MGMT_CORTEX_SEARCH_BATCH"

  CLUSTER BY (USAGE_DATE)
  AS SELECT QUERY_ID, USAGE_DATE, SERVICE_NAME, CONSUMPTION_TYPE, MODEL_NAME, TOKENS, CREDITS FROM (
SELECT
    QUERY_ID,
    CONVERT_TIMEZONE('UTC', START_TIME)::DATE                              AS usage_date,
    NVL(SERVICE_NAME, '(unknown)')                       AS service_name,
    NVL(CONSUMPTION_TYPE, '(unknown)')                   AS consumption_type,
    NVL(MODEL_NAME, '(none)')                      AS model_name,
    SUM(TRY_CAST(TOKENS AS NUMBER))                     AS tokens,
    SUM(NVL(CREDITS_USED, 0))                           AS credits
FROM SNOWFLAKE.ACCOUNT_USAGE.CORTEX_SEARCH_BATCH_QUERY_USAGE_HISTORY
GROUP BY ALL
) _init
  WHERE 1 = 0;
CREATE OR REPLACE TASK "COST_MGMT_DB"."PUBLIC"."COST_MGMT_TASK_CORTEX_SEARCH_BATCH"
  WAREHOUSE = "COST_MGMT_WH"
  SCHEDULE = 'USING CRON 0 1 * * * UTC'
  AS
INSERT OVERWRITE INTO "COST_MGMT_DB"."PUBLIC"."COST_MGMT_CORTEX_SEARCH_BATCH"
SELECT QUERY_ID, USAGE_DATE, SERVICE_NAME, CONSUMPTION_TYPE, MODEL_NAME, TOKENS, CREDITS FROM (
SELECT
    QUERY_ID,
    CONVERT_TIMEZONE('UTC', START_TIME)::DATE                              AS usage_date,
    NVL(SERVICE_NAME, '(unknown)')                       AS service_name,
    NVL(CONSUMPTION_TYPE, '(unknown)')                   AS consumption_type,
    NVL(MODEL_NAME, '(none)')                      AS model_name,
    SUM(TRY_CAST(TOKENS AS NUMBER))                     AS tokens,
    SUM(NVL(CREDITS_USED, 0))                           AS credits
FROM SNOWFLAKE.ACCOUNT_USAGE.CORTEX_SEARCH_BATCH_QUERY_USAGE_HISTORY
GROUP BY ALL
) _body

WHERE USAGE_DATE >= DATEADD('day', -365, CONVERT_TIMEZONE('UTC', CURRENT_TIMESTAMP())::DATE);
ALTER TASK "COST_MGMT_DB"."PUBLIC"."COST_MGMT_TASK_CORTEX_SEARCH_BATCH" RESUME;
SELECT '[PASS] Cortex Search — Batch table + task created and scheduled' AS progress;

-- Warehouse Attribution
CREATE TABLE IF NOT EXISTS "COST_MGMT_DB"."PUBLIC"."COST_MGMT_WAREHOUSE_ATTRIBUTION"

  CLUSTER BY (USAGE_DATE)
  AS SELECT QUERY_ID, QUERY_TAG, WAREHOUSE_NAME, SURFACE, TIER, REQUEST_ID, SOURCE_VIEW, COMPUTE_CREDITS, CLOUD_SERVICES_CREDITS, WAREHOUSE_CREDITS, USAGE_DATE FROM (
WITH attr AS (
    SELECT
        QUERY_ID,
        ANY_VALUE(QUERY_TAG)      AS QUERY_TAG,
        ANY_VALUE(WAREHOUSE_NAME) AS WAREHOUSE_NAME,
        SUM(NVL(CREDITS_ATTRIBUTED_COMPUTE, 0)) AS COMPUTE_CREDITS,
        0::FLOAT                  AS CLOUD_SERVICES_CREDITS,
        'ATTRIBUTION'             AS SOURCE_VIEW,
        ANY_VALUE(CONVERT_TIMEZONE('UTC', START_TIME)::DATE) AS USAGE_DATE
    FROM SNOWFLAKE.ACCOUNT_USAGE.QUERY_ATTRIBUTION_HISTORY
    GROUP BY QUERY_ID
),
meter AS (
    SELECT
        QUERY_ID,
        ANY_VALUE(QUERY_TAG)      AS QUERY_TAG,
        ANY_VALUE(WAREHOUSE_NAME) AS WAREHOUSE_NAME,
        SUM(NVL(CREDITS_USED_COMPUTE, 0))         AS COMPUTE_CREDITS,
        SUM(NVL(CREDITS_USED_CLOUD_SERVICES, 0))::FLOAT  AS CLOUD_SERVICES_CREDITS,
        'METERING'                AS SOURCE_VIEW,
        ANY_VALUE(CONVERT_TIMEZONE('UTC', QUERY_START_TIME)::DATE) AS USAGE_DATE
    FROM SNOWFLAKE.ACCOUNT_USAGE.QUERY_METERING_HISTORY
    WHERE WAREHOUSE_NAME IS NOT NULL
    GROUP BY QUERY_ID
),
combined AS (
    SELECT * FROM attr
    UNION ALL
    SELECT m.* FROM meter m
    WHERE NOT EXISTS (SELECT 1 FROM attr a WHERE a.QUERY_ID = m.QUERY_ID)
),
search_ids AS (
    SELECT DISTINCT QUERY_ID FROM SNOWFLAKE.ACCOUNT_USAGE.CORTEX_SEARCH_BATCH_QUERY_USAGE_HISTORY
    WHERE START_TIME >= DATEADD('day', -365, CONVERT_TIMEZONE('UTC', CURRENT_TIMESTAMP()))
),
aif_ids AS (
    SELECT DISTINCT QUERY_ID FROM SNOWFLAKE.ACCOUNT_USAGE.CORTEX_AI_FUNCTIONS_USAGE_HISTORY
),
classified AS (
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
            WHEN c.QUERY_TAG LIKE 'cortex-agent%'             THEN 'Cortex Agents'
            WHEN c.QUERY_TAG LIKE 'snowflake-intelligence%'   THEN 'CoWork'
            WHEN TRY_PARSE_JSON(c.QUERY_TAG):app::STRING IN ('cortex_code_cli', 'cortex_code_desktop', 'cortex_code_sandbox')                                THEN 'CoCo'
            WHEN si.QUERY_ID IS NOT NULL                      THEN 'Cortex Search'
            WHEN af.QUERY_ID IS NOT NULL                      THEN 'Cortex AI Functions'
        END AS SURFACE,
        CASE
            WHEN c.QUERY_TAG LIKE 'cortex-agent-%'                       THEN 'per_request'
            WHEN c.QUERY_TAG LIKE 'cortex-agent'                         THEN 'surface'
            WHEN c.QUERY_TAG LIKE 'snowflake-intelligence-%'             THEN 'per_request'
            WHEN c.QUERY_TAG LIKE 'snowflake-intelligence'               THEN 'surface'
            WHEN TRY_PARSE_JSON(c.QUERY_TAG):app::STRING IN ('cortex_code_cli', 'cortex_code_desktop', 'cortex_code_sandbox')
                 AND TRY_PARSE_JSON(c.QUERY_TAG):request_id::STRING IS NOT NULL THEN 'per_request'
            WHEN TRY_PARSE_JSON(c.QUERY_TAG):app::STRING IN ('cortex_code_cli', 'cortex_code_desktop', 'cortex_code_sandbox')                                           THEN 'surface'
            WHEN si.QUERY_ID IS NOT NULL OR af.QUERY_ID IS NOT NULL      THEN 'per_request'
        END AS TIER,
        CASE
            WHEN c.QUERY_TAG LIKE 'cortex-agent-%'           THEN SUBSTR(c.QUERY_TAG, 14)
            WHEN c.QUERY_TAG LIKE 'snowflake-intelligence-%' THEN SUBSTR(c.QUERY_TAG, 24)
            WHEN TRY_PARSE_JSON(c.QUERY_TAG):app::STRING IN ('cortex_code_cli', 'cortex_code_desktop', 'cortex_code_sandbox')                                THEN TRY_PARSE_JSON(c.QUERY_TAG):request_id::STRING
            WHEN si.QUERY_ID IS NOT NULL OR af.QUERY_ID IS NOT NULL THEN c.QUERY_ID
        END AS REQUEST_ID
    FROM combined c
    LEFT JOIN search_ids si ON si.QUERY_ID = c.QUERY_ID
    LEFT JOIN aif_ids af ON af.QUERY_ID = c.QUERY_ID
)
SELECT
    QUERY_ID, QUERY_TAG, WAREHOUSE_NAME, SURFACE, TIER, REQUEST_ID, SOURCE_VIEW,
    COMPUTE_CREDITS, CLOUD_SERVICES_CREDITS, WAREHOUSE_CREDITS, USAGE_DATE
FROM classified
WHERE SURFACE IS NOT NULL
) _init
  WHERE 1 = 0;
CREATE OR REPLACE TASK "COST_MGMT_DB"."PUBLIC"."COST_MGMT_TASK_WAREHOUSE_ATTRIBUTION"
  WAREHOUSE = "COST_MGMT_WH"
  SCHEDULE = 'USING CRON 0 1 * * * UTC'
  AS
INSERT OVERWRITE INTO "COST_MGMT_DB"."PUBLIC"."COST_MGMT_WAREHOUSE_ATTRIBUTION"
SELECT QUERY_ID, QUERY_TAG, WAREHOUSE_NAME, SURFACE, TIER, REQUEST_ID, SOURCE_VIEW, COMPUTE_CREDITS, CLOUD_SERVICES_CREDITS, WAREHOUSE_CREDITS, USAGE_DATE FROM (
WITH attr AS (
    SELECT
        QUERY_ID,
        ANY_VALUE(QUERY_TAG)      AS QUERY_TAG,
        ANY_VALUE(WAREHOUSE_NAME) AS WAREHOUSE_NAME,
        SUM(NVL(CREDITS_ATTRIBUTED_COMPUTE, 0)) AS COMPUTE_CREDITS,
        0::FLOAT                  AS CLOUD_SERVICES_CREDITS,
        'ATTRIBUTION'             AS SOURCE_VIEW,
        ANY_VALUE(CONVERT_TIMEZONE('UTC', START_TIME)::DATE) AS USAGE_DATE
    FROM SNOWFLAKE.ACCOUNT_USAGE.QUERY_ATTRIBUTION_HISTORY
    GROUP BY QUERY_ID
),
meter AS (
    SELECT
        QUERY_ID,
        ANY_VALUE(QUERY_TAG)      AS QUERY_TAG,
        ANY_VALUE(WAREHOUSE_NAME) AS WAREHOUSE_NAME,
        SUM(NVL(CREDITS_USED_COMPUTE, 0))         AS COMPUTE_CREDITS,
        SUM(NVL(CREDITS_USED_CLOUD_SERVICES, 0))::FLOAT  AS CLOUD_SERVICES_CREDITS,
        'METERING'                AS SOURCE_VIEW,
        ANY_VALUE(CONVERT_TIMEZONE('UTC', QUERY_START_TIME)::DATE) AS USAGE_DATE
    FROM SNOWFLAKE.ACCOUNT_USAGE.QUERY_METERING_HISTORY
    WHERE WAREHOUSE_NAME IS NOT NULL
    GROUP BY QUERY_ID
),
combined AS (
    SELECT * FROM attr
    UNION ALL
    SELECT m.* FROM meter m
    WHERE NOT EXISTS (SELECT 1 FROM attr a WHERE a.QUERY_ID = m.QUERY_ID)
),
search_ids AS (
    SELECT DISTINCT QUERY_ID FROM SNOWFLAKE.ACCOUNT_USAGE.CORTEX_SEARCH_BATCH_QUERY_USAGE_HISTORY
    WHERE START_TIME >= DATEADD('day', -365, CONVERT_TIMEZONE('UTC', CURRENT_TIMESTAMP()))
),
aif_ids AS (
    SELECT DISTINCT QUERY_ID FROM SNOWFLAKE.ACCOUNT_USAGE.CORTEX_AI_FUNCTIONS_USAGE_HISTORY
),
classified AS (
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
            WHEN c.QUERY_TAG LIKE 'cortex-agent%'             THEN 'Cortex Agents'
            WHEN c.QUERY_TAG LIKE 'snowflake-intelligence%'   THEN 'CoWork'
            WHEN TRY_PARSE_JSON(c.QUERY_TAG):app::STRING IN ('cortex_code_cli', 'cortex_code_desktop', 'cortex_code_sandbox')                                THEN 'CoCo'
            WHEN si.QUERY_ID IS NOT NULL                      THEN 'Cortex Search'
            WHEN af.QUERY_ID IS NOT NULL                      THEN 'Cortex AI Functions'
        END AS SURFACE,
        CASE
            WHEN c.QUERY_TAG LIKE 'cortex-agent-%'                       THEN 'per_request'
            WHEN c.QUERY_TAG LIKE 'cortex-agent'                         THEN 'surface'
            WHEN c.QUERY_TAG LIKE 'snowflake-intelligence-%'             THEN 'per_request'
            WHEN c.QUERY_TAG LIKE 'snowflake-intelligence'               THEN 'surface'
            WHEN TRY_PARSE_JSON(c.QUERY_TAG):app::STRING IN ('cortex_code_cli', 'cortex_code_desktop', 'cortex_code_sandbox')
                 AND TRY_PARSE_JSON(c.QUERY_TAG):request_id::STRING IS NOT NULL THEN 'per_request'
            WHEN TRY_PARSE_JSON(c.QUERY_TAG):app::STRING IN ('cortex_code_cli', 'cortex_code_desktop', 'cortex_code_sandbox')                                           THEN 'surface'
            WHEN si.QUERY_ID IS NOT NULL OR af.QUERY_ID IS NOT NULL      THEN 'per_request'
        END AS TIER,
        CASE
            WHEN c.QUERY_TAG LIKE 'cortex-agent-%'           THEN SUBSTR(c.QUERY_TAG, 14)
            WHEN c.QUERY_TAG LIKE 'snowflake-intelligence-%' THEN SUBSTR(c.QUERY_TAG, 24)
            WHEN TRY_PARSE_JSON(c.QUERY_TAG):app::STRING IN ('cortex_code_cli', 'cortex_code_desktop', 'cortex_code_sandbox')                                THEN TRY_PARSE_JSON(c.QUERY_TAG):request_id::STRING
            WHEN si.QUERY_ID IS NOT NULL OR af.QUERY_ID IS NOT NULL THEN c.QUERY_ID
        END AS REQUEST_ID
    FROM combined c
    LEFT JOIN search_ids si ON si.QUERY_ID = c.QUERY_ID
    LEFT JOIN aif_ids af ON af.QUERY_ID = c.QUERY_ID
)
SELECT
    QUERY_ID, QUERY_TAG, WAREHOUSE_NAME, SURFACE, TIER, REQUEST_ID, SOURCE_VIEW,
    COMPUTE_CREDITS, CLOUD_SERVICES_CREDITS, WAREHOUSE_CREDITS, USAGE_DATE
FROM classified
WHERE SURFACE IS NOT NULL
) _body
;
ALTER TASK "COST_MGMT_DB"."PUBLIC"."COST_MGMT_TASK_WAREHOUSE_ATTRIBUTION" RESUME;
SELECT '[PASS] Warehouse Attribution table + task created and scheduled' AS progress;

-- Model Rates
CREATE TABLE IF NOT EXISTS "COST_MGMT_DB"."PUBLIC"."COST_MGMT_MODEL_RATES"

  AS SELECT MODEL_NAME, R_INPUT, R_OUTPUT, R_CACHE_READ, R_CACHE_WRITE FROM (
WITH coco_granular AS (
    SELECT
        token_model.value AS tg,
        credit_model.value AS cg,
        token_model.key::STRING AS model_name
    FROM SNOWFLAKE.ACCOUNT_USAGE.SNOWFLAKE_COCO_USAGE_HISTORY h,
         LATERAL FLATTEN(INPUT => h.TOKENS_GRANULAR) token_model,
         LATERAL FLATTEN(INPUT => h.CREDITS_GRANULAR) credit_model
    WHERE h.USAGE_TIME >= DATE_TRUNC(
        'day', DATEADD('day', -90, CONVERT_TIMEZONE('UTC', CURRENT_TIMESTAMP()))
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
) _init
  WHERE 1 = 0;
CREATE OR REPLACE TASK "COST_MGMT_DB"."PUBLIC"."COST_MGMT_TASK_MODEL_RATES"
  WAREHOUSE = "COST_MGMT_WH"
  SCHEDULE = 'USING CRON 0 1 * * * UTC'
  AS
INSERT OVERWRITE INTO "COST_MGMT_DB"."PUBLIC"."COST_MGMT_MODEL_RATES"
SELECT MODEL_NAME, R_INPUT, R_OUTPUT, R_CACHE_READ, R_CACHE_WRITE FROM (
WITH coco_granular AS (
    SELECT
        token_model.value AS tg,
        credit_model.value AS cg,
        token_model.key::STRING AS model_name
    FROM SNOWFLAKE.ACCOUNT_USAGE.SNOWFLAKE_COCO_USAGE_HISTORY h,
         LATERAL FLATTEN(INPUT => h.TOKENS_GRANULAR) token_model,
         LATERAL FLATTEN(INPUT => h.CREDITS_GRANULAR) credit_model
    WHERE h.USAGE_TIME >= DATE_TRUNC(
        'day', DATEADD('day', -90, CONVERT_TIMEZONE('UTC', CURRENT_TIMESTAMP()))
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
) _body
;
ALTER TASK "COST_MGMT_DB"."PUBLIC"."COST_MGMT_TASK_MODEL_RATES" RESUME;
SELECT '[PASS] Model Rates table + task created and scheduled' AS progress;

-- ==========================================================================
--  Section 5: Populate immediately
--  Tasks run nightly at 01:00 UTC. Queue one run now so the dashboard has
--  data without waiting for the schedule. Aggregate serving still excludes
--  the current (mutable) UTC day per the settled-day contract.
-- ==========================================================================
EXECUTE TASK "COST_MGMT_DB"."PUBLIC"."COST_MGMT_TASK_COCO";
EXECUTE TASK "COST_MGMT_DB"."PUBLIC"."COST_MGMT_TASK_CORTEX_AI_FUNCTIONS";
EXECUTE TASK "COST_MGMT_DB"."PUBLIC"."COST_MGMT_TASK_CORTEX_AGENTS";
EXECUTE TASK "COST_MGMT_DB"."PUBLIC"."COST_MGMT_TASK_COWORK";
EXECUTE TASK "COST_MGMT_DB"."PUBLIC"."COST_MGMT_TASK_COWORK_SERVICE_MIX";
EXECUTE TASK "COST_MGMT_DB"."PUBLIC"."COST_MGMT_TASK_CORTEX_SEARCH_DAILY";
EXECUTE TASK "COST_MGMT_DB"."PUBLIC"."COST_MGMT_TASK_CORTEX_SEARCH_SERVING";
EXECUTE TASK "COST_MGMT_DB"."PUBLIC"."COST_MGMT_TASK_CORTEX_SEARCH_BATCH";
EXECUTE TASK "COST_MGMT_DB"."PUBLIC"."COST_MGMT_TASK_WAREHOUSE_ATTRIBUTION";
EXECUTE TASK "COST_MGMT_DB"."PUBLIC"."COST_MGMT_TASK_MODEL_RATES";
SELECT '[PASS] Initial refresh queued for all tasks' AS progress;

-- ==========================================================================
--  Section 6: Done
-- ==========================================================================
SELECT 'Query acceleration setup complete. Open the dashboard; Acceleration Settings '
    || 'will show these resources as Active once each task run finishes.' AS status;

