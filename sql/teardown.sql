/*=============================================================================
  Cortex AI Consumption Dashboard — Teardown
  =============================================================================

  Run this script to remove the acceleration layer and its settings: scheduled
  tasks, aggregate tables, the task warehouse, and the configuration table. It
  does not remove the Streamlit apps, App Runtime services, artifact
  repositories, roles, or the database; see "Teardown" in README.md.

  Prefer `make acceleration:teardown` (see docs/OPERATIONS.md): it reads the
  owned_objects registry, prints a dry-run inventory, and keeps the config table.
  This script, by contrast, also drops CORTEX_DASHBOARD_CONFIG.

  IMPORTANT — default prefix assumption:
  This script uses the DEFAULT object prefix (COST_MGMT) and warehouse name
  (COST_MGMT_WH). If you changed the prefix or warehouse name in Acceleration
  Settings, use the in-app teardown instead (Acceleration Settings → "Tear down
  all acceleration objects"), which reads your actual settings from the config
  table and drops only the objects it owns.

  This is a destructive operation. After running, the dashboard will revert to
  reading directly from ACCOUNT_USAGE and Acceleration Settings will start fresh.

  Adjust DATABASE and SCHEMA if you changed the defaults.
=============================================================================*/

USE SCHEMA COST_MGMT_DB.PUBLIC;

-- 1. Suspend and drop scheduled refresh tasks
DROP TASK IF EXISTS COST_MGMT_TASK_COCO;
DROP TASK IF EXISTS COST_MGMT_TASK_CORTEX_AI_FUNCTIONS;
DROP TASK IF EXISTS COST_MGMT_TASK_CORTEX_AGENTS;
DROP TASK IF EXISTS COST_MGMT_TASK_COWORK;
DROP TASK IF EXISTS COST_MGMT_TASK_COWORK_SERVICE_MIX;
DROP TASK IF EXISTS COST_MGMT_TASK_CORTEX_SEARCH_DAILY;
DROP TASK IF EXISTS COST_MGMT_TASK_CORTEX_SEARCH_SERVING;
DROP TASK IF EXISTS COST_MGMT_TASK_CORTEX_SEARCH_BATCH;
DROP TASK IF EXISTS COST_MGMT_TASK_WAREHOUSE_ATTRIBUTION;
DROP TASK IF EXISTS COST_MGMT_TASK_MODEL_RATES;

-- 2. Drop aggregate tables
DROP TABLE IF EXISTS COST_MGMT_COCO;
DROP TABLE IF EXISTS COST_MGMT_CORTEX_AI_FUNCTIONS;
DROP TABLE IF EXISTS COST_MGMT_CORTEX_AGENTS;
DROP TABLE IF EXISTS COST_MGMT_COWORK;
DROP TABLE IF EXISTS COST_MGMT_COWORK_SERVICE_MIX;
DROP TABLE IF EXISTS COST_MGMT_CORTEX_SEARCH_DAILY;
DROP TABLE IF EXISTS COST_MGMT_CORTEX_SEARCH_SERVING;
DROP TABLE IF EXISTS COST_MGMT_CORTEX_SEARCH_BATCH;
DROP TABLE IF EXISTS COST_MGMT_WAREHOUSE_ATTRIBUTION;
DROP TABLE IF EXISTS COST_MGMT_MODEL_RATES;

-- 3. Drop task warehouse
DROP WAREHOUSE IF EXISTS COST_MGMT_WH;

-- 4. Drop configuration table
DROP TABLE IF EXISTS CORTEX_DASHBOARD_CONFIG;

-- 5. (Optional) Drop the database entirely if nothing else uses it.
-- DROP DATABASE IF EXISTS COST_MGMT_DB;
