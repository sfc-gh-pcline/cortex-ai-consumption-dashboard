// Transport contract: preserve SQL NULL before rows are reused as rate binds.
import type snowflake from "snowflake-sdk";
import { afterEach, describe, expect, it, vi } from "vitest";

const { createPool, execute } = vi.hoisted(() => ({
  createPool: vi.fn(),
  execute: vi.fn(),
}));

vi.mock("snowflake-sdk", () => ({ default: { createPool } }));

vi.mock("node:fs", () => ({
  default: {
    readFileSync: vi.fn(() => {
      throw new Error("No credential files in offline tests");
    }),
  },
}));

afterEach(() => {
  vi.unstubAllEnvs();
  vi.clearAllMocks();
  vi.resetModules();
});

async function transport() {
  vi.stubEnv("SNOWFLAKE_ACCOUNT", "offline");
  vi.stubEnv("SNOWFLAKE_USER", "offline");
  vi.stubEnv("SNOWFLAKE_PASSWORD", "not-a-credential");
  createPool.mockReturnValue({
    use: (callback: (connection: { execute: typeof execute }) => unknown) => callback({ execute }),
  });
  return import("@/lib/snowflake");
}

describe("Snowflake transport", () => {
  it("disables the SDK string-NULL default for every pooled connection", async () => {
    await transport();
    expect(createPool).toHaveBeenCalledWith(
      expect.objectContaining({ representNullAsStringNull: false }),
      expect.objectContaining({ min: 0, max: 4, testOnBorrow: true }),
    );
  });

  it("preserves null rates through result coercion and the next statement's binds", async () => {
    const { executeStatement } = await transport();
    const { rowsAsRates } = await import("@/lib/rates");
    const { ratesCte } = await import("@/lib/sql/facts");
    const columns = [
      { name: "MODEL_NAME", type: "text" },
      { name: "R_INPUT", type: "real" },
      { name: "R_OUTPUT", type: "real" },
      { name: "R_CACHE_READ", type: "real" },
      { name: "R_CACHE_WRITE", type: "real" },
    ];
    execute.mockImplementation((options: snowflake.StatementOption) => {
      const statement = {
        getColumns: () =>
          columns.map(({ name, type }) => ({
            getName: () => name,
            getType: () => type,
            getScale: () => 0,
          })),
      } as snowflake.RowStatement;
      options.complete?.(undefined, statement, [
        {
          MODEL_NAME: "partial",
          R_INPUT: "0.01",
          R_OUTPUT: null,
          R_CACHE_READ: null,
          R_CACHE_WRITE: null,
        },
      ]);
    });
    const result = await executeStatement("SELECT rate_snapshot");
    const rates = rowsAsRates(result.rows);
    expect(rates[0]).toEqual({
      MODEL_NAME: "partial",
      R_INPUT: 0.01,
      R_OUTPUT: null,
      R_CACHE_READ: null,
      R_CACHE_WRITE: null,
    });
    const cte = ratesCte(rates);
    await executeStatement(cte.sql, cte.params);
    expect(execute).toHaveBeenLastCalledWith(
      expect.objectContaining({
        binds: ["partial", 0.01, null, null, null],
        fetchAsString: ["Number", "Date"],
      }),
    );
  });

  it("keeps literal NULL text, native nulls, and unsafe integers distinct", async () => {
    const { coerceValue } = await transport();
    expect(coerceValue("NULL", { name: "NAME", type: "text", scale: 0 })).toBe("NULL");
    expect(coerceValue(null, { name: "RATE", type: "real", scale: 0 })).toBeNull();
    expect(coerceValue("0", { name: "RATE", type: "real", scale: 0 })).toBe(0);
    expect(coerceValue("9007199254740993", { name: "ID", type: "fixed", scale: 0 })).toBe(
      "9007199254740993",
    );
  });
});
