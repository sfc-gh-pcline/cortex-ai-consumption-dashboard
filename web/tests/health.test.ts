// Port of the snapshot-identity and generation cases in tests/test_warm_scope.py,
// the source_for cases in tests/test_config.py, plus readiness and probe assembly.
import { describe, expect, it } from "vitest";

import {
  aggregateSnapshotIdentityFor,
  buildStatuses,
  configuredObjects,
  dependencyGeneration,
  depSourceMode,
  emptyStatus,
  hasHealthySurfaces,
  latestSnapshotFreshness,
  parseZonedTimestamp,
  readiness,
  sourceFor,
} from "@/lib/health";
import {
  SURFACE_AGENTS,
  SURFACE_COCO,
  SURFACE_MODEL_RATES,
  SURFACE_REST_API,
  SURFACE_WAREHOUSE_ATTRIBUTION,
} from "@/lib/plans";
import { DEFAULT_SETTINGS } from "@/lib/settings";

const ready = (dataTimestamp: string) =>
  emptyStatus("x", { tableExists: true, schedulingState: "ACTIVE", dataTimestamp });
const enabled = {
  ...DEFAULT_SETTINGS,
  enabled: true,
  database: "DB",
  schema: "S",
  objectPrefix: "P",
};
const disabled = { ...DEFAULT_SETTINGS, enabled: false };

describe("snapshot identity", () => {
  const health = {
    [SURFACE_COCO]: ready("2026-08-20 01:00:00"),
    [SURFACE_WAREHOUSE_ATTRIBUTION]: ready("2026-08-21 09:00:00"),
  };

  it("is direct when disabled", () => {
    expect(aggregateSnapshotIdentityFor([SURFACE_COCO], disabled, health)).toEqual(["direct", ""]);
  });

  it("ignores unrelated task completions", () => {
    expect(aggregateSnapshotIdentityFor([SURFACE_COCO], enabled, health)).toEqual([
      "aggregate",
      "2026-08-20 01:00:00",
    ]);
  });

  it("uses the relevant surfaces' latest generation", () => {
    expect(
      aggregateSnapshotIdentityFor([SURFACE_COCO, SURFACE_WAREHOUSE_ATTRIBUTION], enabled, health),
    ).toEqual(["aggregate", "2026-08-21 09:00:00"]);
  });

  it("falls back to direct without a healthy relevant surface", () => {
    expect(aggregateSnapshotIdentityFor([SURFACE_AGENTS], enabled, health)).toEqual(["direct", ""]);
  });

  it("gives window-stable dependencies an empty generation", () => {
    for (const dep of [SURFACE_MODEL_RATES, "user_tags", SURFACE_REST_API]) {
      expect(dependencyGeneration(dep, enabled, health)).toBe("");
    }
    expect(dependencyGeneration(SURFACE_COCO, enabled, health)).toBe("2026-08-20 01:00:00");
    expect(dependencyGeneration(SURFACE_COCO, disabled, health)).toBe("");
  });
});

describe("source routing", () => {
  it("disabled returns account_usage", () => {
    expect(sourceFor(SURFACE_COCO, disabled, { [SURFACE_COCO]: ready("2026-08-20") })).toBe(
      "account_usage",
    );
  });

  it("inference always reads account_usage", () => {
    expect(sourceFor(SURFACE_REST_API, enabled, {})).toBe("account_usage");
  });

  it("requires a table, an active task, and one successful run", () => {
    expect(sourceFor(SURFACE_COCO, enabled, {})).toBe("account_usage");
    expect(
      sourceFor(SURFACE_COCO, enabled, {
        [SURFACE_COCO]: emptyStatus("x", { tableExists: true, schedulingState: "SUSPENDED" }),
      }),
    ).toBe("account_usage");
    expect(
      sourceFor(SURFACE_COCO, enabled, {
        [SURFACE_COCO]: emptyStatus("x", { tableExists: true, schedulingState: "ACTIVE" }),
      }),
    ).toBe("account_usage");
    expect(sourceFor(SURFACE_COCO, enabled, { [SURFACE_COCO]: ready("2026-08-20") })).toBe("dt");
  });

  it("explains each dependency's mode", () => {
    expect(depSourceMode(SURFACE_REST_API, enabled, {})[0]).toBe("direct_only");
    expect(depSourceMode("user_tags", enabled, {})[0]).toBe("global_cached");
    expect(depSourceMode(SURFACE_COCO, enabled, { [SURFACE_COCO]: ready("t") })).toEqual([
      "aggregate",
      "Reading from aggregate snapshot through the prior UTC day",
    ]);
    expect(depSourceMode(SURFACE_COCO, enabled, {})[1]).toBe(
      "Aggregate table unavailable — reading ACCOUNT_USAGE",
    );
    expect(depSourceMode("nope", enabled, {})[1]).toBe(
      "Unregistered dependency — reading ACCOUNT_USAGE",
    );
  });

  it("reports healthy surfaces and latest freshness", () => {
    const statuses = {
      a: ready("2026-08-20 01:00:00"),
      b: emptyStatus("b", {
        tableExists: true,
        schedulingState: "SUSPENDED",
        dataTimestamp: "2026-09-01",
      }),
    };
    expect(hasHealthySurfaces(statuses)).toBe(true);
    expect(latestSnapshotFreshness(statuses)).toBe("2026-08-20 01:00:00");
    expect(hasHealthySurfaces({})).toBe(false);
    expect(latestSnapshotFreshness({})).toBe("");
  });
});

describe("readiness", () => {
  const now = new Date("2026-09-21T12:00:00Z");

  it("labels each operational state", () => {
    expect(readiness(null, now)).toBe("Unknown");
    expect(readiness(emptyStatus("x", { probeErrors: ["boom"] }), now)).toBe("Unknown");
    expect(readiness(emptyStatus("x", { tableProbeOk: false }), now)).toBe("Unknown");
    expect(readiness(emptyStatus("x"), now)).toBe("Not created");
    expect(readiness(emptyStatus("x", { tableExists: true, taskExists: false }), now)).toBe(
      "Task missing",
    );
    expect(
      readiness(
        emptyStatus("x", { tableExists: true, taskExists: true, latestState: "FAILED" }),
        now,
      ),
    ).toBe("Refresh failed");
    expect(
      readiness(
        emptyStatus("x", { tableExists: true, taskExists: true, schedulingState: "SUSPENDED" }),
        now,
      ),
    ).toBe("Suspended");
    expect(
      readiness(
        emptyStatus("x", { tableExists: true, taskExists: true, schedulingState: "ACTIVE" }),
        now,
      ),
    ).toBe("Awaiting first refresh");
  });

  it("distinguishes fresh, stale, and zone-less snapshots", () => {
    const base = { tableExists: true, taskExists: true, schedulingState: "ACTIVE" };
    expect(
      readiness(emptyStatus("x", { ...base, dataTimestamp: "2026-09-21 01:03:00.000 +0000" }), now),
    ).toBe("Ready");
    expect(
      readiness(emptyStatus("x", { ...base, dataTimestamp: "2026-09-20T01:03:00Z" }), now),
    ).toBe("Stale snapshot");
    expect(
      readiness(emptyStatus("x", { ...base, dataTimestamp: "2026-09-21 01:03:00" }), now),
    ).toBe("Unknown");
    expect(parseZonedTimestamp("2026-09-21 01:03:00.000 -0700")?.toISOString()).toBe(
      "2026-09-21T08:03:00.000Z",
    );
  });
});

describe("buildStatuses", () => {
  it("assembles task, table, and history probes per surface", () => {
    const statuses = buildStatuses("cost_mgmt", {
      tasks: [{ name: "COST_MGMT_TASK_COCO", state: "started" }],
      tables: [{ TABLE_NAME: "COST_MGMT_COCO", ROW_COUNT: "42" }],
      history: [
        {
          NAME: "COST_MGMT_TASK_COCO",
          STATE: "SUCCEEDED",
          SCHEDULED_TIME: "2026-09-21 01:00:00 +0000",
          ERROR_MESSAGE: null,
          LAST_RUN: "2026-09-21 01:03:00 +0000",
        },
        {
          NAME: "COST_MGMT_TASK_COWORK",
          STATE: "FAILED",
          SCHEDULED_TIME: "2026-09-21 01:00:00 +0000",
          ERROR_MESSAGE: "boom",
          LAST_RUN: null,
        },
      ],
      errors: [],
    });
    const coco = statuses[SURFACE_COCO];
    expect(coco.tableExists).toBe(true);
    expect(coco.rowCount).toBe(42);
    expect(coco.taskExists).toBe(true);
    expect(coco.schedulingState).toBe("ACTIVE");
    expect(coco.dataTimestamp).toBe("2026-09-21 01:03:00 +0000");
    expect(coco.latestState).toBe("SUCCEEDED");
    const cowork = statuses.CoWork;
    expect(cowork.tableExists).toBe(false);
    expect(cowork.taskExists).toBe(false);
    expect(cowork.latestState).toBe("FAILED");
    expect(cowork.latestError).toBe("boom");
    expect(Object.keys(statuses)).toHaveLength(10);
  });

  it("marks probe failures as unknown", () => {
    const statuses = buildStatuses("P", {
      tasks: null,
      tables: null,
      history: null,
      errors: ["Task probe: x"],
    });
    expect(statuses[SURFACE_COCO].taskExists).toBeNull();
    expect(statuses[SURFACE_COCO].tableProbeOk).toBe(false);
    expect(readiness(statuses[SURFACE_COCO])).toBe("Unknown");
  });
});

describe("configuredObjects", () => {
  it("enumerates the warehouse plus a table and task per surface", () => {
    const objects = configuredObjects({
      ...DEFAULT_SETTINGS,
      database: "cost_mgmt_db",
      schema: "public",
      warehouseName: "cost_mgmt_wh",
      objectPrefix: "cost_mgmt",
    });
    expect(Object.keys(objects)).toHaveLength(21);
    expect(objects['"COST_MGMT_WH"']).toEqual({ fqn: '"COST_MGMT_WH"', type: "WAREHOUSE" });
    expect(objects['"COST_MGMT_DB"."PUBLIC"."COST_MGMT_TASK_COWORK_SERVICE_MIX"'].type).toBe(
      "TASK",
    );
  });
});
