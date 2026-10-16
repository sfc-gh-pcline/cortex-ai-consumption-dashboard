"use client";

import { EmptyUsage } from "@/components/kit/empty-state";
import { HELP, KpiCards, standardCards } from "@/components/kit/kpi-cards";
import { InsightBanner, PageHeader, Row as ControlRow, Section } from "@/components/kit/layout";
import { MultiSelect } from "@/components/kit/multi-select";
import { Notice } from "@/components/kit/notice";
import { TrendChart } from "@/components/kit/trend-chart";
import { OptionalWarehouse } from "@/components/kit/warehouse-component";
import { Breakdowns, TAG_COLUMN } from "@/components/pages/breakdowns";
import { DataPage, useScopedView } from "@/components/pages/page-frame";
import { byDimension, leader, totals } from "@/lib/agg/metrics";
import { daily, dailyTrend, trendDelta } from "@/lib/agg/periods";
import { options, sumColumn } from "@/lib/agg/rows";
import { useDashboard } from "@/lib/client/dashboard";
import type { PageFacts } from "@/lib/client/page-data";
import { useListParam } from "@/lib/client/url-state";
import { AI_FUNCTIONS_SOURCE_START } from "@/lib/coverage";
import { fmtCredits, fmtInt } from "@/lib/format";
import { SURFACE_AI_FUNCTIONS } from "@/lib/plans";
import type { DateWindow } from "@/lib/view-state";

// Historical coverage notice for windows before the supported AI Functions source.
export function aiFunctionsWarehouseCoverageNotice(window: DateWindow): string | null {
  if (window.start >= AI_FUNCTIONS_SOURCE_START) return null;
  if (window.end < AI_FUNCTIONS_SOURCE_START) {
    return (
      "AI Functions warehouse attribution is incomplete for this date range. " +
      "The supported usage source contains data only from January 5, 2026. " +
      "Missing earlier attribution does not mean zero warehouse usage."
    );
  }
  return (
    "This date range includes days before January 5, 2026. AI Functions warehouse " +
    "attribution is incomplete for those earlier days because the supported usage " +
    "source has no earlier history."
  );
}

export function CortexAiFunctionsPage() {
  const { displayWindow } = useDashboard();
  const notice = displayWindow ? aiFunctionsWarehouseCoverageNotice(displayWindow) : null;
  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        eyebrow="Cortex surface"
        title={SURFACE_AI_FUNCTIONS}
        subtitle="Cortex AI SQL function usage from CORTEX_AI_FUNCTIONS_USAGE_HISTORY."
      />
      {notice && <Notice tone="warning">{notice}</Notice>}
      <DataPage pageKey={SURFACE_AI_FUNCTIONS}>{(facts) => <Body facts={facts} />}</DataPage>
    </div>
  );
}

function Body({ facts }: { facts: PageFacts }) {
  const [functions, setFunctions] = useListParam("aifn_functions");
  const [tags, setTags] = useListParam("aifn_tags");
  const { view, rows, windowDays, notice, comparisonCaption } = useScopedView(
    facts,
    { FUNCTION_NAME: functions, QUERY_TAG: tags },
    { source: SURFACE_AI_FUNCTIONS },
  );
  const controls = (
    <ControlRow>
      <MultiSelect
        label="Function"
        options={options(facts.rows, "FUNCTION_NAME")}
        value={functions}
        onChange={(v) => void setFunctions(v)}
        placeholder="All functions"
      />
      <MultiSelect
        label="Query tag"
        options={options(facts.rows, "QUERY_TAG")}
        value={tags}
        onChange={(v) => void setTags(v)}
        placeholder="All query tags"
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
  const functionCount = options(rows, "FUNCTION_NAME").length;
  const byFunction = byDimension(rows, "FUNCTION_NAME", "FUNCTION_NAME");
  const top = leader(byFunction, t.CREDITS);
  const incomplete = sumColumn(rows, "INCOMPLETE_CALLS");
  const context = view.current.displayWindow
    ? {
        displayWindow: view.current.displayWindow,
        coverages: view.current.coverages,
        seriesValues: view.current.seriesValues,
      }
    : null;
  const cards = standardCards(t, windowDays, {
    creditsHelp: HELP.delta,
    tokensHelp:
      "Summed from the METRICS array, covering token-metered functions only. Functions billed per page or per row (for example AI_PARSE_DOCUMENT) can report credits without tokens. Token totals exclude non-token work; their reported credits are still included.",
    callsLabel: "Usage records",
    callNoun: "usage record",
    callsHelp:
      "Hourly function/model usage records. Long-running invocations can span multiple records; this is not an invocation count.",
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
          detail={`${String(top.top.FUNCTION_NAME)} leads at ${Math.round(top.share * 100)}% of credits · ${fmtInt(functionCount)} distinct functions · ${fmtInt(t.CALLS / windowDays)} observed usage records / day (${windowDays} days).`}
        />
      )}
      <KpiCards
        cards={[
          cards.credits,
          cards.tokens,
          cards.calls,
          cards.users,
          {
            label: "Functions",
            value: fmtInt(functionCount),
            help: "Distinct Cortex AI functions invoked in the selected window, after filters.",
            caption: functionCount ? `${fmtCredits(t.CREDITS / functionCount)} credits each` : null,
          },
        ]}
      />
      {incomplete > 0 && (
        <Notice tone="warning">
          {fmtInt(incomplete)} usage records were not marked complete in their aggregation window.
          This is not a failed-call count. Their reported credits are included above.
        </Notice>
      )}
      <Section title="Credits Over Time">
        <TrendChart
          series={daily(rows, null, context)}
          yLabel="Credits"
          previousDaily={view.current.previousDaily}
          pageId="cortex_ai_functions"
        />
      </Section>
      <Breakdowns
        urlKey="aifn_breakdown"
        defaultLabel="By function"
        labels={{
          "By function": "FUNCTION_NAME",
          "By model": "MODEL_NAME",
          "By user": "USER_NAME",
          "By query tag": "QUERY_TAG",
          "By user tag": TAG_COLUMN,
        }}
        rows={rows}
        tags={facts.tags}
        credits={t.CREDITS}
        captions={{
          FUNCTION_NAME:
            "Token counts come from the METRICS array and cover token-metered functions. Functions billed per page or per row (for example AI_PARSE_DOCUMENT) report credits with zero tokens.",
        }}
      />
      <OptionalWarehouse source={SURFACE_AI_FUNCTIONS} />
    </>
  );
}
