// Dependency loaders: route each declared dependency to its aggregate table or
// ACCOUNT_USAGE, run the statement through the result cache, and attach source
// coverage. Port of the loader section of utils/queries.py. Nothing here throws
// for a source failure; the result carries an unavailable coverage instead.
import { unavailableCoverage, withCoverage } from "@/lib/coverage";
import { effectiveServedWindow } from "@/lib/dates";
import { sourceFor, type Statuses } from "@/lib/health";
import {
  DEPENDENCY_KINDS,
  DEP_COWORK_SERVICE_MIX,
  DEP_SEARCH_BATCH,
  DEP_SEARCH_DAILY,
  DEP_SEARCH_SERVING,
  DEP_USER_TAGS,
  SURFACE_AGENTS,
  SURFACE_AI_FUNCTIONS,
  SURFACE_COCO,
  SURFACE_COWORK,
  SURFACE_MODEL_RATES,
  SURFACE_REST_API,
  SURFACE_WAREHOUSE_ATTRIBUTION,
} from "@/lib/plans";
import { rowsAsRates, withRateCoverage } from "@/lib/rates";
import { RATES_TTL_MS, TAGS_TTL_MS, type QueryResult, type Runner } from "@/lib/server/query";
import type { Settings } from "@/lib/settings";
import { dtReadSql, modelRatesDtSql } from "@/lib/sql/acceleration";
import type { Statement } from "@/lib/sql/core";
import {
  agentFactsSql,
  aiFnFactsSql,
  cocoFactsSql,
  coworkFactsSql,
  coworkServiceMixSql,
  modelRatesSql,
  restFactsSql,
  searchBatchFactsSql,
  searchDailyFactsSql,
  searchServingFactsSql,
  userTagsSql,
} from "@/lib/sql/facts";
import {
  meteringProbeSql,
  warehouseCreditFactsAttributionOnlySql,
  warehouseCreditFactsSql,
} from "@/lib/sql/warehouse";
import type { Row } from "@/lib/snowflake";
import type { DateWindow, IsoDate, SourceCoverage } from "@/lib/view-state";

export interface LoadedDependency {
  readonly dependency: string;
  readonly rows: Row[];
  readonly coverage: SourceCoverage;
  readonly degraded: boolean;
  readonly message: string | null;
  readonly rateSnapshotAt?: string | null;
}

export interface LoaderContext {
  readonly settings: Settings;
  readonly statuses: Statuses;
  readonly today: IsoDate;
  readonly run: Runner;
  readonly bypassCache?: boolean;
}

type WindowedBuilder = (start: IsoDate, end: IsoDate) => Statement;

const WINDOWED_BUILDERS: Readonly<Record<string, WindowedBuilder>> = {
  [SURFACE_COCO]: cocoFactsSql,
  [SURFACE_AI_FUNCTIONS]: aiFnFactsSql,
  [SURFACE_AGENTS]: agentFactsSql,
  [SURFACE_COWORK]: coworkFactsSql,
  [DEP_COWORK_SERVICE_MIX]: coworkServiceMixSql,
  [DEP_SEARCH_DAILY]: searchDailyFactsSql,
  [DEP_SEARCH_SERVING]: searchServingFactsSql,
  [DEP_SEARCH_BATCH]: searchBatchFactsSql,
};

const meta = (result: QueryResult) => ({
  readStartedAt: result.readStartedAt,
  fetchedAt: result.fetchedAt,
  snapshotTimestamp: result.snapshotTimestamp,
});

function loaded(
  dependency: string,
  result: QueryResult,
  coverage: SourceCoverage,
  extra: Partial<LoadedDependency> = {},
): LoadedDependency {
  return { dependency, rows: result.rows, coverage, degraded: false, message: null, ...extra };
}

// Reads the aggregate table when the source router selects it; null on failure.
async function loadFromDt(
  dependency: string,
  window: DateWindow,
  ctx: LoaderContext,
): Promise<{ result: QueryResult; served: DateWindow } | null> {
  const served = effectiveServedWindow(window, { accelerated: true, today: ctx.today });
  const snapshot = ctx.statuses[dependency]?.dataTimestamp ?? "";
  try {
    const result = await ctx.run(dtReadSql(dependency, served.start, served.end, ctx.settings), {
      snapshotTimestamp: snapshot,
      bypassCache: ctx.bypassCache,
    });
    return { result, served };
  } catch {
    return null;
  }
}

const DT_FALLBACK_MESSAGE = "Scheduled table unavailable — reading ACCOUNT_USAGE";

// Aggregate table if enabled and healthy, else ACCOUNT_USAGE, with coverage either way.
async function sourceAwareLoad(
  dependency: string,
  window: DateWindow,
  ctx: LoaderContext,
  direct: () => Statement,
): Promise<LoadedDependency> {
  let message: string | null = null;
  if (sourceFor(dependency, ctx.settings, ctx.statuses) === "dt") {
    const dt = await loadFromDt(dependency, window, ctx);
    if (dt) {
      return loaded(
        dependency,
        dt.result,
        withCoverage(meta(dt.result), dependency, window, "aggregate", {
          servedEnd: dt.served.end,
          rows: dt.result.rows,
        }),
      );
    }
    message = DT_FALLBACK_MESSAGE;
  }
  const result = await ctx.run(direct(), { bypassCache: ctx.bypassCache });
  return loaded(
    dependency,
    result,
    withCoverage(meta(result), dependency, window, "direct", { rows: result.rows }),
    { message },
  );
}

// Two-path warehouse loader: aggregate, else probe metering, else attribution only.
async function loadWarehouse(window: DateWindow, ctx: LoaderContext): Promise<LoadedDependency> {
  const dependency = SURFACE_WAREHOUSE_ATTRIBUTION;
  let message: string | null = null;
  if (sourceFor(dependency, ctx.settings, ctx.statuses) === "dt") {
    const dt = await loadFromDt(dependency, window, ctx);
    if (dt) {
      const degraded = ctx.settings.whDegraded;
      return loaded(
        dependency,
        dt.result,
        withCoverage(meta(dt.result), dependency, window, "aggregate", {
          servedEnd: dt.served.end,
          partialReason: degraded ? "Aggregate excludes adaptive warehouse metering." : null,
          rows: dt.result.rows,
        }),
        { degraded, message: degraded ? "Reading from scheduled table (attribution-only)" : null },
      );
    }
    message = DT_FALLBACK_MESSAGE;
  }
  let probeOk = true;
  try {
    await ctx.run(meteringProbeSql(), { bypassCache: ctx.bypassCache });
  } catch {
    probeOk = false;
  }
  if (!probeOk) {
    const result = await ctx.run(warehouseCreditFactsAttributionOnlySql(window.start, window.end), {
      bypassCache: ctx.bypassCache,
    });
    return loaded(
      dependency,
      result,
      withCoverage(meta(result), dependency, window, "direct", {
        partialReason: "Adaptive warehouse metering is unavailable.",
        rows: result.rows,
      }),
      {
        degraded: true,
        message:
          "Adaptive-warehouse (QUERY_METERING_HISTORY) credits are unavailable -- " +
          "showing standard-warehouse (QUERY_ATTRIBUTION_HISTORY) credits only.",
      },
    );
  }
  const result = await ctx.run(warehouseCreditFactsSql(window.start, window.end), {
    bypassCache: ctx.bypassCache,
  });
  return loaded(
    dependency,
    result,
    withCoverage(meta(result), dependency, window, "direct", { rows: result.rows }),
    { message },
  );
}

// Model rates: the aggregate table when routed there and non-empty, else the live scan.
export async function loadModelRates(ctx: LoaderContext): Promise<LoadedDependency> {
  const dependency = SURFACE_MODEL_RATES;
  const window = { start: ctx.today, end: ctx.today };
  let message: string | null = null;
  if (sourceFor(dependency, ctx.settings, ctx.statuses) === "dt") {
    const snapshot = ctx.statuses[dependency]?.dataTimestamp ?? "";
    try {
      const result = await ctx.run(modelRatesDtSql(ctx.settings), {
        snapshotTimestamp: snapshot,
        ttlMs: RATES_TTL_MS,
        bypassCache: ctx.bypassCache,
      });
      if (result.rows.length > 0) {
        return loaded(
          dependency,
          result,
          withCoverage(meta(result), dependency, window, "aggregate", { rows: result.rows }),
        );
      }
    } catch {
      // fall through to the live scan
    }
    message = DT_FALLBACK_MESSAGE;
  }
  const result = await ctx.run(modelRatesSql(ctx.today), {
    ttlMs: RATES_TTL_MS,
    bypassCache: ctx.bypassCache,
  });
  return loaded(
    dependency,
    result,
    withCoverage(meta(result), dependency, window, "direct", { rows: result.rows }),
    { message },
  );
}

// Cortex Inference API: always direct, priced with the rate snapshot loaded first.
async function loadRest(window: DateWindow, ctx: LoaderContext): Promise<LoadedDependency> {
  const ratesResult = await loadModelRates({ ...ctx, bypassCache: false });
  const rates = rowsAsRates(ratesResult.rows);
  const result = await ctx.run(restFactsSql(window.start, window.end, rates), {
    bypassCache: ctx.bypassCache,
  });
  return {
    dependency: SURFACE_REST_API,
    rows: withRateCoverage(result.rows, rates),
    coverage: withCoverage(meta(result), SURFACE_REST_API, window, "direct", { rows: result.rows }),
    degraded: false,
    message: null,
    rateSnapshotAt: ratesResult.coverage.fetchedAt,
  };
}

async function loadUserTags(window: DateWindow, ctx: LoaderContext): Promise<LoadedDependency> {
  const result = await ctx.run(
    { sql: userTagsSql(), params: [] },
    { ttlMs: TAGS_TTL_MS, bypassCache: ctx.bypassCache },
  );
  return loaded(
    DEP_USER_TAGS,
    result,
    withCoverage(meta(result), DEP_USER_TAGS, window, "direct", { rows: result.rows }),
  );
}

// Loads one declared dependency; failures become an unavailable coverage record.
export async function loadDependency(
  dependency: string,
  window: DateWindow,
  ctx: LoaderContext,
): Promise<LoadedDependency> {
  if (!(dependency in DEPENDENCY_KINDS)) throw new Error(`Unknown dependency: ${dependency}`);
  try {
    if (dependency in WINDOWED_BUILDERS) {
      return await sourceAwareLoad(dependency, window, ctx, () =>
        WINDOWED_BUILDERS[dependency](window.start, window.end),
      );
    }
    if (dependency === SURFACE_WAREHOUSE_ATTRIBUTION) return await loadWarehouse(window, ctx);
    if (dependency === SURFACE_MODEL_RATES) return await loadModelRates(ctx);
    if (dependency === SURFACE_REST_API) return await loadRest(window, ctx);
    return await loadUserTags(window, ctx);
  } catch (err) {
    return {
      dependency,
      rows: [],
      coverage: unavailableCoverage(
        dependency,
        window,
        err instanceof Error ? err.message : String(err),
      ),
      degraded: true,
      message: null,
    };
  }
}
