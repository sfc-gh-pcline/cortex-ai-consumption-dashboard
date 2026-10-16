// Source routing, fallback, and unavailable outcomes with a fake statement runner.
import { describe, expect, it } from "vitest";

import { emptyStatus } from "@/lib/health";
import {
  SURFACE_COCO,
  SURFACE_MODEL_RATES,
  SURFACE_REST_API,
  SURFACE_WAREHOUSE_ATTRIBUTION,
} from "@/lib/plans";
import { loadDependency, type LoaderContext } from "@/lib/server/loaders";
import type { QueryResult, Runner } from "@/lib/server/query";
import { DEFAULT_SETTINGS } from "@/lib/settings";
import { CoverageState } from "@/lib/view-state";

const WINDOW = { start: "2026-09-13", end: "2026-09-19" };
const TODAY = "2026-09-20";

function result(rows: Record<string, unknown>[] = [{ CREDITS: 1 }], snapshot = ""): QueryResult {
  return {
    rows,
    columns: [],
    readStartedAt: "2026-09-20T08:00:00.000Z",
    fetchedAt: "2026-09-20T08:05:00.000Z",
    snapshotTimestamp: snapshot,
  };
}

// Records every statement and lets a test fail specific ones.
function fakeRunner(
  options: { failWhen?: (sql: string) => boolean; rows?: Record<string, unknown>[] } = {},
) {
  const calls: { sql: string; params: readonly unknown[]; snapshot: string }[] = [];
  const run: Runner = async (statement, opts = {}) => {
    calls.push({
      sql: statement.sql,
      params: statement.params,
      snapshot: opts.snapshotTimestamp ?? "",
    });
    if (options.failWhen?.(statement.sql)) throw new Error("synthetic denied");
    return result(options.rows, opts.snapshotTimestamp ?? "");
  };
  return { run, calls };
}

const disabledCtx = (run: Runner): LoaderContext => ({
  settings: { ...DEFAULT_SETTINGS, enabled: false },
  statuses: {},
  today: TODAY,
  run,
});

const enabledCtx = (run: Runner, keys: string[]): LoaderContext => ({
  settings: { ...DEFAULT_SETTINGS, enabled: true },
  statuses: Object.fromEntries(
    keys.map((key) => [
      key,
      emptyStatus(key, {
        tableExists: true,
        schedulingState: "ACTIVE",
        dataTimestamp: "2026-09-20 01:03:00 +0000",
      }),
    ]),
  ),
  today: TODAY,
  run,
});

describe("loadDependency", () => {
  it("reads ACCOUNT_USAGE directly when acceleration is off", async () => {
    const { run, calls } = fakeRunner();
    const loaded = await loadDependency(SURFACE_COCO, WINDOW, disabledCtx(run));
    expect(calls).toHaveLength(1);
    expect(calls[0].sql).toContain("SNOWFLAKE_COCO_USAGE_HISTORY");
    expect(loaded.coverage.actualSource).toBe("direct");
    expect(loaded.coverage.state).toBe(CoverageState.AVAILABLE);
    expect(loaded.message).toBeNull();
  });

  it("reads the aggregate table with its snapshot generation and settled window", async () => {
    const { run, calls } = fakeRunner();
    const loaded = await loadDependency(SURFACE_COCO, WINDOW, enabledCtx(run, [SURFACE_COCO]));
    expect(calls).toHaveLength(1);
    expect(calls[0].sql).toContain('"COST_MGMT_DB"."PUBLIC"."COST_MGMT_COCO"');
    expect(calls[0].snapshot).toBe("2026-09-20 01:03:00 +0000");
    expect(calls[0].params).toEqual(["2026-09-13", "2026-09-20"]);
    expect(loaded.coverage.actualSource).toBe("aggregate");
    expect(loaded.coverage.served).toEqual(WINDOW);
  });

  it("caps an aggregate read at yesterday when the window includes today", async () => {
    const { run, calls } = fakeRunner();
    const window = { start: "2026-09-13", end: TODAY };
    const loaded = await loadDependency(SURFACE_COCO, window, enabledCtx(run, [SURFACE_COCO]));
    expect(calls[0].params).toEqual(["2026-09-13", "2026-09-20"]);
    expect(loaded.coverage.served).toEqual({ start: "2026-09-13", end: "2026-09-19" });
  });

  it("falls back to ACCOUNT_USAGE and records the actual route when the table read fails", async () => {
    const { run, calls } = fakeRunner({ failWhen: (sql) => sql.includes("COST_MGMT_COCO") });
    const loaded = await loadDependency(SURFACE_COCO, WINDOW, enabledCtx(run, [SURFACE_COCO]));
    expect(calls).toHaveLength(2);
    expect(loaded.coverage.actualSource).toBe("direct");
    expect(loaded.coverage.state).toBe(CoverageState.AVAILABLE);
    expect(loaded.message).toContain("unavailable");
  });

  it("degrades warehouse attribution to attribution-only when the metering probe fails", async () => {
    const { run, calls } = fakeRunner({ failWhen: (sql) => sql.includes("LIMIT 0") });
    const loaded = await loadDependency(SURFACE_WAREHOUSE_ATTRIBUTION, WINDOW, disabledCtx(run));
    expect(calls.map((c) => c.sql.includes("QUERY_METERING_HISTORY"))).toEqual([true, false]);
    expect(loaded.degraded).toBe(true);
    expect(loaded.message).toContain("standard-warehouse");
    expect(loaded.coverage.state).toBe(CoverageState.PARTIAL);
  });

  it("runs the full warehouse statement when the probe succeeds", async () => {
    const { run, calls } = fakeRunner();
    const loaded = await loadDependency(SURFACE_WAREHOUSE_ATTRIBUTION, WINDOW, disabledCtx(run));
    expect(calls).toHaveLength(2);
    expect(calls[1].sql).toContain("meter AS");
    expect(loaded.degraded).toBe(false);
  });

  it("prices inference with the rate snapshot loaded first", async () => {
    const rates = [
      { MODEL_NAME: "m", R_INPUT: 0.01, R_OUTPUT: null, R_CACHE_READ: null, R_CACHE_WRITE: null },
    ];
    const calls: string[] = [];
    const run: Runner = async (statement) => {
      calls.push(statement.sql);
      if (statement.sql.includes("GROUP BY model_name")) return result(rates);
      return result([{ MODEL_NAME: "m", TOK_INPUT: 10, TOK_OUTPUT: 5, TOKENS: 15 }]);
    };
    const loaded = await loadDependency(SURFACE_REST_API, WINDOW, disabledCtx(run));
    expect(calls[0]).toContain("GROUP BY model_name");
    expect(calls[1]).toContain("CORTEX_REST_API_USAGE_HISTORY");
    expect(loaded.rows[0]).toMatchObject({ UNRATED_TOKENS: 5, RATE_INCOMPLETE: true });
    expect(loaded.rateSnapshotAt).toBe("2026-09-20T08:05:00.000Z");
  });

  it("falls back to the live rate scan when the aggregate rate table is empty", async () => {
    const { run, calls } = fakeRunner({ rows: [] });
    const loaded = await loadDependency(
      SURFACE_MODEL_RATES,
      WINDOW,
      enabledCtx(run, [SURFACE_MODEL_RATES]),
    );
    expect(calls).toHaveLength(2);
    expect(calls[0].sql).toContain("COST_MGMT_MODEL_RATES");
    expect(calls[1].sql).toContain("?::TIMESTAMP_TZ");
    expect(loaded.coverage.actualSource).toBe("direct");
  });

  it("returns an unavailable coverage instead of throwing", async () => {
    const { run } = fakeRunner({ failWhen: () => true });
    const loaded = await loadDependency(SURFACE_COCO, WINDOW, disabledCtx(run));
    expect(loaded.rows).toEqual([]);
    expect(loaded.coverage.state).toBe(CoverageState.UNAVAILABLE);
    expect(loaded.coverage.reason).toBe("synthetic denied");
    expect(loaded.coverage.fetchedAt).toBeNull();
    await expect(loadDependency("nope", WINDOW, disabledCtx(run))).rejects.toThrow(
      "Unknown dependency",
    );
  });
});
