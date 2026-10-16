// Admin mutations for the acceleration layer: settings persistence, provisioning,
// task lifecycle actions, teardown and ownership reconciliation. Port of the
// mutation half of utils/config.py. Every Snowflake call goes through the injected
// AdminIo so the logic is testable offline; the server wiring lives in
// lib/server/admin.ts. Settings are treated as immutable: functions return the
// updated Settings alongside their results instead of mutating the argument.
import { configuredObjects, type Statuses } from "@/lib/health";
import { DT_SURFACE_KEYS } from "@/lib/plans";
import { CONFIG_TABLE_NAME, type OwnedObject, type Settings } from "@/lib/settings";
import {
  createAggregateTableSql,
  createRefreshTaskSql,
  createRefreshWarehouseSql,
  dropObjectSql,
  executeTaskSql,
  resumeTaskSql,
  resizeRefreshWarehouseSql,
  suspendTaskSql,
  warehouseBodySqlAttributionOnly,
  type DtBodySpec,
} from "@/lib/sql/acceleration";
import { fqn, quoteIdentifier, surfaceToSuffix, type Param, type Statement } from "@/lib/sql/core";
import { meteringProbeSql } from "@/lib/sql/warehouse";

export type Row = Record<string, unknown>;

export interface AdminIo {
  // Runs a DDL/DML statement; rejects on failure.
  execute(sql: string, params?: readonly Param[]): Promise<void>;
  // Runs a metadata read; resolves null when the inventory cannot be read.
  fetch(sql: string, params?: readonly Param[]): Promise<Row[] | null>;
  // Persists settings (save_settings); rejects on failure.
  saveSettings(settings: Settings): Promise<void>;
  // Fresh (uncached) aggregate health for the settings' target.
  loadHealth(settings: Settings): Promise<Statuses>;
}

export type LifecycleAction = "suspend" | "resume" | "refresh" | "drop";
export const LIFECYCLE_ACTIONS: readonly LifecycleAction[] = [
  "suspend",
  "resume",
  "refresh",
  "drop",
];

// ---------------------------------------------------------------------------
// Settings persistence (MERGE-based key-value store)
// ---------------------------------------------------------------------------

// Statement that persists every setting with one set-based MERGE (save_settings).
// The table location is the one the app reads from, so a Setup change of the
// acceleration database never moves the config table itself. sql/setup.sql owns
// table creation, keeping runtime persistence atomic.
export function saveSettingsStatements(
  settings: Settings,
  location: { database: string; schema: string },
): Statement[] {
  const table = fqn(location.database, location.schema, CONFIG_TABLE_NAME);
  const pairs: [string, string][] = [
    ["enabled", String(settings.enabled)],
    ["database", settings.database],
    ["schema", settings.schema],
    ["warehouse_name", settings.warehouseName],
    ["warehouse_size", settings.warehouseSize],
    ["object_prefix", settings.objectPrefix],
    [
      "owned_objects",
      JSON.stringify(settings.ownedObjects.map((o) => ({ fqn: o.fqn, type: o.type }))),
    ],
    ["wh_degraded", String(settings.whDegraded)],
  ];
  const valuesSql = pairs.map(() => "(?, ?)").join(", ");
  return [
    {
      sql:
        `MERGE INTO ${table} t ` +
        `USING (SELECT COLUMN1 AS KEY, COLUMN2 AS VALUE FROM VALUES ${valuesSql}) s ` +
        "ON t.KEY = s.KEY " +
        "WHEN MATCHED THEN UPDATE SET VALUE = s.VALUE, UPDATED_AT = CURRENT_TIMESTAMP() " +
        "WHEN NOT MATCHED THEN INSERT (KEY, VALUE) VALUES (s.KEY, s.VALUE)",
      params: pairs.flat(),
    },
  ];
}

// ---------------------------------------------------------------------------
// Setup form
// ---------------------------------------------------------------------------

export interface SetupDraft {
  readonly database: string;
  readonly schema: string;
  readonly warehouseName: string;
  readonly warehouseSize: string;
  readonly objectPrefix: string;
}

export const WAREHOUSE_SIZES = ["XSMALL", "SMALL", "MEDIUM", "LARGE", "XLARGE"] as const;

// Field-labelled identifier errors for the Setup form; empty when the draft is valid.
export function validateSetupDraft(draft: SetupDraft): Record<string, string> {
  const errors: Record<string, string> = {};
  for (const [label, value] of [
    ["Database", draft.database],
    ["Schema", draft.schema],
    ["Task warehouse", draft.warehouseName],
    ["Object name prefix", draft.objectPrefix],
  ] as const) {
    try {
      quoteIdentifier(value);
    } catch (err) {
      errors[label] = err instanceof Error ? err.message : String(err);
    }
  }
  if (!(WAREHOUSE_SIZES as readonly string[]).includes(draft.warehouseSize)) {
    errors["Warehouse size"] = `Unsupported warehouse size ${JSON.stringify(draft.warehouseSize)}.`;
  }
  return errors;
}

export const TARGET_CHANGE_REFUSED =
  "Registered objects still use the saved target. Tear them down successfully before changing database, schema, warehouse name or prefix.";

// The saved settings with the draft applied, or an error when registered objects
// would be orphaned by the change.
export function applySetup(settings: Settings, draft: SetupDraft): Settings {
  const errors = validateSetupDraft(draft);
  const first = Object.entries(errors)[0];
  if (first) throw new Error(`${first[0]}: ${first[1]}`);
  const candidate: Settings = { ...settings, ...draft };
  if (
    settings.ownedObjects.length > 0 &&
    !sameObjects(configuredObjects(candidate), configuredObjects(settings))
  ) {
    throw new Error(TARGET_CHANGE_REFUSED);
  }
  return candidate;
}

function sameObjects(a: Record<string, OwnedObject>, b: Record<string, OwnedObject>): boolean {
  const ka = Object.keys(a);
  return ka.length === Object.keys(b).length && ka.every((k) => b[k]?.type === a[k].type);
}

// ---------------------------------------------------------------------------
// Provisioning
// ---------------------------------------------------------------------------

// Rejects existing unregistered objects and unknown metadata before registration.
export async function validateProvisionTargets(settings: Settings, io: AdminIo): Promise<void> {
  const expected = configuredObjects(settings);
  const owned = new Set(settings.ownedObjects.map((o) => o.fqn));
  const namespace = `${quoteIdentifier(settings.database)}.${quoteIdentifier(settings.schema)}`;
  for (const [objectType, statement] of [
    ["WAREHOUSE", "SHOW WAREHOUSES"],
    ["TABLE", `SHOW TABLES IN SCHEMA ${namespace}`],
    ["TASK", `SHOW TASKS IN SCHEMA ${namespace}`],
  ] as const) {
    const rows = await io.fetch(statement);
    if (rows === null) throw new Error(`Unknown ${objectType} inventory; provisioning blocked.`);
    if (rows.length === 0) continue;
    if (!rows.every((row) => "name" in row || "NAME" in row)) {
      throw new Error(`Invalid ${objectType} inventory; provisioning blocked.`);
    }
    for (const row of rows) {
      let objectFqn = quoteIdentifier(String(row.name ?? row.NAME));
      if (objectType !== "WAREHOUSE") objectFqn = `${namespace}.${objectFqn}`;
      if (objectFqn in expected && !owned.has(objectFqn)) {
        throw new Error(`Existing target is not registered to this app: ${objectFqn}`);
      }
    }
  }
}

export interface ProvisionReport {
  // surface -> "ok" | error message
  readonly results: Record<string, string>;
  // step label -> "ok" | error message
  readonly steps: Record<string, string>;
  // (database, schema, warehouse, size, prefix) the report belongs to
  readonly target: readonly string[] | null;
  readonly error: string;
}

export const emptyReport = (): ProvisionReport => ({
  results: {},
  steps: {},
  target: null,
  error: "",
});
export const reportFailures = (r: ProvisionReport): Record<string, string> =>
  Object.fromEntries(Object.entries(r.results).filter(([, v]) => v !== "ok"));
export const reportAllOk = (r: ProvisionReport): boolean =>
  !r.error && Object.values(r.results).every((v) => v === "ok");

// Pre-registers new objects in one settings write before provisioning DDL
// (register_owned_objects), so a CREATE that succeeds is always tracked for teardown.
export async function registerOwnedObjects(
  objects: readonly OwnedObject[],
  settings: Settings,
  io: AdminIo,
): Promise<Settings> {
  const existing = new Set(settings.ownedObjects.map((o) => o.fqn));
  const fresh = objects.filter((o) => !existing.has(o.fqn));
  if (fresh.length === 0) return settings;
  const candidate: Settings = { ...settings, ownedObjects: [...settings.ownedObjects, ...fresh] };
  await io.saveSettings(candidate);
  return candidate;
}

const provisionTarget = (s: Settings) => [
  s.database,
  s.schema,
  s.warehouseName,
  s.warehouseSize,
  s.objectPrefix,
];

// Provisions, or retries failed steps without replaying successful object
// mutations (provision_targets). Returns the updated report and settings.
export async function provisionTargets(
  settings: Settings,
  specs: Readonly<Record<string, DtBodySpec>>,
  prior: ProvisionReport,
  io: AdminIo,
): Promise<{ report: ProvisionReport; settings: Settings }> {
  const target = provisionTarget(settings);
  if (prior.target && prior.target.join("\u0000") !== target.join("\u0000")) {
    return {
      report: {
        ...prior,
        error: "Saved provisioning target changed; previous steps cannot be retried.",
      },
      settings,
    };
  }
  const results = { ...prior.results };
  const steps = { ...prior.steps };
  const report = (error: string): ProvisionReport => ({ results, steps, target, error });
  let current = settings;
  try {
    await validateProvisionTargets(current, io);
    const planned = Object.values(configuredObjects(current));
    const ownedFqns = new Set(current.ownedObjects.map((o) => `${o.fqn}\u0000${o.type}`));
    if (
      Object.keys(steps).length > 0 &&
      planned.some((o) => !ownedFqns.has(`${o.fqn}\u0000${o.type}`))
    ) {
      return {
        report: report("Provisioning ownership changed; retry refused."),
        settings: current,
      };
    }
    current = await registerOwnedObjects(planned, current, io);
  } catch (err) {
    return {
      report: report(`Provisioning preflight/registration refused: ${message(err)}`),
      settings: current,
    };
  }

  const executeStep = async (key: string, statement: string) => {
    if (steps[key] === "ok") return;
    try {
      await io.execute(statement);
    } catch (err) {
      steps[key] = message(err);
      throw err;
    }
    steps[key] = "ok";
  };

  try {
    await executeStep(
      "Task warehouse",
      createRefreshWarehouseSql(current.warehouseName, current.warehouseSize),
    );
    if (
      current.ownedObjects.some(
        (obj) => obj.type === "WAREHOUSE" && obj.fqn === quoteIdentifier(current.warehouseName),
      )
    ) {
      await executeStep(
        "Task warehouse size",
        resizeRefreshWarehouseSql(current.warehouseName, current.warehouseSize),
      );
    }
  } catch (err) {
    return { report: report(`Task warehouse/context failed: ${message(err)}`), settings: current };
  }

  const namespace = `${quoteIdentifier(current.database)}.${quoteIdentifier(current.schema)}`;
  const probe = meteringProbeSql();
  const warehouseDegraded = (await io.fetch(probe.sql, probe.params)) === null;
  if (warehouseDegraded !== current.whDegraded) {
    current = { ...current, whDegraded: warehouseDegraded };
    await io.saveSettings(current);
  }
  for (const [surface, sourceSpec] of Object.entries(specs)) {
    if (results[surface] === "ok") continue;
    const spec =
      sourceSpec.degradedFlagKey && warehouseDegraded
        ? { ...sourceSpec, body: warehouseBodySqlAttributionOnly }
        : sourceSpec;
    const suffix = surfaceToSuffix(surface);
    const table = `${namespace}.${quoteIdentifier(`${current.objectPrefix}_${suffix}`)}`;
    const task = `${namespace}.${quoteIdentifier(`${current.objectPrefix}_TASK_${suffix}`)}`;
    try {
      for (const [key, statement] of [
        [`Create ${table}`, createAggregateTableSql(spec, current)],
        [`Create ${task}`, createRefreshTaskSql(spec, current)],
        [`Resume ${task}`, resumeTaskSql(task)],
        [`Queue ${task}`, executeTaskSql(task)],
      ] as const) {
        await executeStep(key, statement);
      }
      results[surface] = "ok";
    } catch (err) {
      results[surface] = message(err);
    }
  }
  return { report: report(""), settings: current };
}

// Objects from the configured set that exist in the account (SHOW inventory).
async function existingConfiguredObjects(settings: Settings, io: AdminIo): Promise<OwnedObject[]> {
  const expected = configuredObjects(settings);
  const namespace = `${quoteIdentifier(settings.database)}.${quoteIdentifier(settings.schema)}`;
  const found: OwnedObject[] = [];
  for (const [objectType, statement] of [
    ["WAREHOUSE", "SHOW WAREHOUSES"],
    ["TABLE", `SHOW TABLES IN SCHEMA ${namespace}`],
    ["TASK", `SHOW TASKS IN SCHEMA ${namespace}`],
  ] as const) {
    const rows = await io.fetch(statement);
    if (rows === null) throw new Error(`Unknown ${objectType} inventory; adoption blocked.`);
    for (const row of rows) {
      let objectFqn = quoteIdentifier(String(row.name ?? row.NAME));
      if (objectType !== "WAREHOUSE") objectFqn = `${namespace}.${objectFqn}`;
      const planned = expected[objectFqn];
      if (planned && planned.type === objectType) found.push(planned);
    }
  }
  return found;
}

// Registers configured objects that already exist but are not owned, so objects
// created outside the app (for example by sql/config.sql) become eligible for
// lifecycle actions and teardown. Only the exact configured names are adopted.
export async function adoptExistingObjects(
  settings: Settings,
  io: AdminIo,
): Promise<{ settings: Settings; adopted: string[] }> {
  const owned = new Set(settings.ownedObjects.map((o) => o.fqn));
  const candidates = (await existingConfiguredObjects(settings, io)).filter(
    (o) => !owned.has(o.fqn),
  );
  if (candidates.length === 0) {
    throw new Error("No unregistered configured objects exist to adopt.");
  }
  const next = await registerOwnedObjects(candidates, settings, io);
  return { settings: next, adopted: candidates.map((o) => o.fqn) };
}

// ---------------------------------------------------------------------------
// Lifecycle actions and teardown
// ---------------------------------------------------------------------------

// Carries completed resource outcomes when the subsequent settings write fails.
export class ActionPersistenceError extends Error {
  constructor(
    readonly results: Record<string, string>,
    cause: unknown,
  ) {
    super(
      `Resource outcomes below are final, but configuration persistence failed: ${message(cause)}. ` +
        "The saved ownership registry may still include dropped objects; reload and reconcile before further actions.",
    );
    this.name = "ActionPersistenceError";
  }
}

const TASK_BUILDERS: Record<Exclude<LifecycleAction, "drop">, (task: string) => string> = {
  suspend: suspendTaskSql,
  resume: resumeTaskSql,
  refresh: executeTaskSql,
};
const DROP_ORDER: Record<OwnedObject["type"], number> = { TASK: 0, TABLE: 1, WAREHOUSE: 2 };

// Surface whose refresh task has this FQN, for health lookups.
function surfaceForTask(taskFqn: string, settings: Settings): string | undefined {
  return DT_SURFACE_KEYS.find((key) =>
    taskFqn.endsWith(
      `.${quoteIdentifier(`${settings.objectPrefix}_TASK_${surfaceToSuffix(key)}`)}`,
    ),
  );
}

// Revalidates every action target before executing; retains individual failures
// (run_owned_action). Drop persists the surviving registry and disables acceleration.
export async function runOwnedAction(
  action: LifecycleAction,
  targets: readonly string[],
  settings: Settings,
  io: AdminIo,
): Promise<{ results: Record<string, string>; settings: Settings }> {
  if (!LIFECYCLE_ACTIONS.includes(action)) throw new Error("Unsupported lifecycle action.");
  const expected = configuredObjects(settings);
  const owned = new Map(settings.ownedObjects.map((o) => [o.fqn, o]));
  const selected: OwnedObject[] = [];
  for (const target of [...new Set(targets)]) {
    const obj = owned.get(target);
    if (!obj || !(target in expected) || obj.type !== expected[target].type) {
      throw new Error(`Not an exact registered target: ${target}`);
    }
    if (action !== "drop" && obj.type !== "TASK") {
      throw new Error("Task lifecycle actions require registered TASK targets.");
    }
    selected.push(obj);
  }
  if (selected.length === 0) return { results: {}, settings };

  if (action !== "drop") {
    const health = await io.loadHealth(settings);
    const expectedState = action === "resume" ? "SUSPENDED" : "ACTIVE";
    for (const obj of selected) {
      const surface = surfaceForTask(obj.fqn, settings);
      const status = surface ? health[surface] : undefined;
      if (
        !status ||
        status.probeErrors.length > 0 ||
        !status.taskExists ||
        status.schedulingState !== expectedState
      ) {
        throw new Error(`Task state is not verified for ${action}: ${obj.fqn}`);
      }
    }
  }

  const results: Record<string, string> = {};
  for (const obj of [...selected].sort((a, b) => DROP_ORDER[a.type] - DROP_ORDER[b.type])) {
    const failedTask = Object.entries(results).some(
      ([target, value]) => value.startsWith("Failed:") && owned.get(target)?.type === "TASK",
    );
    if (action === "drop" && obj.type !== "TASK" && failedTask) {
      results[obj.fqn] = "Failed: blocked by task drop failure; no mutation attempted";
      continue;
    }
    const statement =
      action === "drop" ? dropObjectSql(obj.fqn, obj.type) : TASK_BUILDERS[action](obj.fqn);
    try {
      await io.execute(statement);
      results[obj.fqn] = action === "refresh" ? "Queued" : "Succeeded";
    } catch (err) {
      results[obj.fqn] = `Failed: ${message(err)}`;
    }
  }

  if (action !== "drop") return { results, settings };
  const candidate: Settings = {
    ...settings,
    enabled: false,
    ownedObjects: settings.ownedObjects.filter((o) => results[o.fqn] !== "Succeeded"),
  };
  try {
    await io.saveSettings(candidate);
  } catch (err) {
    throw new ActionPersistenceError(results, err);
  }
  return { results, settings: candidate };
}

// Outcome of one lifecycle action as the Config page shows it (perform_action).
export interface LifecycleReport {
  readonly action: LifecycleAction;
  readonly results: Record<string, string>;
  readonly error: string;
  readonly persistenceFailed: boolean;
}

// ---------------------------------------------------------------------------
// Reconciliation
// ---------------------------------------------------------------------------

// Tables matching this app's naming convention that are not registered for
// teardown (_reconcile_owned_objects). Unreadable inventory reads as none found.
export async function reconcileOwnedObjects(settings: Settings, io: AdminIo): Promise<string[]> {
  if (!settings.database || !settings.schema) return [];
  const prefix = settings.objectPrefix.toUpperCase();
  let tables: Row[] | null;
  try {
    tables = await io.fetch(
      `SELECT TABLE_NAME FROM ${quoteIdentifier(settings.database)}.INFORMATION_SCHEMA.TABLES ` +
        "WHERE TABLE_SCHEMA = ? AND TABLE_NAME LIKE ?",
      [settings.schema.toUpperCase(), `${prefix}_%`],
    );
  } catch {
    return [];
  }
  const owned = new Set(settings.ownedObjects.map((o) => o.fqn));
  return (tables ?? [])
    .map((row) => fqn(settings.database, settings.schema, String(row.TABLE_NAME)))
    .filter((objectFqn) => !owned.has(objectFqn));
}

const message = (err: unknown) => (err instanceof Error ? err.message : String(err));
