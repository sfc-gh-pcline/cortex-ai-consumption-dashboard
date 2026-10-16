import { describe, expect, it } from "vitest";

import {
  SERIES_NAMES,
  breakdownBarSpec,
  hourlyTrendSpec,
  relativeTrendSpec,
  trendSpec,
} from "@/lib/chart-spec";

describe("trendSpec", () => {
  it("binds colors and dashes to stable service names", () => {
    const spec = trendSpec("CREDITS", "SURFACE", "Credits", { dark: true });
    const encoding = spec.encoding as Record<string, Record<string, unknown>>;
    expect(SERIES_NAMES).toHaveLength(11);
    expect((encoding.color.scale as { domain: string[] }).domain).toEqual(SERIES_NAMES);
    expect((encoding.color.scale as { range: string[] }).range).toHaveLength(11);
    expect((encoding.color.scale as { range: string[] }).range[0]).toBe("#29B5E8");
    expect(encoding.strokeDash).toBeDefined();
    expect((spec.mark as { type: string }).type).toBe("line");
    expect(encoding.y.stack).toBeNull();
  });

  it("stacks areas for composition and drops dashes", () => {
    const spec = trendSpec("CREDITS", "SURFACE", "Credits", { composition: true });
    const encoding = spec.encoding as Record<string, Record<string, unknown>>;
    expect((spec.mark as { type: string }).type).toBe("area");
    expect(encoding.y.stack).toBe("zero");
    expect(encoding.strokeDash).toBeUndefined();
    expect((encoding.color.scale as { range: string[] }).range[0]).toBe("#0369a1");
  });

  it("omits color when there is no series", () => {
    const spec = trendSpec("CREDITS", null, "Credits");
    expect((spec.encoding as Record<string, unknown>).color).toBeUndefined();
  });

  it("swaps the x axis for relative and hourly variants", () => {
    const relative = relativeTrendSpec("CREDITS", "Credits", false);
    expect((relative.encoding as Record<string, Record<string, unknown>>).x.field).toBe("Day");
    const hourly = hourlyTrendSpec("CREDITS", "Credits", false);
    expect((hourly.encoding as Record<string, Record<string, unknown>>).x.field).toBe("USAGE_HOUR");
  });

  it("sizes breakdown bars by row count within bounds", () => {
    expect(breakdownBarSpec("NAME", "CREDITS", "Name", "credits", 3).height).toBe(160);
    expect(breakdownBarSpec("NAME", "CREDITS", "Name", "credits", 10).height).toBe(280);
    expect(breakdownBarSpec("NAME", "CREDITS", "Name", "credits", 100).height).toBe(700);
  });
});
