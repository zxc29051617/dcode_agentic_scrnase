import { test, expect } from "@playwright/test";

/**
 * The intake page in a real browser.
 *
 * Two configurations, and the test asserts on whichever one it finds rather
 * than requiring the write side to be installed: `dev-stack.py` starts the
 * controller only when `services/controller/.venv` exists, and a read-only
 * stack is a supported way to run this app.
 *
 * What it checks in both cases is that the page is *honest* — a deployment
 * that cannot start a run says so, and one that can never offers a Confirm
 * button for a request the server would refuse.
 */

test("the new-analysis page states which kind of deployment this is", async ({ page }) => {
  await page.goto("/analysis/new", { waitUntil: "domcontentloaded" });
  await expect(page.getByRole("heading", { name: "New analysis" })).toBeVisible();

  const unconfigured = page.getByTestId("controller-unconfigured");
  if (await unconfigured.count()) {
    // Read-only deployment: it must say so, and must not offer a control that
    // cannot work.
    await expect(unconfigured).toContainText("cannot start one");
    await expect(page.getByTestId("confirm-button")).toHaveCount(0);
    return;
  }

  // Write-capable deployment: the draft card exists and starts empty.
  await expect(page.getByTestId("draft-card")).toBeVisible();
  await expect(page.getByTestId("draft-empty")).toContainText("Nothing prepared yet");
  await expect(page.getByTestId("confirm-button")).toHaveCount(0);
});

test("a request missing required information cannot be confirmed", async ({ page }) => {
  await page.goto("/analysis/new", { waitUntil: "domcontentloaded" });
  if (await page.getByTestId("controller-unconfigured").count()) {
    test.skip(true, "no analysis controller configured in this stack");
    return;
  }

  await page.getByText("Prepare a request without the assistant").click();
  const form = page.getByTestId("manual-form");
  await expect(form).toBeVisible();

  // Deliberately incomplete: no research question, no species.
  const dataset = form.locator('select[name="input_ref"] option').nth(1);
  if (await dataset.count()) {
    await form.locator('select[name="input_ref"]').selectOption({ index: 1 });
  }
  await form.getByRole("button", { name: "Prepare request" }).click();

  await expect(page.getByTestId("request-status")).toBeVisible({ timeout: 15_000 });
  const confirm = page.getByTestId("confirm-button");
  await expect(confirm).toBeDisabled();
  // And the page says why, rather than leaving a dead button.
  await expect(page.getByTestId("confirm-blocked-reason")).toBeVisible();
});

test("the model's configuration state is stated rather than silently degraded", async ({ page }) => {
  await page.goto("/analysis/new", { waitUntil: "domcontentloaded" });
  if (await page.getByTestId("controller-unconfigured").count()) {
    test.skip(true, "no analysis controller configured in this stack");
    return;
  }
  const conversation = page.getByTestId("intake-conversation");
  await expect(conversation).toBeVisible();
  // Either a chat is mounted, or the page says there is no model. Never a
  // chat box that looks live and answers nothing.
  const unconfigured = page.getByTestId("assistant-unconfigured");
  const hasChat = await conversation.locator(".copilotKitInput, textarea").count();
  if (!hasChat) {
    await expect(unconfigured).toContainText("Assistant model is not configured");
    // The form still works without a model, which is the point of it existing.
    await expect(unconfigured).toContainText("form below still works");
  }
});

test("the read-only run pages still work", async ({ page }) => {
  // A regression guard: adding the write side must not have broken the
  // projection this app was built to be.
  const runId = process.env.TEST_RUN_ID ?? "demo-2026-0003";
  await page.goto(`/runs/${runId}`, { waitUntil: "domcontentloaded" });
  await expect(page.getByRole("heading", { name: "Run overview" })).toBeVisible();
  await expect(page.getByText("Workflow progress")).toBeVisible();
});
