"use client";

import { BreakdownTable } from "@/components/kit/breakdown-table";
import { DataTable } from "@/components/kit/data-table";
import { EmptyState, EmptyUsage, inferState } from "@/components/kit/empty-state";
import { HELP, KpiCards, standardCards } from "@/components/kit/kpi-cards";
import { InsightBanner, PageHeader, Row as ControlRow, Section } from "@/components/kit/layout";
import { MultiSelect } from "@/components/kit/multi-select";
import { Caption, Notice } from "@/components/kit/notice";
import { Segmented } from "@/components/kit/segmented";
import { TrendChart } from "@/components/kit/trend-chart";
import { Chart } from "@/components/kit/vega";
import { OptionalWarehouse } from "@/components/kit/warehouse-component";
import { DataPage, useScopedView } from "@/components/pages/page-frame";
import { useIsDark } from "@/components/shell/theme-toggle";
import { Switch } from "@/components/ui/switch";
import { byDimension, leader, totals } from "@/lib/agg/metrics";
import { daily, dailyTrend, periodSlice, trendDelta } from "@/lib/agg/periods";
import { isMissing, options, sumValues, type Row } from "@/lib/agg/rows";
import { scopedUsage } from "@/lib/agg/scoped";
import { hourlyTrendSpec } from "@/lib/chart-spec";
import { useDashboard } from "@/lib/client/dashboard";
import { useAuxiliary, type PageFacts } from "@/lib/client/page-data";
import { useBooleanParam, useListParam, useStringParam } from "@/lib/client/url-state";
import { addDays } from "@/lib/dates";
import { fmtCredits, fmtInt, fmtTokens } from "@/lib/format";
import {
  DEP_SEARCH_BATCH,
  DEP_SEARCH_SERVING,
  SURFACE_SEARCH,
  SURFACE_WAREHOUSE_ATTRIBUTION,
} from "@/lib/plans";

export function CortexSearchPage() {
  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        eyebrow="Cortex surface"
        title={SURFACE_SEARCH}
        subtitle="Cortex Search usage: serving, embedding and batch consumption from CORTEX_SEARCH_*_USAGE_HISTORY."
      />
      <DataPage pageKey={SURFACE_SEARCH} unscopedView>
        {(facts) => <Body facts={facts} />}
      </DataPage>
    </div>
  );
}

function Body({ facts }: { facts: PageFacts }) {
  const [services, setServices] = useListParam("search_services");
  const [types, setTypes] = useListParam("search_types");
  const pageValues = { SERVICE_NAME: services, CONSUMPTION_TYPE: types };
  const { view, rows, windowDays, notice, comparisonCaption } = useScopedView(facts, pageValues, {
    source: SURFACE_SEARCH,
    unscopedView: true,
  });
  const controls = (
    <ControlRow>
      <MultiSelect
        label="Service"
        options={options(facts.rows, "SERVICE_NAME")}
        value={services}
        onChange={(v) => void setServices(v)}
      />
      <MultiSelect
        label="Consumption type"
        options={options(facts.rows, "CONSUMPTION_TYPE")}
        value={types}
        onChange={(v) => void setTypes(v)}
      />
    </ControlRow>
  );
  if (rows.length === 0) {
    return (
      <>
        {controls}
        {notice}
        <EmptyUsage comparison={view.current.comparisons.CREDITS} state={inferState(facts.rows)} />
      </>
    );
  }
  const t = totals(rows);
  const byType = byDimension(rows, "CONSUMPTION_TYPE", "CONSUMPTION_TYPE");
  const top = leader(byType, t.CREDITS);
  const serviceCount = options(rows, "SERVICE_NAME").length;
  const context = view.current.displayWindow
    ? {
        displayWindow: view.current.displayWindow,
        coverages: view.current.coverages,
        seriesValues: view.current.seriesValues,
      }
    : null;
  const cards = standardCards(t, windowDays, {
    creditsHelp: `SERVING + EMBED_TEXT_TOKENS + BATCH credits, from CORTEX_SEARCH_DAILY_USAGE_HISTORY. ${HELP.delta}`,
    trend: dailyTrend(rows, context),
    delta: trendDelta(view.current.comparisons.CREDITS),
  });
  return (
    <>
      {controls}
      {notice}
      {comparisonCaption}
      {top && (
        <InsightBanner
          detail={`${String(top.top.CONSUMPTION_TYPE)} leads at ${Math.round(top.share * 100)}% of credits · ${serviceCount} distinct services.`}
        />
      )}
      <KpiCards
        cards={[
          cards.credits,
          { ...cards.tokens, caption: "Embedding tokens only" },
          { label: "Services", value: fmtInt(serviceCount) },
        ]}
      />
      <Section title="Credits Over Time">
        <TrendChart
          series={daily(rows, null, context)}
          yLabel="Credits"
          previousDaily={view.current.previousDaily}
          pageId="cortex_search"
        />
      </Section>
      <Section title="By Consumption Type">
        <BreakdownTable breakdown={byType} label="Consumption type" labelCol="CONSUMPTION_TYPE" />
        <Caption>
          SERVING is query-time search credits; EMBED_TEXT_TOKENS is embedding-model credits for
          indexing and re-indexing text; BATCH covers batch search batch queries against a search
          service, broken out below.
        </Caption>
      </Section>
      <OptionalWarehouse source={SURFACE_SEARCH} />
      <Detail pageValues={pageValues} />
    </>
  );
}

// Serving and batch detail, loaded only when toggled on.
function Detail({ pageValues }: { pageValues: Record<string, readonly string[]> }) {
  const [open, setOpen] = useBooleanParam("detail_search");
  const serving = useAuxiliary(DEP_SEARCH_SERVING, open);
  const batch = useAuxiliary(DEP_SEARCH_BATCH, open);
  const warehouse = useAuxiliary(SURFACE_WAREHOUSE_ATTRIBUTION, open);
  const { periods, filters, displayWindow } = useDashboard();
  const dark = useIsDark();
  const [mode, setMode] = useStringParam("chart_cortex_search_hourly", "Chart");
  const scope = (rows: Row[] | undefined, coverage: unknown) =>
    rows
      ? scopedUsage(rows, {
          selection: { ...filters, pageValues },
          tagUsers: null,
          source: SURFACE_SEARCH,
          periods,
          coverages: coverage ? [coverage as never] : [],
          unscopedView: true,
        }).current.rows
      : null;
  const servingRows = scope(serving.data?.rows, serving.data?.coverage);
  const batchRows = scope(batch.data?.rows, batch.data?.coverage);
  return (
    <div className="flex flex-col gap-4">
      <label className="flex items-center gap-2 text-sm">
        <Switch
          checked={open}
          onCheckedChange={(checked) => void setOpen(checked)}
          aria-label="Serving and batch detail"
        />
        Serving and batch detail
      </label>
      {open && (
        <>
          <Section title="Hourly Serving Trend">
            {serving.isLoading ? (
              <Caption>Loading serving usage...</Caption>
            ) : !servingRows || servingRows.length === 0 ? (
              <EmptyState
                message="No serving usage in this window."
                state={inferState(servingRows)}
              />
            ) : (
              <HourlyServing
                rows={servingRows}
                window={displayWindow}
                mode={mode}
                setMode={(v) => void setMode(v)}
                dark={dark}
              />
            )}
          </Section>
          <Section title="Batch Detail">
            {batch.isLoading ? (
              <Caption>Loading batch usage...</Caption>
            ) : !batchRows || batchRows.length === 0 ? (
              <EmptyState
                message="No batch query usage in this window. Batch covers batch search queries against a search service, tracked by QUERY_ID."
                state={inferState(batchRows)}
              />
            ) : (
              <BatchDetail
                rows={batchRows}
                warehouseRows={
                  warehouse.data
                    ? periods
                      ? periodSlice(warehouse.data.rows, periods.current)
                      : warehouse.data.rows
                    : null
                }
              />
            )}
          </Section>
          <Section title="Index-Refresh Warehouse Cost">
            <Notice>
              Index-refresh warehouse cost is not attributed by this dashboard. The implemented join
              covers batch-query IDs, not index-refresh warehouse work.
            </Notice>
          </Section>
        </>
      )}
    </div>
  );
}

// Reindexes hourly serving credits onto every hour of the window; gaps stay null.
function HourlyServing({
  rows,
  window,
  mode,
  setMode,
  dark,
}: {
  rows: Row[];
  window: { start: string; end: string } | null;
  mode: string;
  setMode: (v: string) => void;
  dark: boolean;
}) {
  const byHour = new Map<string, unknown[]>();
  for (const row of rows) {
    const hour = String(row.USAGE_HOUR).replace(" ", "T").slice(0, 13);
    byHour.set(hour, [...(byHour.get(hour) ?? []), row.CREDITS]);
  }
  const hours: Row[] = [];
  if (window) {
    for (let day = window.start; day <= window.end; day = addDays(day, 1)) {
      for (let h = 0; h < 24; h += 1) {
        const key = `${day}T${String(h).padStart(2, "0")}`;
        const values = byHour.get(key);
        hours.push({ USAGE_HOUR: `${key}:00:00Z`, CREDITS: values ? sumValues(values) : null });
      }
    }
  }
  return (
    <div className="flex flex-col gap-3">
      <Segmented
        label="Display"
        options={["Chart", "Table"]}
        value={mode === "Table" ? "Table" : "Chart"}
        onChange={setMode}
      />
      {mode === "Table" ? (
        <DataTable
          rows={hours}
          columns={[
            { key: "USAGE_HOUR", label: "Hour (UTC)" },
            {
              key: "CREDITS",
              label: "Credits",
              render: (v) => (isMissing(v) ? "" : fmtCredits(v)),
              align: "right",
            },
          ]}
        />
      ) : (
        <Chart spec={hourlyTrendSpec("CREDITS", "Credits", dark)} data={hours} />
      )}
      <BreakdownTable
        breakdown={byDimension(rows, "SERVICE_NAME", "SERVICE_NAME")}
        label="Service"
        labelCol="SERVICE_NAME"
      />
    </div>
  );
}

// One row per QUERY_ID before joining warehouse credits, so a query with several
// consumption-type rows gets its warehouse allocation exactly once.
function BatchDetail({ rows, warehouseRows }: { rows: Row[]; warehouseRows: Row[] | null }) {
  const perQuery = new Map<string, Row>();
  for (const row of rows) {
    const id = String(row.QUERY_ID);
    const entry = perQuery.get(id) ?? {
      QUERY_ID: id,
      USAGE_DATE: row.USAGE_DATE,
      SERVICE_NAME: row.SERVICE_NAME,
      CREDITS: 0,
      TOKENS: 0,
    };
    if (String(row.USAGE_DATE) < String(entry.USAGE_DATE)) entry.USAGE_DATE = row.USAGE_DATE;
    entry.CREDITS = (entry.CREDITS as number) + (Number(row.CREDITS) || 0);
    entry.TOKENS = (entry.TOKENS as number) + (Number(row.TOKENS) || 0);
    perQuery.set(id, entry);
  }
  const warehouseByQuery = new Map<string, number>();
  for (const row of warehouseRows ?? []) {
    if (row.SURFACE !== SURFACE_SEARCH) continue;
    const id = String(row.QUERY_ID);
    warehouseByQuery.set(
      id,
      (warehouseByQuery.get(id) ?? 0) + (Number(row.WAREHOUSE_CREDITS) || 0),
    );
  }
  const detail = [...perQuery.values()].map((r) => ({
    ...r,
    WAREHOUSE_CREDITS: warehouseByQuery.get(String(r.QUERY_ID)) ?? null,
  }));
  return (
    <div className="flex flex-col gap-2">
      <DataTable
        rows={detail}
        initialSort={{ key: "USAGE_DATE", desc: true }}
        columns={[
          { key: "QUERY_ID", label: "Query ID" },
          { key: "USAGE_DATE", label: "Date" },
          { key: "SERVICE_NAME", label: "Service" },
          {
            key: "CREDITS",
            label: "Batch credits",
            render: (v) => fmtCredits(v),
            align: "right",
          },
          { key: "TOKENS", label: "Tokens", render: fmtTokens, align: "right" },
          {
            key: "WAREHOUSE_CREDITS",
            label: "Warehouse credits",
            render: (v) => (isMissing(v) ? "" : fmtCredits(v)),
            align: "right",
          },
        ]}
      />
      {(!warehouseRows || warehouseRows.length === 0) && (
        <Caption>
          Missing warehouse attribution is blank, not zero. See the Warehouse Attribution page for
          account-wide degradation status.
        </Caption>
      )}
    </div>
  );
}
