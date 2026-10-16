// Golden parity: every TypeScript builder must emit exactly what the Python
// builder emitted for the fixture window. Regenerate the fixture with
// `uv run python scripts/export_golden.py` from the repository root.
import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

import type { Settings } from "@/lib/settings";
import { DEFAULT_SETTINGS } from "@/lib/settings";
import {
  DT_BODY_REGISTRY,
  DT_COLUMNS,
  accelerationLifecycleSql,
  accelerationTableSql,
  accelerationTaskSql,
  createRefreshWarehouseSql,
  dtReadSql,
  modelRatesDtSql,
} from "@/lib/sql/acceleration";
import { inlineParams, quoteIdentifier, surfaceToSuffix } from "@/lib/sql/core";
import {
  type ModelRate,
  agentFactsSql,
  aiFnFactsSql,
  cocoFactsSql,
  coworkFactsSql,
  coworkServiceMixSql,
  modelRatesBodySql,
  modelRatesSql,
  restFactsSql,
  searchBatchFactsSql,
  searchDailyFactsSql,
  searchServingFactsSql,
  userTagsSql,
} from "@/lib/sql/facts";
import { ACCELERATION_SQL_REFERENCE, SQL_REFERENCE, sqlReferenceGroups } from "@/lib/sql/reference";
import {
  meteringAttributionOverlapSql,
  meteringProbeSql,
  warehouseBodyForVariant,
  warehouseCreditFactsAttributionOnlySql,
  warehouseCreditFactsSql,
} from "@/lib/sql/warehouse";

type Stmt = { sql: string; params: (string | number | null)[] };

interface Golden {
  window: { start: string; end: string; today: string };
  settings: {
    database: string;
    schema: string;
    warehouse_name: string;
    warehouse_size: string;
    object_prefix: string;
  };
  rates: ModelRate[];
  windowed: Record<string, Stmt>;
  inlined: Record<string, string>;
  static: { model_rates: Stmt; model_rates_body: string; user_tags: string; metering_probe: Stmt };
  dt_reads: Record<string, Stmt>;
  model_rates_dt: Stmt;
  dt_columns: Record<string, string[]>;
  bodies: Record<string, string>;
  ddl: Record<string, { table: string; task: string; lifecycle: Record<string, string> }>;
  warehouse_ddl: string;
  reference: {
    label: string;
    sources: string[];
    note: string;
    group: string;
    variant: string;
    kind: string;
    runtime_key: string | null;
    statement: Stmt | null;
  }[];
  reference_groups: string[];
  acceleration_reference: {
    surface_key: string;
    object_type: string;
    source_views: string[];
    explanation: string;
  }[];
  identifiers: {
    quote: Record<string, { ok?: string; error?: string }>;
    suffix: Record<string, string>;
  };
}

const golden: Golden = JSON.parse(
  fs.readFileSync(path.resolve(__dirname, "../../tests/fixtures/golden_sql.json"), "utf8"),
);
const { start, end, today } = golden.window;
const settings: Settings = {
  ...DEFAULT_SETTINGS,
  enabled: true,
  database: golden.settings.database,
  schema: golden.settings.schema,
  warehouseName: golden.settings.warehouse_name,
  warehouseSize: golden.settings.warehouse_size,
  objectPrefix: golden.settings.object_prefix,
};

const windowed: Record<string, () => Stmt> = {
  coco: () => cocoFactsSql(start, end) as Stmt,
  ai_fn: () => aiFnFactsSql(start, end) as Stmt,
  agent: () => agentFactsSql(start, end) as Stmt,
  cowork: () => coworkFactsSql(start, end) as Stmt,
  cowork_service_mix: () => coworkServiceMixSql(start, end) as Stmt,
  search_daily: () => searchDailyFactsSql(start, end) as Stmt,
  search_serving: () => searchServingFactsSql(start, end) as Stmt,
  search_batch: () => searchBatchFactsSql(start, end) as Stmt,
  warehouse_full: () => warehouseCreditFactsSql(start, end) as Stmt,
  warehouse_attribution_only: () => warehouseCreditFactsAttributionOnlySql(start, end) as Stmt,
  metering_overlap: () => meteringAttributionOverlapSql(start, end) as Stmt,
  rest_with_rates: () => restFactsSql(start, end, golden.rates) as Stmt,
  rest_no_rates: () => restFactsSql(start, end, []) as Stmt,
};

describe("windowed builders", () => {
  it("renders missing numeric rates as SQL NULL", () => {
    expect(inlineParams("SELECT ?::FLOAT, ?", [null, "O'Brien"])).toBe(
      "SELECT NULL::FLOAT, 'O''Brien'",
    );
  });

  it("cover every golden statement", () => {
    expect(Object.keys(windowed).sort()).toEqual(Object.keys(golden.windowed).sort());
  });

  it.each(Object.keys(golden.windowed))("%s matches byte for byte", (name) => {
    const actual = windowed[name]();
    expect(actual.sql).toBe(golden.windowed[name].sql);
    expect(actual.params).toEqual(golden.windowed[name].params);
    expect(inlineParams(actual.sql, actual.params)).toBe(golden.inlined[name]);
  });
});

describe("static statements", () => {
  it("model rates, body, user tags, and probe", () => {
    expect(modelRatesSql(today)).toEqual(golden.static.model_rates);
    expect(modelRatesBodySql()).toBe(golden.static.model_rates_body);
    expect(userTagsSql()).toBe(golden.static.user_tags);
    expect(meteringProbeSql()).toEqual(golden.static.metering_probe);
  });
});

describe("acceleration layer", () => {
  it("column contracts", () => {
    expect(DT_COLUMNS).toEqual(golden.dt_columns);
    expect(Object.keys(DT_BODY_REGISTRY)).toEqual(Object.keys(golden.ddl));
  });

  it.each(Object.keys(golden.dt_reads))("aggregate read for %s", (key) => {
    expect(dtReadSql(key, start, end, settings)).toEqual(golden.dt_reads[key]);
  });

  it("model rates aggregate read", () => {
    expect(modelRatesDtSql(settings)).toEqual(golden.model_rates_dt);
  });

  it.each(Object.keys(DT_BODY_REGISTRY))("body for %s", (key) => {
    expect(DT_BODY_REGISTRY[key].body()).toBe(golden.bodies[key]);
  });

  it("warehouse attribution-only body", () => {
    expect(warehouseBodyForVariant("attribution_only")).toBe(
      golden.bodies["Warehouse Attribution — attribution_only"],
    );
  });

  it.each(Object.keys(golden.ddl))("DDL and lifecycle for %s", (key) => {
    expect(accelerationTableSql(key, settings)).toBe(golden.ddl[key].table);
    expect(accelerationTaskSql(key, settings)).toBe(golden.ddl[key].task);
    expect(accelerationLifecycleSql(key, settings)).toEqual(golden.ddl[key].lifecycle);
  });

  it("task warehouse DDL", () => {
    expect(createRefreshWarehouseSql("cost_mgmt_wh", "xsmall")).toBe(golden.warehouse_ddl);
  });
});

describe("reference catalogues", () => {
  it("SQL reference metadata and statements", () => {
    expect(SQL_REFERENCE.length).toBe(golden.reference.length);
    golden.reference.forEach((expected, index) => {
      const actual = SQL_REFERENCE[index];
      expect(actual.label).toBe(expected.label);
      expect(actual.sources).toEqual(expected.sources);
      expect(actual.note).toBe(expected.note);
      expect(actual.group).toBe(expected.group);
      expect(actual.variant).toBe(expected.variant);
      expect(actual.kind).toBe(expected.kind);
      expect(actual.runtimeKey).toBe(expected.runtime_key);
      if (expected.statement) {
        expect(actual.builder(start, end, { today, rates: golden.rates })).toEqual(
          expected.statement,
        );
      }
    });
    expect(sqlReferenceGroups()).toEqual(golden.reference_groups);
  });

  it("acceleration reference", () => {
    expect(
      ACCELERATION_SQL_REFERENCE.map((e) => ({
        surface_key: e.surfaceKey,
        object_type: e.objectType,
        source_views: [...e.sourceViews],
        explanation: e.explanation,
      })),
    ).toEqual(golden.acceleration_reference);
  });
});

describe("identifiers", () => {
  it("quote rules", () => {
    for (const [input, expected] of Object.entries(golden.identifiers.quote)) {
      if (expected.ok !== undefined) {
        expect(quoteIdentifier(input)).toBe(expected.ok);
      } else {
        expect(() => quoteIdentifier(input)).toThrow(expected.error);
      }
    }
  });

  it("suffixes", () => {
    for (const [input, expected] of Object.entries(golden.identifiers.suffix)) {
      expect(surfaceToSuffix(input)).toBe(expected);
    }
  });
});
