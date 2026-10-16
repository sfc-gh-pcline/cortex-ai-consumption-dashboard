"use client";

import { DataTable, type ColumnSpec } from "@/components/kit/data-table";
import { EmptyState } from "@/components/kit/empty-state";
import { Caption } from "@/components/kit/notice";
import { Segmented } from "@/components/kit/segmented";
import { Chart } from "@/components/kit/vega";
import type { Breakdown } from "@/lib/agg/metrics";
import { isMissing, num, type Row } from "@/lib/agg/rows";
import { breakdownBarSpec } from "@/lib/chart-spec";
import { useStringParam } from "@/lib/client/url-state";
import { fmtCredits, fmtInt, fmtTokens } from "@/lib/format";

export interface ExtraColumn {
  readonly label: string;
  readonly format?: (value: unknown) => string;
}

export interface BreakdownTableProps {
  readonly breakdown: Breakdown | null | undefined;
  readonly label: string;
  readonly labelCol?: string;
  readonly shareOf?: string;
  readonly shareLabel?: string;
  readonly extraColumns?: Readonly<Record<string, ExtraColumn>>;
  readonly denominator?: number | null;
  readonly limit?: number | null;
  readonly callsLabel?: string;
  readonly chartKey?: string | null;
}

// Credit-like cells: two decimals with the <0.01 indicator, blank when missing.
const credits = (value: unknown) => (isMissing(value) ? "" : fmtCredits(value));

// Standard metric column labels and formats (utils/ui.py _NUMERIC_CONFIG).
const STANDARD: Record<string, ExtraColumn> = {
  CREDITS: { label: "Credits", format: credits },
  TOKENS: { label: "Tokens", format: fmtTokens },
  CALLS: { label: "Usage records", format: fmtInt },
  USERS: { label: "Users", format: fmtInt },
};

const CREDIT_LIKE = new Set(["CREDITS", "WAREHOUSE_CREDITS", "COMPUTE_CREDITS"]);
const TOP_N = ["10", "25", "50", "All"];

function shareBar(value: unknown) {
  const share = num(value) ?? 0;
  return (
    <div className="flex items-center gap-2">
      <div className="h-2 w-24 overflow-hidden rounded-full bg-muted" aria-hidden>
        <div
          className="h-full bg-primary"
          style={{ width: `${Math.min(100, Math.max(0, share * 100))}%` }}
        />
      </div>
      <span className="tabular-nums">{(share * 100).toFixed(1)}%</span>
    </div>
  );
}

// Sortable breakdown with a fixed 0-100% share bar; optional ranked chart and top-N.
export function BreakdownTable({
  breakdown,
  label,
  labelCol,
  shareOf = "CREDITS",
  shareLabel,
  extraColumns = {},
  denominator = null,
  limit = null,
  callsLabel = "Usage records",
  chartKey = null,
}: BreakdownTableProps) {
  const [mode, setMode] = useStringParam(chartKey ? `chart_${chartKey}` : "chart_unused", "Chart");
  const [topN, setTopN] = useStringParam(chartKey ? `top_${chartKey}` : "top_unused", "10");
  if (!breakdown || breakdown.rows.length === 0) return <EmptyState />;

  const labelColumn = labelCol ?? breakdown.columns[0];
  const total = denominator ?? breakdown.rows.reduce((sum, r) => sum + (num(r[shareOf]) ?? 0), 0);
  let rows: Row[] = [...breakdown.rows];
  let effectiveLimit = limit;
  const chartMode = chartKey ? (mode === "Table" ? "Table" : "Chart") : "Table";
  const captions: string[] = [];
  if (chartKey) {
    rows.sort((a, b) => (num(b[shareOf]) ?? 0) - (num(a[shareOf]) ?? 0));
    effectiveLimit = topN === "All" ? null : Number(topN) || 10;
    if (effectiveLimit === null)
      captions.push(`All ${rows.length} groups; full denominator ${fmtCredits(total)}.`);
  }
  rows = rows.map((r) => ({ ...r, SHARE: total ? (num(r[shareOf]) ?? 0) / total : 0 }));
  if (effectiveLimit !== null) {
    captions.push(
      `Top ${Math.min(effectiveLimit, rows.length)} of ${rows.length}; full denominator ${fmtCredits(total)}.`,
    );
    const remainder = rows.slice(effectiveLimit);
    rows = rows.slice(0, effectiveLimit);
    if (remainder.length > 0 && CREDIT_LIKE.has(shareOf)) {
      const otherValue = remainder.reduce((sum, r) => sum + (num(r[shareOf]) ?? 0), 0);
      rows.push({
        [labelColumn]: "Other",
        [shareOf]: otherValue,
        SHARE: total ? otherValue / total : 0,
      });
    }
  }

  const configured: Record<string, ExtraColumn> = {
    ...STANDARD,
    CALLS: { ...STANDARD.CALLS, label: callsLabel },
    ...extraColumns,
  };
  const axisTitle = (configured[shareOf]?.label ?? shareOf.replaceAll("_", " ")).toLowerCase();
  const present = breakdown.columns;
  const ordered = [
    labelColumn,
    ...["CREDITS", "TOKENS", "CALLS", "USERS"].filter((c) => present.includes(c)),
    ...Object.keys(extraColumns).filter(
      (c) =>
        present.includes(c) && !["CREDITS", "TOKENS", "CALLS", "USERS", labelColumn].includes(c),
    ),
  ];
  const columns: ColumnSpec[] = [
    { key: labelColumn, label },
    ...ordered.slice(1).map((key) => ({
      key,
      label: configured[key]?.label ?? key.replaceAll("_", " "),
      render: (value: unknown) => (configured[key]?.format ?? credits)(value),
      align: "right" as const,
    })),
    {
      key: "SHARE",
      label: `Share of ${shareLabel ?? axisTitle}`,
      render: shareBar,
      align: "right",
    },
  ];

  return (
    <div className="flex flex-col gap-2">
      {chartKey && (
        <div className="flex flex-wrap items-end gap-4">
          <Segmented
            label="Display"
            options={["Chart", "Table"]}
            value={chartMode}
            onChange={(v) => void setMode(v)}
          />
          <label className="flex flex-col gap-1 text-xs text-muted-foreground">
            Show
            <select
              value={TOP_N.includes(topN) ? topN : "10"}
              onChange={(e) => void setTopN(e.target.value)}
              className="h-8 rounded-lg border border-input bg-transparent px-2 text-sm text-foreground"
            >
              {TOP_N.map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
          </label>
        </div>
      )}
      {captions.map((c) => (
        <Caption key={c}>{c}</Caption>
      ))}
      {chartMode === "Chart" ? (
        <Chart
          spec={breakdownBarSpec(labelColumn, shareOf, label, axisTitle, rows.length)}
          data={rows}
        />
      ) : (
        <DataTable rows={rows} columns={columns} />
      )}
    </div>
  );
}

// Token-type totals as a ranked table with a share-of-tokens bar.
export function TokenMixTable({ breakdown }: { breakdown: Breakdown }) {
  const total = breakdown.rows.reduce((sum, r) => sum + (num(r.TOKENS) ?? 0), 0);
  if (breakdown.rows.length === 0 || total === 0) {
    return <EmptyState message="No token detail available for the current filters." />;
  }
  return <BreakdownTable breakdown={breakdown} label="Token type" shareOf="TOKENS" />;
}
