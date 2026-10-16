"use client";

import { Caption, Notice } from "@/components/kit/notice";
import { trendDelta } from "@/lib/agg/periods";
import { fmtCredits } from "@/lib/format";
import { ComparisonState, type ComparisonResult } from "@/lib/view-state";

export type LoadState = "ok" | "no_usage" | "filtered_empty" | "source_unavailable";

const STATE_TEXT: Record<Exclude<LoadState, "ok">, string> = {
  filtered_empty: "No usage matches the current filters.",
  no_usage: "No usage recorded in this date window.",
  source_unavailable:
    "This data source is not accessible. Check Acceleration Settings and the connection preflight for diagnostics.",
};

// Why a page has nothing to display, from the unfiltered rows and active filters.
export function inferState(
  rawRows: readonly unknown[] | null,
  options: { activeFilters?: boolean; unavailable?: boolean } = {},
): LoadState {
  if (rawRows === null || options.unavailable) return "source_unavailable";
  if (rawRows.length > 0 && options.activeFilters) return "filtered_empty";
  return "no_usage";
}

// Neutral placeholder instead of an empty chart or table.
export function EmptyState({ message, state }: { message?: string; state?: LoadState }) {
  const effective = state ?? "filtered_empty";
  if (effective === "ok") return null;
  const text = effective === "source_unavailable" || !message ? STATE_TEXT[effective] : message;
  return <Notice tone={effective === "source_unavailable" ? "error" : "info"}>{text}</Notice>;
}

// Covered zero-period comparisons without inventing usage records (ui.empty_usage).
export function EmptyUsage({
  comparison,
  state = "no_usage",
  label = "Credits",
}: {
  comparison: ComparisonResult | null | undefined;
  state?: LoadState;
  label?: string;
}) {
  const unavailable =
    state === "source_unavailable" ||
    (comparison != null && comparison.state === ComparisonState.UNAVAILABLE);
  if (unavailable || !comparison) {
    return (
      <div className="flex flex-col gap-2">
        {unavailable && <Metric label={label} value="Unavailable" />}
        <EmptyState state={unavailable ? "source_unavailable" : state} />
      </div>
    );
  }
  const comparable = comparison.deltaPercent !== null;
  return (
    <div className="flex flex-col gap-2">
      <Metric
        label={comparable ? label : `Observed ${label.toLowerCase()}`}
        value={fmtCredits(comparison.current)}
        delta={trendDelta(comparison)}
      />
      {comparable ? (
        <>
          <Notice>No usage matches this scope in the completed current period.</Notice>
          {comparison.state === ComparisonState.BOTH_ZERO && (
            <Caption>No change: both covered periods have zero usage.</Caption>
          )}
        </>
      ) : (
        <Notice>
          No current-period records were returned. This does not establish zero usage.
        </Notice>
      )}
      {comparison.previous !== null && (
        <Caption>Previous-period observed credits: {fmtCredits(comparison.previous)}.</Caption>
      )}
    </div>
  );
}

// st.metric equivalent for one-off values.
export function Metric({
  label,
  value,
  delta,
  help,
  border = true,
}: {
  label: string;
  value: string;
  delta?: string | null;
  help?: string;
  border?: boolean;
}) {
  return (
    <div className={border ? "min-w-44 rounded-lg border px-4 py-3" : "min-w-44"} title={help}>
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="text-2xl font-semibold">{value}</div>
      {delta && <div className="text-xs text-muted-foreground">{delta}</div>}
    </div>
  );
}
