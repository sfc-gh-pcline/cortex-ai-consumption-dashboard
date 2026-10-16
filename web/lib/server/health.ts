// Aggregate-table health probes. Port of _health_cache in utils/config.py:
// SHOW TASKS for scheduling state, INFORMATION_SCHEMA.TABLES for existence and
// row counts, INFORMATION_SCHEMA.TASK_HISTORY for the last successful run.
// Cached for five minutes; an explicit reload invalidates.
import { buildStatuses, type HealthProbeRows, type Statuses } from "@/lib/health";
import { MINUTES, TtlCache } from "@/lib/server/cache";
import { runRaw } from "@/lib/server/query";
import type { Settings } from "@/lib/settings";
import { quoteIdentifier } from "@/lib/sql/core";
import type { Row } from "@/lib/snowflake";

const healthCache = new TtlCache<Statuses>({ maxEntries: 4 });
const HEALTH_TTL_MS = 5 * MINUTES;

async function probe(sql: string, params: string[], errors: string[], label: string) {
  try {
    return (await runRaw({ sql, params })).rows as Row[];
  } catch (err) {
    errors.push(`${label}: ${String(err)}`);
    return null;
  }
}

// Runs the three probes and assembles one status per materialized surface.
export async function probeStatuses(db: string, schema: string, prefix: string): Promise<Statuses> {
  if (!db || !schema || !prefix) return {};
  const qdb = quoteIdentifier(db);
  const qschema = quoteIdentifier(schema);
  quoteIdentifier(prefix);
  const errors: string[] = [];
  const tasks = await probe(
    `SHOW TASKS LIKE '${prefix.toUpperCase()}_TASK_%' IN SCHEMA ${qdb}.${qschema}`,
    [],
    errors,
    "Task probe",
  );
  const tables = await probe(
    `SELECT TABLE_NAME, ROW_COUNT FROM ${qdb}.INFORMATION_SCHEMA.TABLES ` +
      "WHERE TABLE_SCHEMA = ? AND TABLE_NAME LIKE ?",
    [schema.toUpperCase(), `${prefix.toUpperCase()}_%`],
    errors,
    "Table probe",
  );
  const history = await probe(
    "SELECT NAME, STATE, SCHEDULED_TIME, ERROR_MESSAGE, " +
      "MAX(IFF(STATE = 'SUCCEEDED', COMPLETED_TIME, NULL)) OVER (PARTITION BY NAME) AS LAST_RUN " +
      `FROM TABLE(${qdb}.INFORMATION_SCHEMA.TASK_HISTORY(` +
      "  SCHEDULED_TIME_RANGE_START => DATEADD('day', -7, CURRENT_TIMESTAMP()), " +
      "  SCHEDULED_TIME_RANGE_END => CURRENT_TIMESTAMP(), " +
      "  DATABASE_NAME => ?, SCHEMA_NAME => ?, " +
      "  RESULT_LIMIT => 10000" +
      ")) " +
      "QUALIFY ROW_NUMBER() OVER (PARTITION BY NAME ORDER BY SCHEDULED_TIME DESC) = 1",
    [db.toUpperCase(), schema.toUpperCase()],
    errors,
    "Task history probe",
  );
  const probes: HealthProbeRows = { tasks, tables, history, errors };
  return buildStatuses(prefix, probes);
}

export async function loadStatuses(settings: Settings): Promise<Statuses> {
  const key = `${settings.database}.${settings.schema}.${settings.objectPrefix}`;
  return healthCache.getOrLoad(key, HEALTH_TTL_MS, () =>
    probeStatuses(settings.database, settings.schema, settings.objectPrefix),
  );
}

export function invalidateHealth(): void {
  healthCache.clear();
}
