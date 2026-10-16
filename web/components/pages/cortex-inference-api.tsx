"use client";

import { TokenMixTable } from "@/components/kit/breakdown-table";
import { EmptyUsage } from "@/components/kit/empty-state";
import { HELP, KpiCards, standardCards } from "@/components/kit/kpi-cards";
import { InsightBanner, PageHeader, Row as ControlRow, Section } from "@/components/kit/layout";
import { MultiSelect } from "@/components/kit/multi-select";
import { Caption, Notice } from "@/components/kit/notice";
import { TrendChart } from "@/components/kit/trend-chart";
import { Breakdowns, TAG_COLUMN } from "@/components/pages/breakdowns";
import { DataPage, useScopedView } from "@/components/pages/page-frame";
import { byDimension, leader, tokenMix, totals } from "@/lib/agg/metrics";
import { daily, dailyTrend, trendDelta } from "@/lib/agg/periods";
import { hasColumn, isMissing, options, sumColumn } from "@/lib/agg/rows";
import type { PageFacts } from "@/lib/client/page-data";
import { useListParam } from "@/lib/client/url-state";
import { fmtInt, fmtTokens } from "@/lib/format";
import { SURFACE_REST_API } from "@/lib/plans";
import { inferencePricingLabel, inferencePricingWarning } from "@/lib/rates";

export function CortexInferenceApiPage() {
  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        eyebrow="Cortex surface"
        title={SURFACE_REST_API}
        subtitle="Direct inference calls from CORTEX_REST_API_USAGE_HISTORY."
      />
      <Notice>
        Credits are estimated from observed per-model rates and cannot be pre-aggregated. This page
        always reads live data from ACCOUNT_USAGE, not from an aggregate snapshot.
      </Notice>
      <DataPage pageKey={SURFACE_REST_API}>{(facts) => <Body facts={facts} />}</DataPage>
    </div>
  );
}

function Body({ facts }: { facts: PageFacts }) {
  const [regions, setRegions] = useListParam("rest_regions");
  const [tags, setTags] = useListParam("rest_tags");
  const { view, rows, windowDays, notice, comparisonCaption } = useScopedView(
    facts,
    { INFERENCE_REGION: regions, QUERY_TAG: tags },
    { source: SURFACE_REST_API },
  );
  const controls = (
    <ControlRow>
      <MultiSelect
        label="Inference region"
        options={options(facts.rows, "INFERENCE_REGION")}
        value={regions}
        onChange={(v) => void setRegions(v)}
        placeholder="All regions"
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
        <EmptyUsage
          comparison={view.current.comparisons.CREDITS}
          state="filtered_empty"
          label="Credits (est.)"
        />
      </>
    );
  }
  const t = totals(rows);
  const unrated = sumColumn(rows, "UNRATED_TOKENS");
  const rateKnown =
    hasColumn(rows, "UNRATED_TOKENS") && rows.every((r) => !isMissing(r.UNRATED_TOKENS));
  const coverage = t.TOKENS && rateKnown ? 1 - unrated / t.TOKENS : null;
  const coverageLabel = inferencePricingLabel(rateKnown ? unrated : null);
  const incomplete = !rateKnown || unrated > 0;
  const models = options(rows, "MODEL_NAME").length;
  const byModel = byDimension(rows, "MODEL_NAME", "MODEL_NAME");
  const top = leader(byModel, t.CREDITS);
  const context = view.current.displayWindow
    ? {
        displayWindow: view.current.displayWindow,
        coverages: view.current.coverages,
        seriesValues: view.current.seriesValues,
      }
    : null;
  const cards = standardCards(t, windowDays, {
    creditsLabel: incomplete ? "Credits (partial estimate)" : "Credits (est.)",
    creditsHelp: `Estimated: this view reports tokens but no credits. See the note below. ${HELP.delta}`,
    callsLabel: "Requests",
    callNoun: "request",
    callsHelp: "One row per REST inference request to the Cortex Inference API.",
    trend: dailyTrend(rows, context),
    delta: trendDelta(view.current.comparisons.CREDITS),
  });
  if (incomplete && coverage === 0) cards.credits = { ...cards.credits, value: "Unavailable" };
  return (
    <>
      {controls}
      {notice}
      {comparisonCaption}
      {incomplete && (
        <Notice tone="warning">{inferencePricingWarning(rateKnown ? unrated : null)}</Notice>
      )}
      {top && (
        <InsightBanner
          detail={`${String(top.top.MODEL_NAME)} leads at ${Math.round(top.share * 100)}% of estimated credits · ${fmtTokens(t.TOKENS)} tokens · ${coverageLabel}.`}
        />
      )}
      <KpiCards
        cards={[
          cards.credits,
          cards.tokens,
          cards.calls,
          cards.users,
          {
            label: "Models",
            value: fmtInt(models),
            help: "Distinct models invoked. Tokens without a matching observed rate still count toward usage, but their credits are excluded from the estimate, not free.",
            caption: coverageLabel,
          },
        ]}
      />
      <Notice>
        <strong>Credits on this page are estimated.</strong>{" "}
        <code>CORTEX_REST_API_USAGE_HISTORY</code> reports token counts but no credit column, so
        credits are derived by applying per-model, per-token-type rates observed in CoCo history,
        where both tokens and credits are reported. Those observed rates are an estimate basis, not
        a billing guarantee. <strong>{coverageLabel}.</strong> Coverage checks each reported token
        type against the same rate snapshot used for both periods. A model/token-type rate may be
        absent from the observed CoCo history, or the usage record may lack a token-type breakdown.
        The dashboard cannot price those tokens or determine their missing credit amount.
      </Notice>
      {facts.rateSnapshotAt && (
        <Caption>
          Observed rate snapshot fetched at {facts.rateSnapshotAt}; shared by both periods.
        </Caption>
      )}
      <Section title="Estimated Credits Over Time">
        <TrendChart
          series={daily(rows, null, context)}
          yLabel="Credits (est.)"
          previousDaily={view.current.previousDaily}
          pageId="cortex_inference_api"
        />
      </Section>
      <Section title="Token Type Mix">
        <TokenMixTable breakdown={tokenMix(rows)} />
      </Section>
      <Breakdowns
        urlKey="rest_breakdown"
        defaultLabel="By model"
        labels={{
          "By model": "MODEL_NAME",
          "By user": "USER_NAME",
          "By region": "INFERENCE_REGION",
          "By query tag": "QUERY_TAG",
          "By user tag": TAG_COLUMN,
        }}
        rows={rows}
        tags={facts.tags}
        credits={t.CREDITS}
        callsLabel="Requests"
      />
    </>
  );
}
