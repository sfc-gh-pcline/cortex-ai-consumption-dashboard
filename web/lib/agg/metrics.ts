// KPI totals, dimensional breakdowns, token mix, tag groupings, and the
// warehouse tier split. Port of the corresponding functions in utils/agg.py.
import { TOKEN_TYPES, TOKEN_TYPE_COLUMNS, TOKEN_TYPE_LABELS, UNKNOWN } from "@/lib/sql/core";
import { hasColumn, isMissing, num, sumColumn, sumValues, type Row } from "@/lib/agg/rows";

export const METRICS = ["CREDITS", "TOKENS", "CALLS"] as const;
export const UNTAGGED = "(untagged)";

export interface Totals {
  CREDITS: number;
  TOKENS: number;
  CALLS: number;
  USERS: number;
  ACTIVE_DAYS: number;
}

// Headline KPIs; all zero for an empty input so callers index unconditionally.
export function totals(rows: readonly Row[] | null | undefined): Totals {
  if (!rows || rows.length === 0)
    return { CREDITS: 0, TOKENS: 0, CALLS: 0, USERS: 0, ACTIVE_DAYS: 0 };
  const users = new Set<string>();
  const days = new Set<string>();
  for (const row of rows) {
    if (hasColumn(rows, "USER_NAME")) {
      const user = row.USER_NAME;
      if (!isMissing(user) && user !== UNKNOWN && user !== "") users.add(String(user));
    }
    if (!isMissing(row.USAGE_DATE)) days.add(String(row.USAGE_DATE));
  }
  return {
    CREDITS: sumColumn(rows, "CREDITS"),
    TOKENS: Math.trunc(sumColumn(rows, "TOKENS")),
    CALLS: Math.trunc(sumColumn(rows, "CALLS")),
    USERS: users.size,
    ACTIVE_DAYS: days.size,
  };
}

// Inclusive day count, floored at one so per-day rates never divide by zero.
export function windowDaysFloored(days: number): number {
  return Math.max(days, 1);
}

export interface Breakdown {
  readonly columns: string[];
  readonly rows: Row[];
}

// One row per dimension value with whichever metrics the input has, plus a
// distinct USERS count when USER_NAME exists, sorted by the first metric present.
export function byDimension(
  rows: readonly Row[] | null | undefined,
  column: string,
  labelCol = "DIMENSION",
  metrics: readonly string[] | null = null,
): Breakdown {
  const requested = metrics ? [...metrics] : [...METRICS];
  const hasUsers = hasColumn(rows, "USER_NAME");
  const present = rows && rows.length > 0 ? requested.filter((m) => hasColumn(rows, m)) : [];
  if (!rows || rows.length === 0 || !hasColumn(rows, column) || present.length === 0) {
    return { columns: [labelCol, ...requested, ...(hasUsers ? ["USERS"] : [])], rows: [] };
  }
  const groups = new Map<string, { label: string; rows: Row[] }>();
  for (const row of rows) {
    const raw = row[column];
    const key = isMissing(raw) ? "\u0000null" : String(raw);
    const group = groups.get(key) ?? { label: isMissing(raw) ? UNKNOWN : String(raw), rows: [] };
    group.rows.push(row);
    groups.set(key, group);
  }
  const out: Row[] = [];
  for (const group of groups.values()) {
    const record: Row = { [labelCol]: group.label };
    for (const metric of present) record[metric] = sumValues(group.rows.map((r) => r[metric])) ?? 0;
    if (hasUsers) {
      record.USERS = new Set(
        group.rows.filter((r) => !isMissing(r.USER_NAME)).map((r) => String(r.USER_NAME)),
      ).size;
    }
    out.push(record);
  }
  const first = present[0];
  out.sort((a, b) => (num(b[first]) ?? 0) - (num(a[first]) ?? 0));
  return { columns: [labelCol, ...present, ...(hasUsers ? ["USERS"] : [])], rows: out };
}

// Top row of a breakdown and its share of `total` (0 when total is 0).
export function leader(
  breakdown: Breakdown | null | undefined,
  total: number,
  valueCol = "CREDITS",
): { top: Row; share: number } | null {
  if (!breakdown || breakdown.rows.length === 0 || !breakdown.columns.includes(valueCol))
    return null;
  const top = breakdown.rows[0];
  return { top, share: total ? (num(top[valueCol]) ?? 0) / total : 0 };
}

// Per-token-type totals in long form, zero types dropped, sorted descending.
export function tokenMix(rows: readonly Row[] | null | undefined): Breakdown {
  const columns = ["DIMENSION", "TOKENS"];
  if (!rows || rows.length === 0) return { columns, rows: [] };
  const out: Row[] = [];
  for (const tokenType of TOKEN_TYPES) {
    const column = TOKEN_TYPE_COLUMNS[tokenType];
    if (!hasColumn(rows, column)) continue;
    out.push({ DIMENSION: TOKEN_TYPE_LABELS[tokenType], TOKENS: sumColumn(rows, column) });
  }
  if (out.length === 0) return { columns: [], rows: [] };
  return {
    columns,
    rows: out
      .filter((r) => (num(r.TOKENS) ?? 0) > 0)
      .sort((a, b) => (num(b.TOKENS) ?? 0) - (num(a.TOKENS) ?? 0)),
  };
}

// Credits and tokens by the tags on the spending user; untagged users grouped.
export function byUserTag(
  rows: readonly Row[] | null | undefined,
  tags: readonly Row[] | null | undefined,
): Breakdown {
  if (!rows || rows.length === 0) return { columns: ["TAG_LABEL", ...METRICS, "USERS"], rows: [] };
  const labelsByUser = new Map<string, string[]>();
  for (const tag of tags ?? []) {
    const user = String(tag.USER_NAME);
    const label = String(tag.TAG_LABEL);
    const list = labelsByUser.get(user) ?? [];
    if (!list.includes(label)) list.push(label);
    labelsByUser.set(user, list);
  }
  const merged: Row[] = [];
  for (const row of rows) {
    const labels = labelsByUser.get(String(row.USER_NAME)) ?? [];
    if (labels.length === 0) merged.push({ ...row, TAG_LABEL: UNTAGGED });
    else for (const label of labels) merged.push({ ...row, TAG_LABEL: label });
  }
  return byDimension(merged, "TAG_LABEL", "TAG_LABEL");
}

// Pipe-joined agent tag labels exploded to one row per tag.
export function byAgentTag(rows: readonly Row[] | null | undefined): Breakdown {
  if (!rows || rows.length === 0) return { columns: ["TAG_LABEL", ...METRICS, "USERS"], rows: [] };
  const exploded: Row[] = [];
  for (const row of rows) {
    const raw = isMissing(row.AGENT_TAG_LABELS) ? "" : String(row.AGENT_TAG_LABELS);
    const labels = raw === "" ? [UNTAGGED] : raw.split("|").map((l) => (l === "" ? UNTAGGED : l));
    for (const label of labels) exploded.push({ ...row, TAG_LABEL: label });
  }
  return byDimension(exploded, "TAG_LABEL", "TAG_LABEL");
}

export interface WarehouseSplit {
  PER_REQUEST: number;
  SURFACE_ONLY: number;
  TOTAL: number;
}

// One surface's warehouse credits split by attribution tier.
export function warehouseCreditsForSurface(
  rows: readonly Row[] | null | undefined,
  surface: string,
): WarehouseSplit {
  const subset = (rows ?? []).filter((r) => r.SURFACE === surface);
  if (subset.length === 0) return { PER_REQUEST: 0, SURFACE_ONLY: 0, TOTAL: 0 };
  const perRequest = sumColumn(
    subset.filter((r) => r.TIER === "per_request"),
    "WAREHOUSE_CREDITS",
  );
  const surfaceOnly = sumColumn(
    subset.filter((r) => r.TIER === "surface"),
    "WAREHOUSE_CREDITS",
  );
  return { PER_REQUEST: perRequest, SURFACE_ONLY: surfaceOnly, TOTAL: perRequest + surfaceOnly };
}
