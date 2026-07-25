import assert from "node:assert/strict";
import { mkdirSync, writeFileSync } from "node:fs";
import { pathToFileURL } from "node:url";
import { chromium, webkit } from "playwright";
import { join, resolve } from "node:path";
import { repoRoot } from "./media-library-real-helpers.mjs";

const manualPath = resolve(
  process.env.MEDIA_LIBRARY_MANUAL_PATH
    || join(
      repoRoot,
      "docs/SessionDesign-R2/OpenCrew_素材库综合分析_视频剪辑与跨页面语义检索_用户手册.html",
    ),
);
const artifactDir = resolve(
  process.env.MEDIA_LIBRARY_MANUAL_VALIDATION_DIR
    || join(repoRoot, "frontend/e2e/artifacts/media-library-user-manual"),
);
mkdirSync(artifactDir, { recursive: true });
const expectedScreenshotCount = Number(
  process.env.MEDIA_LIBRARY_MANUAL_EXPECTED_SCREENSHOTS || 57,
);
assert.ok(
  Number.isSafeInteger(expectedScreenshotCount)
    && expectedScreenshotCount > 0,
  "expected manual screenshot count must be a positive integer",
);
const manualUrl = pathToFileURL(manualPath).href;
const results = [];

async function validateSurface({ engine, name, viewport, target, screenshot }) {
  const browser = await engine.launch({ headless: true });
  const page = await browser.newPage({ viewport });
  const consoleErrors = [];
  const pageErrors = [];
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  page.on("pageerror", (error) => pageErrors.push(String(error)));

  try {
    await page.goto(manualUrl, { waitUntil: "load" });
    const imageResult = await page.evaluate(async () => {
      const images = [...document.images];
      for (const image of images) {
        image.loading = "eager";
        image.scrollIntoView({ block: "center" });
        try {
          await image.decode();
        } catch {
          // The assertions below preserve the actual decode failure details.
        }
      }
      return {
        count: images.length,
        embeddedCount: images.filter((image) => image.src.startsWith("data:image/png;base64,")).length,
        failed: images
          .map((image, index) => ({
            index,
            alt: image.alt,
            complete: image.complete,
            naturalWidth: image.naturalWidth,
            naturalHeight: image.naturalHeight,
          }))
          .filter((image) => !image.complete || image.naturalWidth <= 0 || image.naturalHeight <= 0),
      };
    });

    assert.equal(
      imageResult.count,
      expectedScreenshotCount,
      `${name}: expected ${expectedScreenshotCount} manual screenshots`,
    );
    assert.equal(
      imageResult.embeddedCount,
      expectedScreenshotCount,
      `${name}: every screenshot must be embedded`,
    );
    assert.deepEqual(imageResult.failed, [], `${name}: every embedded screenshot must decode`);
    assert.deepEqual(consoleErrors, [], `${name}: console errors`);
    assert.deepEqual(pageErrors, [], `${name}: page errors`);

    await page.locator(target).scrollIntoViewIfNeeded();
    await page.waitForTimeout(150);
    const layout = await page.evaluate(() => ({
      viewportWidth: window.innerWidth,
      documentWidth: document.documentElement.scrollWidth,
      bodyWidth: document.body.scrollWidth,
      overflowElements: [...document.querySelectorAll("body *")]
        .map((element) => {
          const rect = element.getBoundingClientRect();
          return {
            tag: element.tagName,
            className: element.className,
            id: element.id,
            left: Math.round(rect.left),
            right: Math.round(rect.right),
            width: Math.round(rect.width),
          };
        })
        .filter((element) => element.right > window.innerWidth + 1 || element.left < -1)
        .slice(0, 20),
    }));
    assert.ok(
      layout.documentWidth <= layout.viewportWidth && layout.bodyWidth <= layout.viewportWidth,
      `${name}: horizontal overflow ${JSON.stringify(layout)}`,
    );
    await page.screenshot({ path: join(artifactDir, screenshot), fullPage: false });
    results.push({
      browser: name,
      viewport,
      imageResult,
      layout,
      screenshot,
      consoleErrors,
      pageErrors,
    });
  } finally {
    await browser.close();
  }
}

await validateSurface({
  engine: webkit,
  name: "webkit-desktop",
  viewport: { width: 1440, height: 1000 },
  target: "#figure-07-editor-internal-semantic-search",
  screenshot: "18-html-user-manual-desktop.png",
});
await validateSurface({
  engine: chromium,
  name: "chromium-mobile",
  viewport: { width: 390, height: 844 },
  target: "#figure-11-editor-retained-clip-completed",
  screenshot: "19-html-user-manual-mobile.png",
});

const report = {
  ok: true,
  validated_at: new Date().toISOString(),
  manual_path: manualPath,
  manual_url: manualUrl,
  expected_embedded_screenshots: expectedScreenshotCount,
  results,
};
writeFileSync(
  join(artifactDir, "manual-validation-report.json"),
  `${JSON.stringify(report, null, 2)}\n`,
);
console.log(JSON.stringify(report, null, 2));
