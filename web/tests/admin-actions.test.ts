// Port of tests/test_admin_actions.py: exact-ownership lifecycle boundaries,
// provisioning preflight, retry semantics and persistence failures, all against
// an in-memory AdminIo.
import { describe, expect, it } from "vitest";

import {
  ActionPersistenceError,
  applySetup,
  emptyReport,
  provisionTargets,
  reconcileOwnedObjects,
  reportAllOk,
  reportFailures,
  runOwnedAction,
  saveSettingsStatements,
  TARGET_CHANGE_REFUSED,
  validateProvisionTargets,
  validateSetupDraft,
  type AdminIo,
  type Row,
} from "@/lib/admin/actions";
import { configuredObjects, emptyStatus, type Statuses } from "@/lib/health";
import { DT_SURFACE_KEYS, SURFACE_AGENTS, SURFACE_COCO } from "@/lib/plans";
import { DEFAULT_SETTINGS, type Settings } from "@/lib/settings";
import { DT_BODY_REGISTRY } from "@/lib/sql/acceleration";

interface Harness {
  io: AdminIo;
  calls: string[];
  saves: Settings[];
  fail: (statement: string) => boolean;
  failSave: boolean;
  health: Statuses;
  inventory: Row[] | null;
}

function harness(overrides: Partial<Harness> = {}): Harness {
  const h: Harness = {
    calls: [],
    saves: [],
    fail: () => false,
    failSave: false,
    health: Object.fromEntries(
      DT_SURFACE_KEYS.map((s) => [
        s,
        emptyStatus(s, { taskExists: true, schedulingState: "ACTIVE" }),
      ]),
    ),
    inventory: [],
    io: null as unknown as AdminIo,
    ...overrides,
  };
  h.io = {
    async execute(sql) {
      h.calls.push(sql);
      if (h.fail(sql)) throw new Error("denied");
    },
    async fetch() {
      return h.inventory;
    },
    async saveSettings(settings) {
      if (h.failSave) throw new Error("save denied");
      h.saves.push(settings);
    },
    async loadHealth() {
      return h.health;
    },
  };
  return h;
}

const registered = (): Settings => ({
  ...DEFAULT_SETTINGS,
  ownedObjects: Object.values(configuredObjects(DEFAULT_SETTINGS)),
});
const firstTask = (s: Settings) => s.ownedObjects.find((o) => o.type === "TASK")!.fqn;

describe("run_owned_action boundaries", () => {
  it("rejects the entire action before mutation when a target is unregistered", async () => {
    const h = harness();
    const settings = registered();
    await expect(
      runOwnedAction(
        "suspend",
        [firstTask(settings), '"COST_MGMT_DB"."PUBLIC"."COST_MGMT_TASK_UNRELATED"'],
        settings,
        h.io,
      ),
    ).rejects.toThrow(/registered target/);
    expect(h.calls).toEqual([]);
  });

  it("performs no mutations for an empty selection", async () => {
    const h = harness();
    expect(await runOwnedAction("suspend", [], registered(), h.io)).toEqual({
      results: {},
      settings: registered(),
    });
    expect(h.calls).toEqual([]);
  });

  it("reports refresh as queued, not as successful ingestion", async () => {
    const h = harness();
    const settings = registered();
    const target = firstTask(settings);
    const { results } = await runOwnedAction("refresh", [target], settings, h.io);
    expect(results).toEqual({ [target]: "Queued" });
    expect(h.calls).toEqual([`EXECUTE TASK ${target}`]);
  });

  it("blocks mutation when the task state is unknown", async () => {
    const h = harness({ health: {} });
    const settings = registered();
    await expect(runOwnedAction("suspend", [firstTask(settings)], settings, h.io)).rejects.toThrow(
      /not verified/,
    );
    expect(h.calls).toEqual([]);
  });

  it("requires TASK targets for task lifecycle actions", async () => {
    const h = harness();
    const settings = registered();
    const table = settings.ownedObjects.find((o) => o.type === "TABLE")!.fqn;
    await expect(runOwnedAction("resume", [table], settings, h.io)).rejects.toThrow(/TASK targets/);
  });

  it("blocks table and warehouse drops after a failed task drop", async () => {
    const h = harness({ fail: () => true });
    const settings = registered();
    const targets = settings.ownedObjects.slice(0, 3).map((o) => o.fqn);
    const task = settings.ownedObjects.slice(0, 3).find((o) => o.type === "TASK")!.fqn;
    const { results, settings: after } = await runOwnedAction("drop", targets, settings, h.io);
    expect(h.calls).toEqual([`DROP TASK IF EXISTS ${task}`]);
    expect(Object.values(results).every((v) => v.startsWith("Failed:"))).toBe(true);
    expect(after.ownedObjects).toHaveLength(21);
  });

  it("keeps a failed drop registered and retries only the failure", async () => {
    const settings = registered();
    const targets = settings.ownedObjects
      .filter((o) => o.type === "TABLE")
      .slice(0, 2)
      .map((o) => o.fqn);
    const h = harness({ fail: (sql) => sql.includes(targets[0]) });
    const first = await runOwnedAction("drop", targets, settings, h.io);
    expect(first.results[targets[0]]).toMatch(/^Failed:/);
    expect(first.results[targets[1]]).toBe("Succeeded");
    const remaining = new Set(first.settings.ownedObjects.map((o) => o.fqn));
    expect(remaining.has(targets[0])).toBe(true);
    expect(remaining.has(targets[1])).toBe(false);
    expect(first.settings.enabled).toBe(false);
    h.calls.length = 0;
    const failed = Object.entries(first.results)
      .filter(([, v]) => v.startsWith("Failed:"))
      .map(([k]) => k);
    await runOwnedAction("drop", failed, first.settings, h.io);
    expect(h.calls).toHaveLength(1);
    expect(h.calls[0]).toContain(targets[0]);
  });

  it("keeps actual outcomes and the saved registry when persistence fails", async () => {
    const settings = { ...registered(), enabled: true };
    const targets = settings.ownedObjects
      .filter((o) => o.type === "TABLE")
      .slice(0, 2)
      .map((o) => o.fqn);
    const h = harness({ fail: (sql) => sql.includes(targets[0]), failSave: true });
    const failure = await runOwnedAction("drop", targets, settings, h.io).catch((e: unknown) => e);
    expect(failure).toBeInstanceOf(ActionPersistenceError);
    expect((failure as ActionPersistenceError).message).toMatch(/save denied/);
    expect((failure as ActionPersistenceError).results).toEqual({
      [targets[0]]: "Failed: denied",
      [targets[1]]: "Succeeded",
    });
    expect(settings.ownedObjects).toHaveLength(21);
    expect(settings.enabled).toBe(true);
  });
});

describe("provisioning preflight", () => {
  it("refuses existing unregistered objects", async () => {
    const h = harness({ inventory: [{ name: "COST_MGMT_WH" }] });
    await expect(validateProvisionTargets(DEFAULT_SETTINGS, h.io)).rejects.toThrow(
      /not registered/,
    );
  });

  it("refuses unknown inventory", async () => {
    const h = harness({ inventory: null });
    await expect(validateProvisionTargets(DEFAULT_SETTINGS, h.io)).rejects.toThrow(/Unknown/);
  });

  it("accepts registered targets and empty inventory", async () => {
    const h = harness({ inventory: [{ name: "COST_MGMT_WH" }] });
    await expect(validateProvisionTargets(registered(), h.io)).resolves.toBeUndefined();
  });
});

describe("provisioning retry", () => {
  const prefixes: Record<string, string> = {
    warehouse: "CREATE WAREHOUSE",
    table: "CREATE TABLE",
    task: "CREATE OR REPLACE TASK",
    resume: "ALTER TASK",
    queue: "EXECUTE TASK",
  };
  const specs = { [SURFACE_COCO]: DT_BODY_REGISTRY[SURFACE_COCO] };

  it.each(["registration", "warehouse", "table", "task", "resume", "queue"])(
    "skips successful steps when retrying after a %s failure",
    async (step) => {
      let failing = true;
      const h = harness();
      h.fail = (sql) => failing && step !== "registration" && sql.startsWith(prefixes[step]);
      const succeeded: string[] = [];
      const baseIo = h.io;
      h.io = {
        ...baseIo,
        async execute(sql) {
          await baseIo.execute(sql);
          succeeded.push(sql);
        },
        async saveSettings(s) {
          if (failing && step === "registration") throw new Error("registration denied");
          await baseIo.saveSettings(s);
        },
      };
      let settings: Settings = { ...DEFAULT_SETTINGS, ownedObjects: [] };
      let out = await provisionTargets(settings, specs, emptyReport(), h.io);
      expect(out.report.error || Object.keys(reportFailures(out.report)).length > 0).toBeTruthy();
      if (step === "registration") {
        expect(h.calls).toEqual([]);
        expect(out.settings.ownedObjects).toEqual([]);
      }
      settings = out.settings;
      const already = [...succeeded];
      failing = false;
      h.calls.length = 0;
      out = await provisionTargets(settings, specs, out.report, h.io);
      expect(reportAllOk(out.report)).toBe(true);
      expect(out.report.results).toEqual({ [SURFACE_COCO]: "ok" });
      expect(h.calls.some((sql) => already.includes(sql))).toBe(false);
      h.calls.length = 0;
      out = await provisionTargets(out.settings, specs, out.report, h.io);
      expect(h.calls).toEqual([]);
    },
  );

  it("refuses a retry after the saved target changed", async () => {
    const h = harness();
    const first = await provisionTargets(registered(), {}, emptyReport(), h.io);
    h.calls.length = 0;
    const moved = { ...first.settings, database: "OTHER" };
    const second = await provisionTargets(moved, {}, first.report, h.io);
    expect(second.report.error).toMatch(/target changed/);
    expect(h.calls).toEqual([]);
  });

  it("retries a failed surface without recreating a successful one", async () => {
    let failing = true;
    const h = harness();
    h.fail = (sql) => failing && sql.startsWith("EXECUTE TASK") && sql.includes("TASK_COCO");
    const two = {
      [SURFACE_COCO]: DT_BODY_REGISTRY[SURFACE_COCO],
      [SURFACE_AGENTS]: DT_BODY_REGISTRY[SURFACE_AGENTS],
    };
    const first = await provisionTargets(registered(), two, emptyReport(), h.io);
    expect(first.report.results[SURFACE_AGENTS]).toBe("ok");
    expect(reportFailures(first.report)).toEqual({ [SURFACE_COCO]: "denied" });
    h.calls.length = 0;
    failing = false;
    const second = await provisionTargets(first.settings, two, first.report, h.io);
    expect(reportAllOk(second.report)).toBe(true);
    expect(h.calls).toEqual(['EXECUTE TASK "COST_MGMT_DB"."PUBLIC"."COST_MGMT_TASK_COCO"']);
  });

  it("refuses a retry after ownership was lost", async () => {
    const h = harness();
    const first = await provisionTargets(registered(), {}, emptyReport(), h.io);
    const lost = { ...first.settings, ownedObjects: first.settings.ownedObjects.slice(0, -1) };
    h.calls.length = 0;
    const second = await provisionTargets(lost, {}, first.report, h.io);
    expect(second.report.error).toMatch(/ownership changed/);
    expect(h.calls).toEqual([]);
  });

  it("registers every planned object in one write before any DDL", async () => {
    const h = harness();
    const out = await provisionTargets(
      { ...DEFAULT_SETTINGS, ownedObjects: [] },
      specs,
      emptyReport(),
      h.io,
    );
    expect(h.saves).toHaveLength(1);
    expect(h.saves[0].ownedObjects).toHaveLength(21);
    expect(out.settings.ownedObjects).toHaveLength(21);
    expect(h.calls[0]).toMatch(/^CREATE WAREHOUSE IF NOT EXISTS/);
  });
});

describe("setup and persistence", () => {
  it("labels invalid identifiers by field and refuses target changes with registered objects", () => {
    const draft = {
      database: "bad;name",
      schema: "PUBLIC",
      warehouseName: "COST_MGMT_WH",
      warehouseSize: "XSMALL",
      objectPrefix: "COST_MGMT",
    };
    expect(Object.keys(validateSetupDraft(draft))).toEqual(["Database"]);
    expect(() => applySetup(DEFAULT_SETTINGS, draft)).toThrow(/^Database:/);
    expect(() => applySetup(registered(), { ...draft, database: "OTHER" })).toThrow(
      TARGET_CHANGE_REFUSED,
    );
    const resized = applySetup(registered(), {
      ...draft,
      database: "COST_MGMT_DB",
      warehouseSize: "SMALL",
    });
    expect(resized.warehouseSize).toBe("SMALL");
  });

  it("writes every key with one MERGE against the config location", () => {
    const [merge] = saveSettingsStatements(registered(), { database: "CFG", schema: "S" });
    expect(merge.sql).toContain('MERGE INTO "CFG"."S"."CORTEX_DASHBOARD_CONFIG"');
    expect(saveSettingsStatements(registered(), { database: "CFG", schema: "S" })).toHaveLength(1);
    expect(merge.params).toHaveLength(16);
    expect(merge.params[0]).toBe("enabled");
    expect(JSON.parse(String(merge.params[13]))).toHaveLength(21);
  });

  it("lists untracked tables and treats unreadable inventory as none", async () => {
    const h = harness({
      inventory: [{ TABLE_NAME: "COST_MGMT_COCO" }, { TABLE_NAME: "COST_MGMT_ORPHAN" }],
    });
    expect(await reconcileOwnedObjects(registered(), h.io)).toEqual([
      '"COST_MGMT_DB"."PUBLIC"."COST_MGMT_ORPHAN"',
    ]);
    expect(await reconcileOwnedObjects(registered(), harness({ inventory: null }).io)).toEqual([]);
  });
});

describe("adopting existing objects", () => {
  it("registers only configured names that exist and are unowned", async () => {
    const { adoptExistingObjects } = await import("@/lib/admin/actions");
    const h = harness({
      inventory: [{ name: "COST_MGMT_WH" }, { name: "COST_MGMT_COCO" }, { name: "UNRELATED" }],
    });
    const out = await adoptExistingObjects({ ...DEFAULT_SETTINGS, ownedObjects: [] }, h.io);
    // The same inventory answers all three SHOW statements, so both matching names
    // are found under every type they are configured as.
    expect(out.adopted).toEqual(['"COST_MGMT_WH"', '"COST_MGMT_DB"."PUBLIC"."COST_MGMT_COCO"']);
    expect(h.saves).toHaveLength(1);
    expect(out.settings.ownedObjects.map((o) => o.type)).toEqual(["WAREHOUSE", "TABLE"]);
    expect(h.calls).toEqual([]);
  });

  it("refuses when nothing unregistered exists or inventory is unknown", async () => {
    const { adoptExistingObjects } = await import("@/lib/admin/actions");
    await expect(
      adoptExistingObjects(registered(), harness({ inventory: [{ name: "COST_MGMT_WH" }] }).io),
    ).rejects.toThrow(/No unregistered/);
    await expect(
      adoptExistingObjects(DEFAULT_SETTINGS, harness({ inventory: [] }).io),
    ).rejects.toThrow(/No unregistered/);
    await expect(
      adoptExistingObjects(DEFAULT_SETTINGS, harness({ inventory: null }).io),
    ).rejects.toThrow(/Unknown WAREHOUSE inventory/);
  });
});
