// Server wiring for the admin mutations: the real AdminIo over the pooled
// connection, one process-level mutex so mutations never interleave (the
// connection_lock in the Streamlit app), cache invalidation after every write,
// and the last provision and lifecycle reports per administrator so a page
// reload or retry still sees prior outcomes.
import {
  ActionPersistenceError,
  adoptExistingObjects,
  provisionTargets,
  reconcileOwnedObjects,
  runOwnedAction,
  saveSettingsStatements,
  type AdminIo,
  type LifecycleAction,
  type LifecycleReport,
  type ProvisionReport,
  emptyReport,
} from "@/lib/admin/actions";
import { configuredObjects, type Statuses } from "@/lib/health";
import { DT_SURFACE_KEYS } from "@/lib/plans";
import { invalidateHealth, probeStatuses } from "@/lib/server/health";
import { clearResultCache, runRaw } from "@/lib/server/query";
import { configLocation, invalidateSettings } from "@/lib/server/settings";
import type { Settings } from "@/lib/settings";
import { DT_BODY_REGISTRY } from "@/lib/sql/acceleration";
import { quoteIdentifier, surfaceToSuffix } from "@/lib/sql/core";

export const adminIo: AdminIo = {
  async execute(sql, params = []) {
    await runRaw({ sql, params });
  },
  async fetch(sql, params = []) {
    try {
      return (await runRaw({ sql, params })).rows;
    } catch {
      return null;
    }
  },
  async saveSettings(settings) {
    await runRaw(saveSettingsStatements(settings, configLocation())[0]);
  },
  loadHealth(settings) {
    invalidateHealth();
    return probeStatuses(settings.database, settings.schema, settings.objectPrefix);
  },
};

// Serializes mutations: each call waits for the previous one to settle.
let chain: Promise<unknown> = Promise.resolve();
export function withMutationLock<T>(fn: () => Promise<T>): Promise<T> {
  const next = chain.then(fn, fn);
  chain = next.catch(() => undefined);
  return next;
}

// Every mutation ends by dropping the settings, health and result caches.
export function invalidateAfterMutation(): void {
  invalidateSettings();
  invalidateHealth();
  clearResultCache();
}

const provisionReports = new Map<string, ProvisionReport>();
const lifecycleReports = new Map<string, LifecycleReport>();

export const lastProvisionReport = (viewer: string) => provisionReports.get(viewer) ?? null;
export const lastLifecycleReport = (viewer: string) => lifecycleReports.get(viewer) ?? null;

export async function saveSettings(settings: Settings): Promise<void> {
  await withMutationLock(() => adminIo.saveSettings(settings));
  invalidateAfterMutation();
}

// Provision every registered surface, or retry the viewer's last failed report.
export async function provision(
  viewer: string,
  settings: Settings,
  retry: boolean,
): Promise<ProvisionReport> {
  const prior = retry ? (provisionReports.get(viewer) ?? emptyReport()) : emptyReport();
  const { report } = await withMutationLock(() =>
    provisionTargets(settings, DT_BODY_REGISTRY, prior, adminIo),
  );
  provisionReports.set(viewer, report);
  invalidateAfterMutation();
  return report;
}

// Runs a lifecycle action and records the outcome, merging retry results into the
// prior report the way the Streamlit page's perform_action does.
export async function lifecycle(
  viewer: string,
  action: LifecycleAction,
  targets: readonly string[],
  settings: Settings,
  retry: boolean,
): Promise<LifecycleReport> {
  const previous = retry ? lifecycleReports.get(viewer) : undefined;
  const results: Record<string, string> = { ...(previous?.results ?? {}) };
  let error = "";
  let persistenceFailed = false;
  try {
    const outcome = await withMutationLock(() =>
      runOwnedAction(action, targets, settings, adminIo),
    );
    Object.assign(results, outcome.results);
  } catch (err) {
    if (err instanceof ActionPersistenceError) {
      Object.assign(results, err.results);
      persistenceFailed = true;
    }
    error = err instanceof Error ? err.message : String(err);
  } finally {
    invalidateAfterMutation();
  }
  const report: LifecycleReport = { action, results, error, persistenceFailed };
  lifecycleReports.set(viewer, report);
  return report;
}

// Take ownership of configured objects that exist but are unregistered.
export async function adopt(settings: Settings): Promise<string[]> {
  const { adopted } = await withMutationLock(() => adoptExistingObjects(settings, adminIo));
  invalidateAfterMutation();
  return adopted;
}

export const untrackedObjects = (settings: Settings) => reconcileOwnedObjects(settings, adminIo);

// Registered tasks whose verified scheduling state allows each action.
export function eligibleTargets(
  settings: Settings,
  statuses: Statuses,
): Record<"suspend" | "resume" | "refresh", string[]> {
  let allowed: ReturnType<typeof configuredObjects> = {};
  try {
    allowed = configuredObjects(settings);
  } catch {
    allowed = {};
  }
  const ownedTasks = settings.ownedObjects.filter(
    (o) => o.type === "TASK" && allowed[o.fqn]?.type === "TASK",
  );
  const taskFqn = (surface: string) =>
    `${quoteIdentifier(settings.database)}.${quoteIdentifier(settings.schema)}.${quoteIdentifier(`${settings.objectPrefix}_TASK_${surfaceToSuffix(surface)}`)}`;
  const inState = (state: string) =>
    ownedTasks
      .filter((o) =>
        DT_SURFACE_KEYS.some((surface) => {
          const status = statuses[surface];
          return (
            status !== undefined &&
            status.taskExists === true &&
            status.probeErrors.length === 0 &&
            status.schedulingState === state &&
            o.fqn === taskFqn(surface)
          );
        }),
      )
      .map((o) => o.fqn);
  return { suspend: inState("ACTIVE"), resume: inState("SUSPENDED"), refresh: inState("ACTIVE") };
}
