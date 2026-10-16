import type { Page, Route } from "@playwright/test";

import {
  DEP_COWORK_SERVICE_MIX,
  DEP_SEARCH_BATCH,
  DEP_SEARCH_DAILY,
  DEP_SEARCH_SERVING,
  DEP_USER_TAGS,
  DEPENDENCY_KINDS,
  SURFACE_AGENTS,
  SURFACE_AI_FUNCTIONS,
  SURFACE_COCO,
  SURFACE_COWORK,
  SURFACE_MODEL_RATES,
  SURFACE_REST_API,
  SURFACE_SEARCH,
  SURFACE_WAREHOUSE_ATTRIBUTION,
} from "../lib/plans";

export const TODAY = "2026-09-23";

export interface MockOptions {
  readonly isAdmin?: boolean;
  readonly inferenceUnpricedTokens?: number | null;
  // Delay every dependency response, to observe the loading panel.
  readonly dependencyDelayMs?: number;
}

const usage = (date: string, user: string, model: string | null) => ({
  USAGE_DATE: date,
  USER_NAME: user,
  MODEL_NAME: model,
  CALLS: 3,
  TOKENS: 12_000,
  CREDITS: 0.25,
  TOK_INPUT: 4_000,
  TOK_OUTPUT: 2_000,
  TOK_CACHE_READ_INPUT: 5_000,
  TOK_CACHE_WRITE_INPUT: 1_000,
});

// Two rows per surface, on the last two days of the requested window. The request
// window spans both comparison periods, so the current period is its tail.
function rowsFor(dependency: string, end: string): Record<string, unknown>[] {
  const prev = new Date(`${end}T00:00:00Z`);
  prev.setUTCDate(prev.getUTCDate() - 1);
  const start = end;
  const day2 = prev.toISOString().slice(0, 10);
  switch (dependency) {
    case SURFACE_COCO:
      return [
        {
          ...usage(start, "ALICE", "claude-3-5-sonnet"),
          SUB_SURFACE: "cli",
          ROLE_NAME: "ANALYST",
          INFERENCE_REGION: "AWS_US_WEST_2",
        },
        {
          ...usage(day2, "BOB", "llama3.1-8b"),
          SUB_SURFACE: "desktop",
          ROLE_NAME: "ANALYST",
          INFERENCE_REGION: "AWS_US_WEST_2",
        },
      ];
    case SURFACE_AI_FUNCTIONS:
      return [
        {
          ...usage(start, "ALICE", "llama3.1-8b"),
          FUNCTION_NAME: "AI_COMPLETE",
          QUERY_TAG: "",
          INCOMPLETE_CALLS: 0,
        },
        {
          ...usage(day2, "BOB", "llama3.1-8b"),
          FUNCTION_NAME: "AI_CLASSIFY",
          QUERY_TAG: "etl",
          INCOMPLETE_CALLS: 1,
        },
      ];
    case SURFACE_AGENTS:
      return [
        {
          ...usage(start, "ALICE", null),
          AGENT_NAME: "HELPER",
          AGENT_FQN: "DB.S.HELPER",
          AGENT_TAG_LABELS: [],
          ROOT_REQUESTS: 2,
          CHILD_REQUESTS: 4,
        },
      ];
    case SURFACE_COWORK:
      return [
        {
          ...usage(start, "ALICE", null),
          COWORK_NAME: "TEAM",
          AGENT_FQN: "DB.S.HELPER",
          AGENT_NAME: "HELPER",
          ROLE_NAME: "ANALYST",
          AGENT_TAG_LABELS: [],
          SQL_QUERY_CREDITS: 0.1,
          AI_FUNCTIONS_CREDITS: 0,
          ROOT_REQUESTS: 2,
          CHILD_REQUESTS: 1,
        },
      ];
    case DEP_COWORK_SERVICE_MIX:
      return [{ ...usage(start, "ALICE", "claude-3-5-sonnet"), SERVICE_TYPE: "cortex_agents" }];
    case DEP_SEARCH_DAILY:
      return [
        {
          USAGE_DATE: start,
          SERVICE_NAME: "DB.S.DOCS",
          CONSUMPTION_TYPE: "SERVING",
          CREDITS: 0.4,
          TOKENS: 0,
        },
        {
          USAGE_DATE: day2,
          SERVICE_NAME: "DB.S.DOCS",
          CONSUMPTION_TYPE: "EMBED_TEXT_TOKENS",
          CREDITS: 0.1,
          TOKENS: 8_000,
        },
      ];
    case DEP_SEARCH_SERVING:
      return [
        {
          USAGE_DATE: start,
          USAGE_HOUR: `${start} 03:00:00`,
          SERVICE_NAME: "DB.S.DOCS",
          CALLS: 10,
          CREDITS: 0.02,
        },
      ];
    case DEP_SEARCH_BATCH:
      return [];
    case SURFACE_REST_API:
      return [
        {
          ...usage(start, "ALICE", "claude-3-5-sonnet"),
          INFERENCE_REGION: "AWS_US_WEST_2",
          QUERY_TAG: "",
          UNRATED_TOKENS: 0,
          RATE_INCOMPLETE: false,
        },
      ];
    case SURFACE_WAREHOUSE_ATTRIBUTION:
      return [
        {
          QUERY_ID: "q1",
          QUERY_TAG: "",
          WAREHOUSE_NAME: "COMPUTE_WH",
          SURFACE: SURFACE_COCO,
          TIER: "per_request",
          REQUEST_ID: "r1",
          SOURCE_VIEW: "QUERY_ATTRIBUTION_HISTORY",
          COMPUTE_CREDITS: 0.05,
          CLOUD_SERVICES_CREDITS: 0,
          WAREHOUSE_CREDITS: 0.05,
          USAGE_DATE: start,
        },
      ];
    case SURFACE_MODEL_RATES:
      return [
        {
          MODEL_NAME: "claude-3-5-sonnet",
          R_INPUT: 0.000003,
          R_OUTPUT: 0.000015,
          R_CACHE_READ: 0.0000003,
          R_CACHE_WRITE: 0.00000375,
        },
      ];
    case DEP_USER_TAGS:
      return [{ USER_NAME: "ALICE", TAG_NAME: "team", TAG_VALUE: "eng", TAG_LABEL: "team=eng" }];
    default:
      return [];
  }
}

const settings = {
  enabled: false,
  database: "COST_MGMT_DB",
  schema: "PUBLIC",
  warehouseName: "COST_MGMT_WH",
  warehouseSize: "XSMALL",
  objectPrefix: "COST_MGMT",
  ownedObjects: [],
  whDegraded: false,
};

// Intercepts the three route handlers with deterministic fixtures.
export async function mockApi(page: Page, options: MockOptions = {}) {
  await page.route("**/api/preflight", (route: Route) =>
    route.fulfill({
      json: {
        preflight: {
          account: "TESTACCT",
          userName: "MYOUNG",
          role: "ACCOUNTADMIN",
          warehouse: "COMPUTE_WH",
          checks: [],
        },
        today: TODAY,
        viewer: "MYOUNG",
        isAdmin: options.isAdmin ?? false,
      },
    }),
  );
  await page.route("**/api/settings**", (route: Route) =>
    route.fulfill({
      json: {
        settings,
        location: { database: "COST_MGMT_DB", schema: "PUBLIC" },
        today: TODAY,
        statuses: {},
        readiness: {},
        freshness: "",
        healthy: false,
        sourceModes: Object.fromEntries(
          Object.keys(DEPENDENCY_KINDS).map((k) => [k, ["direct", "acceleration disabled"]]),
        ),
        generations: Object.fromEntries(Object.keys(DEPENDENCY_KINDS).map((k) => [k, "direct"])),
        sourceQueryCount: 0,
      },
    }),
  );
  await page.route("**/api/dependency/**", async (route: Route) => {
    const url = new URL(route.request().url());
    const dependency = decodeURIComponent(url.pathname.split("/").pop() ?? "");
    const start = url.searchParams.get("start") ?? TODAY;
    const end = url.searchParams.get("end") ?? TODAY;
    if (options.dependencyDelayMs)
      await new Promise((r) => setTimeout(r, options.dependencyDelayMs));
    let rows = rowsFor(dependency, end);
    if (dependency === SURFACE_REST_API && options.inferenceUnpricedTokens !== undefined) {
      rows = [
        {
          ...rows[0],
          TOKENS: 1_000_000,
          UNRATED_TOKENS: options.inferenceUnpricedTokens,
          RATE_INCOMPLETE: options.inferenceUnpricedTokens !== 0,
        },
        { ...rows[0], USER_NAME: "BOB", UNRATED_TOKENS: 500, RATE_INCOMPLETE: true },
      ];
    }
    await route.fulfill({
      json: {
        dependency,
        rows,
        coverage: {
          dependency,
          actualSource: "direct",
          fetchedAt: `${TODAY}T12:00:00Z`,
          requested: { start, end },
          served: { start, end },
          state: "available",
        },
        degraded: false,
        message: null,
        rateSnapshotAt: dependency === SURFACE_REST_API ? `${TODAY}T12:00:00Z` : null,
        generation: "direct",
        window: { start, end },
      },
    });
  });
}

export { SURFACE_SEARCH };

// ---------------------------------------------------------------------------
// Admin routes
// ---------------------------------------------------------------------------

import type { Page as PwPage, Route as PwRoute } from "@playwright/test";

import { configuredObjects, emptyStatus } from "../lib/health";
import { DEFAULT_SETTINGS } from "../lib/settings";

export interface AdminMock {
  // Every POST body the page sent, in order, with its path.
  readonly posts: { path: string; body: Record<string, unknown> }[];
}

// Admin viewer with every object registered and every task ACTIVE. The action
// route answers with `actionResults` so retry flows can be exercised.
export async function mockAdmin(
  page: PwPage,
  options: { actionResults?: Record<string, string>; actionError?: string } = {},
): Promise<AdminMock> {
  const owned = Object.values(configuredObjects(DEFAULT_SETTINGS));
  const settingsWithObjects = { ...DEFAULT_SETTINGS, ownedObjects: owned };
  const statuses = Object.fromEntries(
    Object.keys(DEPENDENCY_KINDS)
      .filter((k) => DEPENDENCY_KINDS[k] === "windowed_aggregate" || k === SURFACE_MODEL_RATES)
      .map((k) => [
        k,
        emptyStatus(k, {
          tableExists: true,
          taskExists: true,
          schedulingState: "ACTIVE",
          dataTimestamp: `${TODAY}T01:00:00+00:00`,
          rowCount: 10,
        }),
      ]),
  );
  const tasks = owned.filter((o) => o.type === "TASK").map((o) => o.fqn);
  const state = {
    untracked: [] as string[],
    eligible: { suspend: tasks, resume: [] as string[], refresh: tasks },
    provisionReport: null as unknown,
    lifecycleReport: null as unknown,
  };
  const mock: AdminMock = { posts: [] };
  await mockApi(page, { isAdmin: true });
  await page.route("**/api/settings**", (route: PwRoute) =>
    route.fulfill({
      json: {
        settings: settingsWithObjects,
        location: { database: "COST_MGMT_DB", schema: "PUBLIC" },
        today: TODAY,
        statuses,
        readiness: Object.fromEntries(Object.keys(statuses).map((k) => [k, "Ready"])),
        freshness: `${TODAY}T01:00:00+00:00`,
        healthy: true,
        sourceModes: Object.fromEntries(
          Object.keys(DEPENDENCY_KINDS).map((k) => [k, ["direct", "acceleration disabled"]]),
        ),
        generations: Object.fromEntries(Object.keys(DEPENDENCY_KINDS).map((k) => [k, "direct"])),
        sourceQueryCount: 0,
      },
    }),
  );
  await page.route("**/api/admin/state", (route: PwRoute) => route.fulfill({ json: state }));
  await page.route("**/api/admin/**", async (route: PwRoute) => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith("/state")) return route.fallback();
    const body = (route.request().postDataJSON() ?? {}) as Record<string, unknown>;
    mock.posts.push({ path: url.pathname, body });
    if (url.pathname.endsWith("/action")) {
      const results =
        options.actionResults ??
        Object.fromEntries((body.targets as string[]).map((t) => [t, "Succeeded"]));
      const report = {
        action: body.action,
        results,
        error: options.actionError ?? "",
        persistenceFailed: false,
      };
      state.lifecycleReport = report;
      return route.fulfill({ json: { report } });
    }
    if (url.pathname.endsWith("/provision")) {
      const report = {
        results: {},
        steps: { "Task warehouse": "warehouse denied" },
        target: null,
        error: "Task warehouse/context failed: warehouse denied",
      };
      state.provisionReport = report;
      return route.fulfill({ json: { report } });
    }
    return route.fulfill({ json: { settings: settingsWithObjects } });
  });
  return mock;
}
