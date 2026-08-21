/**
 * Unit tests for the theme system: the choice, and the tokens it switches.
 *
 * Run with:
 *     npm run test:unit
 *
 * Two of these guard failures that are invisible rather than loud. A token
 * used and never defined resolves to nothing and the element simply loses that
 * property — which is how `--fg` left the tooltip with no background at all,
 * on every page, for as long as it existed. And the dark palette has to be
 * written twice, because CSS cannot apply one set of custom properties from
 * both a media query and an attribute selector; nothing but a test stops the
 * two copies drifting.
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import {
  THEMES,
  THEME_BOOTSTRAP,
  THEME_STORAGE_KEY,
  applyTheme,
  isTheme,
  themeAttribute,
  themeFromStored,
} from "../lib/theme.ts";

const here = dirname(fileURLToPath(import.meta.url));
const css = readFileSync(join(here, "..", "app", "globals.css"), "utf8");

// --- every token that is used is also defined --------------------------------

test("no CSS variable is read without being defined somewhere", () => {
  const used = new Set([...css.matchAll(/var\(\s*(--[a-z0-9-]+)/g)].map((m) => m[1]));
  const defined = new Set([...css.matchAll(/^\s*(--[a-z0-9-]+)\s*:/gm)].map((m) => m[1]));
  const missing = [...used].filter((name) => !defined.has(name)).sort();
  assert.deepEqual(
    missing,
    [],
    `used but never defined: ${missing.join(", ")}. A missing custom property ` +
      "does not error — the declaration is simply dropped, so the element loses " +
      "that colour and nothing says so.",
  );
});

test("--fg in particular is defined, in light and in dark", () => {
  // The one this test was written for. It is the tooltip's background, so its
  // absence was not subtle once you knew to look, and invisible until then.
  assert.match(css, /--fg:\s*#[0-9a-f]{6}/i);
  const darkBlocks = darkPalettes();
  for (const block of darkBlocks) assert.ok("--fg" in block, "--fg missing from a dark palette");
});

// --- the two dark palettes stay identical ------------------------------------

/** Every `--token: value` pair inside each dark block, in source order. */
function darkPalettes(): Record<string, string>[] {
  const blocks: Record<string, string>[] = [];
  for (const selector of [
    /:root:not\(\[data-theme="light"\]\)\s*\{([^}]*)\}/,
    /:root\[data-theme="dark"\]\s*\{([^}]*)\}/,
  ]) {
    const match = css.match(selector);
    assert.ok(match, `no dark palette matched ${selector}`);
    const pairs: Record<string, string> = {};
    for (const decl of match![1].matchAll(/(--[a-z0-9-]+)\s*:\s*([^;]+);/g)) {
      pairs[decl[1]] = decl[2].trim();
    }
    blocks.push(pairs);
  }
  return blocks;
}

test("the media-query dark palette and the chosen-dark palette agree exactly", () => {
  const [fromSystem, fromChoice] = darkPalettes();
  assert.deepEqual(
    fromChoice,
    fromSystem,
    "the two dark palettes have diverged. They are duplicated because CSS " +
      "cannot share custom properties between a media query and an attribute " +
      "selector; if one gains a token the other must too, or a reader who " +
      "chose dark sees a different palette from one whose system chose it.",
  );
});

test("the dark palette redefines nothing that the light palette does not define", () => {
  // The rule the file states about itself: no colour may have its only
  // definition inside a media query, or a viewer in the other state loses it.
  const light = css.slice(0, css.indexOf("@media"));
  const lightTokens = new Set([...light.matchAll(/^\s*(--[a-z0-9-]+)\s*:/gm)].map((m) => m[1]));
  const [fromSystem] = darkPalettes();
  const orphans = Object.keys(fromSystem).filter((t) => !lightTokens.has(t)).sort();
  assert.deepEqual(orphans, [], `only defined in dark: ${orphans.join(", ")}`);
});

// --- the choice ---------------------------------------------------------------

test("an unset, unknown or corrupt stored value means follow the system", () => {
  for (const value of [null, undefined, "", "System", "midnight", "true"]) {
    assert.equal(themeFromStored(value), "system", `${String(value)} should fall back`);
  }
});

test("only the three known themes are accepted", () => {
  assert.deepEqual([...THEMES], ["system", "light", "dark"]);
  for (const good of THEMES) assert.ok(isTheme(good));
  for (const bad of ["auto", "Dark", 1, null]) assert.ok(!isTheme(bad));
});

test("system means no attribute at all, not an attribute saying system", () => {
  // `data-theme="system"` would match neither CSS rule, leaving the page on
  // the light palette no matter what the device asked for.
  assert.equal(themeAttribute("system"), null);
  assert.equal(themeAttribute("light"), "light");
  assert.equal(themeAttribute("dark"), "dark");
});

test("applying a theme sets or removes the attribute the CSS selects on", () => {
  const calls: string[] = [];
  const root = {
    setAttribute: (name: string, value: string) => calls.push(`set ${name}=${value}`),
    removeAttribute: (name: string) => calls.push(`remove ${name}`),
  };

  applyTheme("dark", root);
  applyTheme("light", root);
  applyTheme("system", root);
  assert.deepEqual(calls, [
    "set data-theme=dark",
    "set data-theme=light",
    "remove data-theme",
  ]);
});

// --- the pre-paint script agrees with the module ------------------------------

test("the inline bootstrap reads the same key the toggle writes", () => {
  // Two copies of the storage key would fail in the worst way: the choice
  // would be saved, and then not found on the next load.
  assert.ok(THEME_BOOTSTRAP.includes(JSON.stringify(THEME_STORAGE_KEY)));
});

test("the bootstrap only ever sets an explicit theme", () => {
  // It must not write `data-theme` for the system state, and it must not throw
  // in a browser with storage disabled — both would be worse than doing
  // nothing, which is exactly what the page did before it existed.
  assert.match(THEME_BOOTSTRAP, /"light"/);
  assert.match(THEME_BOOTSTRAP, /"dark"/);
  assert.ok(!THEME_BOOTSTRAP.includes('"system"'));
  assert.match(THEME_BOOTSTRAP, /try\{/);
  assert.match(THEME_BOOTSTRAP, /catch/);
});

test("the bootstrap is one self-contained expression with no imports", () => {
  // It is inlined into <head> and runs before any module has loaded.
  assert.ok(!/\bimport\b|\brequire\(/.test(THEME_BOOTSTRAP));
  assert.match(THEME_BOOTSTRAP.trim(), /^\(function\(\)\{.*\}\)\(\);$/s);
});

// --- no component reintroduces a hardcoded page colour ------------------------

test("the foreign-content backdrop is a token, and deliberately theme-independent", () => {
  // Report figures and third-party HTML are authored against a white page and
  // this app cannot restyle them, so `--foreign-bg` stays light in both
  // themes. That is a decision, and it is recorded once rather than appearing
  // as a bare #fff in two components.
  assert.match(css, /--foreign-bg:\s*#ffffff/i);
  const [fromSystem] = darkPalettes();
  assert.ok(
    !("--foreign-bg" in fromSystem),
    "--foreign-bg must not be redefined in dark: a transparent PNG with black " +
      "axis labels is unreadable on a dark backdrop",
  );
});
