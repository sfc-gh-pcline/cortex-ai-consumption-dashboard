// Aggregate-table health, source routing, and cache generations. Pure port of
// the status logic in utils/config.py; the probes that run SQL live in
// lib/server/health.ts and hand their rows to buildStatuses here.
import {
  DEPENDENCY_KINDS,
  DT_SURFACE_KEYS,
  SURFACE_MODEL_RATES,
  SURFACE_REST_API,
} from "@/lib/plans";
import type { OwnedObject, Settings } from "@/lib/settings";
import { quoteIdentifier, surfaceToSuffix } from "@/lib/sql/core";

export interface SurfaceStatus {
  readonly surfaceKey: string;
  readonly tableExists: boolean;
  readonly schedulingState: string; // "ACTIVE" | "SUSPENDED" | ""
  readonly rowCount: number;
  readonly dataTimestamp: string; // COMPLETED_TIME of the last successful task run
  readonly taskExists: boolean | null;
  readonly tableProbeOk: boolean;
  readonly latestState: string;
  readonly latestAttempt: string;
  readonly latestError: string;
  readonly probeErrors: readonly string[];
}

export type Statuses = Readonly<Record<string, SurfaceStatus>>;

export function emptyStatus(
  surfaceKey: string,
  overrides: Partial<SurfaceStatus> = {},
): SurfaceStatus {
  return {
    surfaceKey,
    tableExists: false,
    schedulingState: "",
    rowCount: 0,
    dataTimestamp: "",
    taskExists: null,
    tableProbeOk: true,
    latestState: "",
    latestAttempt: "",
    latestError: "",
    probeErrors: [],
    ...overrides,
  };
}

// Parses a Snowflake timestamp string; naive (no zone) or invalid text yields null,
// matching Python's fromisoformat + tzinfo check.
export function parseZonedTimestamp(text: string): Date | null {
  const trimmed = text.trim().replace(/Z$/, "+00:00");
  if (!/([+-]\d{2}:?\d{2})$/.test(trimmed)) return null;
  const isoish = trimmed.replace(" ", "T").replace(/ ([+-]\d{2}:?\d{2})$/, "$1");
  const parsed = new Date(isoish);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

const STALE_AFTER_MS = 26 * 3_600_000;

// Operational readiness label, independent of source routing.
export function readiness(
  status: SurfaceStatus | null | undefined,
  now: Date = new Date(),
): string {
  if (!status || status.probeErrors.length > 0 || !status.tableProbeOk) return "Unknown";
  if (!status.tableExists) return "Not created";
  if (status.taskExists === false) return "Task missing";
  if (status.latestState === "FAILED" || status.latestState === "CANCELLED")
    return "Refresh failed";
  if (status.schedulingState === "SUSPENDED") return "Suspended";
  if (!status.dataTimestamp) return "Awaiting first refresh";
  const timestamp = parseZonedTimestamp(status.dataTimestamp);
  if (!timestamp) return "Unknown";
  if (now.getTime() - timestamp.getTime() > STALE_AFTER_MS) return "Stale snapshot";
  if (status.schedulingState !== "ACTIVE") return "Unknown";
  return "Ready";
}

const healthy = (s: SurfaceStatus | undefined): s is SurfaceStatus =>
  s !== undefined && s.tableExists && s.schedulingState === "ACTIVE" && Boolean(s.dataTimestamp);

// Latest healthy aggregate task completion timestamp, or "".
export function latestSnapshotFreshness(statuses: Statuses): string {
  const stamps = Object.values(statuses)
    .filter(healthy)
    .map((s) => s.dataTimestamp);
  return stamps.length ? stamps.reduce((a, b) => (a > b ? a : b)) : "";
}

// Whether a surface reads its aggregate table or ACCOUNT_USAGE.
export function sourceFor(
  surfaceKey: string,
  settings: Settings,
  statuses: Statuses,
): "dt" | "account_usage" {
  if (!settings.enabled) return "account_usage";
  if (surfaceKey === SURFACE_REST_API) return "account_usage";
  return healthy(statuses[surfaceKey]) ? "dt" : "account_usage";
}

// Mode and freshness that key a page's warmed cache generation, restricted to its
// own aggregate dependencies so unrelated refreshes do not invalidate it.
export function aggregateSnapshotIdentityFor(
  surfaceKeys: readonly string[],
  settings: Settings,
  statuses: Statuses,
): [mode: "aggregate" | "direct", freshness: string] {
  if (!settings.enabled) return ["direct", ""];
  const stamps = surfaceKeys
    .map((key) => statuses[key])
    .filter(healthy)
    .map((s) => s.dataTimestamp);
  return stamps.length ? ["aggregate", stamps.reduce((a, b) => (a > b ? a : b))] : ["direct", ""];
}

// Snapshot generation for one dependency; window-stable kinds return "".
export function dependencyGeneration(dep: string, settings: Settings, statuses: Statuses): string {
  if (DEPENDENCY_KINDS[dep] !== "windowed_aggregate") return "";
  return aggregateSnapshotIdentityFor([dep], settings, statuses)[1];
}

// Active source mode plus a user-facing reason for one dependency.
export function depSourceMode(
  dep: string,
  settings: Settings,
  statuses: Statuses,
): [mode: "aggregate" | "direct_only" | "global_cached", reason: string] {
  const kind = DEPENDENCY_KINDS[dep];
  if (kind === "direct_only")
    return ["direct_only", "Always reads ACCOUNT_USAGE (not aggregate-eligible)"];
  if (kind === "global_cached") {
    return ["global_cached", "Global metadata; cached independently of the date window"];
  }
  if (kind === "windowed_aggregate") {
    if (sourceFor(dep, settings, statuses) === "dt") {
      return ["aggregate", "Reading from aggregate snapshot through the prior UTC day"];
    }
    return ["direct_only", "Aggregate table unavailable — reading ACCOUNT_USAGE"];
  }
  return ["direct_only", "Unregistered dependency — reading ACCOUNT_USAGE"];
}

export function hasHealthySurfaces(statuses: Statuses): boolean {
  return Object.values(statuses).some(healthy);
}

// Exact configured targets, without inferring ownership from a prefix.
export function configuredObjects(settings: Settings): Record<string, OwnedObject> {
  const namespace = `${quoteIdentifier(settings.database)}.${quoteIdentifier(settings.schema)}`;
  const objects: OwnedObject[] = [
    { fqn: quoteIdentifier(settings.warehouseName), type: "WAREHOUSE" },
  ];
  for (const surface of DT_SURFACE_KEYS) {
    const suffix = surfaceToSuffix(surface);
    objects.push(
      {
        fqn: `${namespace}.${quoteIdentifier(`${settings.objectPrefix}_${suffix}`)}`,
        type: "TABLE",
      },
      {
        fqn: `${namespace}.${quoteIdentifier(`${settings.objectPrefix}_TASK_${suffix}`)}`,
        type: "TASK",
      },
    );
  }
  return Object.fromEntries(objects.map((o) => [o.fqn, o]));
}

// Rows the three health probes return, already upper-cased by the query layer.
export interface HealthProbeRows {
  readonly tasks: readonly Record<string, unknown>[] | null;
  readonly tables: readonly Record<string, unknown>[] | null;
  readonly history: readonly Record<string, unknown>[] | null;
  readonly errors: readonly string[];
}

const text = (value: unknown) => (value === null || value === undefined ? "" : String(value));

// Combines the probe rows into one status per materialized surface.
export function buildStatuses(
  prefix: string,
  probes: HealthProbeRows,
): Record<string, SurfaceStatus> {
  const upperPrefix = prefix.toUpperCase();
  const lastRun = new Map<string, string>();
  const attempts = new Map<string, Record<string, unknown>>();
  for (const row of probes.history ?? []) {
    const name = text(row.NAME);
    if (row.LAST_RUN !== null && row.LAST_RUN !== undefined) lastRun.set(name, text(row.LAST_RUN));
    attempts.set(name, row);
  }
  const result: Record<string, SurfaceStatus> = {};
  for (const surfaceKey of DT_SURFACE_KEYS) {
    const suffix = surfaceToSuffix(surfaceKey);
    const tableName = `${upperPrefix}_${suffix}`;
    const taskName = `${upperPrefix}_TASK_${suffix}`;
    let status = emptyStatus(surfaceKey, {
      tableProbeOk: probes.tables !== null,
      taskExists: probes.tasks !== null ? false : null,
      probeErrors: [...probes.errors],
    });
    const task = probes.tasks?.find((row) => text(row.NAME ?? row.name) === taskName);
    if (task) {
      const rawState = text(task.STATE ?? task.state).toUpperCase();
      status = {
        ...status,
        taskExists: true,
        schedulingState: rawState === "STARTED" ? "ACTIVE" : rawState,
      };
    }
    status = { ...status, dataTimestamp: lastRun.get(taskName) ?? "" };
    const attempt = attempts.get(taskName);
    if (attempt) {
      status = {
        ...status,
        latestState: text(attempt.STATE),
        latestAttempt: text(attempt.SCHEDULED_TIME),
        latestError: text(attempt.ERROR_MESSAGE),
      };
    }
    const table = probes.tables?.find((row) => text(row.TABLE_NAME) === tableName);
    if (table) {
      status = { ...status, tableExists: true, rowCount: Number(table.ROW_COUNT ?? 0) || 0 };
    }
    result[surfaceKey] = status;
  }
  return result;
}

export { SURFACE_MODEL_RATES };
