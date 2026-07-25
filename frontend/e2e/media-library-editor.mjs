import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const scriptDir = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(scriptDir, "../..");
const baseUrl = (process.env.OPENCREW_FRONTEND_URL || "http://127.0.0.1:18080").replace(/\/$/, "");
const assetId = String(process.env.MEDIA_LIBRARY_EDITOR_ASSET_ID || "").trim();
assert.ok(
  assetId,
  "MEDIA_LIBRARY_EDITOR_ASSET_ID is required; this E2E deliberately does not mock GET editor or insert fake browser data.",
);
const allowMutation = process.env.MEDIA_LIBRARY_EDITOR_E2E_ALLOW_MUTATION === "1";
const importCreatedClip = process.env.MEDIA_LIBRARY_EDITOR_E2E_IMPORT === "1";
const cancelCreatedJob = process.env.MEDIA_LIBRARY_EDITOR_E2E_CANCEL === "1";
const runSemanticSearch = process.env.MEDIA_LIBRARY_EDITOR_E2E_SEARCH === "1";
const runExternalSearch = process.env.MEDIA_LIBRARY_EDITOR_E2E_EXTERNAL === "1";
const importExternalCandidate = process.env.MEDIA_LIBRARY_EDITOR_E2E_EXTERNAL_IMPORT === "1";
const checkStoryboardReturn = process.env.MEDIA_LIBRARY_EDITOR_E2E_STORYBOARD_RETURN === "1";
assert.ok(
  !importExternalCandidate || runExternalSearch,
  "MEDIA_LIBRARY_EDITOR_E2E_EXTERNAL_IMPORT=1 requires MEDIA_LIBRARY_EDITOR_E2E_EXTERNAL=1",
);
const browser = await chromium.launch({ headless: process.env.HEADED !== "1" });
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
const envFile = readEnvFile(resolve(repoRoot, ".opencrew-e2e-auth.env"));
const adminPassword = process.env.OPENCREW_E2E_ADMIN_PASSWORD || envFile.OPENCREW_E2E_ADMIN_PASSWORD || "";
if (adminPassword) {
  const login = await context.request.post(`${baseUrl}/api/auth/login`, { data: { password: adminPassword } });
  assert.equal(login.status(), 200, `admin login failed: HTTP ${login.status()}`);
}
const capabilitiesResponse = await context.request.get(
  `${baseUrl}/api/media-library/capabilities`,
);
assert.equal(capabilitiesResponse.status(), 200);
const capabilitiesPayload = await capabilitiesResponse.json();
assert.equal(
  capabilitiesPayload.schema_version,
  "media_library_capabilities_v1",
);
for (const key of ["analysis_runs", "library_search", "visual_semantic", "composite", "editor"]) {
  assert.equal(typeof capabilitiesPayload.features?.[key]?.enabled, "boolean");
  assert.equal(
    typeof capabilitiesPayload.features?.[key]?.configuration_valid,
    "boolean",
  );
}
const page = await context.newPage();
const storyboardContext = checkStoryboardReturn || runExternalSearch || importCreatedClip
  ? await findStoryboardContext(context, baseUrl)
  : null;
const editorResponses = [];
page.on("response", (response) => {
  if (/\/api\/media-library\/[^/]+\/editor(?:\?|$)/.test(response.url())) editorResponses.push(response);
});

try {
  const query = new URLSearchParams({
    start_ms: process.env.MEDIA_LIBRARY_EDITOR_START_MS || "599000",
    end_ms: process.env.MEDIA_LIBRARY_EDITOR_END_MS || "600000",
    return_to: "media_library_detail",
  });
  if (storyboardContext) {
    query.set("target_task_id", String(storyboardContext.taskId));
    query.set("dialogue_asset_key", storyboardContext.dialogueAssetKey);
    query.set("return_to", "storyboard_dialogue");
  }
  await page.goto(`${baseUrl}/#/media-library/${encodeURIComponent(assetId)}/editor?${query}`, { waitUntil: "domcontentloaded" });
  await page.locator(".ml-editor-shell").waitFor({ timeout: 20_000 });
  assert.ok(editorResponses.length > 0, "browser must fetch the real GET editor DTO");
  assert.equal(editorResponses.at(-1).status(), 200);
  await page.locator(".ml-editor-timeline-panel").waitFor();
  assert.equal(await page.locator(".ml-editor-track.source").count(), 1);
  assert.equal(await page.locator(".ml-editor-track.dialogue").count(), 1);
  assert.equal(await page.locator(".ml-editor-track.visual").count(), 1);
  assert.equal(await page.locator(".ml-editor-track.composite").count(), 1);
  assert.deepEqual(
    await page.locator(".ml-editor-track").evaluateAll((tracks) => tracks.map((track) => (
      ["composite", "dialogue", "visual", "source"].find((name) => track.classList.contains(name))
    ))),
    ["composite", "dialogue", "visual", "source"],
    "timeline must order C/D/V evidence before the source track",
  );

  const payload = await editorResponses.at(-1).json();
  if (storyboardContext) {
    assert.equal(
      payload.navigation_context?.target_valid,
      true,
      "editor must revalidate the real StoryBoard target",
    );
    assert.equal(
      payload.navigation_context?.dialogue_valid,
      true,
      "editor must validate the same authoritative StoryBoard plan returned by task detail",
    );
  }
  const totalFragments = ["dialogue", "visual", "composite"]
    .reduce((total, scheme) => total + (payload.fragments?.[scheme]?.length || 0), 0);
  const publicHeaderMetadata = await page.locator(".ml-editor-header p").innerText();
  assert.match(publicHeaderMetadata, new RegExp(`\\b${totalFragments}\\b`));
  assert.doesNotMatch(publicHeaderMetadata, /source\s+[a-f0-9]+/i);
  assert.equal(await page.locator(".ml-editor-technical-details").getAttribute("open"), null);
  const renderedFragments = await page.locator(".ml-editor-timeline-canvas .ml-editor-fragment").count();
  assert.ok(renderedFragments <= totalFragments, "timeline DOM must be windowed");

  const durationMs = Number(payload.item?.duration_ms);
  assert.equal(
    durationMs,
    600_000,
    "editor release gate must use the exact 10-minute representative asset",
  );
  const startMs = Math.max(0, durationMs - 1_000);
  await page.getByLabel("入点 ms").fill(String(startMs));
  await page.getByLabel("入点 ms").press("Tab");
  await page.getByLabel("出点 ms").fill(String(durationMs));
  await page.getByLabel("出点 ms").press("Tab");
  assert.equal(await page.getByLabel("入点时间码").innerText(), "09:59.000");
  assert.equal(await page.getByLabel("出点时间码").innerText(), "10:00.000");
  await page.locator(".ml-editor-source-summary").getByText("总时长 10:00.000").waitFor();
  assert.equal(await page.getByText("窗口化渲染", { exact: false }).count(), 0);
  assert.equal(await page.getByText("主刻度", { exact: false }).count(), 0);
  await page.getByRole("button", { name: "预览选区" }).waitFor();
  const selectionBeforeZoom = {
    start: await page.getByLabel("入点 ms").inputValue(),
    end: await page.getByLabel("出点 ms").inputValue(),
  };
  await page.getByLabel("时间轴缩放").fill("90");
  await page.waitForFunction(() => {
    const viewport = document.querySelector(
      ".ml-editor-timeline-viewport",
    );
    const canvas = document.querySelector(
      ".ml-editor-timeline-canvas",
    );
    return Boolean(
      viewport
      && canvas
      && canvas.getBoundingClientRect().width
        > viewport.getBoundingClientRect().width,
    );
  });
  const zoomedGeometry = await page.locator(
    ".ml-editor-timeline-viewport",
  ).evaluate((viewport) => {
    viewport.scrollLeft = Math.max(
      1,
      viewport.scrollWidth - viewport.clientWidth - 2,
    );
    viewport.dispatchEvent(new Event("scroll", { bubbles: true }));
    return {
      scrollLeft: viewport.scrollLeft,
      scrollWidth: viewport.scrollWidth,
      clientWidth: viewport.clientWidth,
    };
  });
  assert.ok(zoomedGeometry.scrollLeft > 0);
  assert.ok(
    zoomedGeometry.scrollWidth > zoomedGeometry.clientWidth,
  );
  assert.equal(
    await page.getByLabel("入点 ms").inputValue(),
    selectionBeforeZoom.start,
  );
  assert.equal(
    await page.getByLabel("出点 ms").inputValue(),
    selectionBeforeZoom.end,
  );
  assert.ok(
    await page.locator(
      ".ml-editor-timeline-canvas .ml-editor-fragment",
    ).count() <= totalFragments,
    "zoomed/scroll timeline must keep fragment DOM windowed",
  );
  await page.getByRole("button", { name: "适应窗口" }).click();
  await page.waitForFunction(() => (
    document.querySelector(".ml-editor-timeline-viewport")
      ?.scrollLeft === 0
  ));
  assert.equal(
    await page.getByLabel("入点 ms").inputValue(),
    selectionBeforeZoom.start,
  );
  assert.equal(
    await page.getByLabel("出点 ms").inputValue(),
    selectionBeforeZoom.end,
  );

  const fragmentEntries = Object.entries(payload.fragments || {})
    .flatMap(([scheme, fragments]) => (
      Array.isArray(fragments)
        ? fragments.map((fragment) => ({ scheme, fragment }))
        : []
    ));
  const staleRunByTrack = {
    dialogue: payload.runs?.dialogue?.status === "stale",
    visual: (
      payload.runs?.visual_semantic?.status === "stale"
      || (
        !payload.runs?.visual_semantic
        && payload.runs?.visual_structure?.status === "stale"
      )
    ),
    composite: payload.runs?.composite?.status === "stale",
  };
  const staleFragment = fragmentEntries.find(({ scheme, fragment }) => (
      fragment.stale || fragment.status === "stale"
      || staleRunByTrack[scheme]
    ));
  if (staleFragment) {
    const staleButton = page.locator(".ml-editor-fragment-index article.stale > button").first();
    await staleButton.click();
    await page.getByText(/stale 分析/).waitFor();
    await page.getByRole("button", { name: "转换为手动范围" }).click();
    await page.getByText(/原 fragment\/run 身份已清除/).waitFor();
  }

  const eligibleSearchToggle = page.locator(
    ".ml-editor-fragment-index article > button:last-child:not(:disabled)",
  ).first();
  if (await eligibleSearchToggle.count()) {
    assert.equal((await eligibleSearchToggle.innerText()).trim(), "加入检索");
    await eligibleSearchToggle.click();
    assert.equal((await eligibleSearchToggle.innerText()).trim(), "移出检索");
    await eligibleSearchToggle.click();
  }

  if (runSemanticSearch || runExternalSearch) {
    await page.getByRole("button", { name: "素材检索" }).click();
    assert.equal(await page.getByRole("button", { name: "清空", exact: true }).count(), 0);
    if (runExternalSearch) {
      assert.ok(storyboardContext, "external editor search requires a real StoryBoard context");
      const externalSource = page.getByLabel("外部素材");
      await externalSource.check();
    }
    await page.getByLabel("补充关键词/要求").fill(process.env.MEDIA_LIBRARY_EDITOR_E2E_QUERY || "产品防水能力 竖屏");
    await page.getByRole("button", { name: "开始检索" }).click();
    await page.locator(".ml-editor-search-summary").waitFor({ timeout: 90_000 });
    await page.getByText(/主要基于对白原句与关键词的确定性召回/).waitFor();
    assert.equal(
      await page.locator(".ml-editor-search-summary details").getAttribute("open"),
      null,
      "Search ID technical details must be collapsed by default",
    );
    assert.doesNotMatch(
      await page.locator(".ml-editor-search-summary").innerText(),
      /mls_[A-Za-z0-9._:-]+/,
      "collapsed search summary must not expose Search ID",
    );
    const externalCards = page.locator(".ml-editor-candidate.external");
    let externalImportChecked = false;
    if (runExternalSearch) {
      assert.ok(
        await externalCards.count() > 0,
        "external E2E requires at least one real provider candidate; zero candidates cannot validate metadata, license confirmation, and button differences",
      );
    }
    for (let index = 0; index < await externalCards.count(); index += 1) {
      const card = externalCards.nth(index);
      assert.doesNotMatch(
        (await card.locator("small").allInnerTexts()).join(" "),
        /(?:text embedding|provider relevance|\b(?:portrait|landscape|square) ratio\b|license metadata confirmed|provider-specific fallback query)/i,
        "external score reasons must use accurate Chinese presentation labels",
      );
      assert.equal(
        await card.getByRole("button", { name: "打开其剪辑页" }).count(),
        0,
        "external candidate must never expose editor action",
      );
      await card.getByText("Provider", { exact: true }).waitFor();
      await card.getByText("License", { exact: true }).waitFor();
      const importWhole = card.getByRole("button", { name: "整条导入" });
      assert.equal(await importWhole.isDisabled(), true, "external import must start disabled before license confirmation");
      const initialTitle = await importWhole.getAttribute("title") || "";
      const importSupported = initialTitle === "请先显式确认 license";
      const licenseConfirmation = card.getByLabel(/我已阅读并确认/);
      await licenseConfirmation.check();
      if (importSupported) {
        const importHandle = await importWhole.elementHandle();
        assert.ok(importHandle, "supported external import button disappeared after license confirmation");
        await page.waitForFunction(
          (button) => !button.disabled,
          importHandle,
          { timeout: 5_000 },
        );
        assert.equal(await importWhole.isEnabled(), true, "explicit license confirmation must unlock supported external import");
        if (importExternalCandidate && !externalImportChecked) {
          const importResponsePromise = page.waitForResponse(
            (response) => (
              response.request().method() === "POST"
              && /\/api\/koubo-storyboard\/tasks\/\d+\/asset-library-search\/import$/.test(
                new URL(response.url()).pathname,
              )
            ),
            { timeout: 180_000 },
          );
          await importWhole.click();
          const importResponse = await importResponsePromise;
          assert.equal(importResponse.status(), 200);
          const importRequest = importResponse.request().postDataJSON();
          assert.equal(importRequest?.confirm_license, true);
          assert.equal(
            importRequest?.candidate_ids?.length,
            1,
          );
          await page.getByText(
            new RegExp(`“${escapeRegExp(
              await card.locator("h4").innerText(),
            )}”已导入目标 StoryBoard`),
          ).waitFor({ timeout: 180_000 });
          externalImportChecked = true;
        }
      } else {
        assert.equal(
          await importWhole.isDisabled(),
          true,
          "unsupported external candidate must remain non-importable after license confirmation",
        );
      }
    }
    const internalCards = page.locator(".ml-editor-candidate.media_library");
    if (runSemanticSearch) {
      assert.ok(
        await internalCards.count() > 0,
        "semantic E2E requires a real internal candidate; zero candidates cannot validate the open-editor action",
      );
    }
    if (importExternalCandidate) {
      assert.equal(
        externalImportChecked,
        true,
        "no real external candidate supported download/import after license confirmation",
      );
    }
    for (let index = 0; index < await internalCards.count(); index += 1) {
      assert.ok(
        await internalCards.nth(index).getByRole("button", { name: "打开其剪辑页" }).count() <= 1,
      );
    }
  }

  if (allowMutation) {
    const clipName = `E2E 尾部 ${Date.now()}`;
    if (cancelCreatedJob) {
      await page.getByLabel("入点 ms").fill("0");
      await page.getByLabel("入点 ms").press("Tab");
      await page.getByLabel("出点 ms").fill(String(durationMs));
      await page.getByLabel("出点 ms").press("Tab");
    }
    await page.getByLabel("片段名称").fill(clipName);
    await page.getByRole("button", { name: "创建剪切任务" }).click();
    await page.getByRole("button", { name: "派生片段" }).click();
    await page.locator(".ml-editor-job").waitFor();
    if (cancelCreatedJob) {
      const cancel = page.getByRole("button", { name: "取消剪切" });
      await cancel.waitFor({ timeout: 10_000 });
      await cancel.click();
      await page.locator(".ml-editor-job.cancelled").waitFor({ timeout: 20_000 });
    } else {
      await page.locator(".ml-editor-job.completed").waitFor({ timeout: 120_000 });
      const clipCard = page.locator(".ml-editor-clip").filter({ hasText: clipName });
      await clipCard.waitFor({ timeout: 20_000 });
      if (importCreatedClip) {
        assert.ok(storyboardContext, "clip import E2E requires a real StoryBoard import target");
        await page.locator(".ml-editor-header-context select").selectOption(String(storyboardContext.taskId));
        await clipCard.getByRole("button", { name: "导入 StoryBoard" }).click();
        await page.getByText(/已导入目标 StoryBoard/).waitFor({ timeout: 60_000 });
      } else {
        page.once("dialog", (dialog) => dialog.accept());
        await clipCard.getByRole("button", { name: "删除" }).click();
        await clipCard.waitFor({ state: "detached", timeout: 20_000 });
      }
    }
  }

  if (checkStoryboardReturn) {
    assert.ok(storyboardContext, "StoryBoard return E2E requires a real task/dialogue context");
    await page.locator(".ml-editor-back").click();
    await page.waitForFunction(
      ({ taskId, dialogueAssetKey }) => window.location.hash === `#/koubo-storyboard/tasks/${taskId}?dialogue_asset_key=${encodeURIComponent(dialogueAssetKey)}`,
      storyboardContext,
      { timeout: 30_000 },
    );
    const selectedDialogue = page.locator(
      `.kbsp-dialogue-card.is-active[data-kbsp-dialogue-asset-key="${storyboardContext.dialogueAssetKey}"]`,
    );
    await selectedDialogue.waitFor({ timeout: 30_000 });
    assert.equal(await page.locator("[data-kbsp-route-notice]").count(), 0);
    await page.evaluate(({ taskId }) => {
      window.location.hash = `#/koubo-storyboard/tasks/${taskId}?dialogue_asset_key=unsafe%2Fkey`;
    }, storyboardContext);
    const unsafeNotice = page.locator("[data-kbsp-route-notice]");
    await unsafeNotice.waitFor({ timeout: 30_000 });
    assert.match(await unsafeNotice.innerText(), /不安全/);
    assert.equal(await page.locator(".kbsp-dialogue-card.is-active").count(), 1, "unsafe key must fall back to one safe default Dialogue");
  }

  console.log(JSON.stringify({
    ok: true,
    asset_id: assetId,
    duration_ms: durationMs,
    total_fragments: totalFragments,
    rendered_fragments: renderedFragments,
    zoom_scroll_checked: true,
    stale_checked: Boolean(staleFragment),
    mutation_checked: allowMutation,
    clip_import_checked: allowMutation && importCreatedClip,
    clip_cancel_checked: allowMutation && cancelCreatedJob,
    semantic_search_checked: runSemanticSearch,
    external_search_checked: runExternalSearch,
    external_import_checked: importExternalCandidate,
    storyboard_return_checked: checkStoryboardReturn,
  }));
} finally {
  await browser.close();
}

function escapeRegExp(value) {
  return String(value).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function readEnvFile(path) {
  if (!existsSync(path)) return {};
  const values = {};
  for (const line of readFileSync(path, "utf8").split(/\r?\n/)) {
    const match = line.match(/^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)\s*$/);
    if (!match) continue;
    let value = match[2].trim();
    if ((value.startsWith("\"") && value.endsWith("\"")) || (value.startsWith("'") && value.endsWith("'"))) {
      value = value.slice(1, -1);
    }
    values[match[1]] = value;
  }
  return values;
}

async function findStoryboardContext(context, baseUrl) {
  const targetsResponse = await context.request.get(
    `${baseUrl}/api/media-library/import-targets/storyboards`,
    { timeout: 15_000 },
  );
  assert.equal(targetsResponse.status(), 200, "failed to load real StoryBoard import targets");
  const targets = (await targetsResponse.json()).items || [];
  for (const target of targets.slice(0, 20)) {
    const taskId = Number(target?.task_id);
    if (!Number.isSafeInteger(taskId) || taskId <= 0) continue;
    let response;
    try {
      response = await context.request.get(
        `${baseUrl}/api/koubo-storyboard/tasks/${taskId}`,
        { timeout: 15_000 },
      );
    } catch {
      continue;
    }
    if (response.status() !== 200) continue;
    const payload = await response.json();
    for (const shot of payload?.plan?.shots || []) {
      for (const scene of shot?.scenes || []) {
        for (const dialogue of scene?.dialogues || []) {
          const dialogueAssetKey = String(dialogue?.dialogue_asset_key || "").trim();
          if (/^[A-Za-z0-9._:-]{1,256}$/.test(dialogueAssetKey)) {
            return { taskId, dialogueAssetKey };
          }
        }
      }
    }
  }
  throw new Error("No real StoryBoard target with a safe dialogue_asset_key is available.");
}
