import { test, expect } from "@playwright/test";

/**
 * The theme, checked the only way it can honestly be checked: by asking a real
 * browser what colour the page actually is.
 *
 * The unit tests assert that the tokens are defined and that the two dark
 * palettes agree. Neither can tell you that the page *changed* — that depends
 * on the attribute landing on <html>, the selector matching it, and the
 * cascade resolving the way it is meant to. This walks the three states and
 * reads the computed background back.
 *
 * Run against a built app:
 *     npm run build && npx playwright test tests/theme_switch.browser.ts
 */

const LIGHT_BG = "rgb(247, 248, 250)"; // --bg on :root
const DARK_BG = "rgb(20, 22, 26)"; // --bg in both dark palettes

async function bodyBackground(page: import("@playwright/test").Page) {
  return page.evaluate(() => getComputedStyle(document.body).backgroundColor);
}

test.describe("colour theme", () => {
  test("a light-preferring device gets the light palette by default", async ({ browser }) => {
    const context = await browser.newContext({ colorScheme: "light" });
    const page = await context.newPage();
    await page.goto("/");
    expect(await bodyBackground(page)).toBe(LIGHT_BG);
    await context.close();
  });

  test("a dark-preferring device gets the dark palette by default", async ({ browser }) => {
    const context = await browser.newContext({ colorScheme: "dark" });
    const page = await context.newPage();
    await page.goto("/");
    expect(await bodyBackground(page)).toBe(DARK_BG);
    await context.close();
  });

  test("choosing dark wins on a light device", async ({ browser }) => {
    // The case the old CSS could not express at all: it had an opt-out from
    // dark and no way to opt in.
    const context = await browser.newContext({ colorScheme: "light" });
    const page = await context.newPage();
    await page.goto("/");
    await page.getByTestId("theme-dark").click();
    expect(await bodyBackground(page)).toBe(DARK_BG);
    await context.close();
  });

  test("choosing light wins on a dark device", async ({ browser }) => {
    const context = await browser.newContext({ colorScheme: "dark" });
    const page = await context.newPage();
    await page.goto("/");
    await page.getByTestId("theme-light").click();
    expect(await bodyBackground(page)).toBe(LIGHT_BG);
    await context.close();
  });

  test("the choice survives a reload, with no flash of the other theme", async ({ browser }) => {
    const context = await browser.newContext({ colorScheme: "light" });
    const page = await context.newPage();
    await page.goto("/");
    await page.getByTestId("theme-dark").click();

    await page.reload();
    // Read before waiting for anything React does: the inline bootstrap is
    // supposed to have set this during the document's own parse, so the very
    // first frame is already dark. If the attribute only appeared after
    // hydration this would catch the white flash.
    const attributeAtParse = await page.evaluate(() =>
      document.documentElement.getAttribute("data-theme"),
    );
    expect(attributeAtParse).toBe("dark");
    expect(await bodyBackground(page)).toBe(DARK_BG);
    await context.close();
  });

  test("going back to Auto forgets the choice and follows the device again", async ({ browser }) => {
    const context = await browser.newContext({ colorScheme: "light" });
    const page = await context.newPage();
    await page.goto("/");
    await page.getByTestId("theme-dark").click();
    await page.getByTestId("theme-system").click();

    expect(await page.evaluate(() => document.documentElement.getAttribute("data-theme"))).toBeNull();
    expect(await bodyBackground(page)).toBe(LIGHT_BG);

    await page.reload();
    expect(await bodyBackground(page)).toBe(LIGHT_BG);
    await context.close();
  });

  test("the whole shell follows, not only the body", async ({ browser }) => {
    // The complaint this work started from: some parts switched and some did
    // not. Panels, the header and the nav rail all read `--panel`, so if any
    // of them had kept a literal colour it would show up here.
    const context = await browser.newContext({ colorScheme: "light" });
    const page = await context.newPage();
    await page.goto("/");

    const before = await page.evaluate(() =>
      [...document.querySelectorAll(".shell-top, .panel, .shell-brand")].map(
        (el) => getComputedStyle(el).backgroundColor,
      ),
    );
    await page.getByTestId("theme-dark").click();
    const after = await page.evaluate(() =>
      [...document.querySelectorAll(".shell-top, .panel, .shell-brand")].map(
        (el) => getComputedStyle(el).backgroundColor,
      ),
    );

    expect(before.length).toBeGreaterThan(0);
    for (let i = 0; i < before.length; i++) {
      expect(after[i], `element ${i} kept its colour across the switch`).not.toBe(before[i]);
    }
    await context.close();
  });
});
