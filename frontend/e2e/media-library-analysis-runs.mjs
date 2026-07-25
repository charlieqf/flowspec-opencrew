import assert from "node:assert/strict";
import { chromium } from "playwright";
import {
  assertMediaLibraryCapabilities,
  baseUrl,
  loginAdmin,
  poll,
  requiredEnv,
  responseJson,
} from "./media-library-real-helpers.mjs";

const url = baseUrl();
const assetId = requiredEnv("MEDIA_LIBRARY_ANALYSIS_E2E_ASSET_ID");
const requireReady = (
  process.env.MEDIA_LIBRARY_ANALYSIS_E2E_REQUIRE_READY === "1"
);
const runComposite = (
  process.env.MEDIA_LIBRARY_ANALYSIS_E2E_RUN_COMPOSITE !== "0"
);
const browser = await chromium.launch({
  headless: process.env.HEADED !== "1",
});
const context = await browser.newContext({
  viewport: { width: 1440, height: 1000 },
});

try {
  await loginAdmin(context, url);
  await assertMediaLibraryCapabilities(context, url);
  const beforeResponse = await context.request.get(
    `${url}/api/media-library/${encodeURIComponent(assetId)}`,
  );
  assert.equal(beforeResponse.status(), 200);
  const before = await responseJson(
    beforeResponse,
    "media-library detail",
  );
  const task = before?.item?.open_cut || {};
  assert.equal(
    task.visual_structure_status,
    "ready",
    "analysis-runs E2E requires a real asset with a published visual_structure run",
  );

  const page = await context.newPage();
  await page.goto(
    `${url}/#/media-library/${encodeURIComponent(assetId)}`,
    { waitUntil: "domcontentloaded" },
  );
  await page.locator(".media-library-detail-shell").waitFor({
    timeout: 30_000,
  });
  await page.getByRole("tab", { name: /画面分析/ }).click();
  const semanticPanel = page.getByRole("region", {
    name: "视觉语义运行控制",
  });
  await semanticPanel.waitFor();
  const consent = semanticPanel.getByLabel(
    /允许本次(?:运行|分析)向已配置的云端视觉模型发送(?:代表画面| Keyframe 图像)/,
  );
  assert.equal(await consent.isChecked(), false);
  const semanticButton = semanticPanel.getByRole("button", {
    name: /运行视觉语义|重新运行视觉语义/,
  });
  assert.equal(
    await semanticButton.isDisabled(),
    true,
    "visual semantic UI must not send Keyframes before explicit consent",
  );
  await consent.check();
  assert.equal(await consent.isChecked(), true);
  assert.equal(
    await semanticButton.isEnabled(),
    true,
    "explicit visual data-transfer consent must unlock the real run action",
  );
  const runResponsePromise = page.waitForResponse(
    (response) => (
      response.request().method() === "POST"
      && new URL(response.url()).pathname
        === `/api/media-library/${assetId}/analyses/visual/run`
    ),
    { timeout: 30_000 },
  );
  await semanticButton.click();
  const runResponse = await runResponsePromise;
  assert.equal(
    runResponse.request().postDataJSON()
      ?.allow_cloud_visual_data_transfer,
    true,
    "real model-capability gate must be reached after explicit consent",
  );
  assert.equal(
    runResponse.status(),
    200,
    `real visual semantic run was not accepted: HTTP ${runResponse.status()}`,
  );
  const submitted = await responseJson(
    runResponse,
    "visual semantic run",
  );
  assert.match(
    String(submitted.semantic_run_id || ""),
    /^mlar_visual_semantic_/,
  );

  const current = await poll(
    async () => {
      const response = await context.request.get(
        `${url}/api/media-library/${encodeURIComponent(assetId)}/analyses/visual/runs/${encodeURIComponent(submitted.semantic_run_id)}`,
        { timeout: 15_000 },
      );
      assert.equal(response.status(), 200);
      return responseJson(response, "known visual semantic run");
    },
    (payload) => (
      payload?.run?.analysis_run_id === submitted.semantic_run_id
      && ["ready", "blocked", "failed"].includes(payload?.run?.status)
    ),
    {
      timeoutMs: 120_000,
      label: "real visual semantic run terminal state",
    },
  );
  assert.ok(
    ["ready", "blocked", "failed"].includes(current.run.status),
    `unexpected visual semantic terminal status: ${current.run.status}`,
  );
  if (current.run.status === "blocked") {
    assert.ok(
      [
        "cloud_visual_data_transfer_not_authorized",
        "visual_model_configuration_unavailable",
        "visual_model_policy_invalid",
        "model_input_capability_missing",
      ].includes(current.run.error?.code),
      `unexpected structured block: ${JSON.stringify(current.run.error)}`,
    );
    assert.ok(current.run.error?.user_message);
    assert.ok(current.run.error?.suggested_action);
    assert.equal("detail" in current.run.error, false);
    assert.equal("traceback" in current.run.error, false);
  }
  if (requireReady) {
    assert.equal(
      current.run.status,
      "ready",
      `release acceptance requires visual semantic ready: ${JSON.stringify(current.run.error)}`,
    );
  }

  await page.reload({ waitUntil: "domcontentloaded" });
  await page.locator(".media-library-detail-shell").waitFor({
    timeout: 30_000,
  });
  await page.getByRole("tab", { name: /画面分析/ }).click();
  await page.getByRole("region", {
    name: "视觉语义运行控制",
  }).waitFor();
  if (current.run.status === "blocked") {
    await page.locator(
      ".media-library-visual-semantic-message",
    ).waitFor();
  }
  await page.getByRole("tab", { name: /综合分析/ }).click();
  const compositePanel = page.getByRole("region", {
    name: "综合分析运行控制",
  });
  await compositePanel.waitFor();
  await compositePanel.getByText(
    /综合分析只读取当前对白、画面结构和视觉语义的已发布结果/,
  ).waitFor();
  let composite = null;
  if (current.run.status === "ready" && runComposite) {
    const compositeButton = compositePanel.getByRole("button", {
      name: /运行综合分析|重新运行综合分析/,
    });
    assert.equal(
      await compositeButton.isEnabled(),
      true,
      "ready dialogue, structure, and semantic runs must unlock composite",
    );
    const compositeResponsePromise = page.waitForResponse(
      (response) => (
        response.request().method() === "POST"
        && new URL(response.url()).pathname
          === `/api/media-library/${assetId}/analyses/composite/run`
      ),
      { timeout: 30_000 },
    );
    await compositeButton.click();
    const compositeResponse = await compositeResponsePromise;
    assert.equal(
      compositeResponse.status(),
      200,
      `real composite run was not accepted: HTTP ${compositeResponse.status()}`,
    );
    const compositeSubmitted = await responseJson(
      compositeResponse,
      "composite run",
    );
    const compositeRunId = String(
      compositeSubmitted.analysis_run_id || "",
    );
    assert.match(
      compositeRunId,
      /^mlar_composite_/,
    );
    composite = await poll(
      async () => {
        const response = await context.request.get(
          `${url}/api/media-library/${encodeURIComponent(assetId)}/analyses/composite/runs/${encodeURIComponent(compositeRunId)}`,
          { timeout: 15_000 },
        );
        assert.equal(response.status(), 200);
        return responseJson(response, "known composite run");
      },
      (payload) => (
        payload?.run?.analysis_run_id
          === compositeRunId
        && ["ready", "blocked", "failed"].includes(
          payload?.run?.status,
        )
      ),
      {
        timeoutMs: 180_000,
        label: "real composite run terminal state",
      },
    );
    if (requireReady) {
      assert.equal(
        composite.run.status,
        "ready",
        `release acceptance requires composite ready: ${JSON.stringify(composite.run.error)}`,
      );
      assert.ok(
        (composite.items?.length || 0) > 0,
        "real composite ready run must publish at least one fragment",
      );
    }
  }

  console.log(JSON.stringify({
    ok: true,
    asset_id: assetId,
    semantic_run_id: submitted.semantic_run_id,
    terminal_status: current.run.status,
    structured_error_code: current.run.error?.code || null,
    explicit_visual_transfer_consent: true,
    visual_items: current.items?.length || 0,
    require_ready: requireReady,
    composite_requested: runComposite,
    composite_run_id: composite?.run?.analysis_run_id || null,
    composite_terminal_status: composite?.run?.status || null,
    composite_items: composite?.items?.length || 0,
  }));
} finally {
  await browser.close();
}
