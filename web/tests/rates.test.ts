// Port of test_token_type_rate_coverage_detects_partial_model_and_unknown_tokens.
import { describe, expect, it } from "vitest";

import { inferencePricingLabel, inferencePricingWarning, withRateCoverage } from "@/lib/rates";

describe("pricing warning copy", () => {
  it.each([
    [1, "1 token not priced", "1 Cortex Inference API token could not be priced"],
    [12_345, "12,345 tokens not priced", "12,345 Cortex Inference API tokens could not be priced"],
    [null, "Token pricing coverage unknown", "Cortex Inference API pricing coverage is unknown"],
    [NaN, "Token pricing coverage unknown", "Cortex Inference API pricing coverage is unknown"],
  ] as const)(
    "describes count %s without rounding away missing prices",
    (count, label, summary) => {
      expect(inferencePricingLabel(count)).toBe(label);
      expect(inferencePricingWarning(count).startsWith(summary)).toBe(true);
      expect(inferencePricingWarning(count, true).startsWith(summary)).toBe(true);
    },
  );

  it("does not claim billing accuracy for fully priced usage", () => {
    expect(inferencePricingLabel(0)).toBe("All reported tokens have observed rates");
  });
});

describe("withRateCoverage", () => {
  it("detects partially rated models and unaccounted tokens", () => {
    const rates = [
      {
        MODEL_NAME: "partial",
        R_INPUT: 0.01,
        R_OUTPUT: null,
        R_CACHE_READ: null,
        R_CACHE_WRITE: null,
      },
      { MODEL_NAME: "free", R_INPUT: 0, R_OUTPUT: 0, R_CACHE_READ: 0, R_CACHE_WRITE: 0 },
    ];
    const rows = [
      { MODEL_NAME: "partial", TOK_INPUT: 10, TOK_OUTPUT: 5, TOKENS: 15 },
      { MODEL_NAME: "free", TOK_INPUT: 10, TOK_OUTPUT: 5, TOKENS: 15 },
      { MODEL_NAME: "absent", TOK_INPUT: 10, TOK_OUTPUT: 5, TOKENS: 15 },
      { MODEL_NAME: "partial", TOK_INPUT: 10, TOK_OUTPUT: 0, TOKENS: 12 },
    ];
    const result = withRateCoverage(rows, rates);
    expect(result.map((r) => r.UNRATED_TOKENS)).toEqual([5, 0, 15, 2]);
    expect(result.map((r) => r.RATE_INCOMPLETE)).toEqual([true, false, true, true]);
    expect("UNRATED_TOKENS" in rows[0]).toBe(false);
  });

  it("treats a fully rated model as complete", () => {
    const rates = [
      {
        MODEL_NAME: "model",
        R_INPUT: 0.01,
        R_OUTPUT: 0.01,
        R_CACHE_READ: 0.01,
        R_CACHE_WRITE: 0.01,
      },
    ];
    expect(
      withRateCoverage([{ MODEL_NAME: "model", TOKENS: 10, TOK_INPUT: 10 }], rates)[0],
    ).toMatchObject({
      UNRATED_TOKENS: 0,
      RATE_INCOMPLETE: false,
    });
  });
});
