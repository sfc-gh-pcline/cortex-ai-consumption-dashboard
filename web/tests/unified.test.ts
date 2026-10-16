// Port of tests/test_unified_facts.py plus the scope boundary from tests/test_comparisons.py.
import { describe, expect, it } from "vitest";

import { totals } from "@/lib/agg/metrics";
import { comparePeriods, daily, trendDelta } from "@/lib/agg/periods";
import { options } from "@/lib/agg/rows";
import { applyFilters } from "@/lib/agg/scope";
import { scopedUsage } from "@/lib/agg/scoped";
import { UNIFIED_COLUMNS, UNIFIED_SOURCES, unifyFacts } from "@/lib/agg/unified";
import { withCoverage } from "@/lib/coverage";
import { resolvePeriods } from "@/lib/dates";
import {
  DEP_SEARCH_DAILY,
  SURFACE_COCO,
  SURFACE_REST_API,
  SURFACE_SEARCH,
  SURFACES,
} from "@/lib/plans";
import { ComparisonState, CoverageState, type SourceCoverage } from "@/lib/view-state";

const PERIODS = resolvePeriods("2026-09-20", "7d");
const WINDOW = { start: PERIODS.previous!.start, end: PERIODS.current!.end };
const META = { readStartedAt: "2026-09-20T08:00:00Z", fetchedAt: "2026-09-20T08:00:00Z" };
const covered = (dependency: string) => withCoverage(META, dependency, WINDOW, "direct");

describe("unifyFacts", () => {
  it("lists the six surfaces in pill order", () => {
    expect(SURFACES).toEqual([
      "CoCo",
      "CoWork",
      "Cortex Agents",
      "Cortex Search",
      "Cortex Inference API",
      "Cortex AI Functions",
    ]);
    expect(UNIFIED_SOURCES.map((s) => s.dependency)).toContain(DEP_SEARCH_DAILY);
  });

  it("includes Search without a user identity", () => {
    const results = Object.fromEntries(
      UNIFIED_SOURCES.map((s) => [
        s.dependency,
        {
          rows:
            s.surface === SURFACE_SEARCH
              ? [
                  {
                    USAGE_DATE: "2026-08-01",
                    SERVICE_NAME: "S",
                    CONSUMPTION_TYPE: "SERVING",
                    MODEL_NAME: "(none)",
                    CALLS: 4,
                    TOKENS: 0,
                    CREDITS: 1.5,
                  },
                ]
              : [],
          coverage: covered(s.dependency),
        },
      ]),
    );
    const unified = unifyFacts(results, WINDOW);
    expect(unified.rows.map((r) => r.SURFACE)).toEqual([SURFACE_SEARCH]);
    expect(unified.rows[0].USER_NAME).toBeNull();
    expect(Object.keys(unified.rows[0])).toEqual([...UNIFIED_COLUMNS]);
    expect(totals(unified.rows).CREDITS).toBe(1.5);
    expect(unified.unavailableSources).toEqual([]);
    expect(unified.coverages).toHaveLength(6);
  });

  it("separates Search rows from a user filter instead of dropping them", () => {
    const rows = [
      {
        SURFACE: SURFACE_COCO,
        USER_NAME: "ALICE",
        MODEL_NAME: "model-a",
        CREDITS: 1,
        TOKENS: 10,
        CALLS: 1,
      },
      {
        SURFACE: SURFACE_SEARCH,
        USER_NAME: null,
        MODEL_NAME: "(none)",
        CREDITS: 2,
        TOKENS: 0,
        CALLS: 2,
      },
    ];
    const filtered = applyFilters(rows, { users: ["ALICE"], models: ["model-a"] });
    expect(filtered.matched.map((r) => r.SURFACE)).toEqual([SURFACE_COCO]);
    expect(filtered.unscoped.map((r) => r.SURFACE)).toEqual([SURFACE_SEARCH]);
    expect(filtered.excluded).toEqual([]);
  });

  it("records an unavailable source without turning it into zero usage", () => {
    const unified = unifyFacts({}, WINDOW);
    expect(unified.rows).toEqual([]);
    expect(unified.unavailableSources).toEqual(UNIFIED_SOURCES.map((s) => s.surface));
    expect(unified.coverages.every((c) => c.state === CoverageState.UNAVAILABLE)).toBe(true);
    const comparison = comparePeriods(unified.rows, PERIODS, unified.coverages);
    expect(comparison.state).toBe(ComparisonState.UNAVAILABLE);
  });

  it.each(["empty", "missing_metadata", "unavailable"])(
    "every source contributes coverage (%s search outcome)",
    (outcome) => {
      const results = Object.fromEntries(
        UNIFIED_SOURCES.map((s) => {
          let coverage: SourceCoverage = covered(s.dependency);
          if (s.surface === SURFACE_SEARCH && outcome === "missing_metadata") {
            coverage = withCoverage(
              { readStartedAt: null, fetchedAt: null },
              s.dependency,
              WINDOW,
              "direct",
            );
          }
          if (s.surface === SURFACE_SEARCH && outcome === "unavailable") {
            coverage = {
              ...coverage,
              state: CoverageState.UNAVAILABLE,
              fetchedAt: null,
              served: null,
            };
          }
          return [s.dependency, { rows: [], coverage }];
        }),
      );
      const unified = unifyFacts(results, WINDOW);
      expect(new Set(unified.coverages.map((c) => c.dependency)).size).toBe(6);
      const search = unified.coverages.find((c) => c.dependency === DEP_SEARCH_DAILY)!;
      const expected = {
        empty: CoverageState.AVAILABLE,
        missing_metadata: CoverageState.UNKNOWN,
        unavailable: CoverageState.UNAVAILABLE,
      }[outcome];
      expect(search.state).toBe(expected);
      expect(comparePeriods(unified.rows, PERIODS, unified.coverages).deltaPercent).toBeNull();
    },
  );

  it("preserves missing token-type evidence on estimated surfaces", () => {
    const rest = UNIFIED_SOURCES.find((s) => s.creditsEstimated)!;
    const unified = unifyFacts(
      {
        [rest.dependency]: {
          rows: [
            {
              USAGE_DATE: "2026-09-19",
              MODEL_NAME: "partially-rated",
              CALLS: 1,
              TOKENS: 15,
              CREDITS: 0.1,
              IS_UNRATED: 0,
              RATE_INCOMPLETE: true,
              UNRATED_TOKENS: 5,
            },
          ],
          coverage: covered(rest.dependency),
        },
      },
      WINDOW,
    );
    expect(unified.rows[0]).toMatchObject({
      SURFACE: SURFACE_REST_API,
      RATE_INCOMPLETE: true,
      UNRATED_TOKENS: 5,
      CREDITS: 0.1,
      CREDITS_ESTIMATED: true,
    });
  });
});

describe("scopedUsage", () => {
  const facts = (previous: number, current: number) =>
    Array.from({ length: 14 }, (_, i) => ({
      USAGE_DATE: new Date(Date.UTC(2026, 8, 6 + i)).toISOString().slice(0, 10),
      CREDITS: i < 7 ? previous : current,
      USER_NAME: i === 0 ? "Bob" : "Alice",
      SURFACE: SURFACE_COCO,
    }));

  it("uses one union read and the identical filter for both periods", () => {
    const rows = facts(1, 2);
    expect(options(rows, "USER_NAME")).toEqual(["Alice", "Bob"]);
    const view = scopedUsage(rows, {
      selection: { users: ["Alice"], models: [], tags: [], pageValues: {} },
      tagUsers: null,
      source: SURFACE_COCO,
      periods: PERIODS,
      coverages: [covered(SURFACE_COCO)],
    });
    expect(view.current.rows).toHaveLength(7);
    const comparison = view.current.comparisons.CREDITS;
    expect(comparison.current).toBe(14);
    expect(comparison.previous).toBe(6);
    expect(trendDelta(comparison)).toMatch(/^\+133\.3% \(\+\d+\.\d{4}\)$/);
    expect(view.current.previousDaily).toHaveLength(7);
    expect(view.unscopedCurrent).toEqual([]);
  });

  it("shows the labelled unscoped view only when nothing matches on a dedicated page", () => {
    const rows = [
      { USAGE_DATE: "2026-09-15", SURFACE: SURFACE_COCO, WAREHOUSE_CREDITS: 2, USER_NAME: "Alice" },
    ];
    const view = scopedUsage(rows, {
      selection: { users: ["Alice"], models: [], tags: [], pageValues: {} },
      tagUsers: null,
      source: "Warehouse Attribution",
      periods: PERIODS,
      coverages: [],
      metric: "WAREHOUSE_CREDITS",
      unscopedView: true,
    });
    expect(view.showingUnscoped).toBe(true);
    expect(view.unscopedAmount).toBe(2);
    expect(view.current.rows).toHaveLength(1);
  });

  it("keeps a prior-only group's current calendar with its own source gaps", () => {
    const rows = facts(2, 2).map((r, i) => ({
      ...r,
      SURFACE: i < 7 ? SURFACE_COCO : SURFACE_SEARCH,
      TOKENS: 10,
      CALLS: 1,
      USER_NAME: "Alice",
    }));
    const search = { ...covered(DEP_SEARCH_DAILY), state: CoverageState.UNKNOWN };
    const view = scopedUsage(rows, {
      selection: { users: [], models: [], tags: [], pageValues: {} },
      tagUsers: null,
      source: null,
      periods: PERIODS,
      coverages: [covered(SURFACE_COCO), search],
    });
    const series = daily(view.current.rows, "SURFACE", {
      displayWindow: view.current.displayWindow!,
      coverages: view.current.coverages,
      seriesValues: view.current.seriesValues,
    });
    const coco = series.rows.filter((r) => r.SURFACE === SURFACE_COCO);
    expect(coco).toHaveLength(7);
    expect(coco.every((r) => r.CREDITS === 0)).toBe(true);
    expect(series.rows).toHaveLength(14);
    expect(
      series.rows.filter((r) => r.SURFACE === SURFACE_SEARCH).every((r) => r.CREDITS === 2),
    ).toBe(true);
  });
});
