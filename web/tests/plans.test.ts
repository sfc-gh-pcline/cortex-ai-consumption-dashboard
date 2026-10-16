// Port of tests/test_refresh_plan.py and the registry checks in tests/test_view_state.py.
import { describe, expect, it } from "vitest";

import {
  DEPENDENCY_KINDS,
  DEP_USER_TAGS,
  DT_SURFACE_KEYS,
  PAGE_CONFIG,
  PAGE_QUERY_ACCELERATION_SQL,
  PAGE_REFRESH_PLANS,
  PAGE_SQL,
  PAGE_TOTAL_CONSUMPTION,
  SURFACE_AGENTS,
  SURFACE_AI_FUNCTIONS,
  SURFACE_COCO,
  SURFACE_COWORK,
  SURFACE_MODEL_RATES,
  SURFACE_REST_API,
  SURFACE_SEARCH,
  SURFACE_WAREHOUSE_ATTRIBUTION,
  aggregateDependenciesFor,
  dependenciesToWarm,
  initialDependenciesFor,
  mainDependencyFor,
  pageHasData,
  reloadDependenciesFor,
  warmDependenciesFor,
} from "@/lib/plans";
import { FILTER_CAPABILITIES, PAGES, urlPath } from "@/lib/view-state";

const plans = Object.values(PAGE_REFRESH_PLANS);

describe("dependency kinds coverage", () => {
  it("registers every plan dependency", () => {
    const undeclared = plans.flatMap((p) =>
      [...p.windowed, ...p.globalCached, ...p.directOnly]
        .filter((key) => !(key in DEPENDENCY_KINDS))
        .map((key) => `${p.pageKey}: ${key}`),
    );
    expect(undeclared).toEqual([]);
  });

  it("backs every windowed dependency with a materialized surface", () => {
    const missing = plans.flatMap((p) =>
      p.windowed.filter((key) => !DT_SURFACE_KEYS.includes(key)),
    );
    expect(missing).toEqual([]);
  });

  it("covers every materialized surface in some page plan", () => {
    const materialized = new Set(plans.flatMap((p) => [...p.windowed, ...p.globalCached]));
    expect(DT_SURFACE_KEYS.filter((key) => !materialized.has(key))).toEqual([]);
  });

  it("classifies the fixed categories", () => {
    expect(DEPENDENCY_KINDS[SURFACE_REST_API]).toBe("direct_only");
    expect(DEPENDENCY_KINDS[SURFACE_MODEL_RATES]).toBe("global_cached");
    expect(DEPENDENCY_KINDS[DEP_USER_TAGS]).toBe("global_cached");
    for (const key of DT_SURFACE_KEYS) {
      expect(["windowed_aggregate", "global_cached"]).toContain(DEPENDENCY_KINDS[key]);
    }
  });
});

describe("refresh plan structure", () => {
  it("has a plan for every page", () => {
    const required = [
      PAGE_TOTAL_CONSUMPTION,
      SURFACE_COCO,
      SURFACE_COWORK,
      SURFACE_AGENTS,
      SURFACE_SEARCH,
      SURFACE_REST_API,
      SURFACE_AI_FUNCTIONS,
      SURFACE_WAREHOUSE_ATTRIBUTION,
      PAGE_SQL,
      PAGE_CONFIG,
    ];
    expect(required.filter((key) => !(key in PAGE_REFRESH_PLANS))).toEqual([]);
  });

  it("gives every data page a windowed or direct dependency", () => {
    const noData = new Set([PAGE_SQL, PAGE_CONFIG, PAGE_QUERY_ACCELERATION_SQL]);
    for (const p of plans) {
      if (noData.has(p.pageKey)) {
        expect(pageHasData(p.pageKey)).toBe(false);
        continue;
      }
      expect(p.windowed.length + p.directOnly.length).toBeGreaterThan(0);
      expect(pageHasData(p.pageKey)).toBe(true);
    }
  });

  it("keeps the inference page direct and unsettled", () => {
    const p = PAGE_REFRESH_PLANS[SURFACE_REST_API];
    expect(p.windowed).toEqual([]);
    expect(p.usesSettledWindow).toBe(false);
  });

  it("never lists a dependency as both windowed and direct", () => {
    for (const p of plans) {
      expect(p.windowed.filter((key) => p.directOnly.includes(key))).toEqual([]);
    }
  });
});

describe("dependency helpers", () => {
  it("warms declared dependencies once, rates before inference", () => {
    expect(warmDependenciesFor(PAGE_TOTAL_CONSUMPTION)).toEqual([
      SURFACE_COCO,
      SURFACE_COWORK,
      SURFACE_AGENTS,
      SURFACE_AI_FUNCTIONS,
      "Cortex Search — Daily",
      SURFACE_MODEL_RATES,
      DEP_USER_TAGS,
      SURFACE_REST_API,
    ]);
    expect(warmDependenciesFor("nope")).toEqual([]);
  });

  it("defers auxiliary facts to detail controls", () => {
    expect(initialDependenciesFor(SURFACE_COWORK)).toEqual([SURFACE_COWORK, DEP_USER_TAGS]);
    expect(initialDependenciesFor(SURFACE_SEARCH)).toEqual(["Cortex Search — Daily"]);
    expect(initialDependenciesFor(SURFACE_WAREHOUSE_ATTRIBUTION)).toEqual([
      SURFACE_WAREHOUSE_ATTRIBUTION,
    ]);
  });

  it("maps page identities to their main facts and aggregate generations", () => {
    expect(mainDependencyFor(SURFACE_SEARCH)).toBe("Cortex Search — Daily");
    expect(mainDependencyFor(SURFACE_COCO)).toBe(SURFACE_COCO);
    expect(aggregateDependenciesFor(SURFACE_REST_API)).toEqual([]);
    expect(aggregateDependenciesFor(SURFACE_COCO)).toEqual([
      SURFACE_COCO,
      SURFACE_WAREHOUSE_ATTRIBUTION,
    ]);
  });

  it("re-warms only missing or stale generations, in order", () => {
    const needed = [SURFACE_COCO, SURFACE_MODEL_RATES, SURFACE_REST_API];
    const warmed = { [SURFACE_COCO]: "2026-09-22", [SURFACE_MODEL_RATES]: "" };
    expect(dependenciesToWarm(needed, warmed, { [SURFACE_COCO]: "2026-09-22" })).toEqual([
      SURFACE_REST_API,
    ]);
    expect(dependenciesToWarm(needed, warmed, { [SURFACE_COCO]: "2026-09-23" })).toEqual([
      SURFACE_COCO,
      SURFACE_REST_API,
    ]);
  });

  it("reloads active usage dependencies without global metadata", () => {
    expect(reloadDependenciesFor(PAGE_TOTAL_CONSUMPTION)).toEqual([
      SURFACE_COCO,
      SURFACE_COWORK,
      SURFACE_AGENTS,
      SURFACE_AI_FUNCTIONS,
      "Cortex Search — Daily",
      SURFACE_REST_API,
    ]);
    expect(reloadDependenciesFor(SURFACE_REST_API)).toEqual([SURFACE_REST_API]);
  });
});

describe("pages", () => {
  it("preserve all dependency keys and routes", () => {
    expect(PAGES).toHaveLength(11);
    expect(new Set(PAGES.map((p) => p.dependencyKey))).toEqual(
      new Set(Object.keys(PAGE_REFRESH_PLANS)),
    );
    expect(new Set(PAGES.map(urlPath)).size).toBe(11);
    expect(urlPath(PAGES[0])).toBe("");
    expect(PAGES.filter((p) => p.group === "AI Services")).toHaveLength(6);
  });

  it("capabilities do not invent attribution", () => {
    expect(FILTER_CAPABILITIES[SURFACE_AGENTS].has("MODEL_NAME")).toBe(false);
    expect(FILTER_CAPABILITIES[SURFACE_COWORK].has("MODEL_NAME")).toBe(false);
    expect(FILTER_CAPABILITIES[SURFACE_SEARCH].has("USER_NAME")).toBe(false);
    expect(FILTER_CAPABILITIES[SURFACE_WAREHOUSE_ATTRIBUTION].has("USER_NAME")).toBe(false);
  });
});
