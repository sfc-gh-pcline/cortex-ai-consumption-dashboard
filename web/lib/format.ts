// Number formatting shared by every page. Port of fmt_int, fmt_credits and
// fmt_tokens in utils/ui.py: missing values read "Unavailable", never zero.

const UNAVAILABLE = "Unavailable";

const missing = (value: unknown): boolean =>
  value === null || value === undefined || (typeof value === "number" && Number.isNaN(value));

// Thousands separators with a fixed number of decimals (en-US grouping).
function grouped(value: number, decimals: number): string {
  const fixed = Math.abs(value).toFixed(decimals);
  const [int, frac] = fixed.split(".");
  const withCommas = int.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  const sign = value < 0 ? "-" : "";
  return frac !== undefined ? `${sign}${withCommas}.${frac}` : `${sign}${withCommas}`;
}

// Count with thousands separators; truncated toward zero.
export function fmtInt(value: unknown): string {
  if (missing(value)) return UNAVAILABLE;
  return grouped(Math.trunc(Number(value)), 0);
}

// Credit amount to fixed decimals, with a below-precision indicator for tiny positives.
export function fmtCredits(value: unknown, decimals = 2): string {
  if (missing(value)) return UNAVAILABLE;
  const n = Number(value);
  const floor = 10 ** -decimals;
  if (n > 0 && n < floor) return `<${floor.toFixed(decimals)}`;
  return grouped(n, decimals);
}

// Compact token count: 1.2B / 340.5M / 12.3K, or the full count below 1,000.
export function fmtTokens(value: unknown): string {
  if (missing(value)) return UNAVAILABLE;
  const n = Number(value);
  for (const [threshold, suffix] of [
    [1e12, "T"],
    [1e9, "B"],
    [1e6, "M"],
    [1e3, "K"],
  ] as const) {
    if (Math.abs(n) >= threshold) return `${grouped(n / threshold, 1)}${suffix}`;
  }
  return grouped(Math.trunc(n), 0);
}
