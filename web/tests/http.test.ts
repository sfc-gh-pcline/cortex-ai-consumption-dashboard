import { describe, expect, it, vi } from "vitest";

import { errorResponse } from "@/lib/server/http";

describe("errorResponse", () => {
  it("does not disclose unexpected Snowflake errors", async () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const response = errorResponse(
      new Error("SQL compilation error in SECRET_DB.PRIVATE.TABLE for role ACCOUNTADMIN"),
    );
    expect(response.status).toBe(500);
    const body = (await response.json()) as { error: string; requestId: string };
    expect(body.error).toBe("The dashboard request failed.");
    expect(body.requestId).toMatch(/^[0-9a-f-]{36}$/);
    expect(JSON.stringify(body)).not.toContain("SECRET_DB");
    expect(spy).toHaveBeenCalled();
    spy.mockRestore();
  });
});
