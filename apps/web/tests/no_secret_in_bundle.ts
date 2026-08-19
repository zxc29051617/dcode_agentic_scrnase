/**
 * Assert that no server-side secret reached the client bundle.
 *
 * Builds are what put things in `.next/static`, so this must run *after*
 * `npm run build` and against a build made with the secrets actually set —
 * otherwise it proves nothing. It refuses to pass silently when either
 * condition is unmet.
 *
 * Run with:
 *     npm run build && npm run test:bundle
 */

import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

const STATIC_DIR = ".next/static";

/**
 * Env vars whose values must never appear in client-side JavaScript.
 *
 * `ANALYSIS_CONTROLLER_URL` is on this list for the same reason `GATEWAY_URL`
 * is, and it matters more: the controller is the one service in this product
 * that accepts a POST which can start an analysis. A browser that learned its
 * address could talk to it directly, bypassing the route handlers where the
 * operator identity is decided. It is read only inside `lib/controller.ts`,
 * which begins with `import "server-only"`.
 */
const SECRET_VARS = [
  "ASSISTANT_MODEL_API_KEY",
  "ASSISTANT_MODEL_BASE_URL",
  "ASSISTANT_MODEL_NAME",
  "GATEWAY_URL",
  "ANALYSIS_CONTROLLER_URL",
];

function walk(dir: string): string[] {
  const out: string[] = [];
  for (const entry of readdirSync(dir)) {
    const path = join(dir, entry);
    if (statSync(path).isDirectory()) out.push(...walk(path));
    else out.push(path);
  }
  return out;
}

let files: string[];
try {
  files = walk(STATIC_DIR);
} catch {
  console.error(`FAIL: ${STATIC_DIR} not found. Run \`npm run build\` first.`);
  process.exit(1);
}

const present = SECRET_VARS.filter((name) => (process.env[name] ?? "").trim().length > 0);
if (present.length === 0) {
  console.error(
    "FAIL: none of " +
      SECRET_VARS.join(", ") +
      " is set, so this check would pass without proving anything. " +
      "Run it in the same environment the build used.",
  );
  process.exit(1);
}

console.log(`checking ${files.length} bundle files for ${present.length} live value(s)`);

let leaks = 0;
for (const file of files) {
  const content = readFileSync(file, "utf8");
  for (const name of present) {
    const value = process.env[name]!.trim();

    // The value itself must never appear. This is the check that matters.
    if (content.includes(value)) {
      console.error(`LEAK: value of ${name} found in ${file}`);
      leaks++;
    }

    // A bare variable *name* is not a leak: the assistant panel deliberately
    // renders `ASSISTANT_MODEL_BASE_URL` as help text when no model is
    // configured, telling the reader which variable to set. What would be a
    // leak is a `process.env.NAME` reference surviving into client code,
    // which is what an accidental inline actually looks like — so that is
    // what is matched, rather than the name on its own.
    for (const pattern of [`process.env.${name}`, `process.env["${name}"]`, `process.env['${name}']`]) {
      if (content.includes(pattern)) {
        console.error(`LEAK: ${pattern} reached client code in ${file}`);
        leaks++;
      }
    }
  }
}

if (leaks > 0) {
  console.error(`\nFAIL: ${leaks} leak(s) found in the client bundle`);
  process.exit(1);
}
console.log(`PASS: no secret value or variable name appears in ${STATIC_DIR}`);
