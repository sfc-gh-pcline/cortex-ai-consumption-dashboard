// Vega-Lite specs shared by every page. Port of trend_spec and the breakdown
// bar in utils/ui.py: colors and dash patterns bind to stable service names so
// the same surface always draws the same way, in both themes.
import { COCO_SUB_SURFACES, SURFACES } from "@/lib/plans";

export const DARK_SERIES_COLORS = [
  "#29B5E8",
  "#a78bfa",
  "#4ade80",
  "#fbbf24",
  "#f472b6",
  "#60a5fa",
  "#2dd4bf",
  "#fb923c",
  "#c4b5fd",
];

export const LIGHT_SERIES_COLORS = [
  "#0369a1",
  "#6d28d9",
  "#166534",
  "#92400e",
  "#be185d",
  "#1d4ed8",
  "#0f766e",
  "#9a3412",
  "#7e22ce",
];

const DASHES = [
  [1, 0],
  [6, 3],
  [2, 2],
  [8, 2, 2, 2],
  [10, 4],
  [4, 2],
  [1, 3],
  [8, 4, 2, 4],
  [12, 2],
  [1, 0],
  [6, 3],
];

// Series names in palette order: six surfaces, three CoCo sub-surfaces, then the
// two comparison periods, which reuse the first two colors.
export const SERIES_NAMES = [...SURFACES, ...COCO_SUB_SURFACES, "Current", "Previous"];

export function seriesColors(dark: boolean): string[] {
  const colors = dark ? [...DARK_SERIES_COLORS] : [...LIGHT_SERIES_COLORS];
  return [...colors, colors[0], colors[1]];
}

export type VegaSpec = Record<string, unknown>;

export interface TrendSpecOptions {
  readonly composition?: boolean;
  readonly dark?: boolean;
}

// Null-breaking daily trend with stable service colors and dash encodings.
export function trendSpec(
  metric: string,
  series: string | null,
  label: string,
  options: TrendSpecOptions = {},
): VegaSpec {
  const composition = options.composition ?? false;
  const encoding: Record<string, unknown> = {
    x: {
      field: "USAGE_DATE",
      type: "temporal",
      title: null,
      axis: { format: "%b %d", tickMinStep: 86_400_000 },
    },
    y: {
      field: metric,
      type: "quantitative",
      title: label,
      stack: composition ? "zero" : null,
    },
    tooltip: [
      { field: "USAGE_DATE", type: "temporal", format: "%Y-%m-%d" },
      { field: metric, type: "quantitative", format: ".2f" },
    ],
  };
  if (series) {
    encoding.color = {
      field: series,
      type: "nominal",
      scale: { domain: SERIES_NAMES, range: seriesColors(options.dark ?? false) },
      legend: { title: null },
    };
    (encoding.tooltip as unknown[]).push({ field: series, type: "nominal" });
    if (!composition) {
      encoding.strokeDash = {
        field: series,
        type: "nominal",
        legend: null,
        scale: { domain: SERIES_NAMES, range: DASHES },
      };
    }
  }
  return {
    mark: {
      type: composition ? "area" : "line",
      invalid: "break-paths-show-domains",
      point: !composition,
    },
    encoding,
    height: 280,
  };
}

// Relative-period variant: x is the day number within each period.
export function relativeTrendSpec(metric: string, label: string, dark: boolean): VegaSpec {
  const spec = trendSpec(metric, "Period", label, { dark });
  (spec.encoding as Record<string, unknown>).x = {
    field: "Day",
    type: "quantitative",
    axis: { tickMinStep: 1 },
  };
  return spec;
}

// Hourly variant used by the Cortex Search serving trend.
export function hourlyTrendSpec(metric: string, label: string, dark: boolean): VegaSpec {
  const spec = trendSpec(metric, null, label, { dark });
  const encoding = spec.encoding as Record<string, unknown>;
  encoding.x = {
    field: "USAGE_HOUR",
    type: "temporal",
    axis: { format: "%b %d %H:%M", tickCount: 8 },
  };
  (encoding.tooltip as unknown[])[0] = {
    field: "USAGE_HOUR",
    type: "temporal",
    format: "%Y-%m-%d %H:%M UTC",
  };
  return spec;
}

// Ranked horizontal bars for a breakdown, labelled with the metric value.
export function breakdownBarSpec(
  labelCol: string,
  shareOf: string,
  label: string,
  axisTitle: string,
  rowCount: number,
): VegaSpec {
  return {
    layer: [
      { mark: "bar" },
      {
        mark: { type: "text", align: "left", dx: 4 },
        encoding: { text: { field: shareOf, type: "quantitative", format: ".2f" } },
      },
    ],
    height: Math.min(700, Math.max(160, rowCount * 28)),
    encoding: {
      y: {
        field: labelCol,
        type: "nominal",
        sort: "-x",
        title: label,
        axis: { labelLimit: 240 },
      },
      x: { field: shareOf, type: "quantitative", title: axisTitle },
      tooltip: [
        { field: labelCol },
        { field: shareOf, format: ".2f" },
        { field: "SHARE", format: ".2%" },
      ],
    },
  };
}
