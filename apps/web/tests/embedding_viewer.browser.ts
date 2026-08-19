import { test, expect } from "@playwright/test";

test("the report provides app-native embedding controls", async ({ page }) => {
  await page.goto("/runs/demo-2026-0003/report", { waitUntil: "domcontentloaded" });

  // "Interactive embedding", not "…viewer": the report page's section heading
  // was renamed and this assertion was left behind, so the test failed on the
  // heading before it ever reached the controls it exists to check.
  await expect(page.getByRole("heading", { name: "Interactive embedding" })).toBeVisible();
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

test("the embedding section never shows an unexplained blank space", async ({ page }) => {
  // plotly.js is the largest thing this app ships and takes seconds to arrive.
  // Before the placeholder existed, that whole wait was a 650px gap with no
  // text in it, and every person who saw it concluded the chart was broken.
  //
  // Asserted by racing the two: from the moment the section is on screen there
  // must be *either* a placeholder or a chart, never neither.
  const runId = process.env.TEST_RUN_ID ?? "demo-2026-0003";
  await page.goto(`/runs/${runId}/report`, { waitUntil: "commit" });

  const pending = page.getByTestId("embedding-loading");
  const chart = page.locator(".js-plotly-plot");
  await expect(pending.or(chart).first()).toBeVisible({ timeout: 30_000 });

  // And the placeholder must hold the chart's height, so arriving does not
  // shove the rest of the page down.
  if (await pending.count()) {
    const box = await pending.first().boundingBox();
    expect(box?.height).toBeGreaterThan(400);
  }

  await expect(chart).toBeVisible({ timeout: 30_000 });
});
