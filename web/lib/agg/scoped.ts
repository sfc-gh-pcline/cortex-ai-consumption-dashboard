// The scope boundary: apply the retained selections to a current-plus-prior
// union read, evaluate the identically filtered baseline, and hand pages only
// the displayed interval. Pure port of scoped_usage / _current_scope in
// utils/ui.py; the warnings and detail tables those functions rendered are the
// caller's job here.
import { calendarDaily, comparePeriods, periodSlice, type CalendarPoint } from "@/lib/agg/periods";
import { hasColumn, sumColumn, uniqueInOrder, type Row } from "@/lib/agg/rows";
import { partitionScope } from "@/lib/agg/scope";
import type {
  ComparisonPeriods,
  ComparisonResult,
  DateWindow,
  FilterSelection,
  SourceCoverage,
} from "@/lib/view-state";

// Facts for the displayed interval plus the evidence charts and deltas read.
export interface ScopedUsage {
  readonly rows: Row[];
  readonly displayWindow: DateWindow | null;
  readonly coverages: readonly SourceCoverage[];
  readonly comparisons: Readonly<Record<string, ComparisonResult>>;
  readonly seriesValues: Readonly<Record<string, readonly unknown[]>>;
  readonly previousDaily: readonly CalendarPoint[] | null;
}

// Slices the displayed interval and evaluates the baseline for one metric.
export function currentScope(
  rows: readonly Row[],
  periods: ComparisonPeriods | null,
  coverages: readonly SourceCoverage[],
  metric: string,
): ScopedUsage {
  if (periods === null) {
    return {
      rows: [...rows],
      displayWindow: null,
      coverages,
      comparisons: {},
      seriesValues: {},
      previousDaily: null,
    };
  }
  const comparison = comparePeriods(rows, periods, coverages, metric);
  const seriesValues: Record<string, readonly unknown[]> = {};
  for (const column of ["SURFACE", "SUB_SURFACE"]) {
    if (hasColumn(rows, column)) seriesValues[column] = uniqueInOrder(rows, column);
  }
  return {
    rows: periodSlice(rows, periods.current),
    displayWindow: periods.current,
    coverages,
    comparisons: { [metric]: comparison },
    seriesValues,
    previousDaily:
      periods.previous !== null ? calendarDaily(rows, periods.previous, coverages, metric) : null,
  };
}

export interface ScopedView {
  // Matching facts for the displayed interval, or the labelled unscoped view.
  readonly current: ScopedUsage;
  // Not-attributable rows inside the displayed interval and their metric total.
  readonly unscopedCurrent: Row[];
  readonly unscopedAmount: number;
  // True when the dedicated page is showing its unscoped view instead of matches.
  readonly showingUnscoped: boolean;
}

export interface ScopeInputs {
  readonly selection: FilterSelection;
  readonly tagUsers: readonly string[] | null;
  readonly source: string | null;
  readonly periods: ComparisonPeriods | null;
  readonly coverages: readonly SourceCoverage[];
  readonly metric?: string;
  readonly unscopedView?: boolean;
}

// Partitions the union read, then scopes the matching (or labelled unscoped) facts.
export function scopedUsage(rows: readonly Row[], inputs: ScopeInputs): ScopedView {
  const metric = inputs.metric ?? "CREDITS";
  const scoped = partitionScope(rows, inputs.selection, {
    source: inputs.source,
    tagUsers: inputs.tagUsers,
  });
  const unscopedCurrent = inputs.periods
    ? periodSlice(scoped.unscoped, inputs.periods.current)
    : scoped.unscoped;
  const showingUnscoped =
    Boolean(inputs.unscopedView) && scoped.matched.length === 0 && scoped.unscoped.length > 0;
  const chosen = showingUnscoped ? scoped.unscoped : scoped.matched;
  return {
    current: currentScope(chosen, inputs.periods, inputs.coverages, metric),
    unscopedCurrent,
    unscopedAmount: sumColumn(unscopedCurrent, metric),
    showingUnscoped,
  };
}
