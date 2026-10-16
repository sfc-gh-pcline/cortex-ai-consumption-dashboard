// Port of tests/test_date_ranges.py plus the settled-day rule.
import { describe, expect, it } from "vitest";

import {
  CUSTOM,
  DATE_PRESETS,
  PRESET_KEYS,
  addDays,
  dateWindow,
  effectiveServedWindow,
  parseLegacyDates,
  presetWindow,
  resolveCustomInput,
  resolvePeriods,
  settledUtcEndDate,
  validateRequestWindow,
  windowDays,
} from "@/lib/dates";

describe("presets", () => {
  it("labels match the sidebar contract", () => {
    expect(PRESET_KEYS).toEqual(["7d", "30d", "60d", "90d"]);
    expect(CUSTOM).toBe("Custom");
  });

  it.each([
    ["7d", "2026-08-12"],
    ["30d", "2026-07-20"],
    ["60d", "2026-06-20"],
    ["90d", "2026-05-21"],
  ])("%s excludes today and keeps the requested day count", (preset, expectedStart) => {
    const today = "2026-08-19";
    const window = presetWindow(today, preset);
    expect(window).toEqual({ start: expectedStart, end: "2026-08-18" });
    expect(windowDays(window)).toBe(DATE_PRESETS[preset]);
  });

  it("rejects an unsupported preset", () => {
    expect(() => presetWindow("2026-08-19", "12m")).toThrow(/Unsupported date preset/);
  });
});

describe("resolvePeriods", () => {
  it("returns seven completed days and the previous period", () => {
    const periods = resolvePeriods("2026-09-17", "7d");
    expect(periods.current).toEqual({ start: "2026-09-10", end: "2026-09-16" });
    expect(periods.previous).toEqual({ start: "2026-09-03", end: "2026-09-09" });
    expect(periods.partialToday).toBeNull();
  });

  it.each(["2024-03-01", "2026-01-01", "2026-05-01"])(
    "calendar boundaries keep adjacent equal periods (today %s)",
    (today) => {
      const periods = resolvePeriods(today, "30d");
      expect(periods.current).not.toBeNull();
      expect(periods.previous).not.toBeNull();
      expect(windowDays(periods.current!)).toBe(30);
      expect(windowDays(periods.previous!)).toBe(30);
      expect(addDays(periods.previous!.end, 1)).toBe(periods.current!.start);
      expect(addDays(periods.current!.end, 1)).toBe(today);
    },
  );

  it("keeps a custom today separate as a partial day", () => {
    const today = "2026-09-20";
    const periods = resolvePeriods(today, CUSTOM, dateWindow("2026-09-17", today));
    expect(windowDays(periods.current!)).toBe(3);
    expect(windowDays(periods.previous!)).toBe(3);
    expect(periods.partialToday).toEqual({ start: today, end: today });
    const todayOnly = resolvePeriods(today, CUSTOM, dateWindow(today, today));
    expect(todayOnly.current).toBeNull();
    expect(todayOnly.previous).toBeNull();
    expect(todayOnly.partialToday).toEqual({ start: today, end: today });
  });

  it("requires custom endpoints and rejects the future", () => {
    const today = "2026-09-20";
    expect(() => resolvePeriods(today, CUSTOM)).toThrow(/both custom endpoints/);
    expect(() => resolvePeriods(today, CUSTOM, dateWindow(today, "2026-09-21"))).toThrow(
      /beyond today/,
    );
  });
});

describe("resolveCustomInput", () => {
  it.each([
    [null],
    [[]],
    [["2026-09-18"]],
    [["2026-09-18", 20260919]],
    [["2026-09-19", "2026-09-18"]],
    [["2026-09-20", "2026-09-21"]],
    [["2020-01-01", "2026-09-19"]],
    [["2026-02-30", "2026-09-19"]],
  ])("validates widget input before application: %j", (chosen) => {
    expect(() => resolveCustomInput("2026-09-20", chosen)).toThrow();
  });

  it("keeps the requested days for complete input", () => {
    const result = resolveCustomInput("2026-09-20", ["2026-08-04", "2026-08-10"]);
    expect(result.requested).toEqual({ start: "2026-08-04", end: "2026-08-10" });
    expect(result.previous).toEqual({ start: "2026-07-28", end: "2026-08-03" });
  });

  it("imports a complete legacy dates link", () => {
    expect(parseLegacyDates("2026-09-20", ["2026-08-04", "2026-08-10"])).toEqual({
      start: "2026-08-04",
      end: "2026-08-10",
    });
    expect(() => parseLegacyDates("2026-09-20", ["2026-08-04"])).toThrow(/two endpoints/);
    expect(() => parseLegacyDates("2026-09-20", ["nope", "2026-08-10"])).toThrow(/invalid date/);
  });
});

describe("windows", () => {
  it("rejects reversed dates and counts inclusive days", () => {
    expect(() => dateWindow("2026-09-20", "2026-09-19")).toThrow(/Window end/);
    expect(windowDays(dateWindow("2026-09-01", "2026-09-07"))).toBe(7);
  });

  it("settled-day rule caps aggregate reads at yesterday", () => {
    const today = "2026-09-23";
    expect(settledUtcEndDate(today)).toBe("2026-09-22");
    const window = dateWindow("2026-09-01", today);
    expect(effectiveServedWindow(window, { accelerated: false, today })).toEqual(window);
    expect(effectiveServedWindow(window, { accelerated: true, today })).toEqual({
      start: "2026-09-01",
      end: "2026-09-22",
    });
    expect(effectiveServedWindow(dateWindow(today, today), { accelerated: true, today })).toEqual({
      start: "2026-09-22",
      end: "2026-09-22",
    });
  });

  it("enforces the UI date boundary at the server boundary", () => {
    expect(validateRequestWindow("2026-09-20", dateWindow("2026-09-13", "2026-09-19"))).toEqual({
      start: "2026-09-13",
      end: "2026-09-19",
    });
    expect(() =>
      validateRequestWindow("2026-09-20", dateWindow("2024-09-19", "2026-09-19")),
    ).toThrow(/supported date control boundary/);
    expect(() =>
      validateRequestWindow("2026-09-20", dateWindow("2026-09-19", "2026-09-21")),
    ).toThrow(/beyond today/);
  });
});
