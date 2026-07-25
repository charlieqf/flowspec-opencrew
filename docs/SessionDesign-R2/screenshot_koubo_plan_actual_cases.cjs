const path = require("path");

function requirePlaywright() {
  if (process.env.PLAYWRIGHT_MODULE) return require(process.env.PLAYWRIGHT_MODULE);
  const frontendRoot = path.resolve(__dirname, "../../frontend");
  const resolved = require.resolve("playwright", { paths: [frontendRoot] });
  return require(resolved);
}

const { chromium } = requirePlaywright();

const docDir = process.env.KOUBO_PLAN_CASE_DOC_DIR || path.resolve(__dirname);
const outDir = path.join(docDir, "koubo_plan_case_actual_results");
const htmlPath = path.join(outDir, "actual_cases.html");

const suites = [
  ["video", 21],
  ["image", 11],
  ["video_only", 11],
];

(async () => {
  const browser = await chromium.launch({
    headless: true,
    executablePath: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  });
  const page = await browser.newPage({ viewport: { width: 1240, height: 1400 }, deviceScaleFactor: 1 });
  await page.goto(`file://${htmlPath}`, { waitUntil: "load" });
  for (const [suite, count] of suites) {
    for (let index = 1; index <= count; index += 1) {
      const id = `${suite}-${String(index).padStart(2, "0")}`;
      const locator = page.locator(`#${id}`);
      await locator.scrollIntoViewIfNeeded();
      await locator.screenshot({ path: path.join(outDir, `${suite}_case_${String(index).padStart(2, "0")}.png`) });
      console.log(`${suite} case ${index} screenshot saved`);
    }
  }
  await browser.close();
})();
