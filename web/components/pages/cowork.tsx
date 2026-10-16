"use client";

import { BreakdownTable, TokenMixTable } from "@/components/kit/breakdown-table";
import { DataTable } from "@/components/kit/data-table";
import { EmptyUsage, Metric } from "@/components/kit/empty-state";
import { HELP, KpiCards, standardCards } from "@/components/kit/kpi-cards";
import { InsightBanner, PageHeader, Row as ControlRow, Section } from "@/components/kit/layout";
import { MultiSelect } from "@/components/kit/multi-select";
import { Caption } from "@/components/kit/notice";
import { TrendChart } from "@/components/kit/trend-chart";
import { OptionalWarehouse } from "@/components/kit/warehouse-component";
import { Breakdowns, TAG_COLUMN } from "@/components/pages/breakdowns";
import { DataPage, useScopedView } from "@/components/pages/page-frame";
import { Switch } from "@/components/ui/switch";
import { byDimension, leader, tokenMix, totals } from "@/lib/agg/metrics";
import { daily, dailyTrend, trendDelta } from "@/lib/agg/periods";
import { options, sumColumn, type Row } from "@/lib/agg/rows";
import { scopedUsage } from "@/lib/agg/scoped";
import { useDashboard } from "@/lib/client/dashboard";
import { useAuxiliary, type PageFacts } from "@/lib/client/page-data";
import { useBooleanParam, useListParam } from "@/lib/client/url-state";
import { fmtCredits, fmtInt, fmtTokens } from "@/lib/format";
import { DEP_COWORK_SERVICE_MIX, SURFACE_COWORK } from "@/lib/plans";

export function CoworkPage() {
  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        eyebrow="Cortex surface"
        title={SURFACE_COWORK}
        subtitle="CoWork message requests from SNOWFLAKE_COWORK_USAGE_HISTORY."
      />
      <DataPage pageKey={SURFACE_COWORK} unscopedView>
        {(facts) => <Body facts={facts} />}
      </DataPage>
    </div>
  );
}

function Body({ facts }: { facts: PageFacts }) {
  const [instances, setInstances] = useListParam("cowork_instances");
  const [agents, setAgents] = useListParam("cowork_agents");
  const pageValues = { COWORK_NAME: instances, AGENT_FQN: agents };
  const { view, rows, windowDays, notice, comparisonCaption } = useScopedView(facts, pageValues, {
    source: SURFACE_COWORK,
    unscopedView: true,
  });
  const controls = (
    <ControlRow>
      <MultiSelect
        label="CoWork instance"
        options={options(facts.rows, "COWORK_NAME")}
        value={instances}
        onChange={(v) => void setInstances(v)}
        placeholder="All instances"
      />
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
        <EmptyUsage
          comparison={view.current.comparisons.CREDITS}
          state="filtered_empty"
          label="Token credits"
        />
      </>
    );
  }
  const t = totals(rows);
  const agentCount = options(rows, "AGENT_FQN").length;
  const sqlCredits = sumColumn(rows, "SQL_QUERY_CREDITS");
  const aiFnCredits = sumColumn(rows, "AI_FUNCTIONS_CREDITS");
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
    creditsLabel: "Token credits",
    creditsHelp: `TOKEN_CREDITS, the model inference portion of the CoWork bill. ${HELP.delta}`,
    callsLabel: "Requests",
    callsHelp:
      "One row per CoWork message request. A single request fans out to one or more agent calls plus any tools it invokes, such as Cortex Analyst or Cortex Search. See the service split below.",
    callNoun: "request",
    trend: dailyTrend(rows, context),
    delta: trendDelta(view.current.comparisons.CREDITS),
  });
  const agentDetail = byAgent.rows.map((r) => {
    const subset = rows.filter((x) => x.AGENT_FQN === r.AGENT_FQN);
    return {
      ...r,
      SQL_QUERY_CREDITS: sumColumn(subset, "SQL_QUERY_CREDITS"),
      ROOT_REQUESTS: sumColumn(subset, "ROOT_REQUESTS"),
    };
  });
  return (
    <>
      {controls}
      {notice}
      {comparisonCaption}
      {top && (
        <InsightBanner
          detail={`${String(top.top.AGENT_FQN)} leads at ${Math.round(top.share * 100)}% of token credits · ${fmtCredits(sqlCredits)} credits of warehouse SQL · ${fmtInt(t.CALLS)} requests from ${fmtInt(t.USERS)} users.`}
        />
      )}
      <KpiCards
        cards={[
          cards.credits,
          cards.tokens,
          cards.calls,
          {
            label: "Agents",
            value: fmtInt(agentCount),
            help: "Distinct agents invoked by CoWork, by fully-qualified name.",
            caption: agentCount ? `${fmtCredits(t.CREDITS / agentCount)} credits each` : null,
          },
        ]}
      />
      <Section title="Credits Over Time">
        <TrendChart
          series={daily(rows, null, context)}
          yLabel="Token credits"
          previousDaily={view.current.previousDaily}
          pageId="cowork"
        />
      </Section>
      <Section title="CoWork metadata costs">
        <Caption>
          Scope: the request records shown above. Observed SQL metadata may lag by eight hours and
          excludes adaptive warehouses. Null metadata does not prove zero usage. These costs can
          overlap warehouse attribution and AI Functions totals; do not add them together.
        </Caption>
        <ControlRow>
          <Metric
            label="Observed SQL metadata credits"
            value={sqlCredits ? fmtCredits(sqlCredits) : "Unknown / none reported"}
          />
          <Metric
            label="Observed AI Functions metadata credits"
            value={aiFnCredits ? fmtCredits(aiFnCredits) : "Unknown / none reported"}
          />
        </ControlRow>
      </Section>
      <OptionalWarehouse source={SURFACE_COWORK} />
      <ServiceMix pageValues={pageValues} />
      <Breakdowns
        urlKey="cowork_breakdown"
        defaultLabel="By agent"
        labels={{
          "By agent": "AGENT_FQN",
          "By CoWork instance": "COWORK_NAME",
          "By user": "USER_NAME",
          "By role": "ROLE_NAME",
          "By user tag": TAG_COLUMN,
        }}
        rows={rows}
        tags={facts.tags}
        credits={t.CREDITS}
        callsLabel="Requests"
        captions={
          t.USERS === 1
            ? {
                USER_NAME:
                  "All CoWork usage in this window is attributed to a single user, so this breakdown shows one row. Attribution is present, there is just one user. CoWork requests made through Cortex Agents are attributed on the Cortex Agents page instead.",
              }
            : {}
        }
      />
      <Section title="Agent Detail">
        <DataTable
          rows={agentDetail}
          columns={[
            { key: "AGENT_FQN", label: "Agent (database.schema.name)" },
            {
              key: "CREDITS",
              label: "Token credits",
              render: (v) => fmtCredits(v),
              align: "right",
            },
            {
              key: "SQL_QUERY_CREDITS",
              label: "SQL credits",
              render: (v) => fmtCredits(v),
              align: "right",
            },
            { key: "TOKENS", label: "Tokens", render: fmtTokens, align: "right" },
            { key: "CALLS", label: "Requests", render: fmtInt, align: "right" },
            { key: "USERS", label: "Users", render: fmtInt, align: "right" },
          ]}
          initialSort={{ key: "CREDITS", desc: true }}
        />
      </Section>
    </>
  );
}

// Service-type and model split from the granular arrays, loaded on demand.
function ServiceMix({ pageValues }: { pageValues: Record<string, readonly string[]> }) {
  const [open, setOpen] = useBooleanParam("detail_cowork_mix");
  const state = useAuxiliary(DEP_COWORK_SERVICE_MIX, open);
  const { periods, filters } = useDashboard();
  const mixRows: Row[] | null = state.data ? state.data.rows : null;
  const scoped = mixRows
    ? scopedUsage(mixRows, {
        selection: { ...filters, pageValues },
        tagUsers: null,
        source: "CoWork service mix",
        periods,
        coverages: [state.data!.coverage],
      }).current.rows
    : [];
  return (
    <div className="flex flex-col gap-4">
      <label className="flex items-center gap-2 text-sm">
        <Switch
          checked={open}
          onCheckedChange={(checked) => void setOpen(checked)}
          aria-label="Service mix detail"
        />
        Service mix detail
      </label>
      {open && state.isLoading && <Caption>Loading service mix...</Caption>}
      {open && scoped.length > 0 && (
        <>
          <Section title="Where CoWork Credits Go">
            <BreakdownTable
              breakdown={byDimension(scoped, "SERVICE_TYPE", "SERVICE_TYPE")}
              label="Service"
              labelCol="SERVICE_TYPE"
            />
            <Caption>
              A CoWork turn calls the agent and then whatever tools it needs, so cortex_agents and
              cortex_analyst are billed within the same request. This split reads TOKENS_GRANULAR /
              CREDITS_GRANULAR, so the agent and instance pickers above do not narrow it.
            </Caption>
          </Section>
          <Section title="Model Split">
            <BreakdownTable
              breakdown={byDimension(scoped, "MODEL_NAME", "MODEL_NAME")}
              label="Model"
              labelCol="MODEL_NAME"
            />
          </Section>
          <Section title="Token Type Mix">
            <TokenMixTable breakdown={tokenMix(scoped)} />
          </Section>
        </>
      )}
    </div>
  );
}
