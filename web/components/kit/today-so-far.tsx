"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { DataTable } from "@/components/kit/data-table";
import { Metric } from "@/components/kit/empty-state";
import { Caption, Notice } from "@/components/kit/notice";
import { Button } from "@/components/ui/button";
import { sumColumn } from "@/lib/agg/rows";
import { scopedUsage } from "@/lib/agg/scoped";
import { unifyFacts, UNIFIED_SOURCES, type SourceResult } from "@/lib/agg/unified";
import { fetchDependency } from "@/lib/client/api";
import { useDashboard } from "@/lib/client/dashboard";
import { fmtCredits } from "@/lib/format";
import {
  DEP_SEARCH_DAILY,
  PAGE_TOTAL_CONSUMPTION,
  SURFACE_SEARCH,
  SURFACE_WAREHOUSE_ATTRIBUTION,
  SURFACES,
} from "@/lib/plans";

// Explicitly requested direct partial-day view with no baseline (ui.today_section).
export function TodaySoFar({ pageKey, tagUsers }: { pageKey: string; tagUsers: string[] | null }) {
  const { today, filters } = useDashboard();
  const [requested, setRequested] = useState(false);
  const dependency = pageKey === SURFACE_SEARCH ? DEP_SEARCH_DAILY : pageKey;
  const deps =
    pageKey === PAGE_TOTAL_CONSUMPTION ? UNIFIED_SOURCES.map((s) => s.dependency) : [dependency];
  const query = useQuery({
    queryKey: ["today", pageKey, today],
    queryFn: async () => {
      const window = { start: today!, end: today! };
      const results = await Promise.all(
        deps.map((dep) => fetchDependency(dep, window, false, true).catch(() => null)),
      );
      return { window, results };
    },
    enabled: requested && today !== null,
    staleTime: 0,
  });
  const metric = pageKey === SURFACE_WAREHOUSE_ATTRIBUTION ? "WAREHOUSE_CREDITS" : "CREDITS";

  return (
    <div className="flex flex-col gap-2">
      <h2>Today so far</h2>
      <Caption>
        Partial UTC day. Source ingestion may lag; these values never enter completed totals or
        deltas.
      </Caption>
      {!requested && (
        <Button variant="outline" size="sm" onClick={() => setRequested(true)}>
          Load today so far
        </Button>
      )}
      {query.isLoading && requested && <Caption>Loading today...</Caption>}
      {query.error && (
        <Notice tone="error">
          Today&apos;s source is unavailable. Completed-period results are unchanged.
        </Notice>
      )}
      {query.data && (
        <TodayResult
          pageKey={pageKey}
          metric={metric}
          data={query.data}
          tagUsers={tagUsers}
          filters={filters}
        />
      )}
    </div>
  );
}

function TodayResult({
  pageKey,
  metric,
  data,
  tagUsers,
  filters,
}: {
  pageKey: string;
  metric: string;
  data: {
    window: { start: string; end: string };
    results: (Awaited<ReturnType<typeof fetchDependency>> | null)[];
  };
  tagUsers: string[] | null;
  filters: { users: string[]; models: string[]; tags: string[] };
}) {
  let rows;
  let unavailable: string[] = [];
  if (pageKey === PAGE_TOTAL_CONSUMPTION) {
    const map: Record<string, SourceResult | null> = {};
    UNIFIED_SOURCES.forEach((source, i) => {
      const result = data.results[i];
      map[source.dependency] = result ? { rows: result.rows, coverage: result.coverage } : null;
    });
    const unified = unifyFacts(map, data.window);
    rows = unified.rows;
    unavailable = unified.unavailableSources;
  } else {
    const result = data.results[0];
    if (!result || result.coverage.state === "unavailable") {
      return (
        <Notice tone="error">
          Today&apos;s source is unavailable. Completed-period results are unchanged.
        </Notice>
      );
    }
    rows = result.rows;
  }
  if (unavailable.length === SURFACES.length) {
    return (
      <>
        <Notice tone="warning">Partial source coverage: {unavailable.join(", ")}</Notice>
        <Metric label="Observed credits today (partial)" value="Unavailable" />
      </>
    );
  }
  const view = scopedUsage(rows, {
    selection: { users: filters.users, models: filters.models, tags: filters.tags, pageValues: {} },
    tagUsers,
    source: pageKey === PAGE_TOTAL_CONSUMPTION ? null : pageKey,
    periods: null,
    coverages: [],
    metric,
  });
  const columns = rows.length > 0 ? Object.keys(rows[0]).map((key) => ({ key, label: key })) : [];
  return (
    <div className="flex flex-col gap-2">
      {unavailable.length > 0 && (
        <Notice tone="warning">Partial source coverage: {unavailable.join(", ")}</Notice>
      )}
      <Caption>
        Scope: dates and global user/model/tag selections only; page-specific selectors are not
        applied to this separate component.
      </Caption>
      <Metric
        label="Observed credits today (partial)"
        value={fmtCredits(sumColumn(view.current.rows, metric))}
      />
      {view.current.rows.length > 0 && <DataTable rows={view.current.rows} columns={columns} />}
    </div>
  );
}
