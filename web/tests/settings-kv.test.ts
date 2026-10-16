import { describe, expect, it } from "vitest";

import { configLocation, parseOwnedObjects, settingsFromKv } from "@/lib/server/settings";
import { DEFAULT_SETTINGS } from "@/lib/settings";

describe("settingsFromKv", () => {
  it("returns defaults for the resolved location when the table is empty", () => {
    expect(settingsFromKv({}, "DB", "S")).toEqual({
      ...DEFAULT_SETTINGS,
      database: "DB",
      schema: "S",
    });
  });

  it("parses persisted values with the Python fallbacks", () => {
    const settings = settingsFromKv(
      {
        enabled: "true",
        database: "COST_MGMT_DB",
        schema: "PUBLIC",
        warehouse_name: "COST_MGMT_WH",
        owned_objects: JSON.stringify([
          { fqn: '"COST_MGMT_WH"', type: "WAREHOUSE" },
          '"COST_MGMT_DB"."PUBLIC"."COST_MGMT_TASK_COCO"',
        ]),
      },
      "DB",
      "S",
    );
    expect(settings.enabled).toBe(true);
    expect(settings.objectPrefix).toBe("CORTEX_DASH");
    expect(settings.ownedObjects).toEqual([
      { fqn: '"COST_MGMT_WH"', type: "WAREHOUSE" },
      { fqn: '"COST_MGMT_DB"."PUBLIC"."COST_MGMT_TASK_COCO"', type: "TASK" },
    ]);
    expect(() => settingsFromKv({ owned_objects: "not json" }, "DB", "S")).toThrow(/owned_objects/);
  });

  it("infers legacy object types", () => {
    expect(parseOwnedObjects(['"WH"', '"D"."S"."T"'])).toEqual([
      { fqn: '"WH"', type: "WAREHOUSE" },
      { fqn: '"D"."S"."T"', type: "TABLE" },
    ]);
    expect(() => parseOwnedObjects([{ fqn: '"D"."S"."X"', type: "bogus" }])).toThrow(
      /owned object type/,
    );
  });

  it("resolves the config table location from the environment", () => {
    expect(
      configLocation({ SNOWFLAKE_DATABASE: "USER$MYOUNG", SNOWFLAKE_SCHEMA: "PUBLIC" }),
    ).toEqual({
      database: "USER$MYOUNG",
      schema: "PUBLIC",
    });
    expect(
      configLocation({
        DASHBOARD_CONFIG_DATABASE: "COST_MGMT_DB",
        SNOWFLAKE_DATABASE: "USER$MYOUNG",
      }),
    ).toEqual({ database: "COST_MGMT_DB", schema: "PUBLIC" });
    expect(configLocation({})).toEqual({ database: "COST_MGMT_DB", schema: "PUBLIC" });
  });
});
