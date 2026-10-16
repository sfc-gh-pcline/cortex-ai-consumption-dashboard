// Port of tests/test_preflight.py check evaluation.
import { describe, expect, it } from "vitest";

import { checkAccountUsage, checkWarehouse, preflightErrors, preflightOk } from "@/lib/preflight";

describe("preflight checks", () => {
  it("warns on a missing warehouse", () => {
    expect(checkWarehouse(null).level).toBe("warning");
    expect(checkWarehouse("").level).toBe("warning");
    expect(checkWarehouse("COMPUTE_WH")).toMatchObject({
      level: "ok",
      detail: "Warehouse: COMPUTE_WH.",
    });
  });

  it("separates a warehouse-less session from a privilege gap", () => {
    expect(checkAccountUsage(true).level).toBe("ok");
    expect(
      checkAccountUsage(false, "No active warehouse selected in the current session").level,
    ).toBe("warning");
    const denied = checkAccountUsage(false, "SQL compilation error: Object does not exist");
    expect(denied.level).toBe("error");
    expect(denied.detail).toContain("Object does not exist");
    expect(denied.remediation).toContain("GRANT IMPORTED PRIVILEGES");
    expect(checkAccountUsage(false).detail).toBe("Cannot read SNOWFLAKE.ACCOUNT_USAGE.");
  });

  it("blocks only on errors", () => {
    const result = {
      account: "A",
      userName: "U",
      role: "R",
      warehouse: "",
      checks: [checkWarehouse(""), checkAccountUsage(true)],
    };
    expect(preflightOk(result)).toBe(true);
    expect(
      preflightErrors({ ...result, checks: [checkAccountUsage(false, "denied")] }),
    ).toHaveLength(1);
  });
});
