#!/usr/bin/env node
import assert from "node:assert/strict";
import { loadPlaywright } from "./playwright-loader.mjs";

const BASE_URL = (process.env.OPENCREW_E2E_FRONTEND_URL || "http://127.0.0.1:18080").replace(/\/$/, "");
const HEADLESS = process.env.OPENCREW_E2E_HEADLESS !== "0";
const REFERENCE_SCRIPT = "这是必须在任务摘要刷新后继续保留的参考脚本。";
const GENERATED_REWRITE_PROMPT = "这是模型生成的脚本改写最终提示词。";
const GENERATED_STORYBOARD_PROMPT = "这是模型生成的故事版最终提示词。";

function json(route, payload, status = 200) {
  return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(payload) });
}

function taskSummary(taskId) {
  return {
    task_id: taskId,
    session_id: taskId + 1000,
    title: `Reference script regression ${taskId}`,
    profile_id: "person_talking_head_v1",
    create_mode: "person_talking_head",
    script_creation_mode: "ai_rewrite",
    input_mode: "reference_script",
    status: "draft",
    script_preview: REFERENCE_SCRIPT,
    updated_at: 2000,
  };
}

function savedTaskDetail(taskId) {
  return {
    ...taskSummary(taskId),
    source_script: REFERENCE_SCRIPT,
    industry: "医美",
    persona: "强判断老板型",
    target_audience: "老板",
    video_formula: "Hook/Trust/CTA",
    product_info: "",
    constraints: "",
    rewrite_simple_prompt: "请改写参考脚本。",
    rewrite_final_prompt: "",
    storyboard_simple_prompt: "请创建故事版。",
    storyboard_final_prompt: "",
    storyboard_quick_config: {
      target_scene_seconds: 15,
      target_shot_seconds: 15,
      split_tolerance_seconds: 0,
      language_boundary_mode: "strict",
    },
    talking_head: {},
  };
}

async function installRoutes(page, taskId, failGeneration = false) {
  const state = {
    created: false,
    createBody: "",
    listRequests: 0,
    promptKinds: [],
  };

  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();

    if (path === "/api/auth/status") {
      return json(route, {
        enabled: true,
        configured: true,
        authenticated: true,
        role: "user",
        capabilities: { can_manage_connection: false, can_view_metering: false },
        debug_console_enabled: false,
      });
    }
    if (path === "/api/koubo-tasks" && method === "GET") {
      state.listRequests += 1;
      return json(route, { items: state.created ? [taskSummary(taskId)] : [] });
    }
    if (path === "/api/koubo-tasks/options") {
      return json(route, {
        industry: ["医美"],
        persona: ["强判断老板型"],
        target_audience: ["老板"],
        video_formula: ["Hook/Trust/CTA"],
      });
    }
    if (path === "/api/openclip/prompt-models") {
      return json(route, {
        items: [{ providerID: "max", providerName: "Max", modelID: "max", modelName: "Max" }],
        default_model: { providerID: "max", modelID: "max" },
      });
    }
    if (path === "/api/openclip/analysis-v1/tts/quick-adv/clone-list") {
      return json(route, { ok: true, result: { voices: [] } });
    }
    if (path === "/api/koubo-tasks/create-talking-head" && method === "POST") {
      state.createBody = request.postData() || "";
      state.created = true;
      return json(route, {
        ok: true,
        task_id: taskId,
        session_id: taskId + 1000,
        item: savedTaskDetail(taskId),
      });
    }
    if (path === `/api/openclip/tasks/${taskId}/generate-prompt` && method === "POST") {
      const payload = request.postDataJSON();
      state.promptKinds.push(payload.prompt_kind);
      if (failGeneration) return json(route, { detail: "模拟模型生成失败" }, 500);
      return json(route, {
        task: {
          id: taskId,
          rewrite_final_prompt: GENERATED_REWRITE_PROMPT,
          final_prompt: GENERATED_REWRITE_PROMPT,
          storyboard_final_prompt: payload.prompt_kind === "storyboard" ? GENERATED_STORYBOARD_PROMPT : "",
        },
      });
    }
    return json(route, {});
  });

  return state;
}

async function openRewriteModal(page) {
  await page.goto(`${BASE_URL}/?talkingHeadReferenceScriptE2E=${Date.now()}#/koubo-tasks`, {
    waitUntil: "domcontentloaded",
    timeout: 30000,
  });
  await page.getByRole("heading", { name: "任务列表（口播）" }).waitFor({ state: "visible", timeout: 15000 });
  await page.getByRole("button", { name: "人物口播", exact: true }).click();
  const modal = page.locator(".koubo-task-list-script-modal.is-talking-head");
  await modal.waitFor({ state: "visible", timeout: 10000 });
  await modal.getByRole("button", { name: /智能改写脚本/ }).click();
  const referenceInput = modal.getByPlaceholder("请粘贴需要 AI 改写的参考脚本。");
  await referenceInput.fill(REFERENCE_SCRIPT);
  return { modal, referenceInput };
}

async function runScenario(browser, { name, scope, failGeneration = false, taskId }) {
  const context = await browser.newContext({ viewport: { width: 1600, height: 1000 } });
  const page = await context.newPage();
  const pageErrors = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));
  const state = await installRoutes(page, taskId, failGeneration);

  try {
    const { modal, referenceInput } = await openRewriteModal(page);
    if (scope === "all") {
      await modal.getByRole("button", { name: "生成全部复杂提示词" }).click();
    } else {
      await modal.getByRole("button", { name: "调用模型创建当前最终提示词" }).click();
    }

    const promptDialog = page.locator(".koubo-task-list-prompt-model-dialog");
    await promptDialog.waitFor({ state: "visible", timeout: 10000 });
    await promptDialog.getByRole("button", { name: "生成", exact: true }).click();

    if (failGeneration) {
      await promptDialog.getByText("模拟模型生成失败", { exact: true }).waitFor({ state: "visible", timeout: 10000 });
    } else {
      await promptDialog.waitFor({ state: "hidden", timeout: 10000 });
      const finalPrompt = modal.locator("textarea.koubo-task-list-final-prompt").first();
      await assert.doesNotReject(() => finalPrompt.waitFor({ state: "visible", timeout: 5000 }));
      assert.equal(await finalPrompt.inputValue(), GENERATED_REWRITE_PROMPT, `${name}: generated final prompt was not applied`);
      assert.ok(state.listRequests >= 2, `${name}: task list did not refresh after prompt generation`);
    }

    assert.equal(await referenceInput.inputValue(), REFERENCE_SCRIPT, `${name}: task promotion cleared the reference script`);
    assert.match(state.createBody, new RegExp(REFERENCE_SCRIPT), `${name}: create request omitted the reference script`);
    assert.deepEqual(pageErrors, [], `${name}: unexpected page errors: ${pageErrors.join(" | ")}`);
    return { name, listRequests: state.listRequests, promptKinds: state.promptKinds };
  } finally {
    await context.close();
  }
}

const { chromium } = loadPlaywright();
const browser = await chromium.launch({ headless: HEADLESS });
try {
  const reports = [];
  reports.push(await runScenario(browser, { name: "rewrite-final", scope: "rewrite", taskId: 267 }));
  reports.push(await runScenario(browser, { name: "all-prompts", scope: "all", taskId: 268 }));
  reports.push(await runScenario(browser, { name: "generation-failure", scope: "rewrite", failGeneration: true, taskId: 269 }));
  assert.deepEqual(reports[0].promptKinds, ["rewrite"]);
  assert.deepEqual(reports[1].promptKinds, ["rewrite", "storyboard"]);
  assert.deepEqual(reports[2].promptKinds, ["rewrite"]);
  console.log(JSON.stringify({ ok: true, reports }, null, 2));
} finally {
  await browser.close();
}
