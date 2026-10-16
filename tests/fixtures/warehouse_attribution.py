"""Read-only Snowflake fixtures for the actual warehouse SQL builders."""

import datetime as dt

from utils import queries as q

FIXTURE_DAY = dt.date(2026, 9, 16)
SOURCE_NAMES = {
    "QUERY_ATTRIBUTION_HISTORY": "fixture_attribution",
    "QUERY_METERING_HISTORY": "fixture_metering",
    "CORTEX_SEARCH_BATCH_QUERY_USAGE_HISTORY": "fixture_search",
    "CORTEX_AI_FUNCTIONS_USAGE_HISTORY": "fixture_ai",
}
FIXTURE_CTES = """
fixture_attribution AS (
    SELECT column1::VARCHAR AS QUERY_ID, column2::VARCHAR AS QUERY_TAG,
        'fixture_wh'::VARCHAR AS WAREHOUSE_NAME,
        column3::FLOAT AS CREDITS_ATTRIBUTED_COMPUTE,
        column4::TIMESTAMP_TZ AS START_TIME
    FROM VALUES
        ('multi', NULL, 1, '2026-09-16 00:30:00 +00:00'),
        ('midnight', NULL, 2, '2026-09-16 23:59:00 +00:00'),
        ('agent', 'cortex-agent-turn', 3, '2026-09-16 12:00:00 +00:00'),
        ('cowork', 'snowflake-intelligence-turn', 4, '2026-09-16 12:00:00 +00:00'),
        ('coco', '{"app":"cortex_code_cli","request_id":"turn"}', 5, '2026-09-16 12:00:00 +00:00'),
        ('search', NULL, 6, '2026-09-16 12:00:00 +00:00'),
        ('overlap', NULL, 7, '2026-09-16 12:00:00 +00:00'),
        ('control', NULL, 8, '2026-09-16 12:00:00 +00:00')
),
fixture_metering AS (
    SELECT column1::VARCHAR AS QUERY_ID, NULL::VARCHAR AS QUERY_TAG,
        'fixture_wh'::VARCHAR AS WAREHOUSE_NAME,
        column2::FLOAT AS CREDITS_USED_COMPUTE,
        column3::FLOAT AS CREDITS_USED_CLOUD_SERVICES,
        '2026-09-16 12:00:00 +00:00'::TIMESTAMP_TZ AS QUERY_START_TIME
    FROM VALUES ('overlap', 70, 7), ('meter_only', 9, 1)
),
fixture_search AS (
    SELECT column1::VARCHAR AS QUERY_ID, CURRENT_TIMESTAMP() AS START_TIME
    FROM VALUES ('agent'), ('cowork'), ('coco'), ('search')
),
fixture_ai AS (
    SELECT column1::VARCHAR AS QUERY_ID, column2::VARCHAR AS FUNCTION_NAME,
        column3::VARCHAR AS MODEL_NAME, column4::TIMESTAMP_TZ AS START_TIME,
        column5::BOOLEAN AS IS_COMPLETED
    FROM VALUES
        ('multi', 'function_a', 'model_a', '2026-09-16 00:00:00 +00:00', FALSE),
        ('multi', 'function_b', 'model_b', '2026-09-16 01:00:00 +00:00', TRUE),
        ('midnight', 'function_a', 'model_a', '2026-09-17 00:00:00 +00:00', FALSE),
        ('agent', 'function_a', 'model_a', '2026-09-16 12:00:00 +00:00', TRUE),
        ('cowork', 'function_a', 'model_a', '2026-09-16 12:00:00 +00:00', TRUE),
        ('coco', 'function_a', 'model_a', '2026-09-16 12:00:00 +00:00', TRUE),
        ('search', 'function_a', 'model_a', '2026-09-16 12:00:00 +00:00', TRUE),
        ('overlap', 'function_a', 'model_a', '2026-09-16 12:00:00 +00:00', TRUE),
        ('meter_only', 'function_a', 'model_a', '2026-09-16 12:00:00 +00:00', TRUE)
)
"""
EXPECTED = (
    ("multi", "Cortex AI Functions", 1),
    ("midnight", "Cortex AI Functions", 2),
    ("agent", "Cortex Agents", 3),
    ("cowork", "CoWork", 4),
    ("coco", "CoCo", 5),
    ("search", "Cortex Search", 6),
    ("overlap", "Cortex AI Functions", 7),
)


def fixture_sql(sql: str, *, include_metering: bool) -> str:
    """Replace exact sources, rejecting incomplete or unexpected substitution.

    Args:
        sql: Fully bound warehouse SELECT from a production builder.
        include_metering: Whether the selected builder includes adaptive metering.

    Returns:
        A SELECT with fixture-only inputs and independent mismatch counts.

    Raises:
        ValueError: Source count or SQL shape differs from the known builders.
    """
    for source, fixture in SOURCE_NAMES.items():
        name = f"{q.AU}.{source}"
        expected_count = 0 if source == "QUERY_METERING_HISTORY" and not include_metering else 1
        if sql.count(name) != expected_count:
            raise ValueError(f"Unexpected source count: {name}")
        sql = sql.replace(name, fixture)
    if q.AU in sql or "?" in sql or not sql.lstrip().startswith("WITH "):
        raise ValueError("Fixture SQL contains an unresolved source, bind, or statement shape")
    expected = (
        (*EXPECTED, ("meter_only", "Cortex AI Functions", 10)) if include_metering else EXPECTED
    )
    values = ", ".join(
        f"('{query_id}', '{surface}', {credits})" for query_id, surface, credits in expected
    )
    return f"""WITH {FIXTURE_CTES}, actual AS (
{sql}
), expected AS (
    SELECT column1::VARCHAR AS QUERY_ID, column2::VARCHAR AS SURFACE,
        column3::FLOAT AS WAREHOUSE_CREDITS FROM VALUES {values}
), missing AS (
    SELECT QUERY_ID, SURFACE, WAREHOUSE_CREDITS FROM expected
    MINUS SELECT QUERY_ID, SURFACE, WAREHOUSE_CREDITS FROM actual
), unexpected AS (
    SELECT QUERY_ID, SURFACE, WAREHOUSE_CREDITS FROM actual
    MINUS SELECT QUERY_ID, SURFACE, WAREHOUSE_CREDITS FROM expected
)
SELECT (SELECT COUNT(*) FROM missing) AS MISSING_COUNT,
    (SELECT COUNT(*) FROM unexpected) AS UNEXPECTED_COUNT,
    COUNT(*) - COUNT(DISTINCT QUERY_ID) AS DUPLICATE_COUNT,
    COUNT(*) AS ROW_COUNT, SUM(WAREHOUSE_CREDITS) AS TOTAL_CREDITS
FROM actual"""
