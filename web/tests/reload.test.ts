import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";

import { useReload } from "@/lib/client/queries";
import { loadDependency } from "@/lib/server/loaders";
import { SURFACE_MODEL_RATES, SURFACE_REST_API } from "@/lib/plans";
import { clearResultCache, runQuery } from "@/lib/server/query";
import { DEFAULT_SETTINGS } from "@/lib/settings";

const execute = vi.hoisted(() => vi.fn(async () => ({ rows: [], columns: [] })));
vi.mock("@/lib/snowflake", () => ({ executeStatement: execute }));

afterEach(() => {
  vi.unstubAllGlobals();
  clearResultCache();
  vi.clearAllMocks();
});

it("bypasses only the selected result for an admin; viewer refetch uses the cache", async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  let reload: ReturnType<typeof useReload> | undefined;
  function Probe() {
    reload = useReload();
    return null;
  }
  renderToStaticMarkup(createElement(QueryClientProvider, { client }, createElement(Probe)));
  const selected = { sql: "SELECT 1", params: [] };
  const other = { sql: "SELECT 2", params: [] };
  await runQuery(selected);
  await runQuery(other);
  const urls: string[] = [];
  vi.stubGlobal("fetch", async (input: string) => {
    urls.push(input);
    return Response.json(
      input.startsWith("/api/settings")
        ? { generations: { CoCo: "" } }
        : await runQuery(selected, { bypassCache: input.includes("refresh=1") }),
    );
  });
  const window = { start: "2026-09-13", end: "2026-09-19" };
  await reload!(["CoCo"], window, false);
  expect(execute).toHaveBeenCalledTimes(2);
  await reload!(["CoCo"], window, true);
  expect(urls.at(-1)).toContain("refresh=1");
  expect(execute).toHaveBeenCalledTimes(3);
  await runQuery(other);
  expect(execute).toHaveBeenCalledTimes(3);
  client.clear();
});

it("keeps model rates cached while bypassing Inference API usage", async () => {
  const window = { start: "2026-09-13", end: "2026-09-19" };
  const context = {
    settings: { ...DEFAULT_SETTINGS, enabled: false },
    statuses: {},
    today: "2026-09-20",
    run: runQuery,
    bypassCache: true,
  };
  await loadDependency(SURFACE_MODEL_RATES, window, { ...context, bypassCache: false });
  expect(execute).toHaveBeenCalledTimes(1);
  await loadDependency(SURFACE_REST_API, window, context);
  expect(execute).toHaveBeenCalledTimes(2);
});
