import { expect, test, type Locator, type Page } from "@playwright/test";

import type { Row } from "../lib/agg/rows";
import { SURFACE_COCO, SURFACE_COWORK, SURFACE_WAREHOUSE_ATTRIBUTION } from "../lib/plans";
import { mockApi, TODAY } from "./mocks";

// Keep the standard preflight/settings mocks; replace only the tested source.
async function mockRows(page: Page, dependency: string, rows: (end: string) => Row[]) {
  await page.route("**/api/dependency/**", async (route) => {
    const url = new URL(route.request().url());
    if (decodeURIComponent(url.pathname.split("/").pop() ?? "") !== dependency) {
      return route.fallback();
    }
    const start = url.searchParams.get("start") ?? TODAY;
    const end = url.searchParams.get("end") ?? TODAY;
    await route.fulfill({
      json: {
        dependency,
        rows: rows(end),
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
        generation: "direct",
        window: { start, end },
      },
    });
  });
}

const warehouseRows = (end: string): Row[] => [
  {
    USAGE_DATE: end,
    SURFACE: SURFACE_COCO,
    TIER: "per_request",
    WAREHOUSE_NAME: "ALPHA",
    QUERY_ID: "q-alpha",
    REQUEST_ID: null,
    WAREHOUSE_CREDITS: 10,
    COMPUTE_CREDITS: 10,
    CLOUD_SERVICES_CREDITS: 0,
  },
  {
    USAGE_DATE: end,
    SURFACE: SURFACE_COCO,
    TIER: "per_request",
    WAREHOUSE_NAME: "ZULU",
    QUERY_ID: "q-zulu",
    WAREHOUSE_CREDITS: 2,
    COMPUTE_CREDITS: null,
    CLOUD_SERVICES_CREDITS: 0,
  },
  {
    USAGE_DATE: end,
    SURFACE: SURFACE_COCO,
    TIER: "surface",
    WAREHOUSE_NAME: "BETA",
    QUERY_ID: "q-beta",
    REQUEST_ID: "r-beta",
    WAREHOUSE_CREDITS: 100,
    COMPUTE_CREDITS: 100,
    CLOUD_SERVICES_CREDITS: 0,
  },
];

const warehouseColumn = (table: Locator) => table.locator("tbody tr td:nth-child(4)");

test("initial numeric sort, numeric and text toggles use actual row order", async ({ page }) => {
  await mockApi(page);
  await mockRows(page, SURFACE_WAREHOUSE_ATTRIBUTION, warehouseRows);
  await page.goto("/warehouse_attribution");
  const table = page.locator("#section-detail table");
  const names = warehouseColumn(table);

  await expect(names).toHaveText(["BETA", "ALPHA", "ZULU"]);
  await expect(table.locator("tbody tr td:nth-child(10)")).toHaveText(["100.00", "10.00", "2.00"]);
  await expect(table.locator("tbody tr td:nth-child(7)")).toHaveText(["r-beta", "", ""]);
  await expect(table.locator("tbody tr td:nth-child(8)")).toHaveText([
    "100.00",
    "10.00",
    "Unavailable",
  ]);

  const numericSort = table.getByRole("button", { name: "Sort by Total", exact: true });
  await numericSort.click();
  await expect(names).toHaveText(["ZULU", "ALPHA", "BETA"]);
  await numericSort.click();
  await expect(names).toHaveText(["ALPHA", "ZULU", "BETA"]);
  await numericSort.click();
  await expect(names).toHaveText(["BETA", "ALPHA", "ZULU"]);

  const textSort = table.getByRole("button", { name: "Sort by Warehouse", exact: true });
  await textSort.click();
  await expect(names).toHaveText(["ALPHA", "BETA", "ZULU"]);
  await textSort.click();
  await expect(names).toHaveText(["ZULU", "BETA", "ALPHA"]);
});

test("controlled sorting survives filtered and refreshed input rows", async ({ page }) => {
  await mockApi(page);
  let refreshed = false;
  let sourceReads = 0;
  await mockRows(page, SURFACE_WAREHOUSE_ATTRIBUTION, (end) => {
    sourceReads += 1;
    if (!refreshed) return warehouseRows(end);
    return warehouseRows(end).map((row, index) => ({
      ...row,
      WAREHOUSE_NAME: ["DELTA", "CHARLIE", "ECHO"][index],
      WAREHOUSE_CREDITS: [9, 20, 1][index],
    }));
  });
  await page.goto("/warehouse_attribution");
  const table = page.locator("#section-detail table");
  const names = warehouseColumn(table);
  await expect(names).toHaveText(["BETA", "ALPHA", "ZULU"]);
  const textSort = table.getByRole("button", { name: "Sort by Warehouse", exact: true });
  await textSort.click();
  await textSort.click();
  await expect(names).toHaveText(["ZULU", "BETA", "ALPHA"]);

  await page.getByRole("button", { name: "Attribution tier", exact: true }).click();
  await page.getByRole("option", { name: "per_request", exact: true }).click();
  await page.keyboard.press("Escape");
  await expect(page).toHaveURL(/warehouse_tiers=per_request/);
  await expect(names).toHaveText(["ZULU", "ALPHA"]);
  await page.getByRole("button", { name: "Clear filters on this view", exact: true }).click();
  await expect(names).toHaveText(["ZULU", "BETA", "ALPHA"]);

  const previousReads = sourceReads;
  refreshed = true;
  await page.getByRole("button", { name: "Refetch cached results", exact: true }).click();
  await expect.poll(() => sourceReads).toBeGreaterThan(previousReads);
  await expect(names).toHaveText(["ECHO", "DELTA", "CHARLIE"]);
  await expect(table.locator("tbody tr td:nth-child(10)")).toHaveText(["1.00", "9.00", "20.00"]);
});

test("empty table keeps its headers and accepts later rows", async ({ page }) => {
  await mockApi(page);
  let populated = false;
  await mockRows(page, SURFACE_COWORK, (end) => {
    const row = { USAGE_DATE: end, USER_NAME: "ALICE", CREDITS: 2, TOKENS: 12_000, CALLS: 1 };
    // Usage without an agent dimension leaves Agent Detail empty, not the whole page.
    return populated
      ? [
          { ...row, AGENT_FQN: "DB.S.LOW" },
          { ...row, AGENT_FQN: "DB.S.HIGH", CREDITS: 10 },
        ]
      : [row];
  });
  await page.goto("/cowork");
  const table = page.locator("#section-agent-detail table");
  await expect(table).toBeVisible();
  await expect(table.getByRole("columnheader")).toHaveText([
    "Agent (database.schema.name)",
    "Token credits",
    "SQL credits",
    "Tokens",
    "Requests",
    "Users",
  ]);
  await expect(table.locator("tbody tr")).toHaveCount(0);
  populated = true;
  await page.getByRole("button", { name: "Refetch cached results", exact: true }).click();
  await expect(table.locator("tbody tr td:first-child")).toHaveText(["DB.S.HIGH", "DB.S.LOW"]);
  await expect(table.locator("tbody tr td:nth-child(4)")).toHaveText(["12.0K", "12.0K"]);
});
