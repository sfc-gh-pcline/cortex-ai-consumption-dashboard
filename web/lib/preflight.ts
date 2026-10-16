// First-run preflight checks. Pure port of utils/preflight.py; the two probe
// queries run in lib/server/preflight.ts and feed their outcomes here.

export type CheckLevel = "ok" | "warning" | "error";

export interface CheckResult {
  readonly name: string;
  readonly level: CheckLevel;
  readonly detail: string;
  readonly remediation: string;
}

export interface PreflightResult {
  readonly account: string;
  readonly userName: string;
  readonly role: string;
  readonly warehouse: string;
  readonly checks: readonly CheckResult[];
}

export const preflightOk = (result: PreflightResult) =>
  !result.checks.some((c) => c.level === "error");
export const preflightErrors = (result: PreflightResult) =>
  result.checks.filter((c) => c.level === "error");
export const preflightWarnings = (result: PreflightResult) =>
  result.checks.filter((c) => c.level === "warning");

// Whether the session has a current warehouse.
export function checkWarehouse(warehouse: string | null | undefined): CheckResult {
  if (!warehouse) {
    return {
      name: "Query warehouse",
      level: "warning",
      detail: "No warehouse is active for this session.",
      remediation:
        "Set a default warehouse on your role or user: " +
        "ALTER USER <name> SET DEFAULT_WAREHOUSE = <warehouse>; " +
        "or add `warehouse` to your connections.toml.",
    };
  }
  return {
    name: "Query warehouse",
    level: "ok",
    detail: `Warehouse: ${warehouse}.`,
    remediation: "",
  };
}

// Read access to SNOWFLAKE.ACCOUNT_USAGE, distinguishing a missing warehouse from a privilege gap.
export function checkAccountUsage(accessible: boolean, errorText = ""): CheckResult {
  if (!accessible) {
    const lower = errorText.toLowerCase();
    if (
      lower.includes("no active warehouse") ||
      (lower.includes("warehouse") && lower.includes("session"))
    ) {
      return {
        name: "ACCOUNT_USAGE access",
        level: "warning",
        detail: "Cannot verify ACCOUNT_USAGE access — no active warehouse in this session.",
        remediation: "Set a default warehouse, then reload to re-run the preflight.",
      };
    }
    return {
      name: "ACCOUNT_USAGE access",
      level: "error",
      detail: errorText
        ? `Cannot read SNOWFLAKE.ACCOUNT_USAGE: ${errorText}`
        : "Cannot read SNOWFLAKE.ACCOUNT_USAGE.",
      remediation:
        "Grant imported privileges on the shared SNOWFLAKE database to your role: " +
        "GRANT IMPORTED PRIVILEGES ON DATABASE SNOWFLAKE TO ROLE <your_role>;",
    };
  }
  return {
    name: "ACCOUNT_USAGE access",
    level: "ok",
    detail: "Can read SNOWFLAKE.ACCOUNT_USAGE views.",
    remediation: "",
  };
}

// A broken connection blocks everything; the ACCOUNT_USAGE probe is skipped.
export function connectionFailure(error: string): CheckResult {
  return {
    name: "Connection",
    level: "error",
    detail: `Cannot connect to Snowflake: ${error}`,
    remediation:
      "Verify the Snowflake connection credentials in connections.toml " +
      "or the Application Service's query warehouse and role.",
  };
}
