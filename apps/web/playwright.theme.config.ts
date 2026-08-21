import { defineConfig } from "@playwright/test";

// Same as `playwright.config.ts`: Playwright's bundled Chromium needs
// `libnspr4` and friends, which on this machine come from the conda env rather
// than from the system.
const condaLib = process.env.CONDA_PREFIX ? `${process.env.CONDA_PREFIX}/lib` : "";
process.env.LD_LIBRARY_PATH = [condaLib, process.env.LD_LIBRARY_PATH]
  .filter(Boolean)
  .join(":");

/**
 * The theme checks, run against an already-running server.
 *
 * The main config starts `dev:stack` — gateway, controller and web together —
 * because the viewer tests need recorded runs to look at. The theme tests need
 * none of that: they read `getComputedStyle` off whatever page they land on,
 * and a gateway that returns nothing renders a page with a header, a panel and
 * an error notice, all of which are themed.
 *
 * So this config exists to let them run in seconds against `npm run start`,
 * with `WEB_URL` naming the port. `npm run test:viewer` still runs them
 * through the full stack alongside everything else.
 */
export default defineConfig({
  testDir: "./tests",
  testMatch: "theme_switch.browser.ts",
  use: {
    baseURL: process.env.WEB_URL ?? "http://127.0.0.1:3000",
    headless: true,
  },
});
