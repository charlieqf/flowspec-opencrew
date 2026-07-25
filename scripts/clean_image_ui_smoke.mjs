import fs from "node:fs";

const CDP_URL = process.env.CHROME_DEBUG_URL || "http://127.0.0.1:9224";
const FRONTEND_URL = process.env.OPENCREW_FRONTEND_URL || `http://127.0.0.1:18080/?cleanImageSmoke=${Date.now()}#/koubo-storyboard/tasks/31`;
const SCREENSHOT_PATH = process.env.CLEAN_IMAGE_SMOKE_SCREENSHOT || "/private/tmp/opencrew-clean-image-modal.png";

const PNG_1X1_BASE64 =
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII=";

const task = { id: 31, session_id: 171, status: "ready", workspace_dir: "/tmp/opencrew-clean-image-ui-smoke" };
const baseDialogueId = "scene_001_dialogue_001";
const existingReferenceAsset = {
  id: "SessionOutput/storyboard/assets/images/reference_existing.png",
  path: "SessionOutput/storyboard/assets/images/reference_existing.png",
  filename: "reference_existing.png",
  label: "Existing reference",
  asset_type: "Image",
  kind: "image",
  source: "upload",
};
const uploadedReferencePath = "SessionScratch/CleanImageGenerations/References/reference_uploaded.png";
const basePlan = {
  schema_version: "koubo_storyboard_edit_0.1",
  title: "UI Smoke StoryBoard",
  shots: [
    {
      shot_id: "shot_001",
      shot_name: "Shot 001",
      start: 0,
      end: 2,
      duration: 2,
      scenes: [
        {
          scene_id: "scene_001",
          scene_name: "Scene 001",
          start: 0,
          end: 2,
          duration: 2,
          working_assets: {
            audio: { slot: "Audio_Final", source_type: "", path: "" },
            images: [
              { slot: "Image_01", source_type: "", path: "" },
              { slot: "Image_02", source_type: "", path: "" },
            ],
            video: { slot: "Video_Final", source_type: "", path: "" },
          },
          dialogues: [
            {
              dialogue_id: baseDialogueId,
              scene_id: "scene_001",
              dialogue_index: 1,
              srt_id: "srt_001",
              srt_ids: ["srt_001"],
              dialogue_asset_key: "srt_001",
              text: "这是一条 UI 自动化测试对白",
              start: 0,
              end: 2,
              duration: 2,
              source_image_paths: [],
              image_path: "",
              bound_image_path: "",
              working_assets: {
                audio: { slot: "Audio_Final", source_type: "", path: "" },
                images: [
                  { slot: "Image_01", source_type: "", path: "" },
                  { slot: "Image_02", source_type: "", path: "" },
                ],
                video: { slot: "Video_Final", source_type: "", path: "" },
              },
            },
          ],
        },
      ],
    },
  ],
};

const smokeState = {
  generation: null,
  calls: [],
};

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

function meta(extra = {}) {
  return {
    analysis_session_id: task.session_id,
    source_asset_groups: [],
    uploaded_images: [existingReferenceAsset],
    uploaded_audios: [],
    uploaded_videos: [],
    history_versions: [],
    video_plan_settings: {},
    ...extra,
  };
}

function detail(plan = basePlan, nextMeta = meta()) {
  return { ok: true, task, meta: nextMeta, plan };
}

function jsonBody(value) {
  return Buffer.from(JSON.stringify(value), "utf8").toString("base64");
}

function textBody(value) {
  return Buffer.from(String(value), "utf8").toString("base64");
}

function parseBody(postData) {
  if (!postData) return {};
  try {
    return JSON.parse(postData);
  } catch {
    return {};
  }
}

function updateDialoguePlan(plan, workingPath) {
  const next = clone(plan || basePlan);
  for (const shot of next.shots || []) {
    for (const scene of shot.scenes || []) {
      for (const dialogue of scene.dialogues || []) {
        if (dialogue.dialogue_id !== baseDialogueId) continue;
        const assets = dialogue.working_assets || {};
        const images = Array.isArray(assets.images) ? assets.images : [{ slot: "Image_01" }, { slot: "Image_02" }];
        images[0] = { slot: "Image_01", source_type: "generated", path: workingPath };
        dialogue.working_assets = { ...assets, images };
        dialogue.bound_image_path = workingPath;
      }
    }
  }
  return next;
}

function responseHeaders(contentType) {
  return [
    { name: "Access-Control-Allow-Origin", value: "*" },
    { name: "Content-Type", value: contentType },
  ];
}

class CdpClient {
  constructor(wsUrl) {
    this.ws = new WebSocket(wsUrl);
    this.nextId = 1;
    this.pending = new Map();
    this.listeners = new Map();
  }

  async open() {
    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error("Timed out connecting to Chrome CDP")), 5000);
      this.ws.addEventListener("open", () => {
        clearTimeout(timer);
        resolve();
      }, { once: true });
      this.ws.addEventListener("error", reject, { once: true });
      this.ws.addEventListener("message", (event) => this.handleMessage(event));
    });
  }

  handleMessage(event) {
    const message = JSON.parse(event.data);
    if (message.id && this.pending.has(message.id)) {
      const { resolve, reject } = this.pending.get(message.id);
      this.pending.delete(message.id);
      if (message.error) reject(new Error(`${message.error.message}: ${JSON.stringify(message.error.data || {})}`));
      else resolve(message.result || {});
      return;
    }
    if (message.method && this.listeners.has(message.method)) {
      for (const listener of this.listeners.get(message.method)) listener(message.params || {});
    }
  }

  send(method, params = {}) {
    const id = this.nextId++;
    this.ws.send(JSON.stringify({ id, method, params }));
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      setTimeout(() => {
        if (!this.pending.has(id)) return;
        this.pending.delete(id);
        reject(new Error(`CDP command timed out: ${method}`));
      }, 10000);
    });
  }

  on(method, listener) {
    const items = this.listeners.get(method) || [];
    items.push(listener);
    this.listeners.set(method, items);
  }

  close() {
    this.ws.close();
  }
}

async function createTarget() {
  const url = `${CDP_URL}/json/new?${encodeURIComponent("about:blank")}`;
  let res = await fetch(url, { method: "PUT" });
  if (!res.ok) res = await fetch(url);
  if (!res.ok) throw new Error(`Unable to create Chrome target: ${res.status} ${await res.text()}`);
  return await res.json();
}

async function closeTarget(id) {
  try {
    await fetch(`${CDP_URL}/json/close/${encodeURIComponent(id)}`);
  } catch {
    // The smoke test has already finished; target cleanup is best-effort.
  }
}

async function waitFor(client, expression, label, timeoutMs = 10000) {
  const start = Date.now();
  let lastValue = null;
  while (Date.now() - start < timeoutMs) {
    const result = await client.send("Runtime.evaluate", {
      expression,
      returnByValue: true,
      awaitPromise: true,
    });
    lastValue = result.result?.value;
    if (lastValue) return lastValue;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(`Timed out waiting for ${label}; last value=${JSON.stringify(lastValue)}`);
}

async function evalValue(client, expression) {
  const result = await client.send("Runtime.evaluate", {
    expression,
    returnByValue: true,
    awaitPromise: true,
  });
  if (result.exceptionDetails) throw new Error(`Runtime exception: ${result.exceptionDetails.text}`);
  return result.result?.value;
}

async function clickSelector(client, selector) {
  const rect = await evalValue(client, `(() => {
    const el = document.querySelector(${JSON.stringify(selector)});
    if (!el) return null;
    el.scrollIntoView({ block: "center", inline: "center" });
    const r = el.getBoundingClientRect();
    return { x: r.left + r.width / 2, y: r.top + r.height / 2, width: r.width, height: r.height, disabled: Boolean(el.disabled) };
  })()`);
  if (!rect) throw new Error(`Missing clickable selector: ${selector}`);
  if (rect.disabled) throw new Error(`Selector is disabled: ${selector}`);
  await client.send("Input.dispatchMouseEvent", { type: "mouseMoved", x: rect.x, y: rect.y });
  await client.send("Input.dispatchMouseEvent", { type: "mousePressed", x: rect.x, y: rect.y, button: "left", clickCount: 1 });
  await client.send("Input.dispatchMouseEvent", { type: "mouseReleased", x: rect.x, y: rect.y, button: "left", clickCount: 1 });
}

async function clickExpression(client, expression, label) {
  const ok = await evalValue(client, `(() => {
    const el = (${expression});
    if (!el) return false;
    el.scrollIntoView({ block: "center", inline: "center" });
    el.click();
    return true;
  })()`);
  if (!ok) throw new Error(`Unable to click ${label}`);
}

async function fillCleanTextarea(client, index, value) {
  const ok = await evalValue(client, `(() => {
    const el = document.querySelectorAll(".kbsp-clean-compose textarea")[${index}];
    if (!el) return false;
    el.value = ${JSON.stringify(value)};
    el.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: ${JSON.stringify(value)} }));
    return true;
  })()`);
  if (!ok) throw new Error(`Unable to fill clean image textarea ${index}`);
}

async function handleFetch(client, params) {
  const { requestId, request } = params;
  const url = new URL(request.url);
  const path = url.pathname;
  const method = request.method || "GET";
  const fulfillJson = (payload, status = 200) => client.send("Fetch.fulfillRequest", {
    requestId,
    responseCode: status,
    responseHeaders: responseHeaders("application/json"),
    body: jsonBody(payload),
  });
  const fulfillPng = () => client.send("Fetch.fulfillRequest", {
    requestId,
    responseCode: 200,
    responseHeaders: responseHeaders("image/png"),
    body: PNG_1X1_BASE64,
  });

  if (path === "/api/auth/status") {
    return fulfillJson({ enabled: true, configured: true, authenticated: true, role: "user", capabilities: {} });
  }
  if (path === "/api/koubo-storyboard/tasks/31" && method === "GET") {
    return fulfillJson(detail());
  }
  if (path === "/api/setup/media-models/image/config") {
    return fulfillJson({
      active_provider: "mock",
      providers: [
        {
          provider: "mock",
          provider_label: "Mock",
          model: "mock-image",
          models: [{ model: "mock-image", label: "Mock Image" }],
        },
      ],
    });
  }
  if (path === "/api/koubo-storyboard/tasks/31/clean-image/generations") {
    return fulfillJson({ ok: true, items: smokeState.generation ? [smokeState.generation] : [] });
  }
  if (path === "/api/koubo-storyboard/tasks/31/clean-image/references" && method === "POST") {
    smokeState.calls.push({ kind: "uploadReferences", bytes: request.postData?.length || 0 });
    return fulfillJson({
      ok: true,
      items: [{
        path: uploadedReferencePath,
        filename: "reference_uploaded.png",
        original_filename: "reference_uploaded.png",
        content_type: "image/png",
        image_url: "/api/koubo-storyboard/tasks/31/clean-image/references/reference_uploaded.png/image",
      }],
    });
  }
  if (path === "/api/koubo-storyboard/tasks/31/clean-image/generate" && method === "POST") {
    const body = parseBody(request.postData);
    smokeState.calls.push({ kind: "generate", body });
    smokeState.generation = {
      generation_id: "cln_ui_smoke_0001",
      created_at: Date.now(),
      provider: body.provider || "mock",
      model: body.model || "mock-image",
      requested_size: body.size || "",
      effective_size: body.size || "1536x1024",
      prompt: body.prompt || "",
      negative_prompt: body.negative_prompt || "",
      reference_paths: body.reference_paths || [],
      output_path: "SessionScratch/CleanImageGenerations/cln_ui_smoke_0001/image.png",
      manifest_path: "SessionScratch/CleanImageGenerations/cln_ui_smoke_0001/manifest.json",
      image_url: "/api/koubo-storyboard/tasks/31/clean-image/cln_ui_smoke_0001/image",
      promotions: [],
    };
    return fulfillJson({ ok: true, generation: smokeState.generation });
  }
  if (path === "/api/koubo-storyboard/tasks/31/clean-image/cln_ui_smoke_0001/image") {
    return fulfillPng();
  }
  if (path === "/api/koubo-storyboard/tasks/31/clean-image/references/reference_uploaded.png/image") {
    return fulfillPng();
  }
  if (path.startsWith("/api/session-tasks/171/raw/") && /\.(png|jpe?g|webp|gif)$/i.test(path)) {
    return fulfillPng();
  }
  if (path === "/api/koubo-storyboard/tasks/31/clean-image/cln_ui_smoke_0001/promote/asset-library" && method === "POST") {
    const body = parseBody(request.postData);
    smokeState.calls.push({ kind: "promoteAsset", body });
    const asset = {
      id: "SessionOutput/storyboard/assets/images/1780999609968_001_clean_generated_ui.png",
      path: "SessionOutput/storyboard/assets/images/1780999609968_001_clean_generated_ui.png",
      filename: "1780999609968_001_clean_generated_ui.png",
      label: "Clean generated image",
      asset_type: "Image",
      kind: "image",
      source: "clean_generated",
    };
    smokeState.generation = {
      ...smokeState.generation,
      promotions: [{ target: "asset_library", target_path: asset.path, created_at: Date.now() }],
    };
    return fulfillJson({
      ok: true,
      asset,
      generation: smokeState.generation,
      ...detail(body.plan || basePlan, meta({ uploaded_images: [asset] })),
    });
  }
  if (path === "/api/koubo-storyboard/tasks/31/clean-image/cln_ui_smoke_0001/promote/dialogue-image" && method === "POST") {
    const body = parseBody(request.postData);
    smokeState.calls.push({ kind: "promoteDialogue", body });
    const workingPath = "SessionOutput/storyboard/Working/srt_001_Image_01.png";
    const plan = updateDialoguePlan(body.plan || basePlan, workingPath);
    smokeState.generation = {
      ...smokeState.generation,
      promotions: [...(smokeState.generation?.promotions || []), { target: "dialogue_image", working_path: workingPath, created_at: Date.now() }],
    };
    return fulfillJson({
      ok: true,
      dialogue_id: body.dialogue_id || baseDialogueId,
      working_path: workingPath,
      generation: smokeState.generation,
      ...detail(plan),
    });
  }

  if (path.startsWith("/api/")) {
    return client.send("Fetch.fulfillRequest", {
      requestId,
      responseCode: 404,
      responseHeaders: responseHeaders("text/plain"),
      body: textBody(`Unhandled UI smoke API: ${method} ${path}`),
    });
  }
  return client.send("Fetch.continueRequest", { requestId });
}

async function run() {
  const target = await createTarget();
  const client = new CdpClient(target.webSocketDebuggerUrl);
  await client.open();
  client.on("Fetch.requestPaused", (params) => {
    handleFetch(client, params).catch((error) => {
      console.error("[fetch handler failed]", error);
    });
  });
  await client.send("Page.enable");
  await client.send("Network.enable");
  await client.send("Network.setCacheDisabled", { cacheDisabled: true });
  await client.send("Runtime.enable");
  await client.send("Fetch.enable", {
    patterns: [
      { urlPattern: "http://127.0.0.1:18080/api/*", requestStage: "Request" },
      { urlPattern: "http://127.0.0.1:8011/api/*", requestStage: "Request" },
    ],
  });

  const results = [];
  const pass = (name, detailText = "") => results.push({ ok: true, name, detail: detailText });
  const fail = (name, error) => results.push({ ok: false, name, detail: error instanceof Error ? error.message : String(error) });

  try {
    await client.send("Page.navigate", { url: FRONTEND_URL });
    await waitFor(client, "Boolean(document.querySelector('.kbsp-clean-image-entry'))", "clean image header entry");
    const headerInfo = await evalValue(client, `(() => {
      const el = document.querySelector(".kbsp-clean-image-entry");
      return { disabled: Boolean(el.disabled), title: el.title, aria: el.getAttribute("aria-label") };
    })()`);
    if (headerInfo.disabled || headerInfo.title !== "干净单次生图") throw new Error(`unexpected header entry ${JSON.stringify(headerInfo)}`);
    pass("Header clean image entry is visible and enabled", JSON.stringify(headerInfo));

    await clickSelector(client, ".kbsp-clean-image-entry");
    await waitFor(client, "Boolean(document.querySelector('.kbsp-clean-dialog'))", "clean image modal from header");
    const modalTitle = await evalValue(client, `document.querySelector(".kbsp-clean-head h3")?.textContent?.trim()`);
    if (modalTitle !== "干净单次生图") throw new Error(`unexpected modal title: ${modalTitle}`);
    pass("Header entry opens Clean Image modal", modalTitle);

    const cleanControlLabels = await evalValue(client, `[...document.querySelectorAll(".kbsp-clean-dialog label > span")].map((item) => item.textContent.trim())`);
    if (cleanControlLabels.includes("Provider") || cleanControlLabels.includes("Model")) {
      throw new Error(`provider/model controls should be hidden: ${JSON.stringify(cleanControlLabels)}`);
    }
    pass("Clean Image modal hides provider/model controls", cleanControlLabels.join(","));

    await clickSelector(client, ".kbsp-clean-ref-choose");
    await waitFor(client, "Boolean(document.querySelector('.kbsp-clean-ref-option'))", "clean image reference picker option");
    await clickExpression(client, `document.querySelector(".kbsp-clean-ref-option")`, "existing reference image");
    await waitFor(client, `document.querySelectorAll(".kbsp-clean-ref-item").length === 1`, "selected existing reference image");
    pass("Clean Image can select an existing reference image");

    const uploadedRefOk = await evalValue(client, `(() => {
      const input = document.querySelector(".kbsp-clean-ref-upload-input");
      if (!input) return false;
      const bytes = Uint8Array.from(atob(${JSON.stringify(PNG_1X1_BASE64)}), (ch) => ch.charCodeAt(0));
      const file = new File([bytes], "reference_uploaded.png", { type: "image/png" });
      const dt = new DataTransfer();
      dt.items.add(file);
      input.files = dt.files;
      input.dispatchEvent(new Event("change", { bubbles: true }));
      return true;
    })()`);
    if (!uploadedRefOk) throw new Error("unable to dispatch clean image reference upload");
    await waitFor(client, `document.querySelectorAll(".kbsp-clean-ref-item").length === 2`, "uploaded reference image");
    const uploadCall = smokeState.calls.find((item) => item.kind === "uploadReferences");
    if (!uploadCall) throw new Error("reference upload endpoint was not called");
    pass("Clean Image can upload a scratch reference image");

    const screenshot = await client.send("Page.captureScreenshot", { format: "png", fromSurface: true });
    fs.writeFileSync(SCREENSHOT_PATH, Buffer.from(screenshot.data, "base64"));
    pass("Captured modal screenshot", SCREENSHOT_PATH);

    await clickSelector(client, ".kbsp-clean-icon.close");
    await waitFor(client, "!document.querySelector('.kbsp-clean-dialog')", "modal close");

    await waitFor(client, "Boolean(document.querySelector('.kbsp-clean-slot-button'))", "dialogue clean slot button");
    await evalValue(client, `(() => {
      const slot = document.querySelector(".kbsp-clean-slot-button")?.closest(".kbsp-media-slot");
      if (!slot) return false;
      slot.scrollIntoView({ block: "center", inline: "center" });
      return true;
    })()`);
    const slotRect = await evalValue(client, `(() => {
      const slot = document.querySelector(".kbsp-clean-slot-button").closest(".kbsp-media-slot");
      const r = slot.getBoundingClientRect();
      return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
    })()`);
    await client.send("Input.dispatchMouseEvent", { type: "mouseMoved", x: slotRect.x, y: slotRect.y });
    const slotOpacity = await waitFor(client, `Number(getComputedStyle(document.querySelector(".kbsp-clean-slot-button")).opacity) > 0.5`, "dialogue slot button visible on hover");
    if (!slotOpacity) throw new Error("slot button did not become visible");
    pass("Dialogue new-image slot reveals clean image shortcut on hover");

    await clickExpression(client, `document.querySelector(".kbsp-clean-slot-button")`, "dialogue clean slot button");
    await waitFor(client, "Boolean(document.querySelector('.kbsp-clean-dialog'))", "clean modal from dialogue slot");
    pass("Dialogue slot entry opens Clean Image modal");

    await fillCleanTextarea(client, 0, "猫");
    await fillCleanTextarea(client, 1, "不要狗");
    await clickSelector(client, ".kbsp-clean-primary");
    await waitFor(client, `document.querySelector(".kbsp-clean-status")?.textContent?.includes("已生成")`, "generated status");
    const generateCall = smokeState.calls.find((item) => item.kind === "generate");
    if (!generateCall || generateCall.body.prompt !== "猫" || generateCall.body.negative_prompt !== "不要狗") {
      throw new Error(`unexpected generate payload ${JSON.stringify(generateCall)}`);
    }
    if ("provider" in generateCall.body || "model" in generateCall.body) {
      throw new Error(`generate payload should rely on default image model: ${JSON.stringify(generateCall.body)}`);
    }
    const referencePaths = generateCall.body.reference_paths || [];
    if (!referencePaths.includes(existingReferenceAsset.path) || !referencePaths.includes(uploadedReferencePath)) {
      throw new Error(`generate payload should include selected/uploaded reference paths: ${JSON.stringify(generateCall.body)}`);
    }
    pass("Generate sends clean prompt payload without explicit model and shows generated state", JSON.stringify(generateCall.body));

    const badge = await evalValue(client, `[...document.querySelectorAll(".kbsp-clean-badges span")].map((item) => item.textContent.trim()).join(",")`);
    if (!badge.includes("未入库")) throw new Error(`missing 未入库 badge: ${badge}`);
    pass("Generated scratch result is shown as not imported", badge);

    const selectedDialogue = await evalValue(client, `document.querySelector(".kbsp-clean-promote select")?.value`);
    if (selectedDialogue !== baseDialogueId) throw new Error(`dialogue selector not prefilled after generation: ${selectedDialogue}`);
    pass("Dialogue target remains selected after generation", selectedDialogue);

    await clickExpression(client, `[...document.querySelectorAll(".kbsp-clean-promote button")].find((item) => item.textContent.includes("加入素材库"))`, "加入素材库");
    await waitFor(client, `document.querySelector(".kbsp-clean-status")?.textContent?.includes("已加入右侧素材库")`, "asset library promoted status");
    const assetCall = smokeState.calls.find((item) => item.kind === "promoteAsset");
    if (!assetCall?.body?.plan?.shots?.length) throw new Error(`asset promote did not send current plan: ${JSON.stringify(assetCall)}`);
    const assetBadge = await waitFor(client, `Boolean(document.querySelector(".kbsp-asset-clean-badge"))`, "asset clean badge").catch(async (error) => {
      const errorMessage = error instanceof Error ? error.message : String(error);
      const debug = await evalValue(client, `(async () => {
        const right = document.querySelector(".right") || document.querySelector(".kbsp-right");
        return {
          error: ${JSON.stringify(errorMessage)},
          rightText: right?.innerText?.slice(0, 1000) || "",
          uploadTabText: [...document.querySelectorAll(".kbsp-asset-tabs button")].map((item) => item.textContent.trim()).join("|"),
          cleanBadgeCount: document.querySelectorAll(".kbsp-asset-clean-badge").length,
          cardCount: document.querySelectorAll(".kbsp-asset-scene-card").length,
          cardTitles: [...document.querySelectorAll(".kbsp-asset-scene-card")].map((item) => item.getAttribute("title")),
          assetPanelResources: performance.getEntriesByType("resource").map((item) => item.name).filter((name) => name.includes("AssetPanel")).slice(-5),
          assetPanelHasBadgeCode: await (async () => {
            const url = performance.getEntriesByType("resource").map((item) => item.name).find((name) => name.includes("AssetPanel"));
            if (!url) return null;
            try {
              const text = await fetch(url).then((res) => res.text());
              return text.includes("kbsp-asset-clean-badge") && text.includes("clean_generated");
            } catch {
              return false;
            }
          })(),
          bodyHasCleanText: document.body.innerText.includes("干净生图"),
        };
      })()`);
      throw new Error(`asset clean badge missing; debug=${JSON.stringify(debug)}`);
    });
    if (!assetBadge) throw new Error("asset clean badge missing");
    pass("Promote to asset library sends current plan and shows clean asset badge");

    await clickExpression(client, `[...document.querySelectorAll(".kbsp-clean-promote button")].find((item) => item.textContent.includes("绑定新图"))`, "绑定新图");
    await waitFor(client, `document.querySelector(".kbsp-clean-status")?.textContent?.includes("已绑定到当前对白")`, "dialogue bound status");
    const dialogueCall = smokeState.calls.find((item) => item.kind === "promoteDialogue");
    if (!dialogueCall?.body?.plan?.shots?.length || dialogueCall.body.dialogue_id !== baseDialogueId) {
      throw new Error(`dialogue promote did not send current plan/dialogue: ${JSON.stringify(dialogueCall)}`);
    }
    pass("Promote to dialogue sends current plan and selected dialogue", JSON.stringify({ dialogue_id: dialogueCall.body.dialogue_id }));
  } catch (error) {
    fail("Clean image UI smoke", error);
  } finally {
    client.close();
    await closeTarget(target.id);
  }

  for (const item of results) {
    console.log(`${item.ok ? "PASS" : "FAIL"} ${item.name}${item.detail ? ` :: ${item.detail}` : ""}`);
  }
  const failed = results.filter((item) => !item.ok);
  if (failed.length) process.exit(1);
}

run().catch((error) => {
  console.error(error);
  process.exit(1);
});
