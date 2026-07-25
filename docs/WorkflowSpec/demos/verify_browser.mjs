#!/usr/bin/env node

/** Browser-level visual and interaction checks for the overview and four demos.
 *
 * Playwright is intentionally reused from frontend/package.json. The demos
 * themselves have no browser/runtime dependency and still open from file://.
 */

import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const demoRoot = fileURLToPath(new URL(".", import.meta.url));
const playwrightModule = new URL(
  "../../../frontend/node_modules/playwright/index.mjs",
  import.meta.url,
);

let chromium;
try {
  ({ chromium } = await import(playwrightModule.href));
} catch (error) {
  console.error("Playwright is unavailable. Run `npm ci` in frontend first.");
  console.error(String(error));
  process.exit(2);
}

const scenarios = [
  "loan-approval",
  "bank-risk-report",
  "due-diligence",
  "opencrew-video",
];
const outputDir = process.argv[2]
  ? path.resolve(process.argv[2])
  : mkdtempSync(path.join(tmpdir(), "flowspec-visual-"));
mkdirSync(outputDir, { recursive: true });

const report = [];
let overviewReport;
let landingReport;
const violations = [];
const check = (condition, message) => {
  if (!condition) violations.push(message);
};

const observeErrors = (page, errors) => {
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
  page.on("pageerror", (error) => errors.push(String(error)));
};

const browser = await chromium.launch({ headless: true });
try {
  for (const scenario of scenarios) {
    const url = pathToFileURL(path.join(demoRoot, scenario, "index.html")).href;
    const errors = [];

    const desktopContext = await browser.newContext({
      viewport: { width: 1440, height: 1000 },
      deviceScaleFactor: 1,
    });
    const desktopPage = await desktopContext.newPage();
    observeErrors(desktopPage, errors);
    await desktopPage.goto(url, { waitUntil: "load" });
    await desktopPage.waitForSelector(".metric-card");
    await desktopPage.waitForFunction(() => document.querySelectorAll(".dag-edge").length > 0);

    await desktopPage.screenshot({
      path: path.join(outputDir, `${scenario}-desktop-top.png`),
    });
    await desktopPage.locator(".scenario-guide").screenshot({
      path: path.join(outputDir, `${scenario}-desktop-guide.png`),
    });
    await desktopPage.locator(".dependency-map").screenshot({
      path: path.join(outputDir, `${scenario}-desktop-dependencies.png`),
    });
    await desktopPage.locator(".dag-map-node").nth(1).click();
    const graphDetailTitle = (await desktopPage.locator(".detail-title").innerText()).trim();
    await desktopPage.locator(".flow-layout").screenshot({
      path: path.join(outputDir, `${scenario}-desktop-flow.png`),
    });

    const runButtons = desktopPage.locator(".run-button");
    const runCount = await runButtons.count();
    await runButtons.first().click();
    const priorRunLabel = (await desktopPage.locator("#current-run-label").innerText()).trim();
    await runButtons.last().click();
    const latestRunLabel = (await desktopPage.locator("#current-run-label").innerText()).trim();
    await desktopPage.locator(".step-card").last().click();
    const detailTitle = (await desktopPage.locator(".detail-title").innerText()).trim();

    const tabState = {};
    for (const tab of ["flow", "trace", "artifacts", "storage", "governance", "spec"]) {
      await desktopPage.locator(`.tab-button[data-tab="${tab}"]`).click();
      await desktopPage.waitForTimeout(220);
      tabState[tab] = await desktopPage.locator(`#${tab}`).isVisible();
    }
    await desktopPage.locator('.tab-button[data-tab="storage"]').click();
    await desktopPage.waitForTimeout(220);
    const latestStorageSummary = (await desktopPage.locator("#storage-run-summary").innerText()).trim();
    await runButtons.first().click();
    await desktopPage.waitForTimeout(120);
    const priorStorageSummary = (await desktopPage.locator("#storage-run-summary").innerText()).trim();
    await runButtons.last().click();
    await desktopPage.waitForTimeout(120);
    await desktopPage.locator('.tab-button[data-tab="artifacts"]').click();
    await desktopPage.waitForTimeout(700);
    const desktopArtifactNavigation = await desktopPage.evaluate(() => ({
      headingTop: Math.round(document.querySelector("#artifacts .section-head").getBoundingClientRect().top),
      runbarBottom: Math.round(document.querySelector(".runbar-wrap").getBoundingClientRect().bottom),
    }));
    const desktopArtifactRunbarDisplay = await desktopPage.locator(".runbar-wrap").evaluate((element) => {
      const previous = element.style.display;
      element.style.display = "none";
      return previous;
    });
    await desktopPage.locator("#artifacts").screenshot({
      path: path.join(outputDir, `${scenario}-desktop-artifacts.png`),
    });
    await desktopPage.locator('.tab-button[data-tab="storage"]').click();
    await desktopPage.waitForTimeout(160);
    await desktopPage.locator("#storage").screenshot({
      path: path.join(outputDir, `${scenario}-desktop-storage.png`),
    });
    await desktopPage.locator(".runbar-wrap").evaluate(
      (element, previous) => { element.style.display = previous; },
      desktopArtifactRunbarDisplay,
    );
    await desktopPage.locator('.tab-button[data-tab="governance"]').click();
    await desktopPage.waitForTimeout(220);
    await desktopPage.screenshot({
      path: path.join(outputDir, `${scenario}-desktop-governance.png`),
    });
    await desktopPage.locator('.tab-button[data-tab="spec"]').click();
    await desktopPage.waitForTimeout(220);
    await desktopPage.locator(".run-control-panel").screenshot({
      path: path.join(outputDir, `${scenario}-desktop-run-controls.png`),
    });
    await desktopPage.locator('.tab-button[data-tab="flow"]').click();
    await desktopPage.waitForTimeout(220);

    const desktop = await desktopPage.evaluate(() => {
      const bundle = JSON.parse(document.getElementById("demo-data").textContent);
      const edgeCount = (dependency) =>
        dependency.step_id
          ? 1
          : (dependency.any_of ?? []).reduce((count, child) => count + edgeCount(child), 0);
      return {
        colorScheme: getComputedStyle(document.documentElement).colorScheme,
        bodyOverflow:
          document.documentElement.scrollWidth - document.documentElement.clientWidth,
        heroTitlePx: parseFloat(
          getComputedStyle(document.querySelector(".hero h1")).fontSize,
        ),
        guideBodyPx: parseFloat(
          getComputedStyle(document.querySelector(".guide-rule p")).fontSize,
        ),
        glossaryPx: parseFloat(
          getComputedStyle(document.querySelector(".glossary-item dd")).fontSize,
        ),
        stepBodyPx: parseFloat(
          getComputedStyle(document.querySelector(".step-desc")).fontSize,
        ),
        panelBodyPx: parseFloat(
          getComputedStyle(document.querySelector(".panel-card > p")).fontSize,
        ),
        runControlBodyPx: parseFloat(
          getComputedStyle(document.querySelector(".run-control-card p")).fontSize,
        ),
        steps: document.querySelectorAll(".step-card").length,
        stepOrders: document.querySelectorAll(".step-order").length,
        localizedStepTitles: [...document.querySelectorAll(".step-card")].filter((card) => {
          const step = bundle.process.steps.find((item) => item.id === card.dataset.stepId);
          return card.querySelector(":scope > strong")?.textContent ===
            bundle.metadata.step_labels?.[step?.id]?.title;
        }).length,
        localizedStepDescriptions: [...document.querySelectorAll(".step-card")].filter((card) => {
          const step = bundle.process.steps.find((item) => item.id === card.dataset.stepId);
          return card.querySelector(".step-desc")?.textContent ===
            bundle.metadata.step_labels?.[step?.id]?.description;
        }).length,
        localizedStages: [...document.querySelectorAll(".stage-column")].filter((column) =>
          column.querySelector(".stage-head h3")?.textContent ===
            bundle.metadata.stage_labels?.[column.dataset.stage]
        ).length,
        expectedLocalizedStages: Object.keys(bundle.metadata.stage_labels ?? {}).length,
        topNotes: document.querySelectorAll(".top-note").length,
        dependencyNodes: document.querySelectorAll(".dag-map-node").length,
        dependencyEdges: document.querySelectorAll(".dag-edge").length,
        declaredEdges: bundle.process.steps.reduce(
          (count, step) => count + (step.depends_on ?? []).reduce(
            (inner, dependency) => inner + edgeCount(dependency),
            0,
          ),
          0,
        ),
        forkNodes: document.querySelectorAll(".dag-role.fork").length,
        joinNodes: document.querySelectorAll(".dag-role.join").length,
        runControlCards: document.querySelectorAll(".run-control-card").length,
        glossaryItems: document.querySelectorAll(".glossary-item").length,
        tldrClauses: document.querySelectorAll(".tldr-clause").length,
        tldrText: document.querySelector(".executive-tldr")?.textContent ?? "",
        usageRows: document.querySelectorAll("#usage-body tr").length,
        agentRefs: document.querySelectorAll("#usage-body .agent-ref").length,
        agentExecutionIds: [
          ...document.querySelectorAll("#usage-body .agent-ref"),
        ].map((element) => element.dataset.agentExecutionId),
        agentLimitPills: document.querySelectorAll("#profile-list .agent-limits").length,
        storageEntries: document.querySelectorAll("#storage-entry-body tr").length,
        storageMaterialized: document.querySelectorAll("#storage-entry-body .locator-state.materialized").length,
        storageContracts: document.querySelectorAll("#storage-entry-body .locator-state.layout_contract").length,
        diagnosticRefs: document.querySelectorAll("#diagnostic-ref-list li").length,
        logLevels: document.querySelectorAll(".log-level").length,
        storageBodyPx: parseFloat(
          getComputedStyle(document.querySelector(".authority-plane p")).fontSize,
        ),
        storagePanelBg: getComputedStyle(
          document.querySelector(".authority-plane"),
        ).backgroundColor,
        artifactScopeCards: document.querySelectorAll(".artifact-scope-card").length,
        artifactRows: document.querySelectorAll("#artifact-body tr").length,
        expectedArtifactRows: bundle.runs.at(-1).artifacts.length,
        artifactDerivations: document.querySelectorAll("#artifact-body .artifact-derivation").length,
        artifactDerivationMissing: [...document.querySelectorAll("#artifact-body .artifact-derivation")]
          .some((cell) => cell.textContent.includes("missing producer digest")),
        artifactInputRevisionCopies: bundle.runs.reduce(
          (count, run) => count + run.artifacts.filter(
            (artifact) => Object.hasOwn(artifact, "input_revision_hash"),
          ).length,
          0,
        ),
        artifactBodyPx: parseFloat(
          getComputedStyle(document.querySelector(".artifact-scope-card p")).fontSize,
        ),
      };
    });
    await desktopContext.close();

    const mobileContext = await browser.newContext({
      viewport: { width: 390, height: 844 },
      deviceScaleFactor: 1,
    });
    const mobilePage = await mobileContext.newPage();
    observeErrors(mobilePage, errors);
    await mobilePage.goto(url, { waitUntil: "load" });
    await mobilePage.waitForSelector(".metric-card");
    await mobilePage.screenshot({
      path: path.join(outputDir, `${scenario}-mobile-top.png`),
    });
    await mobilePage.locator(".scenario-guide").scrollIntoViewIfNeeded();
    await mobilePage.screenshot({
      path: path.join(outputDir, `${scenario}-mobile-guide.png`),
    });
    const priorRunbarDisplay = await mobilePage.locator(".runbar-wrap").evaluate((element) => {
      const previous = element.style.display;
      element.style.display = "none";
      return previous;
    });
    await mobilePage.locator(".dependency-map").screenshot({
      path: path.join(outputDir, `${scenario}-mobile-dependencies.png`),
    });
    await mobilePage.locator(".runbar-wrap").evaluate(
      (element, previous) => { element.style.display = previous; },
      priorRunbarDisplay,
    );
    await mobilePage.locator(".flow-layout").scrollIntoViewIfNeeded();
    await mobilePage.screenshot({
      path: path.join(outputDir, `${scenario}-mobile-flow.png`),
    });
    await mobilePage.locator('.tab-button[data-tab="artifacts"]').click();
    await mobilePage.waitForTimeout(700);
    const mobileArtifact = await mobilePage.evaluate(() => ({
      headingTop: Math.round(document.querySelector("#artifacts .section-head").getBoundingClientRect().top),
      runbarBottom: Math.round(document.querySelector(".runbar-wrap").getBoundingClientRect().bottom),
      tableOverflow:
        document.querySelector(".artifact-table-wrap").scrollWidth -
        document.querySelector(".artifact-table-wrap").clientWidth,
      hintDisplay: getComputedStyle(document.querySelector(".artifact-table-hint")).display,
    }));
    const mobileArtifactRunbarDisplay = await mobilePage.locator(".runbar-wrap").evaluate((element) => {
      const previous = element.style.display;
      element.style.display = "none";
      return previous;
    });
    await mobilePage.locator("#artifacts").screenshot({
      path: path.join(outputDir, `${scenario}-mobile-artifacts.png`),
    });
    await mobilePage.locator('.tab-button[data-tab="storage"]').click();
    await mobilePage.waitForTimeout(220);
    await mobilePage.locator("#storage").screenshot({
      path: path.join(outputDir, `${scenario}-mobile-storage.png`),
    });
    await mobilePage.locator(".runbar-wrap").evaluate(
      (element, previous) => { element.style.display = previous; },
      mobileArtifactRunbarDisplay,
    );
    const mobileStorage = await mobilePage.evaluate(() => ({
      width: Math.round(document.querySelector("#storage").getBoundingClientRect().width),
      treeOverflow:
        document.querySelector(".storage-tree").scrollWidth -
        document.querySelector(".storage-tree").clientWidth,
      bodyPx: parseFloat(
        getComputedStyle(document.querySelector(".authority-plane p")).fontSize,
      ),
    }));
    await mobilePage.locator('.tab-button[data-tab="flow"]').click();
    await mobilePage.waitForTimeout(120);

    const mobile = await mobilePage.evaluate(() => {
      const board = document.querySelector(".flow-board");
      const cards = [...document.querySelectorAll(".step-card")];
      return {
        colorScheme: getComputedStyle(document.documentElement).colorScheme,
        bodyOverflow:
          document.documentElement.scrollWidth - document.documentElement.clientWidth,
        boardOverflow: board.scrollWidth - board.clientWidth,
        guideBodyPx: parseFloat(
          getComputedStyle(document.querySelector(".guide-rule p")).fontSize,
        ),
        glossaryPx: parseFloat(
          getComputedStyle(document.querySelector(".glossary-item dd")).fontSize,
        ),
        heroTitlePx: parseFloat(
          getComputedStyle(document.querySelector(".hero h1")).fontSize,
        ),
        stepBodyPx: parseFloat(
          getComputedStyle(document.querySelector(".step-desc")).fontSize,
        ),
        dependencyRows: document.querySelectorAll(".dependency-mobile-row").length,
        dependencyMapWidth: Math.round(
          document.querySelector(".dependency-map").getBoundingClientRect().width,
        ),
        gridColumns: getComputedStyle(
          document.querySelector(".stage-grid"),
        ).gridTemplateColumns,
        minCardWidth: Math.round(
          Math.min(...cards.map((card) => card.getBoundingClientRect().width)),
        ),
        tldrClauses: document.querySelectorAll(".tldr-clause").length,
        tldrWidth: Math.round(
          document.querySelector(".executive-tldr").getBoundingClientRect().width,
        ),
        heroGridWidth: Math.round(
          document.querySelector(".hero-grid").getBoundingClientRect().width,
        ),
        heroTitleWidth: Math.round(
          document.querySelector(".hero h1").getBoundingClientRect().width,
        ),
        heroSubtitleWidth: Math.round(
          document.querySelector(".hero .subtitle").getBoundingClientRect().width,
        ),
        heroAsideWidth: Math.round(
          document.querySelector(".hero-aside").getBoundingClientRect().width,
        ),
        heroContentRight: Math.ceil(
          Math.max(
            document.querySelector(".hero h1").getBoundingClientRect().right,
            document.querySelector(".hero .subtitle").getBoundingClientRect().right,
            document.querySelector(".hero-aside").getBoundingClientRect().right,
          ),
        ),
      };
    });
    mobile.storageWidth = mobileStorage.width;
    mobile.storageTreeOverflow = mobileStorage.treeOverflow;
    mobile.storageBodyPx = mobileStorage.bodyPx;

    await mobilePage.evaluate(() => scrollTo(0, document.body.scrollHeight));
    await mobilePage.locator('.tab-button[data-tab="trace"]').click();
    await mobilePage.waitForTimeout(1600);
    mobile.traceHeadingTop = await mobilePage
      .locator("#trace .section-head")
      .evaluate((element) => Math.round(element.getBoundingClientRect().top));
    mobile.runbarBottom = await mobilePage
      .locator(".runbar-wrap")
      .evaluate((element) => Math.round(element.getBoundingClientRect().bottom));
    await mobileContext.close();

    check(errors.length === 0, `${scenario}: browser console/page errors: ${errors.join(" | ")}`);
    check(runCount === 2, `${scenario}: expected two Run selectors, found ${runCount}`);
    check(priorRunLabel !== latestRunLabel, `${scenario}: Run switch did not update outcome`);
    check(Boolean(detailTitle), `${scenario}: Step detail drawer did not render`);
    check(Boolean(graphDetailTitle), `${scenario}: dependency-map node did not open Step detail`);
    check(Object.values(tabState).every(Boolean), `${scenario}: at least one tab did not activate`);
    check(priorStorageSummary !== latestStorageSummary, `${scenario}: Run switch did not update storage index`);
    check(desktop.colorScheme === "light", `${scenario}: desktop is not using the light document theme`);
    check(desktop.bodyOverflow === 0, `${scenario}: desktop page overflows by ${desktop.bodyOverflow}px`);
    check(desktop.heroTitlePx <= 56, `${scenario}: desktop hero title is oversized at ${desktop.heroTitlePx}px`);
    check(desktop.guideBodyPx >= 13.5, `${scenario}: desktop guide text is ${desktop.guideBodyPx}px`);
    check(desktop.glossaryPx >= 12.5, `${scenario}: desktop glossary text is ${desktop.glossaryPx}px`);
    check(desktop.stepBodyPx >= 13, `${scenario}: desktop Step text is ${desktop.stepBodyPx}px`);
    check(desktop.localizedStepTitles === desktop.steps, `${scenario}: not every Step has a Chinese business title`);
    check(desktop.localizedStepDescriptions === desktop.steps, `${scenario}: not every Step has a Chinese business description`);
    check(desktop.localizedStages === desktop.expectedLocalizedStages, `${scenario}: not every Stage has a Chinese title`);
    check(desktop.topNotes === 0, `${scenario}: implementation-only top note is still visible`);
    check(desktop.panelBodyPx >= 14, `${scenario}: desktop panel text is ${desktop.panelBodyPx}px`);
    check(desktop.runControlBodyPx >= 13.5, `${scenario}: desktop run-control text is ${desktop.runControlBodyPx}px`);
    check(desktop.storageEntries > 0, `${scenario}: storage index has no visible entries`);
    check(desktop.storageMaterialized > 0, `${scenario}: materialized storage evidence is not visible`);
    check(desktop.storageContracts > 0, `${scenario}: live layout contract is not visible`);
    check(desktop.diagnosticRefs > 0, `${scenario}: diagnostic file references are not visible`);
    check(desktop.logLevels === 5, `${scenario}: expected five diagnostic log levels`);
    check(desktop.storageBodyPx >= 13.5, `${scenario}: desktop storage text is ${desktop.storageBodyPx}px`);
    check(desktop.storagePanelBg === "rgb(255, 255, 255)", `${scenario}: storage panel is not white`);
    check(desktop.artifactScopeCards === 3, `${scenario}: Artifact scope boundary is incomplete`);
    check(desktop.artifactRows === desktop.expectedArtifactRows, `${scenario}: Artifact rows did not render`);
    check(desktop.artifactDerivations === desktop.artifactRows, `${scenario}: producer input digests are incomplete`);
    check(!desktop.artifactDerivationMissing, `${scenario}: an Artifact cannot resolve its producer digest`);
    check(desktop.artifactInputRevisionCopies === 0, `${scenario}: Run input hash is duplicated on Artifact records`);
    check(desktop.artifactBodyPx >= 13.5, `${scenario}: desktop Artifact scope text is ${desktop.artifactBodyPx}px`);
    check(
      desktopArtifactNavigation.headingTop > desktopArtifactNavigation.runbarBottom,
      `${scenario}: Artifact heading is hidden behind the desktop Run bar`,
    );
    check(desktop.tldrClauses === 3, `${scenario}: executive TL;DR is incomplete`);
    check(desktop.stepOrders === desktop.steps, `${scenario}: Step reading order is incomplete`);
    check(desktop.dependencyNodes === desktop.steps, `${scenario}: dependency graph omits Steps`);
    check(
      desktop.dependencyEdges === desktop.declaredEdges,
      `${scenario}: rendered ${desktop.dependencyEdges}/${desktop.declaredEdges} dependency arrows`,
    );
    check(desktop.forkNodes > 0, `${scenario}: no one-to-many fork is identified`);
    check(desktop.joinNodes > 0, `${scenario}: no multi-upstream join is identified`);
    check(desktop.runControlCards === 4, `${scenario}: four execution-scope contracts are not visible`);
    check(
      desktop.tldrText.includes("演示边界") && desktop.tldrText.includes("确定性 Mock"),
      `${scenario}: executive TL;DR does not state the demo boundary`,
    );
    check(mobile.colorScheme === "light", `${scenario}: mobile is not using the light document theme`);
    check(mobile.bodyOverflow === 0, `${scenario}: mobile page overflows by ${mobile.bodyOverflow}px`);
    check(mobile.boardOverflow === 0, `${scenario}: mobile flow board still scrolls horizontally`);
    check(mobile.heroTitlePx <= 36, `${scenario}: mobile hero title is oversized at ${mobile.heroTitlePx}px`);
    check(mobile.guideBodyPx >= 13.5, `${scenario}: mobile guide text is ${mobile.guideBodyPx}px`);
    check(mobile.glossaryPx >= 12.5, `${scenario}: mobile glossary text is ${mobile.glossaryPx}px`);
    check(mobile.stepBodyPx >= 13, `${scenario}: mobile Step text is ${mobile.stepBodyPx}px`);
    check(mobile.minCardWidth >= 320, `${scenario}: mobile Step cards are too narrow`);
    check(mobile.tldrClauses === 3, `${scenario}: mobile executive TL;DR is incomplete`);
    check(mobile.dependencyRows === desktop.steps, `${scenario}: mobile dependency list omits Steps`);
    check(mobile.dependencyMapWidth <= 368, `${scenario}: mobile dependency map is too wide`);
    check(mobile.tldrWidth <= 368, `${scenario}: mobile executive TL;DR is too wide`);
    check(mobile.heroGridWidth <= 368, `${scenario}: mobile Hero grid is internally oversized`);
    check(mobile.heroTitleWidth >= 320, `${scenario}: mobile Hero title is squeezed to ${mobile.heroTitleWidth}px`);
    check(mobile.heroSubtitleWidth >= 320, `${scenario}: mobile Hero subtitle is squeezed to ${mobile.heroSubtitleWidth}px`);
    check(mobile.heroAsideWidth <= 368, `${scenario}: mobile Hero aside is internally oversized`);
    check(mobile.heroContentRight <= 379, `${scenario}: mobile Hero content is clipped on the right`);
    check(mobile.storageWidth <= 368, `${scenario}: mobile storage view is internally oversized`);
    check(mobile.storageBodyPx >= 13.5, `${scenario}: mobile storage text is ${mobile.storageBodyPx}px`);
    check(
      mobileArtifact.headingTop > mobileArtifact.runbarBottom,
      `${scenario}: Artifact heading is hidden behind the mobile Run bar`,
    );
    check(mobileArtifact.tableOverflow > 0, `${scenario}: mobile Artifact table is not scrollable`);
    check(mobileArtifact.hintDisplay !== "none", `${scenario}: mobile Artifact scroll hint is hidden`);
    check(
      mobile.traceHeadingTop > mobile.runbarBottom,
      `${scenario}: tab navigation hides the active heading behind the sticky Run bar`,
    );
    if (scenario === "opencrew-video") {
      check(desktop.usageRows === 17, `${scenario}: expected 17 usage observations`);
      check(desktop.agentRefs === 2, `${scenario}: expected two visible Agent invocations`);
      check(
        new Set(desktop.agentExecutionIds).size === 1,
        `${scenario}: Agent invocations are not grouped by one execution ID`,
      );
      check(desktop.agentLimitPills === 2, `${scenario}: Agent max-turn/tool-call limits are not visible`);
    }

    report.push({
      scenario,
      errors,
      runCount,
      priorRunLabel,
      latestRunLabel,
      detailTitle,
      graphDetailTitle,
      tabs: tabState,
      desktop,
      mobile,
    });
  }

  const landingErrors = [];
  const landingUrl = pathToFileURL(path.join(demoRoot, "index.html")).href;
  const landingDesktopContext = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
    deviceScaleFactor: 1,
  });
  const landingDesktopPage = await landingDesktopContext.newPage();
  observeErrors(landingDesktopPage, landingErrors);
  await landingDesktopPage.goto(landingUrl, { waitUntil: "load" });
  await landingDesktopPage.screenshot({
    path: path.join(outputDir, "demos-landing-desktop.png"),
    fullPage: true,
  });
  const landingDesktop = await landingDesktopPage.evaluate(() => ({
    colorScheme: getComputedStyle(document.documentElement).colorScheme,
    bodyOverflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    heroTitlePx: parseFloat(getComputedStyle(document.querySelector("h1")).fontSize),
    introPx: parseFloat(getComputedStyle(document.querySelector("header > p:not(.eyebrow)")).fontSize),
    cardBodyPx: parseFloat(getComputedStyle(document.querySelector(".card p")).fontSize),
    cards: document.querySelectorAll(".card").length,
  }));
  await landingDesktopContext.close();

  const landingMobileContext = await browser.newContext({
    viewport: { width: 390, height: 844 },
    deviceScaleFactor: 1,
  });
  const landingMobilePage = await landingMobileContext.newPage();
  observeErrors(landingMobilePage, landingErrors);
  await landingMobilePage.goto(landingUrl, { waitUntil: "load" });
  await landingMobilePage.screenshot({
    path: path.join(outputDir, "demos-landing-mobile.png"),
    fullPage: true,
  });
  const landingMobile = await landingMobilePage.evaluate(() => ({
    colorScheme: getComputedStyle(document.documentElement).colorScheme,
    bodyOverflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    heroTitlePx: parseFloat(getComputedStyle(document.querySelector("h1")).fontSize),
    cardBodyPx: parseFloat(getComputedStyle(document.querySelector(".card p")).fontSize),
  }));
  await landingMobileContext.close();

  check(landingErrors.length === 0, `demo landing: browser errors: ${landingErrors.join(" | ")}`);
  check(landingDesktop.colorScheme === "light", "demo landing: desktop is not light");
  check(landingDesktop.bodyOverflow === 0, `demo landing: desktop overflows by ${landingDesktop.bodyOverflow}px`);
  check(landingDesktop.heroTitlePx <= 56, `demo landing: desktop title is ${landingDesktop.heroTitlePx}px`);
  check(landingDesktop.introPx >= 16, `demo landing: intro is ${landingDesktop.introPx}px`);
  check(landingDesktop.cardBodyPx >= 14, `demo landing: card text is ${landingDesktop.cardBodyPx}px`);
  check(landingDesktop.cards === 4, `demo landing: expected four cards, found ${landingDesktop.cards}`);
  check(landingMobile.colorScheme === "light", "demo landing: mobile is not light");
  check(landingMobile.bodyOverflow === 0, `demo landing: mobile overflows by ${landingMobile.bodyOverflow}px`);
  check(landingMobile.heroTitlePx <= 36, `demo landing: mobile title is ${landingMobile.heroTitlePx}px`);
  check(landingMobile.cardBodyPx >= 14, `demo landing: mobile card text is ${landingMobile.cardBodyPx}px`);
  landingReport = { errors: landingErrors, desktop: landingDesktop, mobile: landingMobile };

  const overviewErrors = [];
  const overviewUrl = pathToFileURL(path.join(demoRoot, "..", "index.html")).href;
  const overviewDesktopContext = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
    deviceScaleFactor: 1,
  });
  const overviewDesktopPage = await overviewDesktopContext.newPage();
  observeErrors(overviewDesktopPage, overviewErrors);
  await overviewDesktopPage.goto(overviewUrl, { waitUntil: "load" });
  await overviewDesktopPage.waitForSelector(".contract-map");
  await overviewDesktopPage.screenshot({
    path: path.join(outputDir, "overview-desktop-top.png"),
  });
  await overviewDesktopPage.locator("#model").screenshot({
    path: path.join(outputDir, "overview-desktop-model.png"),
  });
  const overviewDesktopTopbarDisplay = await overviewDesktopPage.locator(".topbar-wrap").evaluate((element) => {
    const previous = element.style.display;
    element.style.display = "none";
    return previous;
  });
  await overviewDesktopPage.locator(".relationship-atlas").screenshot({
    path: path.join(outputDir, "overview-desktop-relationships.png"),
  });
  await overviewDesktopPage.locator("#ai-governance").screenshot({
    path: path.join(outputDir, "overview-desktop-ai-governance.png"),
  });
  await overviewDesktopPage.locator("#status").screenshot({
    path: path.join(outputDir, "overview-desktop-build-stages.png"),
  });
  await overviewDesktopPage.locator(".topbar-wrap").evaluate(
    (element, previous) => { element.style.display = previous; },
    overviewDesktopTopbarDisplay,
  );
  await overviewDesktopPage.locator("#run-controls").screenshot({
    path: path.join(outputDir, "overview-desktop-run-controls.png"),
  });
  await overviewDesktopPage.locator("#storage-logging").screenshot({
    path: path.join(outputDir, "overview-desktop-storage-logging.png"),
  });
  await overviewDesktopPage.locator("#demos").screenshot({
    path: path.join(outputDir, "overview-desktop-demos.png"),
  });
  const overviewDesktop = await overviewDesktopPage.evaluate(() => ({
    colorScheme: getComputedStyle(document.documentElement).colorScheme,
    bodyOverflow:
      document.documentElement.scrollWidth - document.documentElement.clientWidth,
    heroTitlePx: parseFloat(getComputedStyle(document.querySelector("h1")).fontSize),
    sectionTitlePx: parseFloat(getComputedStyle(document.querySelector(".section-head h2")).fontSize),
    sectionBodyPx: parseFloat(getComputedStyle(document.querySelector(".section-head > p")).fontSize),
    cardBodyPx: parseFloat(getComputedStyle(document.querySelector(".value-card p")).fontSize),
    runControlBodyPx: parseFloat(getComputedStyle(document.querySelector(".control-card p")).fontSize),
    docCards: document.querySelectorAll(".doc-card").length,
    demoPages: new Set(
      [...document.querySelectorAll('a[href^="demos/"][href$="/index.html"]')]
        .map((element) => element.getAttribute("href"))
        .filter((href) => href.split("/").length === 3),
    ).size,
    statusCards: document.querySelectorAll(".status-card").length,
    statusSummaries: document.querySelectorAll(".status-summary").length,
    statusLimits: document.querySelectorAll(".status-limit").length,
    statusActions: document.querySelectorAll(".status-action").length,
    statusLabels: [...document.querySelectorAll(".status-badge")].map((element) =>
      element.textContent.trim()
    ),
    statusSummaryPx: parseFloat(
      getComputedStyle(document.querySelector(".status-summary")).fontSize,
    ),
    statusLimitPx: parseFloat(
      getComputedStyle(document.querySelector(".status-limit")).fontSize,
    ),
    aiPlanes: document.querySelectorAll(".ai-plane").length,
    aiFactItems: document.querySelectorAll(".ai-facts span").length,
    aiAuthorityCards: document.querySelectorAll(".authority-card").length,
    aiBodyPx: parseFloat(
      getComputedStyle(document.querySelector(".ai-plane > p")).fontSize,
    ),
    hasPlainAiExplanation: document.body.textContent.includes(
      "一次 AI 调用：先检查权限，再执行，最后逐笔记账",
    ),
    hasLegacyFraming: [
      "全文强制区分",
      "implemented / partial",
      "proposed / executable",
      "现状与目标严格分层",
    ].some((phrase) => document.body.textContent.includes(phrase)),
    roadmapSteps: document.querySelectorAll(".roadmap-step").length,
    runControlCards: document.querySelectorAll(".control-card").length,
    branchNodes: document.querySelectorAll(".branch-node").length,
    storageCards: document.querySelectorAll(".storage-overview-card").length,
    storageAuthorityRows: document.querySelectorAll(".authority-row").length,
    storageLogLevels: document.querySelectorAll(".log-level-strip span").length,
    storageBodyPx: parseFloat(
      getComputedStyle(document.querySelector(".storage-overview-card > p")).fontSize,
    ),
    relationshipAtlases: document.querySelectorAll(".relationship-atlas").length,
    relationshipLegends: document.querySelectorAll(".relation-legend-item").length,
    relationshipNodes: document.querySelectorAll(".relation-node").length,
    relationshipEvidence: document.querySelectorAll(".relation-evidence").length,
    relationshipBodyPx: parseFloat(
      getComputedStyle(document.querySelector(".relation-node span")).fontSize,
    ),
    relationshipOverflow:
      document.querySelector(".relationship-atlas").scrollWidth -
      document.querySelector(".relationship-atlas").clientWidth,
  }));
  await overviewDesktopContext.close();

  const overviewMobileContext = await browser.newContext({
    viewport: { width: 390, height: 844 },
    deviceScaleFactor: 1,
  });
  const overviewMobilePage = await overviewMobileContext.newPage();
  observeErrors(overviewMobilePage, overviewErrors);
  await overviewMobilePage.goto(overviewUrl, { waitUntil: "load" });
  await overviewMobilePage.waitForSelector(".contract-map");
  await overviewMobilePage.screenshot({
    path: path.join(outputDir, "overview-mobile-top.png"),
  });
  await overviewMobilePage.locator("#model").scrollIntoViewIfNeeded();
  await overviewMobilePage.waitForTimeout(250);
  await overviewMobilePage.screenshot({
    path: path.join(outputDir, "overview-mobile-model.png"),
  });
  const priorTopbarDisplay = await overviewMobilePage.locator(".topbar-wrap").evaluate((element) => {
    const previous = element.style.display;
    element.style.display = "none";
    return previous;
  });
  await overviewMobilePage.locator(".relationship-atlas").screenshot({
    path: path.join(outputDir, "overview-mobile-relationships.png"),
  });
  await overviewMobilePage.locator("#run-controls").screenshot({
    path: path.join(outputDir, "overview-mobile-run-controls.png"),
  });
  await overviewMobilePage.locator("#storage-logging").screenshot({
    path: path.join(outputDir, "overview-mobile-storage-logging.png"),
  });
  await overviewMobilePage.locator("#ai-governance").screenshot({
    path: path.join(outputDir, "overview-mobile-ai-governance.png"),
  });
  await overviewMobilePage.locator("#status").screenshot({
    path: path.join(outputDir, "overview-mobile-build-stages.png"),
  });
  await overviewMobilePage.locator(".topbar-wrap").evaluate(
    (element, previous) => { element.style.display = previous; },
    priorTopbarDisplay,
  );
  const overviewMobile = await overviewMobilePage.evaluate(() => {
    const heroTitle = document.querySelector("h1");
    const map = document.querySelector(".contract-map");
    return {
      colorScheme: getComputedStyle(document.documentElement).colorScheme,
      bodyOverflow:
        document.documentElement.scrollWidth - document.documentElement.clientWidth,
      heroTitlePx: parseFloat(getComputedStyle(heroTitle).fontSize),
      cardBodyPx: parseFloat(getComputedStyle(document.querySelector(".value-card p")).fontSize),
      heroTitleOverflow: heroTitle.scrollWidth - heroTitle.clientWidth,
      mapRight: Math.ceil(map.getBoundingClientRect().right),
      mapWidth: Math.round(map.getBoundingClientRect().width),
      topbarWidth: Math.round(
        document.querySelector(".topbar").getBoundingClientRect().width,
      ),
      relationshipWidth: Math.round(
        document.querySelector(".relationship-atlas").getBoundingClientRect().width,
      ),
      relationshipOverflow:
        document.querySelector(".relationship-atlas").scrollWidth -
        document.querySelector(".relationship-atlas").clientWidth,
      relationshipBodyPx: parseFloat(
        getComputedStyle(document.querySelector(".relation-node span")).fontSize,
      ),
      aiBodyPx: parseFloat(
        getComputedStyle(document.querySelector(".ai-plane > p")).fontSize,
      ),
      statusSummaryPx: parseFloat(
        getComputedStyle(document.querySelector(".status-summary")).fontSize,
      ),
      statusLimitPx: parseFloat(
        getComputedStyle(document.querySelector(".status-limit")).fontSize,
      ),
      relationshipFlowColumns: getComputedStyle(
        document.querySelector(".relation-flow"),
      ).gridTemplateColumns,
    };
  });
  await overviewMobileContext.close();

  check(
    overviewErrors.length === 0,
    `overview: browser console/page errors: ${overviewErrors.join(" | ")}`,
  );
  check(
    overviewDesktop.bodyOverflow === 0,
    `overview: desktop overflows by ${overviewDesktop.bodyOverflow}px`,
  );
  check(overviewDesktop.colorScheme === "light", "overview: desktop is not using the light document theme");
  check(overviewDesktop.heroTitlePx <= 56, `overview: desktop title is oversized at ${overviewDesktop.heroTitlePx}px`);
  check(overviewDesktop.sectionTitlePx <= 42, `overview: section title is oversized at ${overviewDesktop.sectionTitlePx}px`);
  check(overviewDesktop.sectionBodyPx >= 15, `overview: section body is ${overviewDesktop.sectionBodyPx}px`);
  check(overviewDesktop.cardBodyPx >= 14, `overview: card body is ${overviewDesktop.cardBodyPx}px`);
  check(overviewDesktop.runControlBodyPx >= 13, `overview: run-control body is ${overviewDesktop.runControlBodyPx}px`);
  check(overviewDesktop.docCards === 13, "overview: expected 13 chapter cards");
  check(overviewDesktop.demoPages === 4, "overview: expected links to four scenario pages");
  check(overviewDesktop.statusCards === 3, "overview: implementation status framing is incomplete");
  check(overviewDesktop.statusSummaries === 3, "overview: build-stage summaries are incomplete");
  check(overviewDesktop.statusLimits === 3, "overview: build-stage boundaries are incomplete");
  check(overviewDesktop.statusActions === 3, "overview: build-stage actions are incomplete");
  check(
    JSON.stringify(overviewDesktop.statusLabels) ===
      JSON.stringify(["可直接复用", "团队统一目标", "上线前补齐"]),
    `overview: unclear build-stage labels: ${overviewDesktop.statusLabels.join(" / ")}`,
  );
  check(overviewDesktop.statusSummaryPx >= 13.5, `overview: build-stage summary is ${overviewDesktop.statusSummaryPx}px`);
  check(overviewDesktop.statusLimitPx >= 12, `overview: build-stage boundary is ${overviewDesktop.statusLimitPx}px`);
  check(overviewDesktop.aiPlanes === 2, "overview: AI pre-call/post-call explanation is incomplete");
  check(overviewDesktop.aiFactItems === 12, "overview: AI governance facts are incomplete");
  check(overviewDesktop.aiAuthorityCards === 3, "overview: AI authority explanation is incomplete");
  check(overviewDesktop.aiBodyPx >= 13.5, `overview: AI governance body is ${overviewDesktop.aiBodyPx}px`);
  check(overviewDesktop.hasPlainAiExplanation, "overview: plain-language AI call explanation is missing");
  check(!overviewDesktop.hasLegacyFraming, "overview: legacy document-maintenance framing is still visible");
  check(overviewDesktop.roadmapSteps === 6, "overview: roadmap dependency chain is incomplete");
  check(overviewDesktop.runControlCards === 4, "overview: four run-control intents are incomplete");
  check(overviewDesktop.branchNodes === 4, "overview: fork/join explanation is incomplete");
  check(overviewDesktop.storageCards === 2, "overview: storage/logging explanation is incomplete");
  check(overviewDesktop.storageAuthorityRows === 4, "overview: authority planes are incomplete");
  check(overviewDesktop.storageLogLevels === 5, "overview: diagnostic levels are incomplete");
  check(overviewDesktop.storageBodyPx >= 13.5, `overview: storage body is ${overviewDesktop.storageBodyPx}px`);
  check(overviewDesktop.relationshipAtlases === 1, "overview: authoritative relationship map is missing");
  check(overviewDesktop.relationshipLegends === 4, "overview: relationship legend is incomplete");
  check(overviewDesktop.relationshipNodes === 4, "overview: execution containment chain is incomplete");
  check(overviewDesktop.relationshipEvidence === 6, "overview: evidence associations are incomplete");
  check(overviewDesktop.relationshipBodyPx >= 13, `overview: relationship body is ${overviewDesktop.relationshipBodyPx}px`);
  check(overviewDesktop.relationshipOverflow === 0, `overview: relationship map overflows by ${overviewDesktop.relationshipOverflow}px`);
  check(
    overviewMobile.bodyOverflow === 0,
    `overview: mobile overflows by ${overviewMobile.bodyOverflow}px`,
  );
  check(overviewMobile.colorScheme === "light", "overview: mobile is not using the light document theme");
  check(overviewMobile.heroTitlePx <= 36, `overview: mobile title is oversized at ${overviewMobile.heroTitlePx}px`);
  check(overviewMobile.cardBodyPx >= 14, `overview: mobile card body is ${overviewMobile.cardBodyPx}px`);
  check(overviewMobile.heroTitleOverflow === 0, "overview: mobile hero title is internally clipped");
  check(overviewMobile.mapRight <= 379, "overview: mobile contract map is clipped on the right");
  check(overviewMobile.mapWidth <= 368, "overview: mobile contract map is internally oversized");
  check(overviewMobile.topbarWidth <= 368, "overview: mobile topbar is internally oversized");
  check(overviewMobile.relationshipWidth <= 368, "overview: mobile relationship map is internally oversized");
  check(overviewMobile.relationshipOverflow === 0, `overview: mobile relationship map overflows by ${overviewMobile.relationshipOverflow}px`);
  check(overviewMobile.relationshipBodyPx >= 13, `overview: mobile relationship body is ${overviewMobile.relationshipBodyPx}px`);
  check(overviewMobile.aiBodyPx >= 13.5, `overview: mobile AI governance body is ${overviewMobile.aiBodyPx}px`);
  check(overviewMobile.statusSummaryPx >= 13.5, `overview: mobile build-stage summary is ${overviewMobile.statusSummaryPx}px`);
  check(overviewMobile.statusLimitPx >= 12, `overview: mobile build-stage boundary is ${overviewMobile.statusLimitPx}px`);
  check(!overviewMobile.relationshipFlowColumns.includes(" "), "overview: mobile execution chain did not stack to one column");
  overviewReport = {
    errors: overviewErrors,
    desktop: overviewDesktop,
    mobile: overviewMobile,
  };
} finally {
  await browser.close();
}

const result = {
  ok: violations.length === 0,
  outputDir,
  screenshots: scenarios.length * 14 + 17,
  violations,
  landing: landingReport,
  overview: overviewReport,
  scenarios: report,
};
writeFileSync(
  path.join(outputDir, "summary.json"),
  `${JSON.stringify(result, null, 2)}\n`,
  "utf8",
);
console.log(JSON.stringify(result, null, 2));
if (violations.length) process.exitCode = 1;
