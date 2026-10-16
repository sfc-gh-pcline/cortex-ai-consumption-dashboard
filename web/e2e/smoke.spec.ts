import { expect, test } from "@playwright/test";

import { dependencyLabel, initialDependenciesFor, SURFACE_COCO } from "../lib/plans";
import { PAGES, urlPath } from "../lib/view-state";
import { mockApi } from "./mocks";

test.describe("every page renders", () => {
  for (const page of PAGES) {
    test(`${page.title} at ${urlPath(page)}`, async ({ page: browser }) => {
      await mockApi(browser, { isAdmin: true });
      await browser.goto(`/${urlPath(page)}`);
      await expect(browser.getByRole("heading", { level: 1 }).first()).toBeVisible();
      await expect(browser.getByRole("navigation", { name: "Pages" })).toContainText(page.title);
      // No unhandled data errors surfaced as the error notice.
      await expect(browser.getByText("Cannot reach the dashboard API")).toHaveCount(0);
    });
  }
});

test("sidebar shows the build line", async ({ page }) => {
  await mockApi(page);
  await page.goto("/");
  const build = page.getByText(/^Next\.js .* \| build [0-9a-f]{12}/);
  await expect(build).toBeVisible();
  await build.click();
  await expect(page.getByRole("list", { name: "Component versions" })).toContainText("React");
});

test("loading panel lists each dependency for the page", async ({ page }) => {
  await mockApi(page, { dependencyDelayMs: 1500 });
  await page.goto("/coco");
  const panel = page.getByRole("status");
  await expect(panel).toContainText("Loading data");
  for (const dependency of initialDependenciesFor(SURFACE_COCO)) {
    await expect(panel).toContainText(dependencyLabel(dependency));
  }
  await expect(panel).not.toContainText("user_tags");
  await expect(page.getByText("Credits Over Time By Surface")).toBeVisible({ timeout: 10_000 });
});

test("date preset and breakdown controls round-trip through the URL", async ({ page }) => {
  await mockApi(page);
  await page.goto("/coco");
  await expect(page.getByText("Credits Over Time By Surface")).toBeVisible();
  await page
    .getByRole("radiogroup", { name: "Date range" })
    .getByRole("radio", { name: "30d" })
    .click();
  await expect(page).toHaveURL(/range=30d/);
  await page
    .getByRole("radiogroup", { name: "Group by" })
    .getByRole("radio", { name: "By model" })
    .click();
  await expect(page).toHaveURL(/coco_breakdown=By\+model|coco_breakdown=By%20model/);
  await page.reload();
  await expect(page.getByRole("radio", { name: "By model" })).toHaveAttribute(
    "aria-checked",
    "true",
  );
});

test("unsupported filters are disabled but their values are preserved", async ({ page }) => {
  await mockApi(page);
  await page.goto("/cortex_search?models=llama3.1-8b");
  await expect(page.getByText("By Consumption Type")).toBeVisible();
  await expect(page.getByRole("button", { name: "Model", exact: true })).toBeDisabled();
  await expect(page).toHaveURL(/models=llama3\.1-8b/);
});

test("repeated Streamlit filter parameters remain selected", async ({ page }) => {
  await mockApi(page);
  await page.goto("/coco?users=ALICE&users=BOB");
  await expect(page).toHaveURL(/users=ALICE.*users=BOB/);
  await expect(page.getByRole("button", { name: "User", exact: true })).toContainText("ALICE, BOB");
});

test("an invalid custom range retains the prior applied results", async ({ page }) => {
  await mockApi(page);
  await page.goto("/coco?range=7d");
  await expect(page.getByText("Credits Over Time By Surface")).toBeVisible();
  await page.getByRole("radio", { name: "Custom" }).click();
  await page.getByRole("button", { name: "Apply dates" }).click();
  await expect(page.getByText("Select both dates before applying.")).toBeVisible();
  await expect(page.getByText("Credits Over Time By Surface")).toBeVisible();
});

test("config page is locked for non-admins and open for admins", async ({ page }) => {
  await mockApi(page, { isAdmin: false });
  await page.goto("/config");
  await expect(
    page.getByText("Acceleration settings require an authorized dashboard administrator."),
  ).toBeVisible();
  await page.unrouteAll();
  await mockApi(page, { isAdmin: true });
  await page.goto("/config");
  await expect(page.getByText("Resource status")).toBeVisible();
  await expect(page.getByText("Source routing")).toBeVisible();
});
