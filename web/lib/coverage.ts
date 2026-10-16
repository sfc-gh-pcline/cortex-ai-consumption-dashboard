// Source-read evidence attached to every dependency result. Port of
// _with_coverage, _unavailable_coverage and _SOURCE_LATENCY_HOURS in
// utils/queries.py. Pure: it reasons only about the read that produced the rows,
// never about the latest settings or the rows themselves.
import { addDays } from "@/lib/dates";
import {
  DEP_COWORK_SERVICE_MIX,
  DEP_SEARCH_BATCH,
  DEP_SEARCH_DAILY,
  DEP_SEARCH_SERVING,
  SURFACE_AGENTS,
  SURFACE_AI_FUNCTIONS,
  SURFACE_COCO,
  SURFACE_COWORK,
} from "@/lib/plans";
import {
  CoverageState,
  type DateWindow,
  type IsoDate,
  type SourceCoverage,
} from "@/lib/view-state";

// Metadata captured by the query layer for one read; timestamps are ISO strings.
export interface ReadMeta {
  readonly readStartedAt: string | null;
  readonly fetchedAt: string | null;
  readonly snapshotTimestamp?: string | null;
}

// Documented ingestion latency per source, in hours; absent means unknown.
export const SOURCE_LATENCY_HOURS: Readonly<Record<string, number>> = {
  [SURFACE_COCO]: 1.0,
  [SURFACE_AGENTS]: 1.0,
  [SURFACE_COWORK]: 1.0,
  [DEP_COWORK_SERVICE_MIX]: 1.0,
  [DEP_SEARCH_DAILY]: 3.0,
  [DEP_SEARCH_SERVING]: 1.0,
  [DEP_SEARCH_BATCH]: 1.0,
};

export const AI_FUNCTIONS_SOURCE_START: IsoDate = "2026-01-05";

function parseInstant(value: string | null | undefined): Date | null {
  if (!value) return null;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

const utcDate = (instant: Date): IsoDate => instant.toISOString().slice(0, 10);

export interface CoverageOptions {
  readonly servedEnd?: IsoDate | null;
  readonly partialReason?: string | null;
  readonly rows?: readonly Readonly<Record<string, unknown>>[];
}

function latestUsage(rows: readonly Readonly<Record<string, unknown>>[]): IsoDate | null {
  const dates = rows
    .map((row) => row.USAGE_DATE)
    .filter(
      (value): value is IsoDate => typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value),
    );
  return dates.length > 0 ? dates.sort().at(-1)! : null;
}

// Builds the coverage record for a successful read.
export function withCoverage(
  meta: ReadMeta,
  dependency: string,
  requested: DateWindow,
  actualSource: string,
  options: CoverageOptions = {},
): SourceCoverage {
  const fetched = parseInstant(meta.fetchedAt);
  const started = parseInstant(meta.readStartedAt);
  const snapshot = parseInstant(meta.snapshotTimestamp);
  const actualEnd =
    options.servedEnd && options.servedEnd < requested.end ? options.servedEnd : requested.end;
  const served: DateWindow | null =
    requested.start <= actualEnd ? { start: requested.start, end: actualEnd } : null;
  const latency = SOURCE_LATENCY_HOURS[dependency] ?? null;
  let retention: IsoDate | null = null;
  let completeThrough: IsoDate | null = null;
  let state: CoverageState = CoverageState.UNKNOWN;
  let reason: string | null =
    "Source latency or acquisition time is unknown; comparison unavailable.";
  if (fetched) retention = addDays(utcDate(fetched), -364);
  if (dependency === SURFACE_AI_FUNCTIONS) {
    retention =
      retention && retention > AI_FUNCTIONS_SOURCE_START ? retention : AI_FUNCTIONS_SOURCE_START;
  }
  if (actualSource === "aggregate") {
    reason =
      "Task completion is known only when recorded; its source-read watermark is unverified.";
    retention = null;
  } else if (actualSource === "direct" && started && latency !== null && retention) {
    const cutoff = new Date(started.getTime() - latency * 3_600_000);
    completeThrough = addDays(utcDate(cutoff), -1);
    const available =
      served !== null &&
      served.start === requested.start &&
      served.end === requested.end &&
      requested.start >= retention &&
      requested.end <= completeThrough;
    state = available ? CoverageState.AVAILABLE : CoverageState.PARTIAL;
    reason = available ? null : "Selected dates exceed supported retention or source completeness.";
  }
  if (options.partialReason) {
    state = CoverageState.PARTIAL;
    reason = options.partialReason;
  }
  return {
    dependency,
    actualSource,
    fetchedAt: fetched ? fetched.toISOString() : null,
    requested,
    served,
    retentionStart: retention,
    latencyHours: latency,
    snapshotAt: snapshot ? snapshot.toISOString() : null,
    state,
    reason,
    readStartedAt: started ? started.toISOString() : null,
    completeThrough,
    latestUsage: latestUsage(options.rows ?? []),
  };
}

// Records a failed read without fabricating acquisition or served dates.
export function unavailableCoverage(
  dependency: string,
  requested: DateWindow,
  reason: string | null | undefined,
): SourceCoverage {
  return {
    dependency,
    actualSource: "unavailable",
    fetchedAt: null,
    requested,
    served: null,
    state: CoverageState.UNAVAILABLE,
    reason: reason || "Source returned no result",
  };
}
