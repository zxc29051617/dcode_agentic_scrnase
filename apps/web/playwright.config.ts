import { defineConfig } from "@playwright/test";

const condaLib = process.env.CONDA_PREFIX ? `${process.env.CONDA_PREFIX}/lib` : "";
process.env.LD_LIBRARY_PATH = [condaLib, process.env.LD_LIBRARY_PATH]
  .filter(Boolean)
  .join(":");

export default defineConfig({
  testDir: "./tests",
  testMatch: "*.browser.ts",
  use: {
    baseURL: process.env.WEB_URL ?? "http://127.0.0.1:13000",
    headless: true,
  },
  webServer: {
    command: "GATEWAY_PORT=18010 WEB_PORT=13000 WEB_MODE=start npm run dev:stack",
    url: "http://127.0.0.1:13000/runs",
    reuseExistingServer: false,
    gracefulShutdown: { signal: "SIGINT", timeout: 5_000 },
    timeout: 120_000,
  },
});
