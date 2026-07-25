import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const PLAYWRIGHT_FALLBACK = "/private/tmp/opencrew-playwright-runner/node_modules/playwright";

export function loadPlaywright() {
  for (const id of ["playwright", PLAYWRIGHT_FALLBACK]) {
    try {
      return require(id);
    } catch {
      // Try the next location.
    }
  }
  throw new Error(
    "Playwright is not installed. Run `npm --prefix frontend install playwright` " +
      "or provide the local fallback at /private/tmp/opencrew-playwright-runner.",
  );
}
