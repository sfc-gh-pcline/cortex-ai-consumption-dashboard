import { describe, expect, it } from "vitest";

import { parseListValues, serializeListValues } from "@/lib/client/url-state";

describe("cross-runtime list URL contract", () => {
  it("accepts repeated and comma-separated values", () => {
    expect(parseListValues(["ALICE", "BOB"])).toEqual(["ALICE", "BOB"]);
    expect(parseListValues(["ALICE,BOB"])).toEqual(["ALICE", "BOB"]);
    expect(parseListValues(["ALICE,BOB", "CAROL"])).toEqual(["ALICE", "BOB", "CAROL"]);
  });

  it("serializes values as repeated parameters for Streamlit links", () => {
    expect(serializeListValues("users", ["ALICE", "BOB"]).toString()).toBe("users=ALICE&users=BOB");
  });
});
