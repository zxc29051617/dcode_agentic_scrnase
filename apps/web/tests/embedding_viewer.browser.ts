import { test, expect } from "@playwright/test";

test("the report provides app-native embedding controls", async ({ page }) => {
  await page.goto("/runs/demo-2026-0003/report", { waitUntil: "domcontentloaded" });

  await expect(page.getByRole("heading", { name: "Interactive embedding viewer" })).toBeVisible();
  await expect(page.getByLabel("View").locator("option")).toHaveText([
    "UMAP 2D",
    "UMAP 3D",
    "t-SNE 2D",
    "t-SNE 3D",
  ]);
  await expect(page.locator(".js-plotly-plot")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText("12 of 12 cells")).toBeVisible();

  await page.getByLabel("View").selectOption({ label: "UMAP 3D" });
  await expect(page.locator(".js-plotly-plot")).toBeVisible();

  await page.getByLabel("Color").selectOption({ label: "leiden" });
  await expect(page.locator(".legendtext").first()).toBeVisible();
});
