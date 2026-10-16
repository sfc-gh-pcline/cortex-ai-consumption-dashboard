// Rate coverage for estimated Cortex Inference API credits. Pure port of
// _with_rate_coverage in utils/queries.py: counts tokens whose model or token
// type had no observed rate in the exact snapshot the query was priced with.
import type { ModelRate } from "@/lib/sql/facts";
import { RATE_COLUMNS, TOKEN_TYPES, TOKEN_TYPE_COLUMNS } from "@/lib/sql/core";
import { fmtInt } from "@/lib/format";

export type Row = Record<string, unknown>;

// Keep this copy aligned with the Streamlit ui.inference_pricing_* helpers.
export function inferencePricingWarning(unrated: number | null, overview = false): string {
  const unknown = unrated === null || Number.isNaN(unrated);
  const noun = unrated === 1 ? "token" : "tokens";
  const summary = unknown
    ? "Cortex Inference API pricing coverage is unknown: the unpriced token count is unavailable because pricing details are missing. "
    : `${fmtInt(unrated)} Cortex Inference API ${noun} could not be priced in this view because a model/token-type rate or token-type breakdown is missing. `;
  const totals = overview ? "Estimated credits and AI usage credits" : "Credits (partial estimate)";
  const impact = unknown
    ? `${totals} may exclude unpriced usage. `
    : `Their credits are excluded from ${totals}. `;
  return (
    summary +
    impact +
    (overview ? "Reported credits are unaffected. " : "") +
    "Tokens and requests are still counted; unpriced does not mean free."
  );
}

export function inferencePricingLabel(unrated: number | null): string {
  if (unrated === null || Number.isNaN(unrated)) return "Token pricing coverage unknown";
  if (unrated > 0) return `${fmtInt(unrated)} ${unrated === 1 ? "token" : "tokens"} not priced`;
  return "All reported tokens have observed rates";
}

const num = (value: unknown): number => {
  const n = typeof value === "number" ? value : Number(value ?? 0);
  return Number.isFinite(n) ? n : 0;
};

// Adds UNRATED_TOKENS and RATE_INCOMPLETE to every row.
export function withRateCoverage(rows: readonly Row[], rates: readonly ModelRate[]): Row[] {
  const byModel = new Map(rates.map((r) => [r.MODEL_NAME, r]));
  return rows.map((row) => {
    const rate = byModel.get(String(row.MODEL_NAME));
    let unrated = 0;
    let accounted = 0;
    TOKEN_TYPES.forEach((tokenType, index) => {
      const column = TOKEN_TYPE_COLUMNS[tokenType];
      if (!(column in row)) return;
      const tokens = num(row[column]);
      const known = rate !== undefined && rate[RATE_COLUMNS[index]] !== null;
      if (!known) unrated += tokens;
      accounted += tokens;
    });
    unrated += Math.max(num(row.TOKENS) - accounted, 0);
    return { ...row, UNRATED_TOKENS: unrated, RATE_INCOMPLETE: unrated > 0 };
  });
}

// Converts model-rate result rows into the typed snapshot the REST builder binds.
// Lives here (not in the server loaders) so the SQL reference pages can use it client-side.
export function rowsAsRates(rows: readonly Row[]): ModelRate[] {
  const num = (v: unknown) => (v === null || v === undefined || v === "" ? null : Number(v));
  return rows.map((row) => ({
    MODEL_NAME: String(row.MODEL_NAME),
    R_INPUT: num(row.R_INPUT),
    R_OUTPUT: num(row.R_OUTPUT),
    R_CACHE_READ: num(row.R_CACHE_READ),
    R_CACHE_WRITE: num(row.R_CACHE_WRITE),
  }));
}
