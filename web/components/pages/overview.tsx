"use client";

import Link from "next/link";
import { useQueryStates, parseAsString } from "nuqs";

import { BreakdownTable } from "@/components/kit/breakdown-table";
import { EmptyState, EmptyUsage, inferState } from "@/components/kit/empty-state";
import { KpiCards } from "@/components/kit/kpi-cards";
import { Expander, PageHeader, Section } from "@/components/kit/layout";
import { Caption, Notice } from "@/components/kit/notice";
import { TrendChart } from "@/components/kit/trend-chart";
import { DataPage, useScopedView } from "@/components/pages/page-frame";
import { Button } from "@/components/ui/button";
import { byDimension, totals } from "@/lib/agg/metrics";
import { daily, dailyTrend, trendDelta } from "@/lib/agg/periods";
import { isMissing, options, sumValues } from "@/lib/agg/rows";
import { inferencePricingWarning } from "@/lib/rates";
import { useDashboard } from "@/lib/client/dashboard";
import type { PageFacts } from "@/lib/client/page-data";
import { fmtCredits, fmtInt, fmtTokens } from "@/lib/format";
import {
  PAGE_TOTAL_CONSUMPTION,
  SURFACES,
  SURFACE_AGENTS,
  SURFACE_AI_FUNCTIONS,
  SURFACE_COCO,
  SURFACE_COWORK,
  SURFACE_REST_API,
  SURFACE_SEARCH,
} from "@/lib/plans";
import { FILTER_CAPABILITIES, PAGES, urlPath } from "@/lib/view-state";

export function OverviewPage() {
  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        eyebrow="Account overview"
        title="AI Consumption Overview"
        subtitle="Reported AI credits and Inference API estimates for six supported services."
        pills={SURFACES}
      />
      <Expander title="Coverage and methodology">
        <div className="flex flex-col gap-2 text-sm">
          <p>
            Selected ACCOUNT_USAGE sources, not an invoice-wide total. Source latency varies;
            completed calendar days may still be incomplete.
          </p>
          <p>
            <strong>Included services:</strong> {SURFACE_COCO} · {SURFACE_COWORK} · {SURFACE_AGENTS}{" "}
            · {SURFACE_SEARCH} · {SURFACE_REST_API} · {SURFACE_AI_FUNCTIONS}
          </p>
          <p>
            <strong>Two things to know before reading numbers:</strong>
          </p>
          <ul className="list-disc pl-5">
            <li>
              <strong>{SURFACE_REST_API} credits are estimated.</strong> That view reports tokens
              only, so credits are derived from per-model token rates.
            </li>
            <li>
              <strong>{SURFACE_SEARCH} whole-service credits lack user/model attribution.</strong>{" "}
              Non-attributable records are separate when filters are active.
            </li>
          </ul>
          <p>
            Excludes warehouse compute, storage, data transfer and AI billing categories not
            represented by these six sources, including standalone Analyst, fine-tuning and
            provisioned throughput. Embedded tool costs may already be included in agent credits.
            Reference SQL shows baseline queries before in-memory filters.
          </p>
        </div>
      </Expander>
      <DataPage pageKey={PAGE_TOTAL_CONSUMPTION}>
        {(facts) => <OverviewBody facts={facts} />}
      </DataPage>
    </div>
  );
}

function OverviewBody({ facts }: { facts: PageFacts }) {
  const { page, filters } = useDashboard();
  const { view, rows, notice, comparisonCaption } = useScopedView(facts, {}, { source: null });
  const [investigate, setInvestigate] = useQueryStates({
    overview_service: parseAsString.withDefault(SURFACES[0]),
    overview_dimension: parseAsString.withDefault("Service only"),
  });
  const unavailable = facts.unavailableSources;

  if (rows.length === 0) {
    const active = filters.users.length > 0 || filters.models.length > 0 || facts.tagUsers !== null;
    return (
      <>
        {notice}
        <EmptyUsage
          comparison={view.current.comparisons.CREDITS}
          state={
            unavailable.length > 0
              ? "source_unavailable"
              : inferState(facts.rows, { activeFilters: active })
          }
          label="AI usage credits"
        />
      </>
    );
  }

  const t = totals(rows);
  const credits = t.CREDITS;
  const bySurface = byDimension(rows, "SURFACE", "SURFACE");
  const estimated =
    sumValues(rows.filter((r) => r.CREDITS_ESTIMATED === true).map((r) => r.CREDITS)) ?? 0;
  const reported = credits - estimated;
  const incompleteRates = rows.some((r) => r.RATE_INCOMPLETE === true);
  const inference = rows.filter((r) => r.CREDITS_ESTIMATED === true);
  const unpriced = inference.every((r) => !isMissing(r.UNRATED_TOKENS))
    ? sumValues(inference.map((r) => r.UNRATED_TOKENS))
    : null;
  const partial = unavailable.length > 0 || incompleteRates;
  const dailyContext = view.current.displayWindow
    ? {
        displayWindow: view.current.displayWindow,
        coverages: view.current.coverages,
        seriesValues: view.current.seriesValues,
      }
    : null;
  const capabilities = FILTER_CAPABILITIES[investigate.overview_service] ?? new Set<string>();
  const dimensions = [
    "Service only",
    ...(capabilities.has("USER_NAME") ? ["User"] : []),
    ...(capabilities.has("MODEL_NAME") ? ["Model"] : []),
  ];
  const dimension = dimensions.includes(investigate.overview_dimension)
    ? investigate.overview_dimension
    : "Service only";
  const driverColumn =
    dimension === "User" ? "USER_NAME" : dimension === "Model" ? "MODEL_NAME" : null;
  const driverChoices = driverColumn
    ? options(
        rows.filter((r) => r.SURFACE === investigate.overview_service),
        driverColumn,
      )
    : [];
  const target = PAGES.find((p) => p.dependencyKey === investigate.overview_service) ?? PAGES[1];

  const perUser = byDimension(rows, "USER_NAME", "USER_NAME");
  const pivot = new Map<string, Record<string, number>>();
  for (const row of rows) {
    const user = String(row.USER_NAME);
    const entry = pivot.get(user) ?? {};
    entry[String(row.SURFACE)] = (entry[String(row.SURFACE)] ?? 0) + (Number(row.CREDITS) || 0);
    pivot.set(user, entry);
  }
  const usersTable = {
    columns: [...perUser.columns.filter((c) => c !== "USERS" && c !== "CALLS"), ...SURFACES],
    rows: perUser.rows.map((r) => {
      const { USERS: _users, CALLS: _calls, ...rest } = r;
      void _users;
      void _calls;
      const extra = pivot.get(String(r.USER_NAME)) ?? {};
      return { ...rest, ...Object.fromEntries(SURFACES.map((s) => [s, extra[s] ?? 0])) };
    }),
  };
  const models = byDimension(
    rows.filter((r) => r.MODEL_NAME !== null && r.MODEL_NAME !== undefined),
    "MODEL_NAME",
    "MODEL_NAME",
  );

  return (
    <>
      {unavailable.length > 0 ? (
        <Notice tone="warning">
          Partial source coverage: unavailable {unavailable.join(", ")}. Totals exclude these
          sources.
        </Notice>
      ) : (
        <Caption>
          Six sources read. Ingestion completeness is not certified. Warehouse compute is excluded.
        </Caption>
      )}
      {notice}
      {comparisonCaption}
      {incompleteRates && <Notice tone="warning">{inferencePricingWarning(unpriced, true)}</Notice>}
      <KpiCards
        cards={[
          {
            label: "AI usage credits" + (partial ? " (partial)" : ""),
            value: fmtCredits(credits),
            delta: trendDelta(view.current.comparisons.CREDITS),
            trend: dailyTrend(rows, dailyContext),
          },
          { label: "Reported credits", value: fmtCredits(reported) },
          {
            label: "Estimated credits" + (incompleteRates ? " (partial)" : ""),
            value: incompleteRates && estimated === 0 ? "Unavailable" : fmtCredits(estimated),
          },
          {
            label: "Attributed users",
            value: fmtInt(t.USERS),
            help: "Identified users in matching facts; missing user attribution is excluded.",
          },
        ]}
      />
      <Caption>
        {fmtTokens(t.TOKENS)} reported tokens; token coverage differs by service. Search serving and
        non-token-metered functions do not report comparable token work.
      </Caption>
      <Section title="Credits Over Time By Surface">
        <TrendChart
          series={daily(rows, "SURFACE", dailyContext)}
          colorCol="SURFACE"
          yLabel="Credits"
          previousDaily={view.current.previousDaily}
          pageId={page.pageId}
        />
      </Section>
      <Section title="Surface Comparison">
        <BreakdownTable
          breakdown={{
            columns: bySurface.columns.filter((c) => c !== "CALLS"),
            rows: bySurface.rows,
          }}
          label="Surface"
          labelCol="SURFACE"
          chartKey="total_consumption_services"
        />
        <Caption>
          {SURFACE_REST_API} credits are estimated from observed per-model token rates. Every other
          surface reports credits directly from Snowflake.
        </Caption>
        <div className="flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1 text-xs text-muted-foreground">
            Investigate service
            <select
              value={investigate.overview_service}
              onChange={(e) =>
                void setInvestigate({
                  overview_service: e.target.value,
                  overview_dimension: "Service only",
                })
              }
              className="h-8 rounded-lg border border-input bg-transparent px-2 text-sm text-foreground"
            >
              {SURFACES.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-xs text-muted-foreground">
            Investigate dimension
            <select
              value={dimension}
              onChange={(e) => void setInvestigate({ overview_dimension: e.target.value })}
              className="h-8 rounded-lg border border-input bg-transparent px-2 text-sm text-foreground"
            >
              {dimensions.map((d) => (
                <option key={d} value={d}>
                  {d}
                </option>
              ))}
            </select>
          </label>
          <DriverLink
            route={urlPath(target)}
            dimension={dimension}
            choices={driverChoices}
            filters={filters}
          />
        </div>
      </Section>
      <Section title="CoCo Surface Split">
        {rows.some((r) => r.SURFACE === SURFACE_COCO) ? (
          <BreakdownTable
            breakdown={byDimension(
              rows.filter((r) => r.SURFACE === SURFACE_COCO),
              "SUB_SURFACE",
              "SUB_SURFACE",
            )}
            label="Surface"
            labelCol="SUB_SURFACE"
          />
        ) : (
          <EmptyState message="No CoCo usage in this window." state="no_usage" />
        )}
      </Section>
      <Section title="Top Users Across All Surfaces">
        <BreakdownTable
          breakdown={usersTable}
          label="User"
          labelCol="USER_NAME"
          limit={25}
          denominator={credits}
          extraColumns={Object.fromEntries(
            SURFACES.map((s) => [s, { label: s, format: fmtCredits }]),
          )}
        />
      </Section>
      <Section title="Top Models Across All Surfaces">
        <BreakdownTable
          breakdown={{ columns: models.columns.filter((c) => c !== "CALLS"), rows: models.rows }}
          label="Model"
          labelCol="MODEL_NAME"
          limit={20}
          denominator={credits}
        />
        <Caption>
          Models cover attributable records only; shares use all matching AI usage credits.
        </Caption>
      </Section>
    </>
  );
}

// "Open service" link carrying the current filters plus the chosen driver value.
function DriverLink({
  route,
  dimension,
  choices,
  filters,
}: {
  route: string;
  dimension: string;
  choices: string[];
  filters: { users: string[]; models: string[]; tags: string[] };
}) {
  const [driver, setDriver] = useQueryStates({
    overview_driver_USER_NAME: parseAsString,
    overview_driver_MODEL_NAME: parseAsString,
  });
  const key = dimension === "User" ? "overview_driver_USER_NAME" : "overview_driver_MODEL_NAME";
  const value = dimension === "Service only" ? null : driver[key];
  const params = new URLSearchParams(typeof window === "undefined" ? "" : window.location.search);
  for (const k of ["users", "models", "tags"]) params.delete(k);
  for (const k of [...params.keys()]) if (k.startsWith("overview_")) params.delete(k);
  const users = dimension === "User" && value ? [value] : filters.users;
  const models = dimension === "Model" && value ? [value] : filters.models;
  for (const user of users) params.append("users", user);
  for (const model of models) params.append("models", model);
  for (const tag of filters.tags) params.append("tags", tag);
  const href = `/${route}${params.toString() ? `?${params.toString()}` : ""}`;
  const disabled = dimension !== "Service only" && !value;
  return (
    <>
      {dimension !== "Service only" && (
        <label className="flex flex-col gap-1 text-xs text-muted-foreground">
          Driver value
          <select
            value={value ?? ""}
            onChange={(e) => void setDriver({ [key]: e.target.value || null })}
            className="h-8 rounded-lg border border-input bg-transparent px-2 text-sm text-foreground"
          >
            <option value="">Choose...</option>
            {choices.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        </label>
      )}
      {disabled ? (
        <Button size="sm" variant="outline" disabled>
          Open service
        </Button>
      ) : (
        <Button size="sm" variant="outline" render={<Link href={href} />}>
          Open service
        </Button>
      )}
    </>
  );
}
