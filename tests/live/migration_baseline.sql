-- ============================================================================
-- Filename: migration_baseline.sql
-- Description: Fixed-window unified-versus-legacy CoCo reconciliation queries.
--
-- Prerequisites: ACCOUNT_USAGE access to unified and legacy CoCo usage views.
-- Creates: No objects. Each query is read-only.
-- ============================================================================

-- baseline_7day, baseline_30day, and baseline_boundary
-- These three fixed windows are intentionally returned in one result set so the
-- live pytest fixture executes exactly one read-only statement per source.
WITH windows AS (
    SELECT column1::STRING AS window_label, column2::TIMESTAMP_TZ AS start_time,
           column3::TIMESTAMP_TZ AS end_time
    FROM VALUES
        ('7day', '2026-08-10 00:00:00 +0000', '2026-08-17 00:00:00 +0000'),
        ('30day', '2026-07-18 00:00:00 +0000', '2026-08-17 00:00:00 +0000'),
        ('boundary', '2026-07-31 00:00:00 +0000', '2026-08-07 00:00:00 +0000')
),
legacy_rows AS (
    SELECT USER_NAME, USAGE_TIME, TOKEN_CREDITS, TOKENS, TOKENS_GRANULAR, METADATA, 'cli' AS interface
    FROM SNOWFLAKE.ACCOUNT_USAGE.CORTEX_CODE_CLI_USAGE_HISTORY
    UNION ALL
    SELECT USER_NAME, USAGE_TIME, TOKEN_CREDITS, TOKENS, TOKENS_GRANULAR, METADATA, 'desktop'
    FROM SNOWFLAKE.ACCOUNT_USAGE.CORTEX_CODE_DESKTOP_USAGE_HISTORY
    UNION ALL
    SELECT USER_NAME, USAGE_TIME, TOKEN_CREDITS, TOKENS, TOKENS_GRANULAR, METADATA, 'snowsight'
    FROM SNOWFLAKE.ACCOUNT_USAGE.CORTEX_CODE_SNOWSIGHT_USAGE_HISTORY
),
source_rows AS (
    SELECT USER_NAME, USAGE_TIME, TOKEN_CREDITS, TOKENS, TOKENS_GRANULAR, METADATA,
           LOWER(INTERFACE) AS interface, 'unified' AS source
    FROM SNOWFLAKE.ACCOUNT_USAGE.SNOWFLAKE_COCO_USAGE_HISTORY
    UNION ALL
    SELECT USER_NAME, USAGE_TIME, TOKEN_CREDITS, TOKENS, TOKENS_GRANULAR, METADATA, interface, 'legacy'
    FROM legacy_rows
),
request_totals AS (
    SELECT w.window_label, r.interface, r.source, COUNT(*) AS request_count,
           SUM(r.TOKENS) AS total_tokens, SUM(r.TOKEN_CREDITS) AS total_credits,
           COUNT(DISTINCT r.USER_NAME) AS distinct_users,
           COUNT(DISTINCT r.METADATA:role_name::STRING) AS distinct_roles,
           COUNT(DISTINCT r.METADATA:inference_region::STRING) AS distinct_regions
    FROM source_rows r JOIN windows w ON r.USAGE_TIME >= w.start_time AND r.USAGE_TIME < w.end_time
    GROUP BY ALL
),
model_totals AS (
    SELECT w.window_label, r.interface, r.source, COUNT(DISTINCT model.key) AS distinct_models
    FROM source_rows r JOIN windows w ON r.USAGE_TIME >= w.start_time AND r.USAGE_TIME < w.end_time,
         LATERAL FLATTEN(INPUT => r.TOKENS_GRANULAR) model
    GROUP BY ALL
)
SELECT r.window_label, r.interface, r.request_count, r.total_tokens, r.total_credits,
       r.distinct_users, m.distinct_models, r.distinct_roles, r.distinct_regions, r.source
FROM request_totals r
JOIN model_totals m USING (window_label, interface, source)
ORDER BY window_label, interface, source;