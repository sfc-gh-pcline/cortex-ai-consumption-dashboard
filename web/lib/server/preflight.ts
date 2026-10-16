// Preflight probes and Snowflake's UTC today. Port of run_preflight and
// utc_today; the check evaluation itself is pure and lives in lib/preflight.ts.
import {
  checkAccountUsage,
  checkWarehouse,
  connectionFailure,
  type PreflightResult,
} from "@/lib/preflight";
import { MINUTES, TtlCache } from "@/lib/server/cache";
import { runRaw } from "@/lib/server/query";
import type { IsoDate } from "@/lib/view-state";

const todayCache = new TtlCache<IsoDate>({ maxEntries: 1 });

// Today as Snowflake sees it, in UTC; the container clock is never consulted.
export async function utcToday(): Promise<IsoDate> {
  return todayCache.getOrLoad("today", 5 * MINUTES, async () => {
    const result = await runRaw({ sql: "SELECT SYSDATE()::DATE AS UTC_TODAY", params: [] });
    return String(result.rows[0]?.UTC_TODAY).slice(0, 10);
  });
}

export async function runPreflight(): Promise<PreflightResult> {
  let info: Record<string, unknown>;
  try {
    const result = await runRaw({
      sql:
        "SELECT CURRENT_ACCOUNT() AS ACCOUNT, CURRENT_USER() AS USER_NAME, " +
        "CURRENT_ROLE() AS ROLE, CURRENT_WAREHOUSE() AS WAREHOUSE",
      params: [],
    });
    info = result.rows[0] ?? {};
  } catch (err) {
    return {
      account: "",
      userName: "",
      role: "",
      warehouse: "",
      checks: [connectionFailure(err instanceof Error ? err.message : String(err))],
    };
  }
  const text = (value: unknown) => (value === null || value === undefined ? "" : String(value));
  const checks = [checkWarehouse(text(info.WAREHOUSE))];
  try {
    await runRaw({
      sql: "SELECT 1 AS OK FROM SNOWFLAKE.ACCOUNT_USAGE.METERING_HISTORY LIMIT 1",
      params: [],
    });
    checks.push(checkAccountUsage(true));
  } catch (err) {
    checks.push(checkAccountUsage(false, err instanceof Error ? err.message : String(err)));
  }
  return {
    account: text(info.ACCOUNT),
    userName: text(info.USER_NAME),
    role: text(info.ROLE),
    warehouse: text(info.WAREHOUSE),
    checks,
  };
}
