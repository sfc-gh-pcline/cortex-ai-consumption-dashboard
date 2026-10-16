import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";

import { partitionScope } from "@/lib/agg/scope";
import { usePageFacts, type PageFacts } from "@/lib/client/page-data";
import { DEP_USER_TAGS, SURFACE_COCO } from "@/lib/plans";
import { loadDependency } from "@/lib/server/loaders";
import { DEFAULT_SETTINGS } from "@/lib/settings";
import { CoverageState } from "@/lib/view-state";

const states = vi.hoisted(() => vi.fn());
vi.mock("@/lib/client/queries", async (original) => ({
  ...(await original<typeof import("@/lib/client/queries")>()),
  useDependencies: states,
}));
vi.mock("@/lib/client/dashboard", () => ({
  useDashboard: () => ({
    loadWindow: { start: "2026-09-13", end: "2026-09-19" },
    settings: undefined,
    filters: { users: [], models: [], tags: ["team = selected"] },
  }),
}));

afterEach(() => vi.clearAllMocks());

it.each(["unavailable", "empty", "matching"])("preserves %s tag lookup meaning", async (kind) => {
  const window = { start: "2026-09-13", end: "2026-09-19" };
  const rows = [
    { USER_NAME: "alice", MODEL_NAME: "m", SURFACE: SURFACE_COCO },
    { USER_NAME: "bob", MODEL_NAME: "m", SURFACE: SURFACE_COCO },
    { USER_NAME: null, MODEL_NAME: "m", SURFACE: SURFACE_COCO },
    { USER_NAME: null, MODEL_NAME: "other", SURFACE: SURFACE_COCO },
  ];
  const tags = await loadDependency(DEP_USER_TAGS, window, {
    settings: DEFAULT_SETTINGS,
    statuses: {},
    today: "2026-09-20",
    run: async () => {
      if (kind === "unavailable") throw new Error("synthetic unavailable");
      return {
        rows: kind === "matching" ? [{ USER_NAME: "alice", TAG_LABEL: "team = selected" }] : [],
        columns: [],
        readStartedAt: "2026-09-20T08:00:00Z",
        fetchedAt: "2026-09-20T08:01:00Z",
        snapshotTimestamp: "",
      };
    },
  });
  if (kind === "unavailable") expect(tags.coverage.state).toBe(CoverageState.UNAVAILABLE);
  states.mockReturnValue([
    { dependency: DEP_USER_TAGS, data: tags, isLoading: false },
    {
      dependency: SURFACE_COCO,
      data: { rows, coverage: { state: CoverageState.AVAILABLE } },
      isLoading: false,
    },
  ]);
  let facts: PageFacts | undefined;
  function Probe() {
    facts = usePageFacts(SURFACE_COCO);
    return null;
  }
  const client = new QueryClient();
  renderToStaticMarkup(createElement(QueryClientProvider, { client }, createElement(Probe)));
  const scoped = partitionScope(
    facts?.rows,
    {
      users: ["alice"],
      models: ["m"],
      tags: ["team = selected"],
      pageValues: {},
    },
    { tagUsers: facts?.tagUsers },
  );
  expect(facts?.tagUsers).toEqual(
    kind === "unavailable" ? null : kind === "matching" ? ["alice"] : [],
  );
  expect(scoped.excluded).toEqual(
    kind === "unavailable"
      ? [rows[1], rows[3]]
      : kind === "empty"
        ? [rows[0], rows[1], rows[3]]
        : [rows[1], rows[3]],
  );
  client.clear();
});
