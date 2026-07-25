#!/usr/bin/env node
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const PLAYWRIGHT_FALLBACK = "/private/tmp/opencrew-playwright-runner/node_modules/playwright";
const BASE_URL = (process.env.OPENCREW_E2E_FRONTEND_URL || "http://127.0.0.1:18081").replace(/\/$/, "");
const HEADLESS = process.env.OPENCREW_E2E_HEADLESS !== "0";
const TASK_ID = Number(process.env.OPENCREW_E2E_ANALYSIS_V1_TASK_ID || 303);
const SESSION_ID = Number(process.env.OPENCREW_E2E_ANALYSIS_V1_SESSION_ID || 9303);
const RUN_ID = process.env.OPENCREW_E2E_RUN_ID || new Date().toISOString().replace(/[-:TZ.]/g, "").slice(0, 14);
const TEST_URL = `${BASE_URL}/?e2eTTSQuickAdv=${RUN_ID}#/analysis-v1/tasks/${TASK_ID}`;
const SCREENSHOT_PATH = process.env.OPENCREW_E2E_TTS_ADV_SCREENSHOT || `/private/tmp/opencrew_tts_quick_adv_${RUN_ID}.png`;

const FINAL_ITEMS_PATH = "SessionOutput/subtitle/final_srt_frame_items.json";
const REWRITTEN_ITEMS_PATH = "SessionOutput/subtitle/rewritten_srt_items.json";
const STORYBOARD_PATH = "SessionOutput/storyboard/srt_storyboard.json";
const FRAME_MAP_PATH = "SessionOutput/visual/srt_frame_map.json";
const STEP_02_RESULT_PATH = "S3_02_01_AudioASR/Report/Result.json";
const STEP_02_01_RESULT_PATH = "S4_02_02_VideoSRTFrame/Report/Result.json";
const AUDIO_REFERENCE_PATH = "SessionOutput/Audio_Reference.wav";
const TTS_BUILDER_CANDIDATES_PATH = "SessionOutput/tts/tts_builder_candidates.json";

const TEST_PLAN = [
  "初始化：mock 认证、任务详情、工作区 JSON、TTS 配置，进入 Analysis V1 并打开音色匹配。",
  "高级匹配：验证状态、音色库、采样、排行按钮和 Stage/SpeechBrain 参数 payload。",
  "云端克隆：验证点击按钮会弹出授权确认，确认后请求携带 consent 并展示 voice_id。",
  "生成候选：验证 03_03 run-only 后台运行 payload 带 stage_count 和 enable_speechbrain。",
  "候选试听：验证候选列表、预览弹窗、应用故事版本确认机制、候选详情和已选用状态可见。",
  "响应式：桌面与 390px 移动宽度检查主对话框不被 viewport 裁切。",
];

function loadPlaywright() {
  for (const id of ["playwright", PLAYWRIGHT_FALLBACK]) {
    try {
      return require(id);
    } catch {
      // Try the next location.
    }
  }
  throw new Error("Playwright is not installed. Run `npm --prefix frontend install playwright` or provide the fallback runner.");
}

async function launchChromium(chromium) {
  const baseOptions = { headless: HEADLESS };
  try {
    return await chromium.launch(baseOptions);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    if (!/Executable doesn't exist|playwright install/i.test(message)) throw error;
    const candidates = [
      process.env.OPENCREW_E2E_CHROME_PATH,
      "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
      "/Applications/Chromium.app/Contents/MacOS/Chromium",
    ].filter(Boolean);
    const executablePath = candidates.find((item) => fs.existsSync(item));
    if (!executablePath) throw error;
    return await chromium.launch({ ...baseOptions, executablePath });
  }
}

function promptModels() {
  return {
    items: [
      {
        providerID: "openai",
        providerName: "OpenAI",
        modelID: "gpt-5.5",
        modelName: "GPT-5.5",
        reasoning: true,
        inputModalities: ["text"],
        contextLimit: 200000,
      },
      {
        providerID: "google",
        providerName: "Google",
        modelID: "gemini-3.1-pro",
        modelName: "Gemini 3.1 Pro",
        reasoning: false,
        inputModalities: ["text"],
        contextLimit: 128000,
      },
    ],
    default_model: { providerID: "openai", modelID: "gpt-5.5" },
  };
}

function taskDetail() {
  return {
    task: {
      id: TASK_ID,
      session_id: SESSION_ID,
      status: "ready",
      reference_video_path: "SessionContext/Video_Source.mp4",
      industry: "医美",
      persona: "强判断老板型",
      target_audience: "老板",
      product_info: "E2E 高级音色匹配测试产品",
      constraints: "保持句数一致，语气自然可信。",
      analysis_goal: "提取整体公式",
      video_formula: "Hook/Trust/CTA",
      prompt_model_provider: "openai",
      prompt_model_id: "gpt-5.5",
      run_model_provider: "openai",
      run_model_id: "gpt-5.5",
      latest_attempt_id: null,
      updated_at: Date.now(),
    },
    current_prompt_version: {
      rewrite_simple_prompt: "E2E rewrite simple prompt",
      rewrite_final_prompt: "E2E rewrite final prompt",
      storyboard_simple_prompt: "E2E storyboard simple prompt",
      storyboard_final_prompt: "E2E storyboard final prompt",
    },
    prompt_models: promptModels(),
    options: {
      industry: ["医美"],
      persona: ["强判断老板型"],
      target_audience: ["老板"],
      analysis_goal: ["提取整体公式"],
      video_formula: ["Hook/Trust/CTA"],
    },
  };
}

function ttsModelConfig() {
  return {
    providers: [
      {
        provider: "google",
        label: "Gemini",
        selected_model: "gemini-3.1-flash-tts-preview",
        selected_voice_by_model: { "gemini-3.1-flash-tts-preview": "E2E-Alpha" },
        models: [
          {
            model: "gemini-3.1-flash-tts-preview",
            label: "Gemini Flash TTS",
            voices: [
              { voice_id: "E2E-Alpha", label: "E2E Alpha" },
              { voice_id: "E2E-Beta", label: "E2E Beta" },
            ],
          },
        ],
      },
    ],
  };
}

function candidatePayload() {
  return {
    ok: true,
    selected_candidate_id: "cand_e2e_alpha",
    scene_profile: {
      speaker_profile: "男声，自然短视频口播，中等语速",
      voice_prompt_guidance: { speaker: "male host" },
    },
    sample_policy: {
      selected_range: { start: 0, end: 16 },
      selected_duration: 16,
    },
    candidates: [
      {
        candidate_id: "cand_e2e_alpha",
        voice: "E2E-Alpha",
        voice_label: "E2E Candidate Alpha",
        provider: "google",
        model: "gemini-3.1-flash-tts-preview",
        sample_audio_path: "SessionOutput/tts/candidate_001.wav",
        match_score: 91.4,
        selected: true,
        scoring_mode: "degraded_resemblyzer_acoustic",
        dimension_scores: {
          timbre_score: 91,
          pitch_score: 84,
          pace_score: 93,
          articulation_score: 79,
          texture_score: 88,
          persona_score: 100,
          style_score: 82,
        },
        explanation: { best_dimensions: ["timbre", "pace"], watch_dimensions: ["articulation"] },
        prompt: "请用自然可信的男声短视频口播风格朗读。",
        generation_prompt: "请用自然可信的男声短视频口播风格朗读。",
      },
      {
        candidate_id: "cand_e2e_beta",
        voice: "E2E-Beta",
        voice_label: "E2E Candidate Beta",
        provider: "google",
        model: "gemini-3.1-flash-tts-preview",
        sample_audio_path: "SessionOutput/tts/candidate_002.wav",
        match_score: 86.2,
        scoring_mode: "degraded_resemblyzer_acoustic",
        dimension_scores: {
          timbre_score: 86,
          pitch_score: 78,
          pace_score: 88,
          articulation_score: 74,
          texture_score: 80,
          persona_score: 100,
          style_score: 79,
        },
        explanation: { best_dimensions: ["persona", "pace"], watch_dimensions: ["pitch"] },
        prompt: "请用更稳的解释型口播风格朗读。",
        generation_prompt: "请用更稳的解释型口播风格朗读。",
      },
    ],
  };
}

function storyboardPlan() {
  return {
    schema_version: "koubo_storyboard_edit_0.1",
    title: "故事版（口播）",
    source_type: "analysis_v1_storyboard",
    storyboard_tts_selection: {
      provider: "google",
      model: "gemini-3.1-flash-tts-preview",
      voice_id: "E2E-Beta",
      voice: "E2E-Beta",
      label: "E2E Beta",
      candidate_id: "cand_e2e_beta",
      prompt: "旧故事版口播提示词",
      prompt_template: "旧故事版口播提示词",
      tempo: 1,
    },
    shots: [
      {
        shot_id: "shot_001",
        shot_name: "Shot 1",
        scenes: [
          {
            scene_id: "scene_001",
            scene_index: 1,
            asset_key: "scene_001",
            dialogues: [
              {
                dialogue_id: "dialogue_001",
                dialogue_index: 1,
                text: "很多老板以为投流没效果，其实真正卡住的是信任表达。",
                duration: 3.2,
              },
            ],
          },
        ],
      },
    ],
  };
}

function baseReferenceProfile() {
  return {
    selected_duration: 16,
    selected_range: { start: 0, end: 16 },
    dialogue: "很多老板以为投流没效果，其实真正卡住的是信任表达。",
    gender_gate: { target_gender: "male", source: "e2e_fixture" },
    features: { pitch_hz: 146.4, pitch_method: "mock-autocorr", tempo_wpm: 168 },
  };
}

function rankBoard(enableSpeechbrain) {
  const scoringMode = enableSpeechbrain ? "full_speechbrain" : "degraded_resemblyzer_acoustic";
  return {
    ok: true,
    score_schema_version: "quick_adv_score_v2",
    ranking_strategy: "two_stage_high_recall",
    scoring_mode: scoringMode,
    available_backends: { resemblyzer: true, speechbrain: Boolean(enableSpeechbrain), acoustic: true },
    reference_profile: baseReferenceProfile(),
    stage2: [
      {
        rank: 1,
        voice: "E2E-Alpha",
        voice_label: "E2E Alpha",
        provider: "google",
        model: "gemini-3.1-flash-tts-preview",
        target_gender: "male",
        candidate_gender: { gender: "male" },
        sample_audio_path: "SessionOutput/tts/catalog_alpha.wav",
        match_score: 92.1,
        scores: { final_score: 92.1, stage2_score: 85.6 },
        dimension_scores: {
          timbre_score: 92,
          pitch_score: 86,
          pace_score: 94,
          articulation_score: 81,
          texture_score: 88,
          persona_score: 100,
          style_score: 84,
        },
        explanation: { best_dimensions: ["timbre", "pace"], watch_dimensions: ["articulation"] },
        prompt: "自然可信、手机口播感、句尾收稳。",
      },
      {
        rank: 2,
        voice: "E2E-Beta",
        voice_label: "E2E Beta",
        provider: "google",
        model: "gemini-3.1-flash-tts-preview",
        target_gender: "male",
        candidate_gender: { gender: "male" },
        sample_audio_path: "SessionOutput/tts/catalog_beta.wav",
        match_score: 88.4,
        scores: { final_score: 88.4, stage2_score: 79.0 },
        dimension_scores: {
          timbre_score: 88,
          pitch_score: 81,
          pace_score: 89,
          articulation_score: 77,
          texture_score: 83,
          persona_score: 100,
          style_score: 80,
        },
        explanation: { best_dimensions: ["persona", "pace"], watch_dimensions: ["articulation"] },
        prompt: "稳定解释型，语速略慢。",
      },
    ],
    recommended: [
      { rank: 1, voice: "E2E-Alpha", voice_label: "E2E Alpha", match_score: 92.1 },
      { rank: 2, voice: "E2E-Beta", voice_label: "E2E Beta", match_score: 88.4 },
    ],
  };
}

function createWavBuffer(durationSeconds = 0.35, sampleRate = 16000) {
  const sampleCount = Math.floor(durationSeconds * sampleRate);
  const dataBytes = sampleCount * 2;
  const buffer = Buffer.alloc(44 + dataBytes);
  buffer.write("RIFF", 0);
  buffer.writeUInt32LE(36 + dataBytes, 4);
  buffer.write("WAVE", 8);
  buffer.write("fmt ", 12);
  buffer.writeUInt32LE(16, 16);
  buffer.writeUInt16LE(1, 20);
  buffer.writeUInt16LE(1, 22);
  buffer.writeUInt32LE(sampleRate, 24);
  buffer.writeUInt32LE(sampleRate * 2, 28);
  buffer.writeUInt16LE(2, 32);
  buffer.writeUInt16LE(16, 34);
  buffer.write("data", 36);
  buffer.writeUInt32LE(dataBytes, 40);
  for (let i = 0; i < sampleCount; i += 1) {
    const value = Math.round(Math.sin((i / sampleRate) * 2 * Math.PI * 440) * 12000);
    buffer.writeInt16LE(value, 44 + i * 2);
  }
  return buffer;
}

function parseJsonBody(route) {
  const raw = route.request().postData() || "{}";
  try {
    return JSON.parse(raw);
  } catch {
    return {};
  }
}

async function fulfillJson(route, payload, status = 200) {
  await fulfillRoute(route, {
    status,
    contentType: "application/json",
    body: JSON.stringify(payload),
  });
}

async function fulfillRoute(route, options) {
  try {
    await route.fulfill(options);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    if (/Target page, context or browser has been closed|TargetClosedError/i.test(message)) return;
    throw error;
  }
}

function decodeRawPath(urlPathname) {
  const marker = `/api/session-tasks/${SESSION_ID}/raw/`;
  const index = urlPathname.indexOf(marker);
  if (index < 0) return "";
  return urlPathname
    .slice(index + marker.length)
    .split("/")
    .map((segment) => {
      try {
        return decodeURIComponent(segment);
      } catch {
        return segment;
      }
    })
    .join("/");
}

function createMockState() {
  const rawJson = {
    [FINAL_ITEMS_PATH]: {
      items: [
        {
          srt_id: "srt_001",
          start: 0,
          end: 3.2,
          duration: 3.2,
          dialogue: "很多老板以为投流没效果，其实真正卡住的是信任表达。",
        },
        {
          srt_id: "srt_002",
          start: 3.2,
          end: 7.1,
          duration: 3.9,
          dialogue: "声音如果不稳，用户第一秒就会滑走。",
        },
      ],
    },
    [REWRITTEN_ITEMS_PATH]: { items: [{ srt_id: "srt_001", dialogue: "很多老板以为投流没效果，其实卡住的是信任表达。" }] },
    [STORYBOARD_PATH]: { scenes: [{ id: "scene_001", title: "E2E Scene" }] },
    [FRAME_MAP_PATH]: { items: [] },
    [STEP_02_RESULT_PATH]: { ok: true, duration_seconds: 18.2 },
    [STEP_02_01_RESULT_PATH]: { ok: true, counts: { selected_frames: 2, needs_review: 0, split_sentences: 2 } },
    [TTS_BUILDER_CANDIDATES_PATH]: candidatePayload(),
  };
  return {
    rawJson,
    samplingAudit: { sampling_score: 88, quality_label: "good", selected_duration: 16 },
    rankingBoard: null,
    clonedVoices: [],
    savedConfigPayloads: [],
    runPayloads: [],
    quickAdv: { state: [], catalog: [], sample: [], rank: [], clone: [] },
    previewPayloads: [],
    selectionPayloads: [],
    storyboardPlan: storyboardPlan(),
    storyboardSaves: [],
    unhandled: [],
    pageErrors: [],
  };
}

function quickAdvStatePayload(state) {
  return {
    ok: true,
    status: "ready",
    reference: {
      profile: baseReferenceProfile(),
      sampling_audit: state.samplingAudit,
    },
    ranking_board: state.rankingBoard,
    cloned_voices: state.clonedVoices,
    final_candidates: state.rawJson[TTS_BUILDER_CANDIDATES_PATH],
  };
}

async function installMockRoutes(page, state) {
  const wav = createWavBuffer();
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const pathname = url.pathname;
    const method = request.method();

    if (pathname === "/api/auth/status" && method === "GET") {
      return fulfillJson(route, {
        enabled: true,
        configured: true,
        authenticated: true,
        role: "user",
        capabilities: {},
        debug_console_enabled: false,
      });
    }

    if (pathname === "/api/openclip/prompt-models" && method === "GET") {
      return fulfillJson(route, promptModels());
    }

    if (pathname === "/api/openclip/tasks" && method === "GET") {
      return fulfillJson(route, { items: [taskDetail().task] });
    }

    if (pathname === `/api/openclip/tasks/${TASK_ID}` && method === "GET") {
      return fulfillJson(route, taskDetail());
    }

    if (pathname === `/api/openclip/tasks/${TASK_ID}/config` && method === "PUT") {
      const body = parseJsonBody(route);
      state.savedConfigPayloads.push(body);
      return fulfillJson(route, { ...taskDetail(), saved: true });
    }

    if (pathname === "/api/setup/media-models/tts/config" && method === "GET") {
      return fulfillJson(route, ttsModelConfig());
    }

    if (pathname.startsWith(`/api/session-tasks/${SESSION_ID}/raw/`) && method === "GET") {
      const relPath = decodeRawPath(pathname);
      if (Object.prototype.hasOwnProperty.call(state.rawJson, relPath)) {
        return fulfillJson(route, state.rawJson[relPath]);
      }
      if (relPath === AUDIO_REFERENCE_PATH || relPath.endsWith(".wav")) {
        return fulfillRoute(route, { status: 200, contentType: "audio/wav", body: wav });
      }
      return fulfillRoute(route, { status: 404, contentType: "text/plain", body: `missing mock raw file: ${relPath}` });
    }

    if (pathname === `/api/openclip/tasks/${TASK_ID}/analysis-v1/tts/quick-adv/state` && method === "POST") {
      state.quickAdv.state.push(parseJsonBody(route));
      return fulfillJson(route, { ok: true, result: quickAdvStatePayload(state) });
    }

    if (pathname === `/api/openclip/tasks/${TASK_ID}/analysis-v1/tts/quick-adv/catalog-list` && method === "POST") {
      state.quickAdv.catalog.push(parseJsonBody(route));
      return fulfillJson(route, {
        ok: true,
        result: {
          ok: true,
          count: 3,
          items: [
            { voice: "E2E-Alpha", voice_label: "E2E Alpha", provider: "google", model: "gemini-3.1-flash-tts-preview" },
            { voice: "E2E-Beta", voice_label: "E2E Beta", provider: "google", model: "gemini-3.1-flash-tts-preview" },
            { voice: "E2E-Gamma", voice_label: "E2E Gamma", provider: "google", model: "gemini-3.1-flash-tts-preview" },
          ],
        },
      });
    }

    if (pathname === `/api/openclip/tasks/${TASK_ID}/analysis-v1/tts/quick-adv/sample-reference` && method === "POST") {
      state.quickAdv.sample.push(parseJsonBody(route));
      state.samplingAudit = { sampling_score: 88, quality_label: "good", selected_duration: 16 };
      return fulfillJson(route, { ok: true, result: { ok: true, ...state.samplingAudit, sampling_audit: state.samplingAudit } });
    }

    if (pathname === `/api/openclip/tasks/${TASK_ID}/analysis-v1/tts/quick-adv/rank` && method === "POST") {
      const body = parseJsonBody(route);
      state.quickAdv.rank.push(body);
      state.rankingBoard = rankBoard(Boolean(body.enable_speechbrain));
      return fulfillJson(route, { ok: true, result: state.rankingBoard });
    }

    if (pathname === `/api/openclip/tasks/${TASK_ID}/analysis-v1/tts/quick-adv/clone-voice` && method === "POST") {
      const body = parseJsonBody(route);
      state.quickAdv.clone.push(body);
      if (!body.clone_consent_confirmed) {
        return fulfillJson(route, {
          ok: true,
          result: {
            ok: false,
            blocked_reasons: [{ code: "clone_consent_required", message: "需要确认已获得声音克隆授权" }],
          },
        });
      }
      const record = {
        voice_id: `${body.clone_prefix || "ocadv"}_voice_e2e_001`,
        voice: `${body.clone_prefix || "ocadv"}_voice_e2e_001`,
        target_model: body.clone_target_model || "cosyvoice-v3.5-flash",
        voice_source: "cloud_clone",
        reference_audio_sha256: "e2e-reference-sha256",
        consent: {
          confirmed: true,
          actor: body.clone_consent_actor || "ui",
          note: body.clone_consent_note || "",
        },
      };
      state.clonedVoices = [record];
      return fulfillJson(route, { ok: true, result: { ok: true, ...record, reused_existing: false } });
    }

    if (pathname === `/api/openclip/tasks/${TASK_ID}/analysis-v1/tts/preview` && method === "POST") {
      const body = parseJsonBody(route);
      state.previewPayloads.push(body);
      return fulfillJson(route, {
        ok: true,
        provider: body.provider,
        model: body.model,
        voice_id: body.voice_id,
        output: "SessionOutput/tts/previews/e2e_preview.wav",
      });
    }

    if (pathname === `/api/openclip/tasks/${TASK_ID}/analysis-v1/tts/selection` && method === "POST") {
      const body = parseJsonBody(route);
      state.selectionPayloads.push(body);
      return fulfillJson(route, {
        ok: true,
        task_id: TASK_ID,
        session_id: SESSION_ID,
        variables_path: "SessionContext/Variables.json",
        selection: body,
      });
    }

    if (pathname === `/api/koubo-storyboard/tasks/${TASK_ID}` && method === "GET") {
      return fulfillJson(route, {
        ok: true,
        task: taskDetail().task,
        meta: {
          title: "故事版（口播）",
          source_type: "analysis_v1_storyboard",
          analysis_task_id: TASK_ID,
          analysis_session_id: SESSION_ID,
          source_path: STORYBOARD_PATH,
        },
        plan: state.storyboardPlan,
      });
    }

    if (pathname === `/api/koubo-storyboard/tasks/${TASK_ID}` && method === "PUT") {
      const body = parseJsonBody(route);
      state.storyboardSaves.push(body);
      state.storyboardPlan = body.plan;
      return fulfillJson(route, {
        ok: true,
        task: taskDetail().task,
        meta: {
          title: "故事版（口播）",
          source_type: "analysis_v1_storyboard",
          analysis_task_id: TASK_ID,
          analysis_session_id: SESSION_ID,
          source_path: STORYBOARD_PATH,
        },
        plan: state.storyboardPlan,
      });
    }

    if (pathname === `/api/openclip/tasks/${TASK_ID}/analysis-v1/run-to-storyboard` && method === "POST") {
      const body = parseJsonBody(route);
      state.runPayloads.push(body);
      state.rawJson[TTS_BUILDER_CANDIDATES_PATH] = candidatePayload();
      return fulfillJson(route, {
        attempt_id: "adv-e2e-1",
        status: "completed",
        attempt_family: "analysis_v1_tool_run",
        plan: { selected_step_ids: ["03_03"] },
        steps: [
          {
            id: "03_03",
            name: "TTSBuilderQuickAdv",
            display_name_zh: "高级声音匹配",
            status: "completed",
            will_execute: true,
            duration_seconds: 0.2,
          },
        ],
      });
    }

    if (pathname === `/api/openclip/tasks/${TASK_ID}/analysis-v1/run-to-storyboard/adv-e2e-1` && method === "GET") {
      return fulfillJson(route, {
        attempt_id: "adv-e2e-1",
        status: "completed",
        attempt_family: "analysis_v1_tool_run",
        plan: { selected_step_ids: ["03_03"] },
        steps: [{ id: "03_03", display_name_zh: "高级声音匹配", status: "completed", will_execute: true }],
      });
    }

    if (pathname === "/api/setup/media-models/tts/voices/preview" && method === "POST") {
      return fulfillJson(route, { ok: true, output: "SessionOutput/tts/preview.wav" });
    }

    state.unhandled.push(`${method} ${pathname}`);
    return fulfillRoute(route, {
      status: 500,
      contentType: "application/json",
      body: JSON.stringify({ ok: false, error: `Unhandled e2e API mock: ${method} ${pathname}` }),
    });
  });
}

async function waitVisible(locator, label, timeout = 15000) {
  await locator.waitFor({ state: "visible", timeout });
  assert.equal(await locator.isVisible(), true, `${label} should be visible`);
}

async function activate(locator, label) {
  await waitVisible(locator, label);
  await locator.evaluate((element) => element.click());
}

async function waitForTextIncludes(locator, text, label, timeout = 15000) {
  const deadline = Date.now() + timeout;
  let lastText = "";
  while (Date.now() < deadline) {
    lastText = await locator.innerText().catch(() => "");
    if (lastText.includes(text)) return;
    await new Promise((resolve) => setTimeout(resolve, 120));
  }
  throw new Error(`${label} should include ${JSON.stringify(text)}. Last text: ${lastText.slice(0, 1000)}`);
}

async function waitForCondition(check, label, timeout = 5000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    if (check()) return;
    await new Promise((resolve) => setTimeout(resolve, 80));
  }
  throw new Error(`${label} did not happen before timeout`);
}

async function clickButtonIn(root, text) {
  const button = root.locator("button").filter({ hasText: new RegExp(`^\\s*${text}\\s*$`) }).first();
  await activate(button, `button ${text}`);
}

async function fillAdvNumber(dialog, label, value) {
  const input = dialog.locator(".analysis-v1-tts-adv-controls label").filter({ hasText: label }).locator("input").first();
  await waitVisible(input, `${label} input`);
  await input.fill(String(value));
}

async function checkAdvCheckbox(dialog, label) {
  const input = dialog.locator(".analysis-v1-tts-adv-check").filter({ hasText: label }).locator("input").first();
  await waitVisible(input, `${label} checkbox`);
  await input.check();
}

async function assertDialogWithinViewport(page, label) {
  const layout = await page.evaluate(() => {
    const dialog = document.querySelector(".analysis-v1-tts-dialog");
    if (!dialog) return { exists: false };
    const rect = dialog.getBoundingClientRect();
    const close = dialog.querySelector(".analysis-v1-tts-close")?.getBoundingClientRect();
    return {
      exists: true,
      viewport: { width: window.innerWidth, height: window.innerHeight },
      rect: { left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom, width: rect.width, height: rect.height },
      close: close ? { left: close.left, right: close.right, top: close.top, bottom: close.bottom } : null,
      dialogOverflowX: dialog.scrollWidth - dialog.clientWidth,
      documentOverflowX: document.documentElement.scrollWidth - window.innerWidth,
    };
  });
  assert.equal(layout.exists, true, `${label}: TTS dialog should exist`);
  assert.ok(layout.rect.left >= -1, `${label}: dialog should not be clipped on the left (${JSON.stringify(layout)})`);
  assert.ok(layout.rect.right <= layout.viewport.width + 1, `${label}: dialog should not be clipped on the right (${JSON.stringify(layout)})`);
  assert.ok(layout.rect.top >= -1, `${label}: dialog should not be clipped on top (${JSON.stringify(layout)})`);
  assert.ok(layout.close && layout.close.right <= layout.viewport.width + 1, `${label}: close button should remain reachable`);
  return layout;
}

async function runDesktopFlow(page, state) {
  await page.goto(TEST_URL, { waitUntil: "domcontentloaded", timeout: 30000 });
  await waitVisible(page.getByRole("heading", { name: "视频分析（口播)" }).first(), "Analysis V1 heading").catch(async () => {
    await waitVisible(page.getByText("视频分析（口播）", { exact: true }).first(), "Analysis V1 title fallback");
  });
  await waitVisible(page.locator(".analysis-v1-tts-builder-entry"), "TTS Builder entry");
  await page.locator(".analysis-v1-tts-builder-entry").click();

  const dialog = page.locator(".analysis-v1-tts-dialog").first();
  await waitVisible(dialog, "TTS Builder dialog");
  await waitVisible(dialog.getByText("音色匹配", { exact: true }), "TTS Builder title");
  await waitVisible(dialog.locator(".analysis-v1-tts-builder-actions button").filter({ hasText: "匹配配置" }).first(), "match config button");
  await waitVisible(dialog.getByText("E2E Candidate Alpha", { exact: true }), "main candidate audio list");
  await assertDialogWithinViewport(page, "desktop initial");
  await clickButtonIn(dialog.locator(".analysis-v1-tts-builder-actions"), "匹配配置");
  const matchConfigDialog = page.locator(".analysis-v1-tts-match-config-dialog").first();
  await waitVisible(matchConfigDialog.getByText("匹配配置", { exact: true }), "match config title");
  await waitVisible(matchConfigDialog.locator(".analysis-v1-tts-match-head-actions button").filter({ hasText: "状态" }).first(), "match config actions");
  await waitVisible(matchConfigDialog.getByText("还没有高级匹配排行结果", { exact: true }), "empty ranking state");

  state.quickAdv.state.length = 0;
  await clickButtonIn(matchConfigDialog.locator(".analysis-v1-tts-match-head-actions"), "状态");
  await waitForTextIncludes(matchConfigDialog, "状态已更新", "state status");
  assert.equal(state.quickAdv.state.at(-1)?.stage1_count, 24, "state request should use default Stage1 count");
  assert.equal(state.quickAdv.state.at(-1)?.stage2_count, 6, "state request should use default Stage2 count");
  assert.equal(state.quickAdv.state.at(-1)?.final_count, 3, "state request should use default final count");
  assert.equal(state.quickAdv.state.at(-1)?.enable_speechbrain, true, "state request should keep SpeechBrain enabled by default");

  await clickButtonIn(matchConfigDialog.locator(".analysis-v1-tts-match-head-actions"), "音色库");
  await waitForTextIncludes(matchConfigDialog, "已读取 3 个系统音色", "catalog status");
  assert.equal(state.quickAdv.catalog.length, 1, "catalog endpoint should be called once by user action");

  await clickButtonIn(matchConfigDialog.locator(".analysis-v1-tts-match-head-actions"), "采样");
  await waitForTextIncludes(matchConfigDialog, "采样完成，质量 88", "sampling status");
  assert.equal(state.quickAdv.sample.at(-1)?.reference_duration, 16, "sampling request should use 16s reference duration");

  await fillAdvNumber(matchConfigDialog, "首轮数量", 9);
  await fillAdvNumber(matchConfigDialog, "二轮数量", 4);
  await fillAdvNumber(matchConfigDialog, "候选数量", 2);
  await checkAdvCheckbox(matchConfigDialog, "高精度匹配");
  await clickButtonIn(matchConfigDialog.locator(".analysis-v1-tts-match-head-actions"), "排行");
  await waitForTextIncludes(matchConfigDialog, "排行完成，推荐 2 个音色", "ranking status");
  const rankPayload = state.quickAdv.rank.at(-1);
  assert.equal(rankPayload.stage1_count, 9, "rank should send Stage1 value from UI");
  assert.equal(rankPayload.stage2_count, 4, "rank should send Stage2 value from UI");
  assert.equal(rankPayload.final_count, 2, "rank should send final value from UI");
  assert.equal(rankPayload.enable_speechbrain, true, "rank should send SpeechBrain checkbox state");
  await waitVisible(matchConfigDialog.getByText("E2E Alpha", { exact: true }).first(), "rank row alpha");
  await waitVisible(matchConfigDialog.getByText("92", { exact: true }).first(), "rank score");
  await waitVisible(matchConfigDialog.getByText("音色、语速", { exact: true }).first(), "rank closest dimensions");
  await waitVisible(matchConfigDialog.locator(".analysis-v1-tts-adv-metrics").getByText("高精度匹配", { exact: true }).first(), "high precision mode metric");

  await activate(matchConfigDialog.getByRole("button", { name: /测试 E2E Alpha/ }).first(), "rank preview button");
  await waitVisible(page.locator(".analysis-v1-tts-preview-dialog"), "ranking preview dialog");
  await waitVisible(page.getByText("试听调音", { exact: true }), "preview title");
  await waitVisible(page.getByText("试听模板", { exact: true }), "preview scenario label");
  await waitVisible(page.getByText("音色提示词", { exact: true }), "preview tts prompt label");
  const rankingPreviewDialog = page.locator(".analysis-v1-tts-preview-dialog");
  const rankingScenarioSelect = rankingPreviewDialog.locator(".analysis-v1-tts-preview-field").filter({ hasText: "试听模板" }).locator("select").first();
  await rankingScenarioSelect.selectOption("short-video-natural");
  assert.equal(await rankingScenarioSelect.inputValue(), "short-video-natural", "preview scenario should switch to short-video natural");
  assert.equal(state.storyboardSaves.length, 0, "changing the preview scenario should not silently save StoryBoard TTS settings");
  await activate(rankingPreviewDialog.locator("button[aria-label='保存提示词']").first(), "save ranking preview prompt");
  await waitVisible(rankingPreviewDialog.locator("button[aria-label='已保存提示词']").first(), "saved ranking preview prompt");
  assert.equal(await rankingScenarioSelect.inputValue(), "short-video-natural", "saved preview scenario should not reset to the default option");
  assert.equal(state.storyboardSaves.length, 0, "saving the test prompt should not silently apply it to StoryBoard");
  await activate(rankingPreviewDialog.locator("button").filter({ hasText: "应用到故事版本" }).first(), "open StoryBoard apply confirmation");
  await waitVisible(rankingPreviewDialog.getByText("确认应用到整个故事版本口播", { exact: true }), "StoryBoard apply confirmation");
  await activate(rankingPreviewDialog.locator(".analysis-v1-tts-apply-confirm-actions button").filter({ hasText: "取消" }).first(), "cancel StoryBoard apply");
  assert.equal(state.storyboardSaves.length, 0, "canceling StoryBoard apply should not save settings");
  await activate(rankingPreviewDialog.locator("button").filter({ hasText: "应用到故事版本" }).first(), "reopen StoryBoard apply confirmation");
  await activate(rankingPreviewDialog.locator(".analysis-v1-tts-apply-confirm-actions button").filter({ hasText: "确认应用" }).first(), "confirm StoryBoard apply");
  await waitVisible(rankingPreviewDialog.getByText("已应用到故事版本口播；重新生成 Scene / Dialogue 音频后可听到变化。", { exact: true }), "StoryBoard apply success");
  assert.equal(state.storyboardSaves.length, 1, "confirmed StoryBoard apply should save once");
  const appliedSelection = state.storyboardSaves.at(-1)?.plan?.storyboard_tts_selection || {};
  assert.equal(appliedSelection.source, "analysis_v1_tts_preview_template", "StoryBoard selection should record Analysis V1 preview as the source");
  assert.equal(appliedSelection.provider, "google", "StoryBoard selection should keep candidate provider");
  assert.equal(appliedSelection.model, "gemini-3.1-flash-tts-preview", "StoryBoard selection should keep candidate model");
  assert.equal(appliedSelection.voice_id, "E2E-Alpha", "StoryBoard selection should use the preview voice");
  assert.equal(appliedSelection.candidate_id, "E2E-Alpha", "StoryBoard selection should keep the preview candidate key");
  assert.equal(appliedSelection.scenario_id, "short-video-natural", "StoryBoard selection should persist the chosen preview scenario");
  assert.equal(appliedSelection.language, "zh", "StoryBoard selection should persist language");
  assert.equal(appliedSelection.tempo, 1, "StoryBoard selection should persist tempo");
  assert.ok(String(appliedSelection.prompt || "").includes("自然短视频口播"), "StoryBoard prompt should use the chosen preview template");
  await activate(page.locator(".analysis-v1-tts-preview-dialog .analysis-v1-tts-close").first(), "preview close button");
  await page.locator(".analysis-v1-tts-preview-dialog").waitFor({ state: "hidden", timeout: 10000 });
  await activate(matchConfigDialog.getByRole("button", { name: /测试 E2E Alpha/ }).first(), "rank preview button after save");
  await waitVisible(page.locator(".analysis-v1-tts-preview-dialog"), "reopened ranking preview dialog");
  const reopenedScenarioSelect = page.locator(".analysis-v1-tts-preview-dialog .analysis-v1-tts-preview-field").filter({ hasText: "试听模板" }).locator("select").first();
  assert.equal(await reopenedScenarioSelect.inputValue(), "short-video-natural", "saved preview scenario should persist after reopening the same candidate");
  await activate(page.locator(".analysis-v1-tts-preview-dialog .analysis-v1-tts-close").first(), "reopened preview close button");
  await page.locator(".analysis-v1-tts-preview-dialog").waitFor({ state: "hidden", timeout: 10000 });
  await activate(matchConfigDialog.locator(".analysis-v1-tts-close").first(), "close match config before clone");
  await matchConfigDialog.waitFor({ state: "hidden", timeout: 10000 });

  await activate(dialog.locator(".analysis-v1-tts-close").first(), "close TTS Builder before clone");
  await page.locator(".analysis-v1-tts-dialog").waitFor({ state: "hidden", timeout: 10000 });
  await waitVisible(page.locator(".analysis-v1-tts-clone-entry"), "TTS clone entry");
  await page.locator(".analysis-v1-tts-clone-entry").click();
  const cloneDialog = page.locator(".analysis-v1-tts-dialog").first();
  await waitVisible(cloneDialog, "TTS Clone dialog");
  await waitVisible(cloneDialog.getByText("音色克隆", { exact: true }), "TTS Clone title");
  await waitVisible(cloneDialog.locator(".analysis-v1-tts-clone-top-actions button").filter({ hasText: "授权克隆" }).first(), "clone authorize button");
  await waitVisible(cloneDialog.getByText("还没有克隆音色 ID", { exact: true }), "empty clone voice id state");

  const cloneRequestsBeforeCancel = state.quickAdv.clone.length;
  await clickButtonIn(cloneDialog.locator(".analysis-v1-tts-clone-top-actions"), "授权克隆");
  const cloneConsentDialog = page.locator(".analysis-v1-tts-clone-consent-dialog").first();
  await waitVisible(cloneConsentDialog.getByText("请确认你已获得参考声音权利人的授权。", { exact: true }), "clone consent copy");
  await activate(cloneConsentDialog.locator(".analysis-v1-tts-clone-consent-actions button").filter({ hasText: "取消" }).first(), "cancel clone consent");
  await page.waitForTimeout(250);
  assert.equal(state.quickAdv.clone.length, cloneRequestsBeforeCancel, "dismissed clone confirmation should not call the clone API");

  await clickButtonIn(cloneDialog.locator(".analysis-v1-tts-clone-top-actions"), "克隆配置");
  const cloneConfigDialog = page.locator(".analysis-v1-tts-clone-config-dialog").first();
  await waitVisible(cloneConfigDialog.getByText("克隆配置", { exact: true }), "clone config title");
  await waitVisible(cloneConfigDialog.locator(".analysis-v1-tts-clone-head-actions button").filter({ hasText: "状态" }).first(), "clone config status button");
  await cloneConfigDialog.locator(".analysis-v1-tts-clone-config-fields label").filter({ hasText: "克隆前缀" }).locator("input").fill("e2eadv");
  await cloneConfigDialog.locator(".analysis-v1-tts-clone-config-fields label").filter({ hasText: "授权备注" }).locator("input").fill("E2E consent note");
  await activate(cloneConfigDialog.locator(".analysis-v1-tts-close").first(), "close clone config");
  await cloneConfigDialog.waitFor({ state: "hidden", timeout: 10000 });
  await clickButtonIn(cloneDialog.locator(".analysis-v1-tts-clone-top-actions"), "授权克隆");
  await waitVisible(cloneConsentDialog.getByText("请确认你已获得参考声音权利人的授权。", { exact: true }), "clone consent copy before accept");
  await activate(cloneConsentDialog.locator(".analysis-v1-tts-clone-consent-actions button").filter({ hasText: "确认授权" }).first(), "confirm clone consent");
  await waitVisible(cloneDialog.getByText("e2eadv_voice_e2e_001", { exact: true }), "clone voice id chip");
  const successClonePayload = state.quickAdv.clone.at(-1);
  assert.equal(successClonePayload.clone_consent_confirmed, true, "clone success request should include consent");
  assert.equal(successClonePayload.clone_prefix, "e2eadv", "clone success request should include prefix");
  assert.equal(successClonePayload.clone_target_model, "cosyvoice-v3.5-flash", "clone success request should include target model");
  assert.equal(successClonePayload.clone_consent_note, "E2E consent note", "clone success request should include note");
  await waitVisible(cloneDialog.getByText("cosyvoice / cosyvoice-v3.5-flash", { exact: true }), "clone provider and model");

  await activate(cloneDialog.locator(".analysis-v1-tts-adv-clones button[title='试听克隆音色']").first(), "clone preview button");
  await waitVisible(page.locator(".analysis-v1-tts-preview-dialog"), "clone preview dialog");
  await waitVisible(page.getByText("试听调音", { exact: true }), "clone preview title");
  const clonePreviewPromptText = await page.locator(".analysis-v1-tts-preview-dialog textarea[aria-label='试听提示词']").inputValue();
  assert.ok(!clonePreviewPromptText.includes("当前 voice"), "clone preview prompt textarea should not display the voice id");
  await activate(page.locator(".analysis-v1-tts-preview-dialog button[aria-label='播放预览']").first(), "clone generated preview button");
  await page.waitForTimeout(250);
  const previewPayload = state.previewPayloads.at(-1);
  assert.equal(previewPayload.provider, "cosyvoice", "clone preview should use the CosyVoice provider");
  assert.equal(previewPayload.model, "cosyvoice-v3.5-flash", "clone preview should use the clone target model");
  assert.equal(previewPayload.voice_id, "e2eadv_voice_e2e_001", "clone preview should use the cloned voice id");
  assert.equal(previewPayload.text, "欢迎使用 OpenCrew。这是一段克隆音色试听，请用自然、清晰、稳定的短视频口播方式朗读。", "clone preview should send plain speech text");
  assert.ok(!String(previewPayload.prompt || "").includes("当前 voice"), "clone preview prompt should not embed the voice id");
  await activate(page.locator(".analysis-v1-tts-preview-dialog .analysis-v1-tts-close").first(), "clone preview close button");
  await page.locator(".analysis-v1-tts-preview-dialog").waitFor({ state: "hidden", timeout: 10000 });

  await activate(cloneDialog.locator(".analysis-v1-tts-close").first(), "close TTS Clone");
  await page.locator(".analysis-v1-tts-dialog").waitFor({ state: "hidden", timeout: 10000 });
  await page.locator(".analysis-v1-tts-builder-entry").click();
  const reopenedDialog = page.locator(".analysis-v1-tts-dialog").first();
  await waitVisible(reopenedDialog.getByText("克隆音色 e2eadv_voice_e2e_001", { exact: true }), "clone merged into candidates");
  await activate(reopenedDialog.locator(".analysis-v1-tts-row").filter({ hasText: "克隆音色 e2eadv_voice_e2e_001" }).first(), "select clone candidate row");
  await waitVisible(reopenedDialog.getByText("当前选用", { exact: true }), "clone selected badge");
  await waitForCondition(() => state.selectionPayloads.length > 0, "candidate selection save request");
  const savedCloneSelection = state.selectionPayloads.at(-1);
  assert.equal(savedCloneSelection.candidate_id, "e2eadv_voice_e2e_001", "selecting a candidate should save its id to Session Variables");
  assert.equal(savedCloneSelection.provider, "cosyvoice", "saved candidate should keep provider");
  assert.equal(savedCloneSelection.model, "cosyvoice-v3.5-flash", "saved candidate should keep model");
  assert.equal(savedCloneSelection.voice_id, "e2eadv_voice_e2e_001", "saved candidate should keep voice id");
  assert.ok(String(savedCloneSelection.prompt || "").trim(), "saved candidate should include the TTS prompt");
  await clickButtonIn(reopenedDialog.locator(".analysis-v1-tts-builder-actions"), "匹配配置");
  const reopenedMatchConfigDialog = page.locator(".analysis-v1-tts-match-config-dialog").first();
  await waitVisible(reopenedMatchConfigDialog.getByText("匹配配置", { exact: true }), "reopened match config title");

  await clickButtonIn(reopenedMatchConfigDialog.locator(".analysis-v1-tts-match-head-actions"), "生成候选");
  await waitForTextIncludes(reopenedDialog, "高级匹配已启动后台运行 #adv-e2e-1", "generate status");
  const runPayload = state.runPayloads.at(-1);
  assert.equal(runPayload.mode, "run_only_step", "generate should start run-only mode");
  assert.equal(runPayload.run_only_step_id, "03_03", "generate should run 03_03");
  assert.equal(runPayload.tts_builder_mode, "quick_adv", "generate should set quick_adv builder mode");
  assert.deepEqual(
    {
      stage1_count: runPayload.options?.stage1_count,
      stage2_count: runPayload.options?.stage2_count,
      final_count: runPayload.options?.final_count,
      enable_speechbrain: runPayload.options?.enable_speechbrain,
    },
    { stage1_count: 9, stage2_count: 4, final_count: 2, enable_speechbrain: true },
    "generate should forward advanced ranking options",
  );
  assert.ok(state.savedConfigPayloads.length >= 1, "generate should save task config before run");

  await activate(reopenedMatchConfigDialog.locator(".analysis-v1-tts-close").first(), "close match config after generate");
  await reopenedMatchConfigDialog.waitFor({ state: "hidden", timeout: 10000 });
  await waitVisible(reopenedDialog.getByText("克隆音色 e2eadv_voice_e2e_001", { exact: true }), "clone candidate remains merged after generate");
  await waitVisible(reopenedDialog.getByText("E2E Candidate Alpha", { exact: true }), "candidate alpha row");
  await waitVisible(reopenedDialog.getByText("当前选用", { exact: true }), "selected candidate badge");
  const alphaCandidateRow = reopenedDialog.locator(".analysis-v1-tts-row").filter({ hasText: "E2E Candidate Alpha" }).first();
  await activate(alphaCandidateRow.locator(".analysis-v1-tts-info").first(), "candidate alpha score button");
  await waitVisible(reopenedDialog.getByText("最接近：音色、语速；试听关注：清晰度", { exact: true }), "candidate recommendation explanation");
  await waitVisible(reopenedDialog.getByText("声音质感", { exact: true }).first(), "candidate dimension score");
  await waitForTextIncludes(reopenedDialog, "评分 91.40", "candidate detail score");
  await assertDialogWithinViewport(page, "desktop final");

  fs.mkdirSync(path.dirname(SCREENSHOT_PATH), { recursive: true });
  await page.screenshot({ path: SCREENSHOT_PATH, fullPage: false });
}

async function runMobileLayoutCheck(page) {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(350);
  const dialog = page.locator(".analysis-v1-tts-dialog").first();
  await waitVisible(dialog, "mobile TTS dialog");
  const layout = await assertDialogWithinViewport(page, "mobile");
  assert.ok(layout.rect.width <= 390, `mobile: dialog width should fit viewport (${JSON.stringify(layout)})`);
  await clickButtonIn(dialog.locator(".analysis-v1-tts-builder-actions"), "匹配配置");
  const matchConfigDialog = page.locator(".analysis-v1-tts-match-config-dialog").first();
  await waitVisible(matchConfigDialog.locator(".analysis-v1-tts-match-head-actions button").filter({ hasText: "状态" }), "mobile action buttons");
  await waitVisible(dialog.locator(".analysis-v1-tts-close"), "mobile close button");
}

async function main() {
  console.log("TTSBuilderQuickAdv UI automation plan:");
  TEST_PLAN.forEach((item, index) => console.log(`${index + 1}. ${item}`));

  const { chromium } = loadPlaywright();
  const browser = await launchChromium(chromium);
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  await context.addInitScript(() => {
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: {
        writeText: async (text) => {
          window.__opencrewE2EClipboard = String(text || "");
        },
      },
    });
  });
  const page = await context.newPage();
  const state = createMockState();
  page.on("pageerror", (error) => state.pageErrors.push(error.message));

  try {
    await installMockRoutes(page, state);
    await runDesktopFlow(page, state);
    await runMobileLayoutCheck(page);
    assert.deepEqual(state.unhandled, [], "all API requests should be covered by the e2e mock");
    assert.deepEqual(state.pageErrors, [], "page should not throw uncaught errors");
    console.log(JSON.stringify({
      ok: true,
      url: TEST_URL,
      screenshot: SCREENSHOT_PATH,
      quick_adv_calls: {
        state: state.quickAdv.state.length,
        catalog: state.quickAdv.catalog.length,
        sample: state.quickAdv.sample.length,
        rank: state.quickAdv.rank.length,
        clone: state.quickAdv.clone.length,
      },
      run_payload: state.runPayloads.at(-1),
    }, null, 2));
  } catch (error) {
    const debug = await page.evaluate(() => {
      const dialog = document.querySelector(".analysis-v1-tts-dialog");
      return {
        url: window.location.href,
        dialogText: dialog?.innerText?.slice(0, 2500) || "",
        activeElement: document.activeElement?.outerHTML?.slice(0, 500) || "",
      };
    }).catch((exc) => ({ debug_error: exc instanceof Error ? exc.message : String(exc) }));
    console.error(JSON.stringify({
      e2e_debug: debug,
      quick_adv_calls: Object.fromEntries(Object.entries(state.quickAdv).map(([key, value]) => [key, {
        count: value.length,
        last: value.at(-1) || null,
      }])),
      saved_config_count: state.savedConfigPayloads.length,
      run_payload_count: state.runPayloads.length,
      unhandled: state.unhandled,
      page_errors: state.pageErrors,
    }, null, 2));
    throw error;
  } finally {
    await page.unrouteAll({ behavior: "ignoreErrors" }).catch(() => {});
    await browser.close().catch(() => {});
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
