// Port of the pure cases in tests/test_source_coverage.py.
import { describe, expect, it } from "vitest";

import { unavailableCoverage, withCoverage } from "@/lib/coverage";
import { CoverageState } from "@/lib/view-state";

const WINDOW = { start: "2026-09-13", end: "2026-09-19" };

function acquired(hour = 8) {
  const hh = String(hour).padStart(2, "0");
  return {
    readStartedAt: `2026-09-20T${hh}:00:00.000Z`,
    fetchedAt: `2026-09-20T${hh}:05:00.000Z`,
  };
}

describe("withCoverage", () => {
  it("uses the read start, not query completion, for direct completeness", () => {
    const evidence = withCoverage(
      { readStartedAt: "2026-09-20T00:00:00.000Z", fetchedAt: "2026-09-20T04:00:00.000Z" },
      "CoCo",
      WINDOW,
      "direct",
    );
    expect(evidence.state).toBe(CoverageState.PARTIAL);
    expect(evidence.completeThrough).toBe("2026-09-18");
    expect(evidence.served).toEqual(WINDOW);
  });

  it("records the newest usage day from the acquired rows", () => {
    const evidence = withCoverage(acquired(), "CoCo", WINDOW, "direct", {
      rows: [{ USAGE_DATE: "2026-09-17" }, { USAGE_DATE: "2026-09-18" }],
    });
    expect(evidence.latestUsage).toBe("2026-09-18");
  });

  it("treats a fresh direct read as available even when empty", () => {
    const evidence = withCoverage(acquired(), "CoCo", WINDOW, "direct");
    expect(evidence.state).toBe(CoverageState.AVAILABLE);
    expect(evidence.reason).toBeNull();
  });

  it.each(["Cortex AI Functions", "Cortex Inference API", "Warehouse Attribution"])(
    "never invents completeness for %s",
    (dependency) => {
      const evidence = withCoverage(acquired(), dependency, WINDOW, "direct");
      expect(evidence.state).toBe(CoverageState.UNKNOWN);
      expect(evidence.completeThrough).toBeNull();
    },
  );

  it("does not replace a missing read timestamp with now", () => {
    const evidence = withCoverage(
      { readStartedAt: null, fetchedAt: null },
      "CoCo",
      WINDOW,
      "direct",
    );
    expect(evidence.fetchedAt).toBeNull();
    expect(evidence.state).toBe(CoverageState.UNKNOWN);
  });

  it("does not let an aggregate snapshot imply complete ingestion", () => {
    const evidence = withCoverage(
      { ...acquired(), snapshotTimestamp: "2026-09-20T01:03:00+00:00" },
      "Cortex Search — Daily",
      WINDOW,
      "aggregate",
    );
    expect(evidence.snapshotAt).toBe("2026-09-20T01:03:00.000Z");
    expect(evidence.state).toBe(CoverageState.UNKNOWN);
    expect(evidence.completeThrough).toBeNull();
    expect(evidence.retentionStart).toBeNull();
  });

  it("marks retention and source introduction as partial, not zero", () => {
    const old = withCoverage(
      acquired(),
      "CoCo",
      { start: "2025-01-01", end: WINDOW.end },
      "direct",
    );
    expect(old.state).toBe(CoverageState.PARTIAL);
    const functions = withCoverage(acquired(), "Cortex AI Functions", WINDOW, "direct");
    expect(functions.retentionStart).toBe("2026-01-05");
  });

  it("keeps an invalid snapshot unknown", () => {
    const evidence = withCoverage(
      { ...acquired(), snapshotTimestamp: "invalid timestamp" },
      "CoCo",
      WINDOW,
      "aggregate",
    );
    expect(evidence.snapshotAt).toBeNull();
    expect(evidence.state).toBe(CoverageState.UNKNOWN);
  });

  it("caps the served window at the aggregate boundary and honors partial reasons", () => {
    const capped = withCoverage(acquired(), "CoCo", WINDOW, "aggregate", {
      servedEnd: "2026-09-18",
    });
    expect(capped.served).toEqual({ start: "2026-09-13", end: "2026-09-18" });
    const degraded = withCoverage(acquired(), "Warehouse Attribution", WINDOW, "direct", {
      partialReason: "Adaptive warehouse metering is unavailable.",
    });
    expect(degraded.state).toBe(CoverageState.PARTIAL);
    expect(degraded.reason).toBe("Adaptive warehouse metering is unavailable.");
  });
});

describe("unavailableCoverage", () => {
  it("preserves the error without fabricating a fetch", () => {
    const evidence = unavailableCoverage("CoCo", WINDOW, "synthetic denied");
    expect(evidence.state).toBe(CoverageState.UNAVAILABLE);
    expect(evidence.fetchedAt).toBeNull();
    expect(evidence.served).toBeNull();
    expect(evidence.reason).toBe("synthetic denied");
    expect(unavailableCoverage("CoCo", WINDOW, null).reason).toBe("Source returned no result");
  });
});
