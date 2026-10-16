/*=============================================================================
  Cortex AI Consumption Dashboard — Pre-deployment Setup
  =============================================================================

  Run this script with `make bootstrap` (see docs/OPERATIONS.md), which verifies
  the account and target first. It only creates missing objects, so re-running
  it keeps existing objects and settings.
  It creates the key-value configuration table that the acceleration layer
  reads and writes. Without it, the first "Save" in Acceleration Settings will
  attempt to create the table itself (and succeed if privileges allow), but
  running this script ahead of time lets you verify permissions cleanly and
  keeps DDL out of the app runtime.

  Both views read the config table from COST_MGMT_DB.PUBLIC (snowflake.yml and
  web/app.yml), so `make bootstrap` refuses any other location. To relocate,
  edit both manifests and this script together.

  Requirements:
    - The executing role needs CREATE DATABASE on the account (or an existing
      COST_MGMT_DB with CREATE SCHEMA), plus CREATE TABLE on the schema.
    - The app's role needs SELECT on the resulting table, plus INSERT and
      UPDATE to save settings (MERGE runs under those privileges).
      `make grants` grants these to existing roles.
=============================================================================*/

-- 1. Target database and schema (create if they do not exist)
CREATE DATABASE IF NOT EXISTS COST_MGMT_DB;
CREATE SCHEMA IF NOT EXISTS COST_MGMT_DB.PUBLIC;

-- 2. Configuration table
-- Stores acceleration-layer settings as key-value pairs.
-- The dashboard's Acceleration Settings page reads and writes this table via MERGE.
CREATE TABLE IF NOT EXISTS COST_MGMT_DB.PUBLIC.CORTEX_DASHBOARD_CONFIG (
    KEY         VARCHAR NOT NULL,
    VALUE       VARCHAR,
    UPDATED_AT  TIMESTAMP_TZ DEFAULT CURRENT_TIMESTAMP(),
    PRIMARY KEY (KEY)
);

-- Next: grant access with `make grants`, and optionally provision the
-- acceleration layer with `make acceleration:provision` (sql/config.sql) or from
-- Acceleration Settings. README.md ("Setup, deployment, and teardown") gives the order.
