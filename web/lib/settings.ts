// Acceleration-layer settings, mirroring the Settings dataclass in utils/config.py.
// Loading from CORTEX_DASHBOARD_CONFIG lives in the server data layer; this module
// holds only the shape and defaults so SQL builders and tests stay offline.

export interface OwnedObject {
  readonly fqn: string;
  readonly type: "TABLE" | "TASK" | "WAREHOUSE";
}

export interface Settings {
  readonly enabled: boolean;
  readonly database: string;
  readonly schema: string;
  readonly warehouseName: string;
  readonly warehouseSize: string;
  readonly objectPrefix: string;
  readonly ownedObjects: readonly OwnedObject[];
  readonly whDegraded: boolean;
}

// Matches the Settings dataclass defaults in utils/config.py.
export const DEFAULT_SETTINGS: Settings = {
  enabled: false,
  database: "COST_MGMT_DB",
  schema: "PUBLIC",
  warehouseName: "COST_MGMT_WH",
  warehouseSize: "XSMALL",
  objectPrefix: "COST_MGMT",
  ownedObjects: [],
  whDegraded: false,
};

export const CONFIG_TABLE_NAME = "CORTEX_DASHBOARD_CONFIG";
export const TASK_RETENTION_DAYS = 365;

// Task warehouse name, defaulting to <prefix>_WH like the Python builders.
export function taskWarehouse(settings: Settings): string {
  return settings.warehouseName || `${settings.objectPrefix}_WH`;
}
