import { expect, test } from "@playwright/test";
import { mockApi } from "./mocks";

for (const route of ["/", "/cortex_inference_api"]) {
  for (const unpriced of [349, null, 0]) {
    test(`${route} explains pricing coverage ${unpriced} for filtered usage`, async ({ page }) => {
      await mockApi(page, { inferenceUnpricedTokens: unpriced });
      await page.goto(`${route}?users=ALICE`);
      await expect(
        page
          .getByText("Estimated Credits Over Time", { exact: true })
          .or(page.getByText("Credits Over Time By Surface", { exact: true })),
      ).toBeVisible();
      if (unpriced === 0) {
        await expect(page.getByText(/Cortex Inference API tokens could not be priced/)).toHaveCount(
          0,
        );
        await expect(page.getByText(/pricing coverage is unknown/)).toHaveCount(0);
        return;
      }
      const warning = page.getByRole("status").filter({
        hasText:
          unpriced === null
            ? "pricing coverage is unknown"
            : "349 Cortex Inference API tokens could not be priced",
      });
      await expect(warning).toBeVisible();
      await expect(warning).toContainText("Tokens and requests are still counted");
      await expect(warning).toContainText("unpriced does not mean free");
      await expect(warning).not.toContainText("849 Cortex");
      if (unpriced === null) {
        await expect(warning).toContainText("unpriced token count is unavailable");
        await expect(warning).not.toContainText("0 Cortex Inference API tokens");
      }
      if (route === "/") {
        await expect(warning).toContainText("Estimated credits and AI usage credits");
        await expect(warning).toContainText("Reported credits are unaffected");
      } else {
        await expect(warning).toContainText("Credits (partial estimate)");
        await expect(page.getByText(/100% of tokens rated/)).toHaveCount(0);
      }
    });
  }
}
