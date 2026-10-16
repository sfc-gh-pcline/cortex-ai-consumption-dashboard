"use client";

import { DataTable, type ColumnSpec } from "@/components/kit/data-table";
import { EmptyState } from "@/components/kit/empty-state";
import { Segmented } from "@/components/kit/segmented";
import { Chart } from "@/components/kit/vega";
import { Switch } from "@/components/ui/switch";
import { useIsDark } from "@/components/shell/theme-toggle";
import { relativePeriodDaily, type CalendarPoint, type DailySeries } from "@/lib/agg/periods";
import { fmtCredits } from "@/lib/format";
import { isMissing, sumValues, type Row } from "@/lib/agg/rows";
import { relativeTrendSpec, trendSpec } from "@/lib/chart-spec";
import { useBooleanParam, useStringParam } from "@/lib/client/url-state";

const PRESENTATIONS = ["Total", "Compare services", "Composition"] as const;
const MODES = ["Chart", "Table"] as const;

export interface TrendChartProps {
  readonly series: DailySeries;
  readonly valueCol?: string;
  readonly colorCol?: string | null;
  readonly yLabel?: string;
  readonly previousDaily?: readonly CalendarPoint[] | null;
  readonly pageId: string;
}

const number = (value: unknown) => (isMissing(value) ? "" : fmtCredits(value));

// Daily trend with Chart/Table, previous-period, and Total/Compare/Composition
// controls, each bound to the URL under the same keys the Streamlit app used.
export function TrendChart({
  series,
  valueCol = "CREDITS",
  colorCol = null,
  yLabel = "Credits",
  previousDaily = null,
  pageId,
}: TrendChartProps) {
  const dark = useIsDark();
  const [mode, setMode] = useStringParam(`chart_${pageId}_${valueCol}`, "Chart");
  const [compare, setCompare] = useBooleanParam(`compare_${pageId}`);
  const [presentation, setPresentation] = useStringParam(`trend_${pageId}`, "Total");
  if (series.rows.length === 0) return <EmptyState />;
  const canCompare = previousDaily !== null && previousDaily.length > 0;
  const splitting = colorCol !== null && series.columns.includes(colorCol);

  let data: Row[] = series.rows;
  let spec = trendSpec(valueCol, null, yLabel, { dark });
  let columns: ColumnSpec[] = [
    { key: "USAGE_DATE", label: "Date" },
    { key: valueCol, label: yLabel, render: number, align: "right" },
  ];
  if (compare && canCompare) {
    data = relativePeriodDaily(series.rows, previousDaily as unknown as Row[], valueCol);
    spec = relativeTrendSpec(valueCol, yLabel, dark);
    columns = [
      { key: "Period", label: "Period" },
      { key: "Day", label: "Day" },
      { key: "USAGE_DATE", label: "Date" },
      { key: valueCol, label: yLabel, render: number, align: "right" },
    ];
  } else {
    const shown = splitting ? presentation : "Total";
    if (shown === "Total") {
      const byDay = new Map<string, unknown[]>();
      for (const row of series.rows) {
        const day = String(row.USAGE_DATE);
        byDay.set(day, [...(byDay.get(day) ?? []), row[valueCol]]);
      }
      data = [...byDay.entries()]
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([day, values]) => ({
          USAGE_DATE: day,
          [valueCol]: sumValues(values, values.length),
        }));
    } else {
      spec = trendSpec(valueCol, colorCol, yLabel, { composition: shown === "Composition", dark });
      columns = [{ key: colorCol as string, label: colorCol as string }, ...columns];
    }
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-end gap-4">
        <Segmented
          label="Display"
          options={MODES}
          value={mode === "Table" ? "Table" : "Chart"}
          onChange={(v) => void setMode(v)}
        />
        {canCompare && (
          <label className="flex items-center gap-2 text-sm">
            <Switch
              checked={compare}
              onCheckedChange={(checked) => void setCompare(checked)}
              aria-label="Previous period"
            />
            Previous period
          </label>
        )}
        {splitting && !compare && (
          <label className="flex flex-col gap-1 text-xs text-muted-foreground">
            Trend
            <select
              value={
                PRESENTATIONS.includes(presentation as (typeof PRESENTATIONS)[number])
                  ? presentation
                  : "Total"
              }
              onChange={(e) => void setPresentation(e.target.value)}
              className="h-8 rounded-lg border border-input bg-transparent px-2 text-sm text-foreground"
            >
              {PRESENTATIONS.map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>
      {mode === "Table" ? (
        <DataTable rows={data} columns={columns} initialSort={{ key: "USAGE_DATE", desc: false }} />
      ) : (
        <Chart spec={spec} data={data} />
      )}
    </div>
  );
}
