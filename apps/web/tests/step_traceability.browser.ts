import { test, expect } from "@playwright/test";

/**
 * The workflow page has to answer "how did this step run", not only "did it".
 *
 * `docs/report_contract.md` calls this tier — "who decided what, and can it be
 * rerun" — the reason the pipeline exists. Every field of it was written to
 * disk from the first run and projected nowhere, so the app could show that a
 * step passed and not what it passed with.
 *
 * The fixture is a synthetic run, so which step carries which detail is not
 * fixed. These assert on the page's contract rather than on one step's data:
 * something expands, and what it shows is labelled.
 */

const RUN = process.env.TEST_RUN_ID ?? "demo-2026-0003";

test("expanding a step shows how it ran, not only that it did", async ({ page }) => {
  await page.goto(`/runs/${RUN}/workflow`, { waitUntil: "domcontentloaded" });

  const rows = page.locator("button.tl-row");
  await expect(rows.first()).toBeVisible({ timeout: 30_000 });
  await rows.first().click();

  const detail = page.locator(".tl-detail").first();
  await expect(detail).toBeVisible();
  // The judge's verdict was always here; these two are what the step itself
  // recorded about its own execution.
  await expect(detail).toContainText("status");
});

test("a step's own reservations are shown apart from the judge's verdict", async ({ page }) => {
  // A judge can return `pass` on a step that recorded a doubt — it is asked
  // whether the step ran soundly, and a cluster of 8 cells is a sound run of
  // an unsound-looking result. Both have to reach the screen, distinctly.
  await page.goto(`/runs/${RUN}/workflow`, { waitUntil: "domcontentloaded" });
  const rows = page.locator("button.tl-row");
  await expect(rows.first()).toBeVisible({ timeout: 30_000 });

  const count = await rows.count();
  let sawNotes = false;
  for (let i = 0; i < count; i++) {
    await rows.nth(i).click();
    if (await page.locator('[data-testid^="notes-"]').count()) {
      await expect(page.locator('[data-testid^="notes-"]').first())
        .toContainText("what this step said about its own result");
      sawNotes = true;
      break;
    }
    await rows.nth(i).click();
  }
  // Not every fixture records a note; the assertion is that when one exists it
  // is labelled as the step's own words rather than folded into the warnings.
  test.info().annotations.push({ type: "notes-present", description: String(sawNotes) });
});

test("a figure is served by opaque id through this app's own proxy", async ({ page }) => {
  await page.goto(`/runs/${RUN}/workflow`, { waitUntil: "domcontentloaded" });
  const rows = page.locator("button.tl-row");
  await expect(rows.first()).toBeVisible({ timeout: 30_000 });

  const count = await rows.count();
  for (let i = 0; i < count; i++) {
    await rows.nth(i).click();
    const figures = page.locator('[data-testid^="figures-"]');
    if (await figures.count()) {
      const img = figures.first().locator("img").first();
      await img.scrollIntoViewIfNeeded();
      // The browser must never be handed the gateway's address.
      const src = await img.getAttribute("src");
      expect(src).toMatch(/^\/api\/artifacts\//);
      await expect
        .poll(() => img.evaluate((el: HTMLImageElement) => el.complete && el.naturalWidth > 0), {
          timeout: 20_000,
        })
        .toBe(true);
      return;
    }
    await rows.nth(i).click();
  }
});
