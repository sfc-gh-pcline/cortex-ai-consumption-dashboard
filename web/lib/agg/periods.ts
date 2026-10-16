// Completed-period comparisons and calendar-complete daily series. Port of
// period_slice, covers_window, compare_periods, calendar_daily, daily,
// relative_period_daily, daily_trend, and trend_delta in utils/agg.py.
//
// Streamlit carried the scope boundary as frame attrs; here it is an explicit
// ScopedUsage object so nothing hides in mutable metadata.
import {
  hasColumn,
  isMissing,
  num,
  sumColumn,
  sumValues,
  uniqueInOrder,
  usageDate,
  type Row,
} from "@/lib/agg/rows";
import { METRICS } from "@/lib/agg/metrics";
import { addDays, windowDays } from "@/lib/dates";
import { mainDependencyFor } from "@/lib/plans";
import {
  ComparisonState,
  CoverageState,
  type ComparisonPeriods,
  type ComparisonResult,
  type DateWindow,
  type SourceCoverage,
} from "@/lib/view-state";

// Rows inside the inclusive UTC interval.
export function periodSlice(rows: readonly Row[], window: DateWindow | null): Row[] {
  if (window === null || rows.length === 0 || !hasColumn(rows, "USAGE_DATE")) return [];
  return rows.filter((row) => {
    const day = usageDate(row.USAGE_DATE);
    return day >= window.start && day <= window.end;
  });
}

// Verified read bounds, retention, and completeness for every day of the window.
export function coversWindow(coverage: SourceCoverage, window: DateWindow): boolean {
  return (
    (coverage.state === CoverageState.AVAILABLE || coverage.state === CoverageState.PARTIAL) &&
    coverage.actualSource === "direct" &&
    coverage.served != null &&
    coverage.retentionStart != null &&
    coverage.completeThrough != null &&
    coverage.served.start <= window.start &&
    window.end <= coverage.served.end &&
    coverage.retentionStart <= window.start &&
    window.end <= coverage.completeThrough
  );
}

const unavailable = (reason: string): ComparisonResult => ({
  current: null,
  previous: null,
  deltaPercent: null,
  state: ComparisonState.UNAVAILABLE,
  reason,
});

// Adjacent completed periods compared after the same scope predicate.
export function comparePeriods(
  rows: readonly Row[] | null,
  periods: ComparisonPeriods,
  coverages: readonly SourceCoverage[],
  valueCol = "CREDITS",
): ComparisonResult {
  if (rows === null) return unavailable("A required source is unavailable.");
  if (periods.current === null || periods.previous === null) {
    return {
      current: null,
      previous: null,
      deltaPercent: null,
      state: ComparisonState.INCOMPLETE,
      reason: "No completed comparison period.",
    };
  }
  if (
    windowDays(periods.current) !== windowDays(periods.previous) ||
    addDays(periods.previous.end, 1) !== periods.current.start
  ) {
    throw new RangeError("Comparison periods must be adjacent and equal length.");
  }
  const currentValue = sumColumn(periodSlice(rows, periods.current), valueCol);
  const previousValue = sumColumn(periodSlice(rows, periods.previous), valueCol);
  if (coverages.some((c) => c.state === CoverageState.UNAVAILABLE)) {
    return {
      current: currentValue,
      previous: previousValue,
      deltaPercent: null,
      state: ComparisonState.UNAVAILABLE,
      reason: "A required source is unavailable; observed values exclude it.",
    };
  }
  let reason: string | null = null;
  if (!hasColumn(rows, valueCol)) reason = "The metric is unavailable.";
  else if (rows.some((r) => isMissing(r[valueCol]))) reason = "Some metric values are unavailable.";
  else if (
    valueCol === "CREDITS" &&
    hasColumn(rows, "IS_UNRATED") &&
    rows.some((r) => num(r.IS_UNRATED) === 1)
  ) {
    reason =
      "Credit change is unavailable because one or both periods contain inference usage without an observed model rate.";
  } else if (
    valueCol === "CREDITS" &&
    hasColumn(rows, "RATE_INCOMPLETE") &&
    rows.some((r) => r.RATE_INCOMPLETE === true)
  ) {
    reason =
      "Credit change is unavailable because one or both periods lack pricing details for some inference tokens (model rates, token types, or their breakdown).";
  } else if (
    coverages.length === 0 ||
    !coverages.every((c) => coversWindow(c, periods.current!) && coversWindow(c, periods.previous!))
  ) {
    reason = "Source coverage does not verify both complete periods.";
  }
  if (reason) {
    return {
      current: currentValue,
      previous: previousValue,
      deltaPercent: null,
      state: ComparisonState.INCOMPLETE,
      reason,
    };
  }
  if (previousValue === 0) {
    const bothZero = currentValue === 0;
    return {
      current: currentValue,
      previous: previousValue,
      deltaPercent: bothZero ? 0 : null,
      state: bothZero ? ComparisonState.BOTH_ZERO : ComparisonState.ZERO_BASELINE,
      reason: bothZero ? "No change" : "New usage",
    };
  }
  return {
    current: currentValue,
    previous: previousValue,
    deltaPercent: ((currentValue - previousValue) / previousValue) * 100,
    state: ComparisonState.COMPARABLE,
    reason: null,
  };
}

export interface CalendarPoint {
  USAGE_DATE: string;
  [metric: string]: unknown;
}

// Completes the calendar; absent days are 0 only when every source verifies that day.
export function calendarDaily(
  rows: readonly Row[],
  window: DateWindow,
  coverages: readonly SourceCoverage[],
  valueCol = "CREDITS",
): CalendarPoint[] {
  const selected = periodSlice(rows, window);
  const observed = new Map<string, unknown[]>();
  if (selected.length > 0 && hasColumn(selected, valueCol)) {
    for (const row of selected) {
      const day = usageDate(row.USAGE_DATE);
      const list = observed.get(day) ?? [];
      list.push(row[valueCol]);
      observed.set(day, list);
    }
  }
  const points: CalendarPoint[] = [];
  for (let day = window.start; day <= window.end; day = addDays(day, 1)) {
    const covered =
      coverages.length > 0 && coverages.every((c) => coversWindow(c, { start: day, end: day }));
    const values = observed.get(day);
    const value = values ? sumValues(values, 1) : covered ? 0 : null;
    points.push({ USAGE_DATE: day, [valueCol]: value });
  }
  return points;
}

// Scope boundary that Streamlit kept in frame attrs.
export interface DailyContext {
  readonly displayWindow: DateWindow;
  readonly coverages: readonly SourceCoverage[];
  readonly seriesValues?: Readonly<Record<string, readonly unknown[]>>;
}

export interface DailySeries {
  readonly columns: string[];
  readonly rows: Row[];
}

// Daily totals, optionally split by a series column. With a context the
// calendar is completed per series with that series' own evidence.
export function daily(
  rows: readonly Row[] | null | undefined,
  colorCol: string | null = null,
  context: DailyContext | null = null,
): DailySeries {
  const base = ["USAGE_DATE", ...METRICS];
  if (!rows) return { columns: base, rows: [] };
  const splitting = colorCol !== null && hasColumn(rows, colorCol);
  if (context) {
    let groups: [unknown, Row[]][] = [[null, [...rows]]];
    if (splitting) {
      const labels = context.seriesValues?.[colorCol] ?? uniqueInOrder(rows, colorCol);
      groups = labels.map((label) => [label, rows.filter((r) => r[colorCol] === label)]);
      if (groups.length === 0) groups = [[null, [...rows]]];
    }
    const out: Row[] = [];
    for (const [label, subset] of groups) {
      let evidence = context.coverages;
      if (colorCol === "SURFACE" && label !== null) {
        const dependency = mainDependencyFor(String(label));
        evidence = context.coverages.filter((c) => c.dependency === dependency);
      }
      const credits = calendarDaily(subset, context.displayWindow, evidence, "CREDITS");
      const tokens = calendarDaily(subset, context.displayWindow, evidence, "TOKENS");
      const calls = calendarDaily(subset, context.displayWindow, evidence, "CALLS");
      credits.forEach((point, i) => {
        const record: Row = {
          USAGE_DATE: point.USAGE_DATE,
          CREDITS: point.CREDITS,
          TOKENS: tokens[i].TOKENS,
          CALLS: calls[i].CALLS,
        };
        if (splitting && label !== null) record[colorCol] = label;
        out.push(record);
      });
    }
    const columns =
      splitting && groups.some(([label]) => label !== null) ? [...base, colorCol] : base;
    return { columns, rows: out };
  }
  if (rows.length === 0) return { columns: base, rows: [] };
  const keys = ["USAGE_DATE", ...(splitting ? [colorCol] : [])];
  const groups = new Map<string, { key: Row; rows: Row[] }>();
  for (const row of rows) {
    const keyRow: Row = Object.fromEntries(keys.map((k) => [k, row[k]]));
    const key = JSON.stringify(keys.map((k) => (isMissing(row[k]) ? null : row[k])));
    const group = groups.get(key) ?? { key: keyRow, rows: [] };
    group.rows.push(row);
    groups.set(key, group);
  }
  const out: Row[] = [...groups.values()].map((g) => ({
    ...g.key,
    CREDITS: sumValues(g.rows.map((r) => r.CREDITS)) ?? 0,
    TOKENS: sumValues(g.rows.map((r) => r.TOKENS)) ?? 0,
    CALLS: sumValues(g.rows.map((r) => r.CALLS)) ?? 0,
  }));
  out.sort((a, b) => {
    const byDate = String(a.USAGE_DATE).localeCompare(String(b.USAGE_DATE));
    if (byDate !== 0 || !splitting) return byDate;
    return String(a[colorCol]).localeCompare(String(b[colorCol]));
  });
  return { columns: [...keys, ...METRICS], rows: out };
}

// Aligns two daily calendars by day number, keeping dates and null observations.
export function relativePeriodDaily(
  current: readonly Row[],
  previous: readonly Row[],
  metric = "CREDITS",
): Row[] {
  const frames: Row[][] = [];
  for (const [label, source] of [
    ["Current", current],
    ["Previous", previous],
  ] as const) {
    const byDay = new Map<string, unknown[]>();
    for (const row of source) {
      const day = usageDate(row.USAGE_DATE);
      const list = byDay.get(day) ?? [];
      list.push(row[metric]);
      byDay.set(day, list);
    }
    const days = [...byDay.keys()].sort();
    frames.push(
      days.map((day, index) => ({
        USAGE_DATE: day,
        [metric]: sumValues(byDay.get(day)!, byDay.get(day)!.length),
        Day: index + 1,
        Period: label,
      })),
    );
  }
  if (frames[0].length !== frames[1].length) {
    throw new RangeError("Comparison calendars must contain equal numbers of days.");
  }
  return [...frames[0], ...frames[1]];
}

// Sparkline values, or null when an unknown gap cannot be rendered safely.
export function dailyTrend(
  rows: readonly Row[] | null,
  context: DailyContext | null,
  valueCol = "CREDITS",
): number[] | null {
  if (!rows || !context) return null;
  const points = calendarDaily(rows, context.displayWindow, context.coverages, valueCol);
  if (points.some((p) => isMissing(p[valueCol]))) return null;
  return points.map((p) => Number(p[valueCol]));
}

// Signed percentage plus absolute change, like Python's f"{d:+.1f}% ({a:+.4f})".
export function trendDelta(
  comparison: ComparisonResult | null | undefined,
  absDigits = 4,
): string | null {
  if (
    !comparison ||
    comparison.deltaPercent === null ||
    comparison.current === null ||
    comparison.previous === null
  ) {
    return null;
  }
  const signed = (value: number, digits: number) =>
    `${value >= 0 ? "+" : "-"}${Math.abs(value).toFixed(digits)}`;
  return `${signed(comparison.deltaPercent, 1)}% (${signed(comparison.current - comparison.previous, absDigits)})`;
}
