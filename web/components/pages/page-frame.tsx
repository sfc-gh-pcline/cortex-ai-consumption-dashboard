"use client";

import { useMemo, type ReactNode } from "react";

import { DataTable } from "@/components/kit/data-table";
import { EmptyUsage, inferState } from "@/components/kit/empty-state";
import { Expander } from "@/components/kit/layout";
import { Caption, Notice } from "@/components/kit/notice";
import { TodaySoFar } from "@/components/kit/today-so-far";
import { FilterBar } from "@/components/shell/filter-bar";
import { LoadingPanel } from "@/components/shell/loading-panel";
import { scopedUsage, type ScopedView } from "@/lib/agg/scoped";
import type { Row } from "@/lib/agg/rows";
import { useDashboard } from "@/lib/client/dashboard";
import { usePageFacts, type PageFacts } from "@/lib/client/page-data";
import { CUSTOM, addDays, windowDays } from "@/lib/dates";
import { fmtCredits } from "@/lib/format";
import { PAGE_TOTAL_CONSUMPTION } from "@/lib/plans";
import { ComparisonState } from "@/lib/view-state";

export interface DataPageProps {
  readonly pageKey: string;
  readonly metric?: string;
  readonly unscopedView?: boolean;
  readonly children: (facts: PageFacts) => ReactNode;
}

// The data gate every surface page shares: wait for the completed period, load
// the page's dependencies with progress, render the filter bar, and stop early
// on unavailable or empty facts with an honest explanation.
export function DataPage({
  pageKey,
  metric = "CREDITS",
  unscopedView = false,
  children,
}: DataPageProps) {
  const { periods, displayWindow, loadWindow, settings, today, appliedRange, filters } =
    useDashboard();
  const facts = usePageFacts(pageKey);
  const source = pageKey === PAGE_TOTAL_CONSUMPTION ? null : pageKey;

  if (!periods || !displayWindow || !loadWindow || !today) return null;
  if (periods.current === null) return <TodaySoFar pageKey={pageKey} tagUsers={facts.tagUsers} />;
  if (facts.loading) {
    return (
      <LoadingPanel states={facts.states} window={loadWindow} settings={settings} today={today} />
    );
  }

  const activeFilters =
    filters.users.length > 0 || filters.models.length > 0 || facts.tagUsers !== null;
  const showToday =
    periods.partialToday !== null ||
    (appliedRange !== CUSTOM && displayWindow.end === addDays(today, -1));

  return (
    <div className="flex flex-col gap-6">
      <FilterBar facts={facts} />
      {facts.degradedMessage && <Caption>⚠ {facts.degradedMessage}</Caption>}
      {facts.coverages
        .filter(
          (coverage) =>
            coverage.latestUsage !== null &&
            coverage.latestUsage !== undefined &&
            coverage.latestUsage < displayWindow.end,
        )
        .map((coverage) => (
          <Caption key={`${coverage.dependency}-freshness`}>
            {coverage.dependency} newest recorded usage day: {coverage.latestUsage}. The source has
            not reported usage through {displayWindow.end}.
          </Caption>
        ))}
      {facts.rows === null || facts.rows.length === 0 ? (
        <EmptyUsage
          comparison={
            scopedUsage(facts.rows ?? [], {
              selection: { ...filters, pageValues: {} },
              tagUsers: facts.tagUsers,
              source,
              periods,
              coverages: facts.coverages,
              metric,
              unscopedView,
            }).current.comparisons[metric]
          }
          state={inferState(facts.rows, { activeFilters, unavailable: facts.rows === null })}
          label={metric === "WAREHOUSE_CREDITS" ? "Warehouse credits" : "Credits"}
        />
      ) : (
        children(facts)
      )}
      {showToday && (
        <Expander title="Today so far (partial)">
          <TodaySoFar pageKey={pageKey} tagUsers={facts.tagUsers} />
        </Expander>
      )}
    </div>
  );
}

export interface ScopeResult {
  readonly view: ScopedView;
  readonly rows: Row[];
  readonly windowDays: number;
  // Warning and detail for rows not attributable to the selected filters.
  readonly notice: ReactNode;
  readonly comparisonCaption: ReactNode;
}

// Applies the retained global and page selections to the union read and hands
// pages the displayed interval (ui.scoped_usage + _current_scope).
export function useScopedView(
  facts: PageFacts,
  pageValues: Readonly<Record<string, readonly string[]>>,
  options: { source: string | null; metric?: string; unscopedView?: boolean },
): ScopeResult {
  const { periods, displayWindow, filters } = useDashboard();
  const metric = options.metric ?? "CREDITS";
  // Pages pass fresh object literals each render; key on the serialized form instead.
  const pageValuesKey = JSON.stringify(pageValues);
  const stablePageValues = useMemo(
    () => JSON.parse(pageValuesKey) as Readonly<Record<string, readonly string[]>>,
    [pageValuesKey],
  );
  const view = useMemo(
    () =>
      scopedUsage(facts.rows ?? [], {
        selection: { ...filters, pageValues: stablePageValues },
        tagUsers: facts.tagUsers,
        source: options.source,
        periods,
        coverages: facts.coverages,
        metric,
        unscopedView: options.unscopedView,
      }),
    [
      facts.rows,
      facts.tagUsers,
      facts.coverages,
      filters,
      stablePageValues,
      options.source,
      periods,
      metric,
      options.unscopedView,
    ],
  );
  const selected = Object.entries(pageValues)
    .filter(([, values]) => values.length > 0)
    .map(
      ([column, values]) =>
        `${column.replaceAll("_", " ").replace(/\b\w/g, (c) => c.toUpperCase())}: ${values.join(", ")}`,
    );
  const comparison = view.current.comparisons[metric];
  const notice = (
    <>
      {selected.length > 0 && <Caption>Selected: {selected.join("; ")}</Caption>}
      {view.unscopedCurrent.length > 0 && (
        <>
          <Notice tone="warning">
            Not attributable to selected filters: {fmtCredits(view.unscopedAmount)} credits. These
            records lack at least one selected attribution dimension and are not included in the
            matching total. Known mismatches are excluded.
          </Notice>
          {view.showingUnscoped ? (
            <>
              <h2>Not attributable to selected filters</h2>
              <Caption>
                All metrics and detail below describe unscoped usage, not filter matches.
              </Caption>
            </>
          ) : (
            <Expander title="Not attributable: usage detail">
              <DataTable
                rows={view.unscopedCurrent}
                columns={Object.keys(view.unscopedCurrent[0] ?? {}).map((key) => ({
                  key,
                  label: key,
                }))}
              />
            </Expander>
          )}
        </>
      )}
    </>
  );
  const comparisonCaption =
    comparison && periods?.previous ? (
      comparison.state === ComparisonState.COMPARABLE ||
      comparison.state === ComparisonState.BOTH_ZERO ? (
        <Caption>
          Compared with {periods.previous.start} to {periods.previous.end} UTC using the same
          filters.
        </Caption>
      ) : (
        <Caption>{comparison.reason ?? "Comparison unavailable."}</Caption>
      )
    ) : null;
  return {
    view,
    rows: view.current.rows,
    windowDays: displayWindow ? Math.max(windowDays(displayWindow), 1) : 1,
    notice,
    comparisonCaption,
  };
}
