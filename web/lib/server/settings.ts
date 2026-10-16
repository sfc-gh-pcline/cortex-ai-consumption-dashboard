// Acceleration-layer settings persisted in CORTEX_DASHBOARD_CONFIG. Port of
// load_settings in utils/config.py. The table's location comes from
// DASHBOARD_CONFIG_DATABASE / DASHBOARD_CONFIG_SCHEMA, falling back to the
// service's own database and schema (SNOWFLAKE_DATABASE / SNOWFLAKE_SCHEMA), so
// a dev deploy in a personal database can still read the shared COST_MGMT_DB table.
import { TtlCache } from "@/lib/server/cache";
import { runRaw } from "@/lib/server/query";
import {
  CONFIG_TABLE_NAME,
  DEFAULT_SETTINGS,
  type OwnedObject,
  type Settings,
} from "@/lib/settings";
import { fqn } from "@/lib/sql/core";

const settingsCache = new TtlCache<Record<string, string>>({ maxEntries: 4 });
const SETTINGS_TTL_MS = 30_000;

export function configLocation(env: Readonly<Record<string, string | undefined>> = process.env): {
  database: string;
  schema: string;
} {
  return {
    database: env.DASHBOARD_CONFIG_DATABASE || env.SNOWFLAKE_DATABASE || DEFAULT_SETTINGS.database,
    schema: env.DASHBOARD_CONFIG_SCHEMA || env.SNOWFLAKE_SCHEMA || DEFAULT_SETTINGS.schema,
  };
}

// Parses the owned_objects JSON list, tolerating the legacy bare-string form.
export function parseOwnedObjects(raw: unknown): OwnedObject[] {
  if (!Array.isArray(raw)) throw new TypeError("owned_objects must be a JSON array.");
  const objects: OwnedObject[] = [];
  for (const item of raw) {
    if (typeof item === "string") {
      objects.push({ fqn: item, type: inferObjectType(item) });
    } else if (item && typeof item === "object" && typeof (item as OwnedObject).fqn === "string") {
      const typed = item as { fqn: string; type?: string };
      const type = typed.type;
      if (type !== undefined && type !== "TABLE" && type !== "TASK" && type !== "WAREHOUSE") {
        throw new TypeError(`Unsupported owned object type: ${type}.`);
      }
      objects.push({
        fqn: typed.fqn,
        type:
          type === "TABLE" || type === "TASK" || type === "WAREHOUSE"
            ? type
            : inferObjectType(typed.fqn),
      });
    }
  }
  return objects;
}

function inferObjectType(objectFqn: string): OwnedObject["type"] {
  if (objectFqn.includes("_TASK_")) return "TASK";
  if (!objectFqn.includes(".")) return "WAREHOUSE";
  return "TABLE";
}

// Turns the key-value rows into Settings with the same fallbacks as Python.
export function settingsFromKv(
  kv: Readonly<Record<string, string>>,
  database: string,
  schema: string,
): Settings {
  if (Object.keys(kv).length === 0) return { ...DEFAULT_SETTINGS, database, schema };
  let owned: OwnedObject[] = [];
  try {
    owned = parseOwnedObjects(JSON.parse(kv.owned_objects ?? "[]"));
  } catch (err) {
    throw new Error("Invalid owned_objects in dashboard configuration.", { cause: err });
  }
  return {
    enabled: (kv.enabled ?? "false") === "true",
    database: kv.database ?? database,
    schema: kv.schema ?? schema,
    warehouseName: kv.warehouse_name ?? "",
    warehouseSize: kv.warehouse_size ?? "XSMALL",
    objectPrefix: kv.object_prefix ?? "CORTEX_DASH",
    ownedObjects: owned,
    whDegraded: (kv.wh_degraded ?? "false") === "true",
  };
}

async function loadKv(database: string, schema: string): Promise<Record<string, string>> {
  return settingsCache.getOrLoad(`${database}.${schema}`, SETTINGS_TTL_MS, async () => {
    const result = await runRaw({
      sql: `SELECT KEY, VALUE FROM ${fqn(database, schema, CONFIG_TABLE_NAME)}`,
      params: [],
    });
    return Object.fromEntries(
      result.rows.map((row) => [String(row.KEY), row.VALUE === null ? "" : String(row.VALUE)]),
    );
  });
}

// Settings from the config table, or defaults for the resolved location.
export async function loadSettings(): Promise<Settings> {
  const { database, schema } = configLocation();
  return settingsFromKv(await loadKv(database, schema), database, schema);
}

export function invalidateSettings(): void {
  settingsCache.clear();
}
