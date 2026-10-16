// Row-level helpers shared by the aggregation modules. Rows are plain objects
// keyed by upper-case column name, as the server data layer returns them.
// Missing values are null or undefined; pandas NaN semantics map onto that.
import type { IsoDate } from "@/lib/view-state";

export type Row = Record<string, unknown>;

export const isMissing = (value: unknown): boolean =>
  value === null || value === undefined || (typeof value === "number" && Number.isNaN(value));

// Numeric view of a cell; missing or non-numeric text is null.
export function num(value: unknown): number | null {
  if (isMissing(value)) return null;
  if (typeof value === "number") return value;
  if (typeof value === "boolean") return value ? 1 : 0;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

// pandas-style sum: skips missing values; null only when `minCount` is not met.
export function sumValues(values: readonly unknown[], minCount = 0): number | null {
  let total = 0;
  let count = 0;
  for (const value of values) {
    const n = num(value);
    if (n === null) continue;
    total += n;
    count += 1;
  }
  return count < minCount ? null : total;
}

// Sums one column over rows, tolerating a missing column or empty input.
export function sumColumn(rows: readonly Row[] | null | undefined, column: string): number {
  if (!rows || rows.length === 0 || !(column in (rows[0] ?? {}))) return 0;
  return sumValues(rows.map((row) => row[column])) ?? 0;
}

export function hasColumn(rows: readonly Row[] | null | undefined, column: string): boolean {
  return !!rows && rows.length > 0 && column in rows[0];
}

// The UTC calendar day of a USAGE_DATE cell (ISO date or timestamp text).
export function usageDate(value: unknown): IsoDate {
  return String(value).slice(0, 10);
}

// Distinct non-missing values as sorted strings, for a filter control.
export function options(rows: readonly Row[] | null | undefined, column: string): string[] {
  if (!rows || rows.length === 0 || !hasColumn(rows, column)) return [];
  const values = new Set<string>();
  for (const row of rows) {
    if (!isMissing(row[column])) values.add(String(row[column]));
  }
  return [...values].sort();
}

// Distinct non-missing values in first-seen order (pandas unique()).
export function uniqueInOrder(rows: readonly Row[], column: string): unknown[] {
  const seen = new Set<unknown>();
  const out: unknown[] = [];
  for (const row of rows) {
    const value = row[column];
    if (isMissing(value) || seen.has(value)) continue;
    seen.add(value);
    out.push(value);
  }
  return out;
}
