// Date-range presets and inclusive UTC calendar-window helpers.
// Port of utils/date_ranges.py plus the settled-day rule from utils/config.py.
// All dates are ISO calendar strings; arithmetic goes through Date.UTC so the
// container's timezone can never shift a day.
import type { ComparisonPeriods, DateWindow, IsoDate } from "@/lib/view-state";

const ISO_DATE = /^(\d{4})-(\d{2})-(\d{2})$/;

export function isIsoDate(value: unknown): value is IsoDate {
  if (typeof value !== "string") return false;
  const match = ISO_DATE.exec(value);
  if (!match) return false;
  const [, y, m, d] = match.map(Number);
  const probe = new Date(Date.UTC(y, m - 1, d));
  return probe.getUTCFullYear() === y && probe.getUTCMonth() === m - 1 && probe.getUTCDate() === d;
}

function toEpochDays(date: IsoDate): number {
  const [y, m, d] = date.split("-").map(Number);
  return Math.round(Date.UTC(y, m - 1, d) / 86_400_000);
}

function fromEpochDays(days: number): IsoDate {
  return new Date(days * 86_400_000).toISOString().slice(0, 10);
}

export function addDays(date: IsoDate, days: number): IsoDate {
  return fromEpochDays(toEpochDays(date) + days);
}

// Signed calendar-day difference, later minus earlier.
export function daysBetween(earlier: IsoDate, later: IsoDate): number {
  return toEpochDays(later) - toEpochDays(earlier);
}

// Inclusive UTC calendar interval; rejects reversed windows before any read.
export function dateWindow(start: IsoDate, end: IsoDate): DateWindow {
  if (end < start) throw new RangeError("Window end must be on or after its start.");
  return { start, end };
}

// Inclusive calendar length.
export function windowDays(window: DateWindow): number {
  return daysBetween(window.start, window.end) + 1;
}

export function sameWindow(a: DateWindow | null, b: DateWindow | null): boolean {
  return a?.start === b?.start && a?.end === b?.end;
}

export const DATE_PRESETS: Readonly<Record<string, number>> = {
  "7d": 7,
  "30d": 30,
  "60d": 60,
  "90d": 90,
};
export const PRESET_KEYS = Object.keys(DATE_PRESETS);
export const CUSTOM = "Custom";
export const DEFAULT_PRESET = "7d";

// Inclusive completed window for a preset: the last N days before today.
export function presetWindow(today: IsoDate, preset: string): DateWindow {
  const days = DATE_PRESETS[preset];
  if (days === undefined) throw new RangeError(`Unsupported date preset: ${preset}`);
  return { start: addDays(today, -days), end: addDays(today, -1) };
}

// Adjacent equal completed periods without mixing in today.
export function resolvePeriods(
  today: IsoDate,
  preset: string,
  custom: DateWindow | null = null,
): ComparisonPeriods {
  let requested: DateWindow;
  if (preset === CUSTOM) {
    if (custom === null) throw new RangeError("Select both custom endpoints before applying.");
    requested = custom;
  } else {
    requested = presetWindow(today, preset);
  }
  if (requested.end > today) {
    throw new RangeError("The selected range cannot extend beyond today UTC.");
  }
  const partialToday = requested.end === today ? { start: today, end: today } : null;
  if (requested.start === today) {
    return { requested, current: null, previous: null, partialToday };
  }
  const yesterday = addDays(today, -1);
  const current = dateWindow(
    requested.start,
    requested.end < yesterday ? requested.end : yesterday,
  );
  const length = windowDays(current);
  const previous = dateWindow(addDays(current.start, -length), addDays(current.start, -1));
  return { requested, current, previous, partialToday };
}

export const CONTROL_BOUNDARY_DAYS = 730;

// Applies the date-control contract to API requests as well as browser input.
export function validateRequestWindow(today: IsoDate, window: DateWindow): DateWindow {
  if (window.start < addDays(today, -CONTROL_BOUNDARY_DAYS)) {
    throw new RangeError("The selected range starts before the supported date control boundary.");
  }
  if (window.end > today) {
    throw new RangeError("The selected range cannot extend beyond today UTC.");
  }
  return window;
}

// Validates both custom endpoints before committing a custom selection.
// Throws TypeError for non-date endpoints and RangeError for incomplete,
// reversed, future, or out-of-bounds ranges, mirroring the Python contract.
export function resolveCustomInput(today: IsoDate, chosen: unknown): ComparisonPeriods {
  if (
    !Array.isArray(chosen) ||
    chosen.length !== 2 ||
    chosen.some((endpoint) => endpoint == null)
  ) {
    throw new RangeError("Select both dates before applying.");
  }
  const [start, end] = chosen as unknown[];
  if (!isIsoDate(start) || !isIsoDate(end)) {
    throw new TypeError("Both endpoints must be calendar dates.");
  }
  const requested = dateWindow(start, end);
  validateRequestWindow(today, requested);
  return resolvePeriods(today, CUSTOM, requested);
}

// Imports a complete legacy `dates` link without guessing a missing endpoint.
export function parseLegacyDates(today: IsoDate, values: readonly string[]): DateWindow {
  if (values.length !== 2) {
    throw new RangeError("Legacy dates link needs two endpoints. Select Start date and End date.");
  }
  if (!values.every(isIsoDate)) {
    throw new RangeError("Legacy dates link contains an invalid date.");
  }
  return resolveCustomInput(today, [...values]).requested;
}

// Latest UTC date eligible for aggregate serving: ACCOUNT_USAGE can backfill
// today's rows after the nightly task ran, so today is never served from a snapshot.
export function settledUtcEndDate(today: IsoDate): IsoDate {
  return addDays(today, -1);
}

// Direct reads keep the requested window; aggregate reads stop at the settled day
// and collapse a future-only range to that day.
export function effectiveServedWindow(
  window: DateWindow,
  options: { accelerated: boolean; today: IsoDate },
): DateWindow {
  if (!options.accelerated) return window;
  const settled = settledUtcEndDate(options.today);
  return {
    start: window.start < settled ? window.start : settled,
    end: window.end < settled ? window.end : settled,
  };
}
