import { afterEach, expect, it, vi } from "vitest";

import { GET } from "@/app/api/dependency/[key]/route";

const state = vi.hoisted(() => ({ load: vi.fn(), context: vi.fn() }));
vi.mock("@/lib/server/context", () => ({ requestContext: state.context }));
vi.mock("@/lib/server/loaders", () => ({ loadDependency: state.load }));
vi.mock("@/lib/identity", async (original) => {
  const actual = await original<typeof import("@/lib/identity")>();
  return {
    ...actual,
    requireAdmin: (headers: Headers) => {
      if (headers.get("sf-context-current-user") !== "admin") throw new actual.ForbiddenError();
      return "admin";
    },
  };
});

const routeContext = { params: Promise.resolve({ key: "CoCo" }) };
const url = "http://localhost/api/dependency/CoCo?start=2026-09-13&end=2026-09-19";

afterEach(() => vi.clearAllMocks());

it("rejects a viewer refresh before reading shared settings or usage", async () => {
  const response = await GET(new Request(`${url}&refresh=1`), routeContext);
  expect(response.status).toBe(403);
  expect(state.context).not.toHaveBeenCalled();
  expect(state.load).not.toHaveBeenCalled();
});

it("admits a scoped admin refresh without a settings/health reload", async () => {
  state.context.mockResolvedValue({
    today: "2026-09-20",
    settings: { enabled: false },
    statuses: {},
  });
  state.load.mockResolvedValue({ rows: [], coverage: { state: "available" } });
  const response = await GET(
    new Request(`${url}&refresh=1`, { headers: { "sf-context-current-user": "admin" } }),
    routeContext,
  );
  expect(response.status).toBe(200);
  expect(state.context).toHaveBeenCalledWith({ reload: false, refresh: true });
  expect(state.load).toHaveBeenCalledTimes(1);
});
