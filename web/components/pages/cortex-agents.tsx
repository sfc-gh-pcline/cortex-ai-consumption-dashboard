"use client";

import { BreakdownTable } from "@/components/kit/breakdown-table";
import { EmptyUsage, Metric } from "@/components/kit/empty-state";
import { HELP, KpiCards, standardCards } from "@/components/kit/kpi-cards";
import { InsightBanner, PageHeader, Row as ControlRow, Section } from "@/components/kit/layout";
import { MultiSelect } from "@/components/kit/multi-select";
import { TrendChart } from "@/components/kit/trend-chart";
import { OptionalWarehouse } from "@/components/kit/warehouse-component";
import { AGENT_TAG_COLUMN, Breakdowns, TAG_COLUMN } from "@/components/pages/breakdowns";
import { DataPage, useScopedView } from "@/components/pages/page-frame";
import { byDimension, leader, totals } from "@/lib/agg/metrics";
import { daily, dailyTrend, trendDelta } from "@/lib/agg/periods";
import { options, sumColumn } from "@/lib/agg/rows";
import type { PageFacts } from "@/lib/client/page-data";
import { useListParam } from "@/lib/client/url-state";
import { fmtCredits, fmtInt } from "@/lib/format";
import { SURFACE_AGENTS } from "@/lib/plans";

export function CortexAgentsPage() {
  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        eyebrow="Cortex surface"
        title={SURFACE_AGENTS}
        subtitle="Agent request usage from CORTEX_AGENT_USAGE_HISTORY."
      />
      <DataPage pageKey={SURFACE_AGENTS} unscopedView>
        {(facts) => <Body facts={facts} />}
      </DataPage>
    </div>
  );
}

function Body({ facts }: { facts: PageFacts }) {
  const [agents, setAgents] = useListParam("agent_fqns");
  const { view, rows, windowDays, notice, comparisonCaption } = useScopedView(
    facts,
    { AGENT_FQN: agents },
    {
      source: SURFACE_AGENTS,
      unscopedView: true,
    },
  );
  const controls = (
    <ControlRow>
      <MultiSelect
        label="Agent"
        options={options(facts.rows, "AGENT_FQN")}
        value={agents}
        onChange={(v) => void setAgents(v)}
        placeholder="All agents"
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
  const roots = sumColumn(rows, "ROOT_REQUESTS");
  const children = sumColumn(rows, "CHILD_REQUESTS");
  const agentCount = options(rows, "AGENT_FQN").length;
  const byAgent = byDimension(rows, "AGENT_FQN", "AGENT_FQN");
  const top = leader(byAgent, t.CREDITS);
  const context = view.current.displayWindow
    ? {
        displayWindow: view.current.displayWindow,
        coverages: view.current.coverages,
        seriesValues: view.current.seriesValues,
      }
    : null;
  const cards = standardCards(t, windowDays, {
    creditsHelp: HELP.delta,
    callsLabel: "Requests",
    callNoun: "request",
    callsHelp:
      "Total agent requests, including nested child requests spawned while servicing a turn. See the root and child breakdown below.",
    trend: dailyTrend(rows, context),
    delta: trendDelta(view.current.comparisons.CREDITS),
  });
  const detail = byAgent.rows.map((r) => {
    const subset = rows.filter((x) => x.AGENT_FQN === r.AGENT_FQN);
    return {
      ...r,
      ROOT_REQUESTS: sumColumn(subset, "ROOT_REQUESTS"),
      CHILD_REQUESTS: sumColumn(subset, "CHILD_REQUESTS"),
    };
  });
  return (
    <>
      {controls}
      {notice}
      {comparisonCaption}
      {top && (
        <InsightBanner
          detail={`${String(top.top.AGENT_FQN)} leads at ${Math.round(top.share * 100)}% of credits · ${fmtInt(roots)} user turns · ${(roots ? children / roots : 0).toFixed(2)} child requests per turn.`}
        />
      )}
      <KpiCards
        cards={[
          cards.credits,
          cards.tokens,
          cards.calls,
          cards.users,
          {
            label: "Agents",
            value: fmtInt(agentCount),
            help: "Distinct agents by fully-qualified name (database.schema.name).",
            caption: agentCount ? `${fmtCredits(t.CREDITS / agentCount)} credits each` : null,
          },
        ]}
      />
      <Section title="Credits Over Time">
        <TrendChart
          series={daily(rows, null, context)}
          yLabel="Credits"
          previousDaily={view.current.previousDaily}
          pageId="cortex_agents"
        />
      </Section>
      <ControlRow>
        <Metric
          label="Root requests"
          value={fmtInt(roots)}
          help="Requests with no parent, one per user turn."
        />
        <Metric
          label="Child requests"
          value={fmtInt(children)}
          help="Nested requests spawned by an agent while servicing a turn."
        />
        <Metric
          label="Child per root"
          value={roots ? (children / roots).toFixed(2) : "0.00"}
          help="Higher ratios mean more internal tool and reasoning steps per user turn."
        />
      </ControlRow>
      <Breakdowns
        urlKey="agent_breakdown"
        defaultLabel="By agent"
        labels={{
          "By agent": "AGENT_FQN",
          "By user": "USER_NAME",
          "By agent tag": AGENT_TAG_COLUMN,
          "By user tag": TAG_COLUMN,
        }}
        rows={rows}
        tags={facts.tags}
        credits={t.CREDITS}
        callsLabel="Requests"
      />
      <OptionalWarehouse source={SURFACE_AGENTS} />
      <Section title="Agent Detail">
        <BreakdownTable
          breakdown={{
            columns: [...byAgent.columns, "ROOT_REQUESTS", "CHILD_REQUESTS"],
            rows: detail,
          }}
          label="Agent (database.schema.name)"
          labelCol="AGENT_FQN"
          callsLabel="Requests"
          extraColumns={{
            ROOT_REQUESTS: { label: "Root", format: fmtInt },
            CHILD_REQUESTS: { label: "Child", format: fmtInt },
          }}
        />
      </Section>
    </>
  );
}
