// Cached statement execution. Replaces run_query / _cached_query in
// utils/queries.py: results are cached for 30 minutes per (sql, params,
// snapshot generation) in one process-wide LRU, and every result carries the
// acquisition timestamps that coverage evidence is built from.
import { HOURS, MINUTES, TtlCache } from "@/lib/server/cache";
import { executeStatement, type ColumnMeta, type Row } from "@/lib/snowflake";
import type { Param, Statement } from "@/lib/sql/core";

export interface QueryResult {
  readonly rows: Row[];
  readonly columns: readonly ColumnMeta[];
  readonly readStartedAt: string;
  readonly fetchedAt: string;
  readonly snapshotTimestamp: string;
}

export interface RunOptions {
  readonly snapshotTimestamp?: string;
  readonly ttlMs?: number;
  readonly bypassCache?: boolean;
}

export type Runner = (statement: Statement, options?: RunOptions) => Promise<QueryResult>;

export const RESULT_TTL_MS = 30 * MINUTES;
export const RATES_TTL_MS = 24 * HOURS;
export const TAGS_TTL_MS = 1 * HOURS;

export const resultCache = new TtlCache<QueryResult>({ maxEntries: 64 });

export function cacheKey(sql: string, params: readonly Param[], snapshot = ""): string {
  return JSON.stringify([sql, params, snapshot]);
}

let sourceQueryCount = 0;
export const sourceQueries = () => sourceQueryCount;

// Executes one statement against Snowflake, uncached; used by probes and metadata reads.
export async function runRaw(statement: Statement): Promise<QueryResult> {
  const readStartedAt = new Date().toISOString();
  sourceQueryCount += 1;
  const { rows, columns } = await executeStatement(statement.sql, statement.params);
  return {
    rows,
    columns,
    readStartedAt,
    fetchedAt: new Date().toISOString(),
    snapshotTimestamp: "",
  };
}

// Executes through the result cache. A bypass evicts the entry first so the
// fresh read replaces it, which is what "Reload results" means.
export const runQuery: Runner = async (statement, options = {}) => {
  const snapshot = options.snapshotTimestamp ?? "";
  const key = cacheKey(statement.sql, statement.params, snapshot);
  if (options.bypassCache) resultCache.delete(key);
  return resultCache.getOrLoad(key, options.ttlMs ?? RESULT_TTL_MS, async () => ({
    ...(await runRaw(statement)),
    snapshotTimestamp: snapshot,
  }));
};

// Drops every cached statement result; model rates included, as clear_cache does not.
export function clearResultCache(): void {
  resultCache.clear();
}
