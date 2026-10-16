// Numeric parity with utils/agg.py and the utils/ui.py formatters. Regenerate the
// fixture with `uv run python scripts/export_agg_cases.py` from the repository root.
import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

import {
  byAgentTag,
  byDimension,
  byUserTag,
  leader,
  tokenMix,
  totals,
  warehouseCreditsForSurface,
} from "@/lib/agg/metrics";
import {
  calendarDaily,
  comparePeriods,
  daily,
  dailyTrend,
  relativePeriodDaily,
  trendDelta,
} from "@/lib/agg/periods";
import { options, type Row } from "@/lib/agg/rows";
import { partitionScope, tagLabelOptions, usersForTags } from "@/lib/agg/scope";
import { fmtCredits, fmtInt, fmtTokens } from "@/lib/format";
import type { ComparisonPeriods, SourceCoverage } from "@/lib/view-state";

interface Frame {
  columns: string[];
  rows: Row[];
}

const cases = JSON.parse(
  fs.readFileSync(path.resolve(__dirname, "../../tests/fixtures/agg_cases.json"), "utf8"),
);
const periods: ComparisonPeriods = cases.periods;

// Numeric equality with a tiny tolerance; everything else must match exactly.
function expectClose(actual: unknown, expected: unknown, label: string) {
  if (typeof expected === "number" && typeof actual === "number") {
    expect(actual, label).toBeCloseTo(expected, 9);
  } else {
    expect(actual, label).toEqual(expected);
  }
}

function expectFrame(actual: Frame, expected: Frame, label: string) {
  expect(actual.columns, `${label} columns`).toEqual(expected.columns);
  expect(actual.rows.length, `${label} row count`).toBe(expected.rows.length);
  actual.rows.forEach((row, i) => {
    for (const column of expected.columns) {
      expectClose(
        row[column] ?? null,
        expected.rows[i][column] ?? null,
        `${label} row ${i} ${column}`,
      );
    }
  });
}

describe("partitionScope", () => {
  it.each(cases.partition.map((c: { name: string }) => c.name) as string[])("%s", (name) => {
    const c = cases.partition.find((x: { name: string }) => x.name === name);
    const result = partitionScope(
      c.frame,
      { users: c.users, models: c.models, tags: c.tags, pageValues: c.page_values },
      { source: c.source, tagUsers: c.tag_users },
    );
    const ids = (rows: Row[]) => rows.map((r) => r.ROW_ID);
    expect(ids(result.matched)).toEqual(c.expected.matched);
    expect(ids(result.unscoped)).toEqual(c.expected.unscoped);
    expect(ids(result.excluded)).toEqual(c.expected.excluded);
  });
});

describe("byDimension", () => {
  it.each(cases.by_dimension.map((c: { name: string }) => c.name) as string[])("%s", (name) => {
    const c = cases.by_dimension.find((x: { name: string }) => x.name === name);
    const frame = c.frame.rows.length === 0 && c.frame.columns.length === 0 ? [] : c.frame.rows;
    expectFrame(byDimension(frame, c.column, c.label_col, c.metrics), c.expected, name);
  });
});

describe("comparePeriods", () => {
  it.each(cases.compare.map((c: { name: string }) => c.name) as string[])("%s", (name) => {
    const c = cases.compare.find((x: { name: string }) => x.name === name);
    const result = comparePeriods(
      c.frame ? c.frame.rows : null,
      periods,
      c.coverages as SourceCoverage[],
      c.value_col,
    );
    expectClose(result.current, c.expected.current, "current");
    expectClose(result.previous, c.expected.previous, "previous");
    expectClose(result.deltaPercent, c.expected.deltaPercent, "delta");
    expect(result.state).toBe(c.expected.state);
    expect(result.reason ?? null).toBe(c.expected.reason);
  });
});

describe("calendarDaily", () => {
  it.each(cases.calendar.map((c: { name: string }) => c.name) as string[])("%s", (name) => {
    const c = cases.calendar.find((x: { name: string }) => x.name === name);
    const result = calendarDaily(c.frame.rows, c.window, c.coverages, c.value_col);
    expect(result.length).toBe(c.expected.length);
    result.forEach((point, i) => {
      expect(point.USAGE_DATE).toBe(c.expected[i].USAGE_DATE);
      expectClose(point[c.value_col], c.expected[i][c.value_col], `day ${i}`);
    });
  });
});

describe("daily", () => {
  it.each(cases.daily.map((c: { name: string }) => c.name) as string[])("%s", (name) => {
    const c = cases.daily.find((x: { name: string }) => x.name === name);
    const result = daily(c.frame.rows, c.color_col, c.context);
    expectFrame(result, c.expected, name);
    if (c.context) expect(dailyTrend(c.frame.rows, c.context)).toEqual(c.expected_trend);
  });
});

describe("totals, options, mix, tags, warehouse", () => {
  it("totals", () => {
    for (const c of cases.totals) expect(totals(c.frame.rows)).toEqual(c.expected);
  });
  it("options", () => {
    for (const c of cases.options) expect(options(c.frame.rows, c.column)).toEqual(c.expected);
  });
  it("token mix", () => {
    for (const c of cases.token_mix) expectFrame(tokenMix(c.frame.rows), c.expected, "token mix");
  });
  it("tag helpers", () => {
    expect(tagLabelOptions(cases.tag_label_options.tags.rows)).toEqual(
      cases.tag_label_options.expected,
    );
    expect(usersForTags(cases.users_for_tags.tags.rows, cases.users_for_tags.selected)).toEqual(
      cases.users_for_tags.expected,
    );
    expectFrame(
      byUserTag(cases.by_user_tag.frame.rows, cases.by_user_tag.tags.rows),
      cases.by_user_tag.expected,
      "by user tag",
    );
    expectFrame(
      byAgentTag(cases.by_agent_tag.frame.rows),
      cases.by_agent_tag.expected,
      "by agent tag",
    );
  });
  it("warehouse split", () => {
    for (const c of cases.warehouse_surface) {
      expect(warehouseCreditsForSurface(c.frame.rows, c.surface)).toEqual(c.expected);
    }
  });
  it("relative periods and trend delta", () => {
    const relative = relativePeriodDaily(cases.relative.current.rows, cases.relative.previous.rows);
    expectFrame(
      { columns: cases.relative.expected.columns, rows: relative },
      cases.relative.expected,
      "relative",
    );
    expect(() =>
      relativePeriodDaily(cases.relative.current.rows, cases.relative.previous.rows.slice(0, 1)),
    ).toThrow(/equal numbers/);
    expect(trendDelta(cases.trend_delta.comparison, 4)).toBe(cases.trend_delta.expected);
    expect(trendDelta(null)).toBeNull();
  });
  it("leader", () => {
    const result = leader(cases.leader.frame, cases.leader.total);
    expect(result?.top.SURFACE).toBe(cases.leader.expected.label);
    expect(result?.share).toBeCloseTo(cases.leader.expected.share, 12);
    expect(leader({ columns: [], rows: [] }, 1)).toBeNull();
  });
});

describe("formatters", () => {
  it("fmtInt", () => {
    for (const [value, expected] of cases.format.fmt_int) expect(fmtInt(value)).toBe(expected);
  });
  it("fmtCredits", () => {
    for (const [value, decimals, expected] of cases.format.fmt_credits)
      expect(fmtCredits(value, decimals)).toBe(expected);
  });
  it("fmtTokens", () => {
    for (const [value, expected] of cases.format.fmt_tokens)
      expect(fmtTokens(value)).toBe(expected);
  });
});
