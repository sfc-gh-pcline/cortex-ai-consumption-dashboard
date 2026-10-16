"use client";

import { BreakdownTable } from "@/components/kit/breakdown-table";
import { DataTable } from "@/components/kit/data-table";
import { EmptyUsage, inferState } from "@/components/kit/empty-state";
import { KpiCards } from "@/components/kit/kpi-cards";
import { InsightBanner, PageHeader, Row as ControlRow, Section } from "@/components/kit/layout";
import { MultiSelect } from "@/components/kit/multi-select";
import { Caption, Notice } from "@/components/kit/notice";
import { TrendChart } from "@/components/kit/trend-chart";
import { aiFunctionsWarehouseCoverageNotice } from "@/components/pages/cortex-ai-functions";
import { DataPage, useScopedView } from "@/components/pages/page-frame";
import { byDimension, leader } from "@/lib/agg/metrics";
import { calendarDaily, trendDelta } from "@/lib/agg/periods";
import { options, sumColumn } from "@/lib/agg/rows";
import { useDashboard } from "@/lib/client/dashboard";
import type { PageFacts } from "@/lib/client/page-data";
import { useListParam } from "@/lib/client/url-state";
import { fmtCredits } from "@/lib/format";
import { SURFACE_WAREHOUSE_ATTRIBUTION } from "@/lib/plans";

const METRICS = ["WAREHOUSE_CREDITS", "COMPUTE_CREDITS", "CLOUD_SERVICES_CREDITS"];
const COLUMNS = {
  WAREHOUSE_CREDITS: { label: "Total credits", format: fmtCredits },
  COMPUTE_CREDITS: { label: "Compute", format: fmtCredits },
  CLOUD_SERVICES_CREDITS: { label: "Cloud services", format: fmtCredits },
};

export function WarehouseAttributionPage() {
  const { displayWindow } = useDashboard();
  const notice = displayWindow ? aiFunctionsWarehouseCoverageNotice(displayWindow) : null;
  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        eyebrow="Cross-surface"
        title={SURFACE_WAREHOUSE_ATTRIBUTION}
        subtitle="Snowflake virtual-warehouse compute credits attributed to Cortex surfaces, from QUERY_ATTRIBUTION_HISTORY and QUERY_METERING_HISTORY."
      />
      {notice && <Notice tone="warning">{notice}</Notice>}
      <DataPage pageKey={SURFACE_WAREHOUSE_ATTRIBUTION} metric="WAREHOUSE_CREDITS" unscopedView>
        {(facts) => <Body facts={facts} />}
      </DataPage>
    </div>
  );
}

function Body({ facts }: { facts: PageFacts }) {
  const [surfaces, setSurfaces] = useListParam("warehouse_surfaces");
  const [names, setNames] = useListParam("warehouse_names");
  const [tiers, setTiers] = useListParam("warehouse_tiers");
  const { view, rows, windowDays, notice, comparisonCaption } = useScopedView(
    facts,
    { SURFACE: surfaces, WAREHOUSE_NAME: names, TIER: tiers },
    { source: SURFACE_WAREHOUSE_ATTRIBUTION, metric: "WAREHOUSE_CREDITS", unscopedView: true },
  );
  const main = facts.states.find((s) => s.dependency === SURFACE_WAREHOUSE_ATTRIBUTION)?.data;
  const controls = (
    <ControlRow>
      <MultiSelect
        label="Service"
        options={options(facts.rows, "SURFACE")}
        value={surfaces}
        onChange={(v) => void setSurfaces(v)}
      />
      <MultiSelect
        label="Warehouse"
        options={options(facts.rows, "WAREHOUSE_NAME")}
        value={names}
        onChange={(v) => void setNames(v)}
      />
      <MultiSelect
        label="Attribution tier"
        options={options(facts.rows, "TIER")}
        value={tiers}
        onChange={(v) => void setTiers(v)}
      />
    </ControlRow>
  );
  if (rows.length === 0) {
    return (
      <>
        {controls}
        {main?.degraded && <Notice tone="warning">{main.message}</Notice>}
        {notice}
        <EmptyUsage
          comparison={view.current.comparisons.WAREHOUSE_CREDITS}
          state={inferState(facts.rows)}
          label="Warehouse credits"
        />
      </>
    );
  }
  const total = sumColumn(rows, "WAREHOUSE_CREDITS");
  const compute = sumColumn(rows, "COMPUTE_CREDITS");
  const cloud = sumColumn(rows, "CLOUD_SERVICES_CREDITS");
  const perRequest = sumColumn(
    rows.filter((r) => r.TIER === "per_request"),
    "WAREHOUSE_CREDITS",
  );
  const perRequestShare = total ? perRequest / total : 0;
  const bySurface = byDimension(rows, "SURFACE", "SURFACE", METRICS);
  const top = leader(bySurface, total, "WAREHOUSE_CREDITS");
  const trend = view.current.displayWindow
    ? calendarDaily(rows, view.current.displayWindow, view.current.coverages, "WAREHOUSE_CREDITS")
    : [];
  return (
    <>
      {controls}
      {main?.degraded && <Notice tone="warning">{main.message}</Notice>}
      {notice}
      {comparisonCaption}
      {top && (
        <InsightBanner
          detail={`${String(top.top.SURFACE)} leads at ${Math.round(top.share * 100)}% of credits · ${Math.round(perRequestShare * 100)}% is per-request attributable.`}
        />
      )}
      <KpiCards
        cards={[
          {
            label: "Warehouse credits",
            value: fmtCredits(total),
            delta: trendDelta(view.current.comparisons.WAREHOUSE_CREDITS),
            help: "COMPUTE_CREDITS + CLOUD_SERVICES_CREDITS, summed over every Cortex-attributable row. This is the headline total; see the caveat below on why it isn't a uniform metric across surfaces.",
            caption: `${fmtCredits(total / windowDays)} observed / day (${windowDays} days)`,
          },
          {
            label: "Compute credits",
            value: fmtCredits(compute),
            help: "Warehouse compute portion only, from both source views.",
          },
          {
            label: "Cloud services credits",
            value: fmtCredits(cloud),
            help: "Always 0 for QUERY_ATTRIBUTION_HISTORY rows (that view excludes cloud services); the true value for QUERY_METERING_HISTORY (Adaptive warehouse) rows.",
          },
          {
            label: "Per-request attributable",
            value: `${Math.round(perRequestShare * 100)}%`,
            help: "Share of warehouse credits carrying a REQUEST_ID, so they attribute to a single user turn rather than a bare surface total.",
          },
        ]}
      />
      <Caption>
        <strong>Headline metric caveat:</strong> WAREHOUSE_CREDITS = COMPUTE_CREDITS +
        CLOUD_SERVICES_CREDITS, and CLOUD_SERVICES_CREDITS is 0 by construction for
        standard-warehouse (QUERY_ATTRIBUTION_HISTORY) rows. Surfaces running entirely on standard
        warehouses therefore show compute-only credits, while surfaces on Adaptive warehouses show
        compute plus cloud-services, so the two are not a uniform like-for-like comparison. See the
        compute/cloud-services split above.
      </Caption>
      <Section title="Attributed Credits Over Time">
        <TrendChart
          series={{ columns: ["USAGE_DATE", "WAREHOUSE_CREDITS"], rows: trend }}
          valueCol="WAREHOUSE_CREDITS"
          yLabel="Attributed warehouse credits"
          previousDaily={view.current.previousDaily}
          pageId="warehouse_attribution"
        />
      </Section>
      <Section title="By Surface">
        <BreakdownTable
          breakdown={bySurface}
          label="Surface"
          labelCol="SURFACE"
          shareOf="WAREHOUSE_CREDITS"
          extraColumns={COLUMNS}
        />
      </Section>
      <Section title="By Warehouse">
        <BreakdownTable
          breakdown={byDimension(rows, "WAREHOUSE_NAME", "WAREHOUSE_NAME", METRICS)}
          label="Warehouse"
          labelCol="WAREHOUSE_NAME"
          shareOf="WAREHOUSE_CREDITS"
          extraColumns={COLUMNS}
        />
      </Section>
      <Section title="Tier Split">
        <BreakdownTable
          breakdown={byDimension(rows, "TIER", "TIER", METRICS)}
          label="Tier"
          labelCol="TIER"
          shareOf="WAREHOUSE_CREDITS"
          extraColumns={COLUMNS}
        />
        <Caption>
          <strong>per_request</strong> rows carry a request/query identifier;{" "}
          <strong>surface</strong> rows have service-level tags only. Neither tier is narrowed by
          user, model, user-tag or agent selections. Request identity alone does not establish that
          attribution.
        </Caption>
      </Section>
      <Section title="Detail">
        <DataTable
          rows={rows}
          initialSort={{ key: "WAREHOUSE_CREDITS", desc: true }}
          columns={[
            { key: "USAGE_DATE", label: "Date" },
            { key: "SURFACE", label: "Surface" },
            { key: "TIER", label: "Tier" },
            { key: "WAREHOUSE_NAME", label: "Warehouse" },
            { key: "SOURCE_VIEW", label: "Source view" },
            { key: "QUERY_ID", label: "Query ID" },
            { key: "REQUEST_ID", label: "Request ID" },
            {
              key: "COMPUTE_CREDITS",
              label: "Compute",
              render: (v) => fmtCredits(v),
              align: "right",
            },
            {
              key: "CLOUD_SERVICES_CREDITS",
              label: "Cloud services",
              render: (v) => fmtCredits(v),
              align: "right",
            },
            {
              key: "WAREHOUSE_CREDITS",
              label: "Total",
              render: (v) => fmtCredits(v),
              align: "right",
            },
          ]}
        />
      </Section>
    </>
  );
}
