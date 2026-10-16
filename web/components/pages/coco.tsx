"use client";

import { BreakdownTable, TokenMixTable } from "@/components/kit/breakdown-table";
import { EmptyUsage } from "@/components/kit/empty-state";
import { HELP, KpiCards, standardCards } from "@/components/kit/kpi-cards";
import { Expander, PageHeader, Row as ControlRow, Section } from "@/components/kit/layout";
import { MultiSelect } from "@/components/kit/multi-select";
import { Caption } from "@/components/kit/notice";
import { TrendChart } from "@/components/kit/trend-chart";
import { OptionalWarehouse } from "@/components/kit/warehouse-component";
import { Breakdowns, TAG_COLUMN } from "@/components/pages/breakdowns";
import { DataPage, useScopedView } from "@/components/pages/page-frame";
import { byDimension, leader, tokenMix, totals } from "@/lib/agg/metrics";
import { daily, dailyTrend, trendDelta } from "@/lib/agg/periods";
import { options } from "@/lib/agg/rows";
import type { PageFacts } from "@/lib/client/page-data";
import { useListParam } from "@/lib/client/url-state";
import { fmtInt } from "@/lib/format";
import { COCO_SUB_SURFACES, SURFACE_COCO } from "@/lib/plans";

export function CocoPage() {
  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        eyebrow="Cortex surface"
        title={SURFACE_COCO}
        subtitle="Token, usage-record and credit consumption across cli, desktop, and snowsight."
        pills={COCO_SUB_SURFACES}
      />
      <DataPage pageKey={SURFACE_COCO}>{(facts) => <Body facts={facts} />}</DataPage>
    </div>
  );
}

function Body({ facts }: { facts: PageFacts }) {
  const [subs, setSubs] = useListParam("coco_subs");
  const [roles, setRoles] = useListParam("coco_roles");
  const { view, rows, windowDays, notice, comparisonCaption } = useScopedView(
    facts,
    { SUB_SURFACE: subs, ROLE_NAME: roles },
    { source: SURFACE_COCO },
  );
  const controls = (
    <ControlRow>
      <MultiSelect
        label="Surface"
        options={COCO_SUB_SURFACES}
        value={subs}
        onChange={(v) => void setSubs(v)}
        placeholder="All surfaces"
      />
      <MultiSelect
        label="Role"
        options={options(facts.rows, "ROLE_NAME")}
        value={roles}
        onChange={(v) => void setRoles(v)}
        placeholder="All roles"
      />
    </ControlRow>
  );
  if (rows.length === 0) {
    return (
      <>
        {controls}
        {notice}
        <EmptyUsage comparison={view.current.comparisons.CREDITS} state="filtered_empty" />
      </>
    );
  }
  const t = totals(rows);
  const bySub = byDimension(rows, "SUB_SURFACE", "SUB_SURFACE");
  const top = leader(bySub, t.CREDITS);
  const context = view.current.displayWindow
    ? {
        displayWindow: view.current.displayWindow,
        coverages: view.current.coverages,
        seriesValues: view.current.seriesValues,
      }
    : null;
  const cards = standardCards(t, windowDays, {
    creditsHelp: HELP.delta,
    callsLabel: "Usage records",
    callNoun: "usage record",
    callsHelp:
      "Model-expanded usage records. A request using multiple models contributes multiple records; this is not a distinct request or user-prompt count.",
    trend: dailyTrend(rows, context),
    delta: trendDelta(view.current.comparisons.CREDITS),
  });
  return (
    <>
      {controls}
      {notice}
      {comparisonCaption}
      <KpiCards
        cards={[
          cards.credits,
          cards.tokens,
          cards.calls,
          cards.users,
          {
            label: "Active days",
            value: fmtInt(t.ACTIVE_DAYS),
            help: HELP.activeDays,
            caption: `of ${windowDays} in window`,
          },
        ]}
      />
      <Section title="Credits Over Time By Surface">
        <TrendChart
          series={daily(rows, "SUB_SURFACE", context)}
          colorCol="SUB_SURFACE"
          yLabel="Credits"
          previousDaily={view.current.previousDaily}
          pageId="coco"
        />
      </Section>
      <Section title="Surface Split">
        {top && (
          <Caption>
            {String(top.top.SUB_SURFACE)} leads at {Math.round(top.share * 100)}% of observed
            credits.
          </Caption>
        )}
        <BreakdownTable breakdown={bySub} label="Surface" labelCol="SUB_SURFACE" />
      </Section>
      <Section title="Token Type Mix">
        <TokenMixTable breakdown={tokenMix(rows)} />
        <Caption>
          Cache-read tokens dominate agentic coding workloads and bill at a fraction of input-token
          rates, which is why credits scale far more slowly than raw tokens.
        </Caption>
      </Section>
      <Breakdowns
        urlKey="coco_breakdown"
        defaultLabel="By user"
        labels={{
          "By user": "USER_NAME",
          "By model": "MODEL_NAME",
          "By role": "ROLE_NAME",
          "By inference region": "INFERENCE_REGION",
          "By user tag": TAG_COLUMN,
        }}
        rows={rows}
        tags={facts.tags}
        credits={t.CREDITS}
        captions={{
          [TAG_COLUMN]:
            "Tags come from current USER-domain tag references, so they reflect each user's tags today rather than at request time. Users with several tags appear under each.",
        }}
      />
      <OptionalWarehouse source={SURFACE_COCO} />
      <Expander title="Methodology">
        <Caption>
          Unified account-level usage across cli, desktop, and snowsight. Data can arrive up to one
          hour late and is retained for up to 365 days.
        </Caption>
      </Expander>
    </>
  );
}
