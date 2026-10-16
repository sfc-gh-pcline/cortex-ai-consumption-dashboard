// Port of tests/test_admin_workflows.py against the mocked admin routes: the
// Config page's edit lock, non-mutating cancel, field-labelled setup errors and
// failure-only retries.
import { expect, test } from "@playwright/test";

import { mockAdmin } from "./mocks";

const LIFECYCLE = [
  "Provision aggregate tables",
  "Suspend refreshes",
  "Resume refreshes",
  "Refresh aggregate tables",
  "Tear down all acceleration objects",
];

test("editing setup disables every lifecycle action", async ({ page }) => {
  const mock = await mockAdmin(page);
  await page.goto("/config");
  await expect(page.getByRole("button", { name: "Suspend refreshes" })).toBeEnabled();
  await page.getByRole("switch", { name: "Edit setup" }).click();
  for (const label of LIFECYCLE) {
    await expect(page.getByRole("button", { name: label })).toBeDisabled();
  }
  await expect(page.getByLabel("Target lag (minutes)")).toHaveCount(0);
  expect(mock.posts).toEqual([]);
});

test("teardown cancel performs no mutation", async ({ page }) => {
  const mock = await mockAdmin(page);
  await page.goto("/config");
  await page.getByRole("button", { name: "Tear down all acceleration objects" }).click();
  await expect(page.getByRole("dialog")).toContainText("permanently dropped");
  await page.getByRole("button", { name: "Cancel" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  expect(mock.posts).toEqual([]);
});

test("invalid setup names the field and performs no write", async ({ page }) => {
  const mock = await mockAdmin(page);
  await page.goto("/config");
  await page.getByRole("button", { name: "Setup" }).click();
  await page.getByRole("switch", { name: "Edit setup" }).click();
  await page.getByLabel("Database").fill("bad;name");
  await page.getByRole("button", { name: "Save configuration" }).click();
  await expect(page.getByText(/^Database: /)).toBeVisible();
  expect(mock.posts).toEqual([]);
});

test("leaving edit mode discards the draft without persistence", async ({ page }) => {
  const mock = await mockAdmin(page);
  await page.goto("/config");
  await page.getByRole("button", { name: "Setup" }).click();
  const edit = page.getByRole("switch", { name: "Edit setup" });
  await edit.click();
  await page.getByLabel("Database").fill("DRAFT");
  await edit.click();
  await expect(page.getByLabel("Database")).toHaveValue("COST_MGMT_DB");
  await expect(page.getByLabel("Database")).toBeDisabled();
  expect(mock.posts).toEqual([]);
});

test("a failed target survives and retry sends only the failures", async ({ page }) => {
  const mock = await mockAdmin(page);
  await page.goto("/config");
  await expect(page.getByRole("button", { name: "Suspend refreshes" })).toBeEnabled();
  await page.getByRole("button", { name: "Suspend refreshes" }).click();
  await expect(mock.posts.length).toBeGreaterThan(0);
  const first = mock.posts[0];
  expect(first.path).toContain("/api/admin/action");
  expect(first.body.action).toBe("suspend");
  const targets = first.body.targets as string[];
  expect(targets.length).toBe(10);
  await expect(page.getByText("Succeeded").first()).toBeVisible();
});

test("failed lifecycle targets show a warning and retry only them", async ({ page }) => {
  const failed = '"COST_MGMT_DB"."PUBLIC"."COST_MGMT_TASK_COCO"';
  const mock = await mockAdmin(page, {
    actionResults: {
      [failed]: "Failed: denied",
      '"COST_MGMT_DB"."PUBLIC"."COST_MGMT_TASK_COWORK"': "Succeeded",
    },
  });
  await page.goto("/config");
  await page.getByRole("button", { name: "Suspend refreshes" }).click();
  await expect(
    page.getByText("1 target(s) failed. Successful targets will not be retried."),
  ).toBeVisible();
  await page.getByRole("button", { name: "Retry failed targets" }).click();
  await expect.poll(() => mock.posts.length).toBe(2);
  expect(mock.posts[1].body).toEqual({ action: "suspend", targets: [failed], retry: true });
});

test("provisioning failure is shown with its step and offers a retry", async ({ page }) => {
  const mock = await mockAdmin(page);
  await page.goto("/config");
  await page.getByRole("button", { name: "Provision aggregate tables" }).click();
  await expect(page.getByText("Task warehouse/context failed: warehouse denied")).toBeVisible();
  await expect(page.getByText("warehouse denied", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Retry failed provisioning" }).click();
  await expect.poll(() => mock.posts.length).toBe(2);
  expect(mock.posts[1].body).toEqual({ retry: true });
  expect(mock.posts[0].body).toEqual({ retry: false });
});
