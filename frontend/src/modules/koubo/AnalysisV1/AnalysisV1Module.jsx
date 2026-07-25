import { For, Show, createEffect, createMemo, createSignal, onCleanup, onMount } from "solid-js";
import { Portal } from "solid-js/web";
import { analysisV1Api } from "./analysisV1Api";
import { PROMPT_TABS, createRewriteDraft, formatDateTime, normalizeDialogueItems, normalizePromptBundle, statusTone, taskIdFromAnalysisV1Hash, FINAL_ITEMS_PATH, REWRITTEN_ITEMS_PATH, STORYBOARD_PATH, FRAME_MAP_PATH } from "./analysisV1Model";
import AnalysisV1DialogueView from "./components/AnalysisV1DialogueView.jsx";
import AnalysisV1PromptBuilder from "./components/AnalysisV1PromptBuilder.jsx";
import AnalysisV1TTSBuilder from "./components/AnalysisV1TTSBuilder.jsx";
import OneClickMovieDialog from "../DanceMimicV1/OneClickMovieDialog.jsx";
import { CodeIcon, CloseIcon, PlayClipIcon, RefreshIcon, SaveIcon, SimpleChevronIcon, SlidersIcon, SpeechIcon, TrashIcon, WaveformIcon } from "./analysisV1Icons.jsx";
import { ModelPresetCards, findModelPresetItem } from "../../../components/ModelPresetCards.jsx";
import StoryboardIcon from "../shared/StoryboardIcon.jsx";
import "../styles.css";
import "../DanceMimicV1/danceMimicV1.css";

function StatusBadge(props) {
  return <span class={`status-tag tag-${statusTone(props.status) === "ready" ? "ready" : statusTone(props.status) === "running" ? "available" : statusTone(props.status) === "failed" ? "failed" : "idle"}`}>{String(props.status || "draft")}</span>;
}

function isTerminalRunStatus(status) {
  return ["completed", "completed_with_sync_error", "failed", "blocked", "cancelled", "stale_running", "error"].includes(String(status || "").toLowerCase());
}

function isActiveRunStatus(status) {
  return ["queued", "running", "paused", "stopping"].includes(String(status || "").toLowerCase());
}

function finiteDurationSeconds(value) {
  if (value === null || value === undefined || value === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? Math.max(0, number) : null;
}

function stepDuration(step) {
  const finished = finiteDurationSeconds(step?.duration_seconds);
  if (finished !== null) return formatRunDuration(finished);
  const startedAt = Number(step?.started_at || 0);
  if (String(step?.status || "").toLowerCase() === "running" && startedAt > 0) {
    return formatRunDuration(Math.max(0, (Date.now() - startedAt) / 1000));
  }
  return "-";
}

function formatRunDuration(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "-";
  const totalSeconds = Math.max(0, Math.round(number));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  if (hours > 0) return `${hours}小时${minutes}分${seconds}秒`;
  if (minutes > 0) return `${minutes}分${seconds}秒`;
  return `${seconds}秒`;
}

function stepDurationSeconds(step) {
  const finished = finiteDurationSeconds(step?.duration_seconds);
  if (finished !== null) return finished;
  const startedAt = Number(step?.started_at || 0);
  if (String(step?.status || "").toLowerCase() === "running" && startedAt > 0) {
    return Math.max(0, (Date.now() - startedAt) / 1000);
  }
  return 0;
}

function asrModeNeedsCloudConsent(mode) {
  return ["default", "cloud"].includes(String(mode || "default").toLowerCase());
}

const TASK_LIST_AUTO_RUN_KEY = "koubo_task_list_auto_run";
const TASK_LIST_AUTO_ANALYSIS_DRAWER_KEY = "koubo_task_list_auto_analysis_drawer";

const TTS_BUILDER_RUN_CONFIG = {
  quick: { run_only_step_id: "03_02", tts_builder_mode: "quick", label: "快速匹配" },
  quick_adv: { run_only_step_id: "03_03", tts_builder_mode: "quick_adv", label: "高级匹配" },
};

const ANALYSIS_V1_STEP_DISPLAY_ZH = {
  "00": "准备会话变量",
  "01": "读取视频元数据",
  "02_01": "音频识别",
  "02_02": "字幕帧对齐",
  "03_01": "全量声音匹配",
  "03_02": "快速声音匹配",
  "03_03": "高级声音匹配",
  "04_01": "SRT 改写",
  "04_02": "全量分组",
  "04_03": "快速分组",
  "05_01": "视频计划生成",
  "05_02": "视频计划执行",
  "05_03": "图片计划生成",
  "05_04": "图片计划执行",
  "06_01": "视频计划合成",
};

const RUN_DETAIL_TABS = [
  { id: "overview", label: "概览" },
  { id: "metering", label: "计费" },
  { id: "parameters", label: "参数" },
  { id: "command", label: "命令" },
  { id: "files", label: "文件" },
  { id: "logs", label: "日志" },
];

const FREE_REWRITE_STORYBOARD_STEP_IDS = ["00", "04_01", "04_02"];
const FREE_REWRITE_STORYBOARD_PENDING_STEPS = [
  { id: "00", name: "Prepare Session Variables", display_name_zh: "准备会话变量" },
  { id: "04_01", name: "SRT Rewrite Free", display_name_zh: "SRT 自由改写" },
  { id: "04_02", name: "StoryBoard", display_name_zh: "全量分组" },
];
const FREE_REWRITE_PREREQUISITE_HINT = `自由改写需要先完成 02_02 字幕帧对齐，并生成 ${FINAL_ITEMS_PATH}。请先运行全部任务，或从 02_02 开始运行后再重试自由改写。`;

function rewriterPendingSteps(rewriteMode = "free") {
  return FREE_REWRITE_STORYBOARD_PENDING_STEPS.map((step) => {
    if (step.id !== "04_01") return step;
    return String(rewriteMode || "free") === "free"
      ? step
      : { ...step, name: "SRT Rewrite", display_name_zh: "SRT 改写" };
  });
}

function stepsForSelectedIds(ids, sourceSteps, fallbackSteps = []) {
  const sourceById = new Map((sourceSteps || []).map((step) => [String(step.id || ""), step]));
  const fallbackById = new Map((fallbackSteps || []).map((step) => [String(step.id || ""), step]));
  return ids.map((item) => {
    const id = String(item || "");
    const fallback = fallbackById.get(id) || null;
    const source = sourceById.get(id) || null;
    if (!fallback && !source) return null;
    return { ...(source || {}), ...(fallback || {}), id };
  }).filter(Boolean);
}

function runStatusLabel(status) {
  const value = String(status || "").toLowerCase();
  return {
    queued: "排队中",
    running: "运行中",
    paused: "已暂停",
    stopping: "正在停止",
    completed: "已完成",
    completed_with_sync_error: "完成但同步失败",
    failed: "失败",
    blocked: "阻断",
    cancelled: "已取消",
    stale_running: "运行状态失联",
    unavailable: "不可用",
  }[value] || value || "未知";
}

function stepStatusLabel(status) {
  const value = String(status || "").toLowerCase();
  return {
    pending: "等待",
    queued: "排队",
    reused: "复用",
    running: "运行中",
    completed: "完成",
    failed: "失败",
    blocked: "阻断",
    skipped: "跳过",
    cancelled: "已取消",
    stale_running: "失联",
    unavailable: "不可用",
  }[value] || value || "等待";
}

function prettyJson(value) {
  if (value === undefined || value === null || value === "") return "-";
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function quickWatchFileRows(quickWatch) {
  const files = quickWatch?.files || {};
  return [
    ...(files.inputs || []).map((item) => ({ ...item, group: "输入" })),
    ...(files.outputs || []).map((item) => ({ ...item, group: "输出" })),
    ...(files.prompts || []).map((item) => ({ ...item, group: "Prompt" })),
    ...(files.results || []).map((item) => ({ ...item, group: "结果" })),
  ];
}

function unitLabel(unit) {
  const value = String(unit || "");
  if (value === "input_token") return "输入 token";
  if (value === "output_token") return "输出 token";
  if (value === "image") return "图片";
  if (value === "video_second") return "视频秒";
  if (value === "audio_second") return "音频秒";
  if (value === "artifact_json_kb") return "JSON KB";
  if (value === "artifact_image_kb") return "图片 KB";
  if (value === "artifact_wav_kb") return "WAV KB";
  if (value === "request") return "请求";
  return value || "-";
}

function formatMicrosUsd(value) {
  const amount = Number(value || 0) / 1000000;
  const sign = amount < 0 ? "-" : "";
  const abs = Math.abs(amount);
  if (abs >= 100) return `${sign}$${abs.toFixed(0)}`;
  if (abs >= 1) return `${sign}$${abs.toFixed(2)}`;
  return `${sign}$${abs.toFixed(4)}`;
}

function formatOptionalMicrosUsd(value, available = true) {
  if (!available && Number(value || 0) === 0) return "-";
  return formatMicrosUsd(value);
}

function formatUnitMap(units) {
  const entries = Object.entries(units || {}).filter(([, value]) => Number(value || 0) > 0);
  if (entries.length === 0) return "-";
  return entries.map(([key, value]) => `${Number(value).toLocaleString(undefined, { maximumFractionDigits: 3 })} ${unitLabel(key)}`).join(" · ");
}

function formatPriceLines(lines) {
  const items = Array.isArray(lines) ? lines : [];
  if (items.length === 0) return "-";
  const priced = items.filter((line) => Number(line.provider_unit_cost_micros || 0) > 0 || Number(line.customer_unit_price_micros || 0) > 0);
  const displayItems = priced.length > 0 ? priced : items;
  return displayItems
    .slice(0, 3)
    .map((line) => `${formatMicrosUsd(line.provider_unit_cost_micros)}/${unitLabel(line.unit_key)} → ${formatMicrosUsd(line.customer_unit_price_micros)}`)
    .join(" · ");
}

function costBasisLabel(value) {
  if (value === "actual") return "实际";
  if (value === "estimated") return "估算";
  return "未计价";
}

function meteringTotals(metering) {
  return metering?.totals || {};
}

function hasMeteringData(metering) {
  return Number(meteringTotals(metering).request_count || 0) > 0;
}

function meteringChannelRows(metering) {
  return [
    { key: "api", label: "API", totals: metering?.api || {} },
    { key: "local_artifacts", label: "本地产物", totals: metering?.local_artifacts || {} },
  ];
}

function MeteringMetric(props) {
  return (
    <div class="analysis-v1-run-metering-metric">
      <label>{props.label}</label>
      <strong>{props.value}</strong>
      <Show when={props.detail}><small>{props.detail}</small></Show>
    </div>
  );
}

function StepMeteringOverview(props) {
  const totals = () => meteringTotals(props.metering);
  return (
    <Show when={hasMeteringData(props.metering)} fallback={<div class="analysis-v1-run-metering-empty">本步骤暂无 API 或本地产物计费记录</div>}>
      <div class="analysis-v1-run-metering-grid">
        <MeteringMetric label="请求" value={Number(totals().request_count || 0).toLocaleString()} detail={`${Number(totals().actual_cost_count || 0).toLocaleString()} 实际 / ${Number(totals().estimated_cost_count || 0).toLocaleString()} 估算`} />
        <MeteringMetric label="流量" value={formatUnitMap(totals().units)} />
        <MeteringMetric label="成本" value={formatMicrosUsd(totals().cost_micros ?? totals().provider_cost_micros)} detail={formatOptionalMicrosUsd(totals().actual_cost_micros, Number(totals().actual_cost_count || 0) > 0)} />
        <MeteringMetric label="收费" value={formatMicrosUsd(totals().charge_micros ?? totals().sell_micros)} />
        <MeteringMetric label="利润" value={formatMicrosUsd(totals().profit_micros)} />
      </div>
    </Show>
  );
}

function StepMeteringBreakdown(props) {
  return (
    <div class="analysis-v1-run-metering-panel">
      <StepMeteringOverview metering={props.metering} />
      <div class="analysis-v1-run-metering-table">
        <div class="analysis-v1-run-metering-row is-head">
          <span>来源</span>
          <span>请求</span>
          <span>流量</span>
          <span>成本</span>
          <span>收费</span>
          <span>利润</span>
        </div>
        <For each={meteringChannelRows(props.metering)}>{(row) => (
          <div class="analysis-v1-run-metering-row">
            <span>{row.label}</span>
            <span>{Number(row.totals?.request_count || 0).toLocaleString()}</span>
            <span>{formatUnitMap(row.totals?.units)}</span>
            <span>{formatMicrosUsd(row.totals?.cost_micros ?? row.totals?.provider_cost_micros)}</span>
            <span>{formatMicrosUsd(row.totals?.charge_micros ?? row.totals?.sell_micros)}</span>
            <span class="analysis-v1-run-metering-profit">{formatMicrosUsd(row.totals?.profit_micros)}</span>
          </div>
        )}</For>
      </div>
      <div class="analysis-v1-run-metering-items">
        <Show when={(props.metering?.items || []).length > 0} fallback={<div class="analysis-v1-run-metering-empty">暂无明细行</div>}>
          <For each={props.metering?.items || []}>{(item) => (
            <div class="analysis-v1-run-metering-item">
              <div>
                <strong>{item.provider || "-"} / {item.model_id || "-"}</strong>
                <span>{item.modality || "-"} · {costBasisLabel(item.cost_basis)}</span>
              </div>
              <span>{formatUnitMap(item.units)}</span>
              <span>{formatPriceLines(item.unit_lines)}</span>
              <span>{formatMicrosUsd(item.provider_cost_micros ?? item.cost_micros)}</span>
              <span>{formatMicrosUsd(item.charge_micros ?? item.sell_micros)}</span>
              <span class="analysis-v1-run-metering-profit">{formatMicrosUsd(item.profit_micros)}</span>
            </div>
          )}</For>
        </Show>
      </div>
    </div>
  );
}

function findPreferredRunModel(models) {
  return findModelPresetItem(models, "max") ?? null;
}

export default function AnalysisV1Module(props) {
  const isAdmin = () => Boolean(props.roleAccess?.isAdmin);
  const emptyPromptModels = { items: [], default_model: { providerID: "", modelID: "" } };
  const [tasks, setTasks] = createSignal([]);
  const [selectedTaskId, setSelectedTaskId] = createSignal(taskIdFromAnalysisV1Hash(props.routeHash));
  const [detail, setDetail] = createSignal(null);
  const [draft, setDraft] = createSignal(createRewriteDraft(null));
  const [modelCatalog, setModelCatalog] = createSignal(emptyPromptModels);
  const [dialogueItems, setDialogueItems] = createSignal([]);
  const [dialogueEditMode, setDialogueEditMode] = createSignal(false);
  const [dialogueDrafts, setDialogueDrafts] = createSignal({});
  const [dialogueSaveStatus, setDialogueSaveStatus] = createSignal("");
  const [savingDialogue, setSavingDialogue] = createSignal(false);
  const [storyboardPayload, setStoryboardPayload] = createSignal(null);
  const [ttsBuilderPayload, setTtsBuilderPayload] = createSignal(null);
  const [ttsModelConfig, setTtsModelConfig] = createSignal(null);
  const [taskListOpen, setTaskListOpen] = createSignal(false);
  const [promptDrawerOpen, setPromptDrawerOpen] = createSignal(false);
  const [ttsBuilderOpen, setTtsBuilderOpen] = createSignal(false);
  const [ttsCloneOpen, setTtsCloneOpen] = createSignal(false);
  const [activePromptTab, setActivePromptTab] = createSignal("rewrite");
  const [promptDrawerMode, setPromptDrawerMode] = createSignal("prompt_builder");
  const [promptModelDialogOpen, setPromptModelDialogOpen] = createSignal(false);
  const [promptModelFilter, setPromptModelFilter] = createSignal("");
  const [runModelDialogOpen, setRunModelDialogOpen] = createSignal(false);
  const [runModelDialogPosition, setRunModelDialogPosition] = createSignal(null);
  const [runDialogPurpose, setRunDialogPurpose] = createSignal("task");
  const [runModelFilter, setRunModelFilter] = createSignal("");
  const [runProgressOpen, setRunProgressOpen] = createSignal(false);
  const [runProgressDialogPosition, setRunProgressDialogPosition] = createSignal(null);
  const [runProgress, setRunProgress] = createSignal(null);
  const [oneClickMovieOpen, setOneClickMovieOpen] = createSignal(false);
  const [oneClickMovieProgress, setOneClickMovieProgress] = createSignal(null);
  const [runPlan, setRunPlan] = createSignal(null);
  const [pendingRunOverrides, setPendingRunOverrides] = createSignal(null);
  const [runMode, setRunMode] = createSignal("run_all");
  const [runStartStepId, setRunStartStepId] = createSignal("");
  const [runEndStepId, setRunEndStepId] = createSignal("");
  const [runOnlyStepId, setRunOnlyStepId] = createSignal("");
  const [runPauseBeforeStepId, setRunPauseBeforeStepId] = createSignal("");
  const [selectedRunStepId, setSelectedRunStepId] = createSignal("");
  const [runStepDetailOpen, setRunStepDetailOpen] = createSignal(false);
  const [runDetailTab, setRunDetailTab] = createSignal("overview");
  const [stepQuickWatch, setStepQuickWatch] = createSignal(null);
  const [stepMenu, setStepMenu] = createSignal(null);
  const [runCommandBusy, setRunCommandBusy] = createSignal("");
  const [runAsrMode, setRunAsrMode] = createSignal("default");
  const [runAllowCloudAsr, setRunAllowCloudAsr] = createSignal(true);
  const [runTtsBuilderMode, setRunTtsBuilderMode] = createSignal("quick");
  const [runRewriteMode, setRunRewriteMode] = createSignal("strict");
  const [runStoryboardMode, setRunStoryboardMode] = createSignal("model");
  const [paramsCollapsed, setParamsCollapsed] = createSignal(true);
  const [loading, setLoading] = createSignal(false);
  const [busy, setBusy] = createSignal(false);
  const [creatingTask, setCreatingTask] = createSignal(false);
  const [uploadingVideo, setUploadingVideo] = createSignal(false);
  const [error, setError] = createSignal("");

  const task = createMemo(() => detail()?.task || null);
  const promptModels = createMemo(() => {
    const models = detail()?.prompt_models;
    return models?.items?.length ? models : modelCatalog();
  });
  const promptModelProviders = createMemo(() => {
    const seen = new Map();
    promptModels().items.forEach((item) => {
      if (!seen.has(item.providerID)) seen.set(item.providerID, item.providerName);
    });
    return Array.from(seen.entries()).map(([providerID, providerName]) => ({ providerID, providerName }));
  });
  const selectedPromptModel = createMemo(() => promptModels().items.find((item) => item.providerID === draft()?.prompt_model_provider && item.modelID === draft()?.prompt_model_id));
  const filteredPromptModels = createMemo(() => {
    const providerID = draft()?.prompt_model_provider;
    const keyword = promptModelFilter().trim().toLowerCase();
    return promptModels().items.filter((item) => {
      if (providerID && item.providerID !== providerID) return false;
      if (!keyword) return true;
      return `${item.providerName} ${item.modelName} ${item.modelID}`.toLowerCase().includes(keyword);
    });
  });
  const userPromptModelOptions = createMemo(() => {
    const seen = new Map();
    promptModels().items.forEach((item) => {
      if (!seen.has(item.modelID)) seen.set(item.modelID, item);
    });
    return Array.from(seen.values());
  });
  const promptModelSelectItems = createMemo(() => isAdmin() ? filteredPromptModels() : userPromptModelOptions());
  const filteredRunModels = createMemo(() => {
    const providerID = draft()?.run_model_provider;
    const keyword = runModelFilter().trim().toLowerCase();
    return promptModels().items.filter((item) => {
      if (providerID && item.providerID !== providerID) return false;
      if (!keyword) return true;
      return `${item.providerName} ${item.modelName} ${item.modelID}`.toLowerCase().includes(keyword);
    });
  });
  const userRunModelOptions = createMemo(() => {
    const seen = new Map();
    promptModels().items.forEach((item) => {
      if (!seen.has(item.modelID)) seen.set(item.modelID, item);
    });
    return Array.from(seen.values());
  });
  const runModelSelectItems = createMemo(() => isAdmin() ? filteredRunModels() : userRunModelOptions());
  const hasStoryBoardFile = createMemo(() => storyboardPayload() !== null);
  const runProgressMessage = createMemo(() => runProgress()?.error || runProgress()?.sync_error || runProgress()?.summary || "");
  const runPlanSteps = createMemo(() => runPlan()?.steps || runProgress()?.steps || []);
  const runStepIds = createMemo(() => runPlanSteps().map((step) => String(step.id || "")).filter(Boolean));
  const isFreeRewriteRunDialog = createMemo(() => runDialogPurpose() === "free_rewrite_storyboard");
  const isScriptStoryboardRunDialog = createMemo(() => runDialogPurpose() === "script_storyboard");
  const isThreeStepStoryboardRunDialog = createMemo(() => isFreeRewriteRunDialog() || isScriptStoryboardRunDialog());
  const isPromptBuilderFullTaskRunDialog = createMemo(() => runDialogPurpose() === "prompt_builder_full_task");
  const activeRunProgress = createMemo(() => runProgress() && isActiveRunStatus(runProgress()?.status) ? runProgress() : null);
  const activeOneClickMovieProgress = createMemo(() => oneClickMovieProgress() && isActiveRunStatus(oneClickMovieProgress()?.status) ? oneClickMovieProgress() : null);
  const oneClickMovieMessage = createMemo(() => oneClickMovieProgress()?.error || oneClickMovieProgress()?.summary || "");
  const displayedRunStepIds = createMemo(() => {
    const progressPlan = runProgress()?.plan || {};
    const progressIds = progressPlan.selected_step_ids || progressPlan.execute_step_ids || [];
    const pendingIds = pendingRunOverrides()?.selected_step_ids || [];
    return (progressIds.length ? progressIds : pendingIds).map((item) => String(item || "")).filter(Boolean);
  });
  const visibleRunPlanSteps = createMemo(() => {
    const ids = displayedRunStepIds();
    const steps = runPlanSteps();
    if (!ids.length) return steps;
    const fallbackSteps = pendingRunOverrides() ? rewriterPendingSteps(pendingRunOverrides()?.rewrite_mode) : [];
    return stepsForSelectedIds(ids, steps, fallbackSteps);
  });
  const visibleRunProgressSteps = createMemo(() => {
    const ids = displayedRunStepIds();
    const steps = runProgress()?.steps || [];
    if (!ids.length) return steps;
    const fallbackSteps = (pendingRunOverrides() || runProgress()?.plan?.rewrite_mode) ? rewriterPendingSteps(pendingRunOverrides()?.rewrite_mode || runProgress()?.plan?.rewrite_mode) : [];
    return stepsForSelectedIds(ids, steps, fallbackSteps);
  });
  const selectedRunStep = createMemo(() => visibleRunProgressSteps().find((step) => String(step.id || "") === selectedRunStepId()) || visibleRunProgressSteps()[0] || null);
  const selectedStepQuickWatch = createMemo(() => stepQuickWatch()?.step_id === selectedRunStepId() ? stepQuickWatch()?.quick_watch || {} : selectedRunStep()?.quick_watch || {});
  const plannedPauseBeforeStepId = createMemo(() => runPauseBeforeStepId() || runProgress()?.pause_before_step_id || runProgress()?.plan?.pause_before_step_id || "");
  const runProgressTotalDuration = createMemo(() => visibleRunProgressSteps().reduce((total, step) => total + stepDurationSeconds(step), 0));
  const isTtsBuilderDialogRunProgress = createMemo(() => String(runProgress()?.plan?.options?.source || "") === "tts_builder_dialog");
  const ttsBuilderDialogRunHint = createMemo(() => {
    if (!isTtsBuilderDialogRunProgress()) return "";
    if (String(runProgress()?.status || "").toLowerCase() !== "stale_running") return "";
    const count = Array.isArray(ttsBuilderPayload()?.candidates) ? ttsBuilderPayload().candidates.length : 0;
    if (count > 0) return `运行心跳已失联，但已读取到 ${count} 个候选声音。关闭此窗口后，在“音色选择 > 候选试听”里试听。`;
    return "运行心跳已失联。关闭此窗口后回到“音色选择”，点“状态”刷新；如果仍没有候选，再重新生成。";
  });
  let runProgressTimer = null;
  let oneClickMovieTimer = null;
  let busyInFlight = 0;
  let loadingInFlight = 0;
  let uploadingVideoInFlight = 0;
  let runCommandBusyToken = 0;

  function sameTaskId(left, right) {
    return String(left ?? "") === String(right ?? "");
  }

  function isSelectedTask(taskId) {
    return taskId !== null && taskId !== undefined && sameTaskId(taskId, selectedTaskId());
  }

  function taskSummaryFromDetail(nextDetail) {
    const item = nextDetail?.task || {};
    if (!item?.id) return null;
    return {
      id: item.id,
      session_id: item.session_id,
      title: item.title || `Task ${item.id}`,
      status: item.status || "draft",
      workflow_mode: item.workflow_mode || "analysis_v1",
      reference_video_path: item.reference_video_path || "",
      industry: item.industry || "",
      persona: item.persona || "",
      target_audience: item.target_audience || "",
      analysis_goal: item.analysis_goal || "",
      video_formula: item.video_formula || "",
      latest_attempt_id: item.latest_attempt_id || null,
      run_model_provider: item.run_model_provider || "",
      run_model_id: item.run_model_id || "",
      created_at: item.created_at || Date.now(),
      updated_at: item.updated_at || item.created_at || Date.now(),
    };
  }

  function upsertTaskSummary(summary) {
    if (!summary?.id) return;
    setTasks((items) => [summary, ...items.filter((item) => !item?._creating && !sameTaskId(item?.id, summary.id))]);
  }

  function consumeTaskListIntent(storageKey, taskId) {
    try {
      const raw = window.sessionStorage?.getItem(storageKey);
      if (!raw) return false;
      const payload = JSON.parse(raw);
      const createdAt = Number(payload?.createdAt || 0);
      const isFresh = !createdAt || Date.now() - createdAt < 5 * 60 * 1000;
      if (!isFresh) {
        window.sessionStorage?.removeItem(storageKey);
        return false;
      }
      if (!sameTaskId(payload?.taskId, taskId)) return false;
      window.sessionStorage?.removeItem(storageKey);
      return true;
    } catch {
      window.sessionStorage?.removeItem(storageKey);
      return false;
    }
  }

  async function maybeOpenTaskListAutoRun(taskId) {
    if (!consumeTaskListIntent(TASK_LIST_AUTO_RUN_KEY, taskId)) return;
    await openScriptStoryboardRunDialog();
  }

  function maybeOpenTaskListAnalysisDrawer(taskId) {
    if (!consumeTaskListIntent(TASK_LIST_AUTO_ANALYSIS_DRAWER_KEY, taskId)) return;
    openPromptBuilderDrawer();
  }

  function setTaskError(taskId, exc) {
    if (isSelectedTask(taskId)) setError(exc instanceof Error ? exc.message : String(exc));
  }

  function dialogueDraftFromItems(items) {
    const next = {};
    for (const item of items || []) {
      if (item?.id) next[item.id] = String(item.rewrittenDialogue || "");
    }
    return next;
  }

  function startDialogueEdit() {
    setDialogueDrafts(dialogueDraftFromItems(dialogueItems()));
    setDialogueSaveStatus("");
    setDialogueEditMode(true);
  }

  function cancelDialogueEdit() {
    setDialogueDrafts({});
    setDialogueSaveStatus("");
    setDialogueEditMode(false);
  }

  function updateDialogueDraft(srtId, value) {
    setDialogueDrafts((prev) => ({ ...prev, [srtId]: value }));
  }

  function beginBusy() {
    busyInFlight += 1;
    setBusy(true);
  }

  function endBusy() {
    busyInFlight = Math.max(0, busyInFlight - 1);
    setBusy(busyInFlight > 0);
  }

  function beginLoading() {
    loadingInFlight += 1;
    setLoading(true);
  }

  function endLoading() {
    loadingInFlight = Math.max(0, loadingInFlight - 1);
    setLoading(loadingInFlight > 0);
  }

  function beginUploadingVideo() {
    uploadingVideoInFlight += 1;
    setUploadingVideo(true);
  }

  function endUploadingVideo() {
    uploadingVideoInFlight = Math.max(0, uploadingVideoInFlight - 1);
    setUploadingVideo(uploadingVideoInFlight > 0);
  }

  function beginRunCommand(command) {
    runCommandBusyToken += 1;
    const token = runCommandBusyToken;
    setRunCommandBusy(command);
    return token;
  }

  function endRunCommand(token) {
    if (token === runCommandBusyToken) setRunCommandBusy("");
  }

  function resetRunCommandBusy() {
    runCommandBusyToken += 1;
    setRunCommandBusy("");
  }

  function clearRunScopedState() {
    clearRunProgressTimer();
    clearOneClickMovieTimer();
    setRunProgress(null);
    setOneClickMovieProgress(null);
    setRunPlan(null);
    setPendingRunOverrides(null);
    setSelectedRunStepId("");
    setRunStepDetailOpen(false);
    setStepQuickWatch(null);
    setStepMenu(null);
    resetRunCommandBusy();
    setRunProgressOpen(false);
    setOneClickMovieOpen(false);
  }

  function runStepDisplayName(step) {
    if (!step) return "";
    const stepId = String(step.id || "").trim();
    const rawName = step.display_name_zh || step.name_zh || step.label_zh || ANALYSIS_V1_STEP_DISPLAY_ZH[stepId] || step.name || stepId;
    const name = String(rawName || stepId).trim();
    if (!stepId) return name;
    if (name === stepId || name.startsWith(`${stepId} `)) return name;
    if (name.startsWith(`${stepId}_`)) return `${stepId} ${name.slice(stepId.length + 1)}`;
    return `${stepId} ${name}`;
  }

  const stepMenuStep = createMemo(() => {
    const menu = stepMenu();
    if (!menu?.stepId) return null;
    return visibleRunProgressSteps().find((step) => String(step.id || "") === String(menu.stepId))
      || visibleRunPlanSteps().find((step) => String(step.id || "") === String(menu.stepId))
      || null;
  });

  function openStepContextMenu(event, stepId) {
    event.preventDefault();
    event.stopPropagation();
    const width = 184;
    const height = 296;
    const margin = 8;
    const viewportWidth = typeof window !== "undefined" ? window.innerWidth : event.clientX + width + margin;
    const viewportHeight = typeof window !== "undefined" ? window.innerHeight : event.clientY + height + margin;
    const x = Math.max(margin, Math.min(event.clientX, viewportWidth - width - margin));
    const preferredY = event.clientY + height + margin > viewportHeight ? event.clientY - height : event.clientY;
    const y = Math.max(margin, Math.min(preferredY, viewportHeight - Math.min(height, viewportHeight - margin * 2) - margin));
    setSelectedRunStepId(stepId);
    setStepMenu({
      stepId,
      x,
      y,
    });
  }

  function latestRunnableStepId() {
    const steps = runProgress()?.steps || [];
    const runnableStatuses = new Set(["failed", "blocked", "cancelled", "stale_running", "error"]);
    const blockedStep = steps.find((step) => runnableStatuses.has(String(step.status || "").toLowerCase()));
    if (blockedStep?.id) return String(blockedStep.id);
    const pendingStep = steps.find((step) => String(step.status || "").toLowerCase() === "pending");
    if (pendingStep?.id) return String(pendingStep.id);
    return "";
  }

  function defaultRunOverrides() {
    if (pendingRunOverrides()) return pendingRunOverrides();
    if (shouldRerunEntireTask()) {
      return { mode: "rerun_all", previous_attempt_id: previousRunAttemptId() };
    }
    const stepId = latestRunnableStepId();
    return stepId ? { mode: "run_from_step", start_step_id: stepId } : { mode: "run_all" };
  }

  function previousRunAttemptId() {
    return runProgress()?.attempt_id || task()?.latest_attempt_id || "";
  }

  function shouldRerunEntireTask() {
    const previousAttemptId = previousRunAttemptId();
    return Boolean(previousAttemptId && (!runProgress() || isTerminalRunStatus(runProgress()?.status)));
  }

  function primaryRunActionLabel() {
    if (busy()) return "执行中";
    if (pendingRunOverrides()) {
      if (pendingRunOverrides()?.mode === "run_selected_steps") return pendingRunOverrides()?.rewrite_mode === "free" ? "运行自由改写链路" : "运行 SRT 改写链路";
      if (pendingRunOverrides()?.mode === "run_all") return "运行全部任务";
    }
    return shouldRerunEntireTask() ? "重跑整个任务" : "执行";
  }

  function runPayloadExecutesStep(payload, stepId) {
    const ids = runStepIds();
    const targetIndex = ids.indexOf(stepId);
    if (targetIndex < 0) return false;
    const mode = String(payload?.mode || "run_all");
    if (mode === "run_only_step") return payload?.run_only_step_id === stepId;
    if (mode === "run_selected_steps") return (payload?.selected_step_ids || []).map((item) => String(item || "")).includes(stepId);
    if (mode === "rerun_failed") return true;
    const startId = mode === "run_range" || mode === "run_from_step" || mode === "rerun_from_step" ? (payload?.start_step_id || ids[0]) : ids[0];
    const endId = mode === "run_range" ? (payload?.end_step_id || ids[ids.length - 1]) : ids[ids.length - 1];
    const startIndex = ids.indexOf(startId);
    const endIndex = ids.indexOf(endId);
    if (startIndex < 0 || endIndex < 0) return false;
    return targetIndex >= startIndex && targetIndex <= endIndex;
  }

  function freeRewriteStoryboardRunOverrides() {
    return {
      mode: "run_selected_steps",
      selected_step_ids: FREE_REWRITE_STORYBOARD_STEP_IDS,
      rewrite_mode: runRewriteMode(),
      storyboard_mode: "model",
      include_tts_builder: false,
      tts_builder_mode: "skip",
      run_model_provider: draft().run_model_provider,
      run_model_id: draft().run_model_id,
      force: true,
      options: {
        source: "free_rewrite_storyboard_button",
      },
    };
  }

  function fullTaskRunOverrides() {
    return {
      mode: "run_all",
      rewrite_mode: runRewriteMode(),
      storyboard_mode: runStoryboardMode(),
      include_tts_builder: runTtsBuilderMode() !== "skip",
      tts_builder_mode: runTtsBuilderMode(),
      allow_cloud_asr_data_transfer: runAllowCloudAsr(),
      run_model_provider: draft().run_model_provider,
      run_model_id: draft().run_model_id,
      force: true,
      options: {
        source: "prompt_builder_full_task_run",
      },
    };
  }

  function createDraftFromDetail(nextDetail) {
    const models = nextDetail?.prompt_models ?? promptModels();
    const defaultProvider = models.default_model?.providerID || "";
    const defaultModel = models.default_model?.modelID || "";
    const defaultPromptModel = findModelPresetItem(models.items, "max") ?? findModelPresetItem(models.items, "flash") ?? null;
    const defaultRunModel = findPreferredRunModel(models.items) ?? models.items?.find((item) => item.providerID === defaultProvider && item.modelID === defaultModel) ?? null;
    const next = createRewriteDraft(nextDetail?.task);
    const currentPrompt = nextDetail?.current_prompt_version || {};
    return normalizePromptBundle({
      ...next,
      prompt_model_provider: next.prompt_model_provider || nextDetail?.current_prompt_version?.prompt_model_provider || defaultPromptModel?.providerID || defaultProvider,
      prompt_model_id: next.prompt_model_id || nextDetail?.current_prompt_version?.prompt_model_id || defaultPromptModel?.modelID || defaultModel,
      run_model_provider: next.run_model_provider || defaultRunModel?.providerID || defaultProvider,
      run_model_id: next.run_model_id || defaultRunModel?.modelID || defaultModel,
      rewrite_simple_prompt: next.rewrite_simple_prompt || currentPrompt.rewrite_simple_prompt || currentPrompt.simple_prompt || "",
      rewrite_final_prompt: next.rewrite_final_prompt || currentPrompt.rewrite_final_prompt || currentPrompt.final_prompt || "",
      storyboard_simple_prompt: next.storyboard_simple_prompt || currentPrompt.storyboard_simple_prompt || "",
      storyboard_final_prompt: next.storyboard_final_prompt || currentPrompt.storyboard_final_prompt || "",
    });
  }

  function updatePromptModelProvider(providerID) {
    const models = promptModels().items.filter((item) => item.providerID === providerID);
    const preferred = models.find((item) => item.modelID === promptModels().default_model?.modelID) ?? models[0];
    setDraft((prev) => ({ ...prev, prompt_model_provider: providerID, prompt_model_id: preferred?.modelID ?? "" }));
  }

  function updatePromptModelId(modelID) {
    if (isAdmin()) {
      setDraft((prev) => ({ ...prev, prompt_model_id: modelID }));
      return;
    }
    const selected = promptModels().items.find((item) => item.modelID === modelID) || userPromptModelOptions()[0];
    setDraft((prev) => ({ ...prev, prompt_model_provider: selected?.providerID || prev.prompt_model_provider || "", prompt_model_id: selected?.modelID || modelID }));
  }

  function updateRunModelProvider(providerID) {
    const models = promptModels().items.filter((item) => item.providerID === providerID);
    const preferred = findPreferredRunModel(models) ?? models.find((item) => item.modelID === promptModels().default_model?.modelID) ?? models[0];
    setDraft((prev) => ({ ...prev, run_model_provider: providerID, run_model_id: preferred?.modelID ?? "" }));
  }

  function updateRunModelId(modelID) {
    if (isAdmin()) {
      setDraft((prev) => ({ ...prev, run_model_id: modelID }));
      return;
    }
    const selected = promptModels().items.find((item) => item.modelID === modelID) || userRunModelOptions()[0];
    setDraft((prev) => ({ ...prev, run_model_provider: selected?.providerID || prev.run_model_provider || "", run_model_id: selected?.modelID || modelID }));
  }

  function selectPromptModelPreset(selection) {
    setDraft((prev) => ({ ...prev, prompt_model_provider: selection.providerID, prompt_model_id: selection.modelID }));
  }

  function selectRunModelPreset(selection) {
    setDraft((prev) => ({ ...prev, run_model_provider: selection.providerID, run_model_id: selection.modelID }));
  }

  function applyPromptModelDefaults(models) {
    const defaultProvider = models?.default_model?.providerID || "";
    const defaultModel = models?.default_model?.modelID || "";
    const defaultPromptModel = findModelPresetItem(models?.items, "max") ?? findModelPresetItem(models?.items, "flash") ?? null;
    if (!defaultProvider && !defaultPromptModel) return;
    setDraft((prev) => ({
      ...prev,
      prompt_model_provider: prev.prompt_model_provider || defaultPromptModel?.providerID || defaultProvider,
      prompt_model_id: prev.prompt_model_id || defaultPromptModel?.modelID || defaultModel,
      run_model_provider: prev.run_model_provider || findPreferredRunModel(models.items)?.providerID || defaultProvider,
      run_model_id: prev.run_model_id || findPreferredRunModel(models.items)?.modelID || defaultModel,
    }));
  }

  async function loadPromptModels() {
    const models = await analysisV1Api.promptModels();
    setModelCatalog(models);
    applyPromptModelDefaults(models);
    return models;
  }

  async function openPromptModelDialog() {
    const currentTask = task();
    if (!currentTask) return;
    const taskId = currentTask.id;
    if (!isSelectedTask(taskId)) return;
    try {
      if (!promptModels().items.length) await loadPromptModels();
      if (!isSelectedTask(taskId)) return;
      setPromptModelDialogOpen(true);
    } catch (exc) {
      setTaskError(taskId, exc);
    }
  }

  function applyRunPlanDefaults(planPayload) {
    const steps = planPayload?.steps || [];
    const ids = steps.map((step) => String(step.id || "")).filter(Boolean);
    if (!ids.length) return;
    setRunStartStepId((current) => current && ids.includes(current) ? current : ids[0]);
    setRunEndStepId((current) => current && ids.includes(current) ? current : ids[ids.length - 1]);
    setRunOnlyStepId((current) => current && ids.includes(current) ? current : ids[0]);
  }

  async function loadRunPlan(taskId = task()?.id) {
    if (!taskId) return null;
    const nextPlan = await analysisV1Api.runToStoryBoardPlan(taskId);
    if (!isSelectedTask(taskId)) return null;
    setRunPlan(nextPlan);
    applyRunPlanDefaults(nextPlan);
    return nextPlan;
  }

  function closeRunModelDialog() {
    setRunModelDialogOpen(false);
    setRunModelDialogPosition(null);
    setRunDialogPurpose("task");
  }

  function runModelDialogStyle() {
    const position = runModelDialogPosition();
    if (!position) return {};
    return { left: `${position.left}px`, top: `${position.top}px`, transform: "none" };
  }

  function startRunModelDialogDrag(event) {
    if (event.button !== 0 || event.target.closest("button,input,select,textarea")) return;
    const dialog = event.currentTarget.closest(".openclip-run-model-dialog");
    const rect = dialog?.getBoundingClientRect();
    if (!rect) return;
    event.preventDefault();
    const offsetX = event.clientX - rect.left;
    const offsetY = event.clientY - rect.top;
    const onMove = (moveEvent) => {
      const maxLeft = Math.max(8, window.innerWidth - rect.width - 8);
      const maxTop = Math.max(8, window.innerHeight - rect.height - 8);
      setRunModelDialogPosition({
        left: Math.min(Math.max(8, moveEvent.clientX - offsetX), maxLeft),
        top: Math.min(Math.max(8, moveEvent.clientY - offsetY), maxTop),
      });
    };
    const onUp = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("blur", onUp);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("blur", onUp);
  }

  function runProgressDialogStyle() {
    const position = runProgressDialogPosition();
    if (!position) return {};
    return { left: `${position.left}px`, top: `${position.top}px`, transform: "none" };
  }

  function startRunProgressDialogDrag(event) {
    if (event.button !== 0 || event.target.closest("button,input,select,textarea")) return;
    const dialog = event.currentTarget.closest(".analysis-v1-run-progress-dialog");
    const rect = dialog?.getBoundingClientRect();
    if (!rect) return;
    event.preventDefault();
    const offsetX = event.clientX - rect.left;
    const offsetY = event.clientY - rect.top;
    const onMove = (moveEvent) => {
      const maxLeft = Math.max(8, window.innerWidth - rect.width - 8);
      const maxTop = Math.max(8, window.innerHeight - rect.height - 8);
      setRunProgressDialogPosition({
        left: Math.min(Math.max(8, moveEvent.clientX - offsetX), maxLeft),
        top: Math.min(Math.max(8, moveEvent.clientY - offsetY), maxTop),
      });
    };
    const onUp = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("blur", onUp);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("blur", onUp);
  }

  async function ensureFreeRewritePrerequisites(currentTask) {
    if (!currentTask?.session_id) return false;
    if (dialogueItems().length > 0) return true;
    const finalItems = await analysisV1Api.readWorkspaceJson(currentTask.session_id, FINAL_ITEMS_PATH);
    const hasItems = Array.isArray(finalItems?.items) && finalItems.items.length > 0;
    if (hasItems) return true;
    setError(FREE_REWRITE_PREREQUISITE_HINT);
    return false;
  }

  async function openRunModelDialog(purpose = "task") {
    const currentTask = task();
    if (!currentTask) return;
    const taskId = currentTask.id;
    if (!isSelectedTask(taskId)) return;
    setError("");
    try {
      if (purpose === "free_rewrite_storyboard" && !(await ensureFreeRewritePrerequisites(currentTask))) return false;
      if (!promptModels().items.length) await loadPromptModels();
      if (!isSelectedTask(taskId)) return;
      await loadRunPlan(taskId);
      if (!isSelectedTask(taskId)) return;
      setRunDialogPurpose(purpose);
      if (purpose === "free_rewrite_storyboard") {
        setRunMode("run_selected_steps");
        setRunTtsBuilderMode("skip");
        setRunRewriteMode("free");
        setRunStoryboardMode("model");
        setRunPauseBeforeStepId("");
      } else if (purpose === "script_storyboard") {
        setRunMode("run_selected_steps");
        setRunTtsBuilderMode("skip");
        setRunRewriteMode("strict");
        setRunStoryboardMode("model");
        setRunPauseBeforeStepId("");
      } else if (purpose === "prompt_builder_full_task") {
        setPendingRunOverrides(null);
        setRunMode("run_all");
        setRunPauseBeforeStepId("");
      }
      setRunModelDialogPosition(null);
      setRunModelDialogOpen(true);
      return true;
    } catch (exc) {
      setTaskError(taskId, exc);
      return false;
    }
  }

  async function openFreeRewriteConfigDialog() {
    return openRunModelDialog("free_rewrite_storyboard");
  }

  async function openPromptBuilderFullTaskRunDialog() {
    return openRunModelDialog("prompt_builder_full_task");
  }

  async function openScriptStoryboardRunDialog() {
    return openRunModelDialog("script_storyboard");
  }

  async function openFreeRewriteFromRewriter() {
    const opened = await openFreeRewriteConfigDialog();
    if (opened) setPromptDrawerOpen(false);
    return opened;
  }

  function openPromptBuilderDrawer() {
    setPromptDrawerMode("prompt_builder");
    setPromptDrawerOpen(true);
  }

  function openSrtRewriterDrawer() {
    setPromptDrawerMode("srt_rewriter");
    setPromptDrawerOpen(true);
  }

  async function loadTasks() {
    setError("");
    const items = await analysisV1Api.tasks();
    setTasks(items);
    if (!selectedTaskId() && items[0]?.id) selectTask(items[0].id, false);
  }

  async function loadTask(taskId) {
    if (!taskId) return;
    const switchingTask = !sameTaskId(taskId, task()?.id);
    if (switchingTask) clearRunScopedState();
    beginLoading();
    setError("");
    try {
      const nextDetail = await analysisV1Api.taskDetail(taskId);
      if (!isSelectedTask(taskId)) return;
      setDetail(nextDetail);
      setDraft(createDraftFromDetail(nextDetail));
      setDialogueEditMode(false);
      setDialogueDrafts({});
      setDialogueSaveStatus("");
      setStoryboardPayload(null);
      if (!nextDetail.prompt_models?.items?.length) void loadPromptModels();
      const sessionId = nextDetail.task?.session_id;
      // Tool Report directories are intentionally protected by the workspace file policy.
      // Load only customer-facing SessionOutput files here so an internal report cannot
      // prevent the completed dialogue from rendering.
      const [finalItems, rewrittenItems, storyboard, frameMap, ttsPayload, ttsConfig] = await Promise.all([
        analysisV1Api.readWorkspaceJson(sessionId, FINAL_ITEMS_PATH),
        analysisV1Api.readWorkspaceJson(sessionId, REWRITTEN_ITEMS_PATH),
        analysisV1Api.readWorkspaceJson(sessionId, STORYBOARD_PATH),
        analysisV1Api.readWorkspaceJson(sessionId, FRAME_MAP_PATH),
        analysisV1Api.ttsBuilderCandidates(taskId).catch(() => null),
        analysisV1Api.ttsModelConfigForTask(taskId).catch(() => analysisV1Api.ttsModelConfig().catch(() => null)),
      ]);
      if (!isSelectedTask(taskId)) return;
      setDialogueItems(normalizeDialogueItems(finalItems, frameMap, analysisV1Api, sessionId, rewrittenItems));
      setStoryboardPayload(storyboard);
      setTtsBuilderPayload(ttsPayload);
      setTtsModelConfig(ttsConfig);
      await restoreLatestRun(nextDetail);
      await restoreLatestOneClickMovie(nextDetail);
    } catch (exc) {
      setTaskError(taskId, exc);
    } finally {
      endLoading();
    }
  }

  async function saveDialogueEdits() {
    const currentTask = task();
    if (!currentTask?.id || !currentTask?.session_id || !dialogueEditMode()) return;
    setSavingDialogue(true);
    setError("");
    setDialogueSaveStatus("");
    try {
      const drafts = dialogueDrafts();
      const items = dialogueItems().map((item) => ({
        srt_id: item.id,
        dialogue: String(Object.prototype.hasOwnProperty.call(drafts, item.id) ? drafts[item.id] : item.rewrittenDialogue || ""),
      }));
      const result = await analysisV1Api.saveRewrittenSrt(currentTask.id, { items });
      const [finalItems, frameMap] = await Promise.all([
        analysisV1Api.readWorkspaceJson(currentTask.session_id, FINAL_ITEMS_PATH),
        analysisV1Api.readWorkspaceJson(currentTask.session_id, FRAME_MAP_PATH),
      ]);
      const rewrittenItems = result?.payload || await analysisV1Api.readWorkspaceJson(currentTask.session_id, REWRITTEN_ITEMS_PATH);
      setDialogueItems(normalizeDialogueItems(finalItems, frameMap, analysisV1Api, currentTask.session_id, rewrittenItems));
      setDialogueDrafts({});
      setDialogueEditMode(false);
      setDialogueSaveStatus("保存成功");
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setSavingDialogue(false);
    }
  }

  function selectTask(taskId, pushHash = true) {
    setSelectedTaskId(taskId);
    if (pushHash) window.location.hash = `#/analysis-v1/tasks/${taskId}`;
    void loadTask(taskId).then(() => {
      maybeOpenTaskListAnalysisDrawer(taskId);
      void maybeOpenTaskListAutoRun(taskId);
    });
  }

  async function createTask() {
    if (creatingTask()) return;
    const placeholder = { id: "__creating_task__", status: "creating", updated_at: Date.now(), title: "新任务创建中", _creating: true };
    beginBusy();
    setCreatingTask(true);
    setError("");
    setTasks((items) => [placeholder, ...items.filter((item) => !item?._creating)]);
    try {
      const res = await analysisV1Api.createTask();
      const taskId = res.task?.id;
      if (!taskId) throw new Error("新建任务没有返回 task id");
      upsertTaskSummary(taskSummaryFromDetail(res));
      setSelectedTaskId(taskId);
      window.location.hash = `#/analysis-v1/tasks/${taskId}`;
      clearRunScopedState();
      setDetail(res);
      setDraft(createDraftFromDetail(res));
      setDialogueItems([]);
      setDialogueEditMode(false);
      setDialogueDrafts({});
      setDialogueSaveStatus("");
      setStoryboardPayload(null);
      setTtsBuilderPayload(null);
      if (!res.prompt_models?.items?.length) void loadPromptModels();
    } catch (exc) {
      setTasks((items) => items.filter((item) => !item?._creating));
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setCreatingTask(false);
      endBusy();
    }
  }

  async function deleteTask(taskId) {
    if (!window.confirm("Delete this Analysis V1 task?")) return;
    beginBusy();
    setError("");
    try {
      await analysisV1Api.deleteTask(taskId);
      if (sameTaskId(selectedTaskId(), taskId)) {
        setSelectedTaskId(null);
        setDetail(null);
        setDialogueItems([]);
        setStoryboardPayload(null);
        setTtsBuilderPayload(null);
        clearRunScopedState();
      }
      await loadTasks();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      endBusy();
    }
  }

  async function saveDraft() {
    const currentTask = task();
    if (!currentTask) return;
    const taskId = currentTask.id;
    if (!isSelectedTask(taskId)) return;
    const payload = normalizePromptBundle({ ...currentTask, ...draft() });
    beginBusy();
    setError("");
    try {
      const res = await analysisV1Api.saveConfig(taskId, payload);
      if (!isSelectedTask(taskId)) return;
      setDetail(res);
      setDraft(createDraftFromDetail(res));
    } catch (exc) {
      setTaskError(taskId, exc);
    } finally {
      endBusy();
    }
  }

  async function uploadTargetVideo(file) {
    const currentTask = task();
    if (!currentTask?.session_id || !file) return;
    const taskId = currentTask.id;
    if (!isSelectedTask(taskId)) return;
    const sessionId = currentTask.session_id;
    beginUploadingVideo();
    setError("");
    try {
      const res = await analysisV1Api.uploadSessionFile(sessionId, file, "inbox");
      if (!isSelectedTask(taskId)) return;
      setDraft((prev) => ({ ...prev, reference_video_path: res.path || prev.reference_video_path || "" }));
    } catch (exc) {
      setTaskError(taskId, exc);
    } finally {
      endUploadingVideo();
    }
  }

  async function generateFinalPrompt() {
    const currentTask = task();
    if (!currentTask) return;
    const taskId = currentTask.id;
    if (!isSelectedTask(taskId)) return;
    const payload = normalizePromptBundle({ ...currentTask, ...draft() });
    const promptRequest = {
      prompt_model_provider: payload.prompt_model_provider,
      prompt_model_id: payload.prompt_model_id,
      prompt_kind: activePromptTab(),
    };
    beginBusy();
    setError("");
    try {
      await analysisV1Api.saveConfig(taskId, payload);
      if (!isSelectedTask(taskId)) return;
      const res = await analysisV1Api.generatePrompt(taskId, promptRequest);
      if (!isSelectedTask(taskId)) return;
      setDetail(res);
      setDraft(createDraftFromDetail(res));
      setPromptModelDialogOpen(false);
    } catch (exc) {
      setTaskError(taskId, exc);
    } finally {
      endBusy();
    }
  }

  async function saveRunModel() {
    const currentTask = task();
    if (!currentTask) return;
    const taskId = currentTask.id;
    if (!isSelectedTask(taskId)) return;
    const payload = normalizePromptBundle({ ...currentTask, ...draft() });
    beginBusy();
    setError("");
    try {
      const res = await analysisV1Api.saveConfig(taskId, payload);
      if (!isSelectedTask(taskId)) return;
      setDetail(res);
      setDraft(createDraftFromDetail(res));
    } catch (exc) {
      setTaskError(taskId, exc);
    } finally {
      endBusy();
    }
  }

  function clearRunProgressTimer() {
    if (!runProgressTimer) return;
    window.clearTimeout(runProgressTimer);
    runProgressTimer = null;
  }

  function clearOneClickMovieTimer() {
    if (!oneClickMovieTimer) return;
    window.clearTimeout(oneClickMovieTimer);
    oneClickMovieTimer = null;
  }

  function scheduleRunProgressPoll(taskId, attemptId, status = runProgress()?.status) {
    clearRunProgressTimer();
    const delay = String(status || "").toLowerCase() === "paused" ? 3000 : 1000;
    runProgressTimer = window.setTimeout(() => void pollRunProgress(taskId, attemptId), delay);
  }

  function scheduleOneClickMoviePoll(taskId, runId, status = oneClickMovieProgress()?.status) {
    clearOneClickMovieTimer();
    const delay = String(status || "").toLowerCase() === "queued" ? 1200 : 1000;
    oneClickMovieTimer = window.setTimeout(() => void pollOneClickMovie(taskId, runId), delay);
  }

  async function restoreLatestRun(nextDetail) {
    const taskId = nextDetail?.task?.id;
    const attemptId = nextDetail?.task?.latest_attempt_id;
    if (!taskId || !attemptId) return;
    try {
      const latest = await analysisV1Api.runToStoryBoardStatus(taskId, attemptId);
      if (!isSelectedTask(taskId)) return;
      if (latest?.attempt_family !== "analysis_v1_tool_run" && latest?.attempt?.attempt_family !== "analysis_v1_tool_run") return;
      setRunProgress(latest);
      if (!selectedRunStepId() && latest.steps?.[0]?.id) setSelectedRunStepId(String(latest.steps[0].id));
      if (isActiveRunStatus(latest.status)) {
        setRunProgressOpen(true);
        scheduleRunProgressPoll(taskId, attemptId, latest.status);
      }
    } catch {
      // Latest attempt may be a legacy OpenClip analysis attempt without tool-run state.
    }
  }

  async function restoreLatestOneClickMovie(nextDetail) {
    const taskId = nextDetail?.task?.id;
    if (!taskId) return;
    try {
      const latest = await analysisV1Api.oneClickMovieStatus(taskId);
      if (!isSelectedTask(taskId)) return;
      setOneClickMovieProgress(latest);
      if (isActiveRunStatus(latest.status)) {
        setOneClickMovieOpen(true);
        scheduleOneClickMoviePoll(taskId, latest.run_id, latest.status);
      }
    } catch {
      // A task can predate the one-click movie state file.
    }
  }

  async function pollRunProgress(taskId, attemptId) {
    if (!taskId || !attemptId) return;
    try {
      const next = await analysisV1Api.runToStoryBoardStatus(taskId, attemptId);
      if (!isSelectedTask(taskId)) return;
      setRunProgress(next);
      if (!selectedRunStepId() && next.steps?.[0]?.id) setSelectedRunStepId(String(next.steps[0].id));
      if (isTerminalRunStatus(next.status)) {
        clearRunProgressTimer();
        await loadTask(taskId);
        await loadTasks();
        return;
      }
      scheduleRunProgressPoll(taskId, attemptId, next.status);
    } catch (exc) {
      if (isSelectedTask(taskId)) {
        setError(exc instanceof Error ? exc.message : String(exc));
        runProgressTimer = window.setTimeout(() => void pollRunProgress(taskId, attemptId), 3000);
      }
    }
  }

  async function pollOneClickMovie(taskId, runId = "") {
    if (!taskId) return;
    try {
      const next = await analysisV1Api.oneClickMovieStatus(taskId, runId);
      if (!isSelectedTask(taskId)) return;
      setOneClickMovieProgress(next);
      if (isTerminalRunStatus(next.status) || next.status === "idle") {
        clearOneClickMovieTimer();
        await loadTask(taskId);
        await loadTasks();
        return;
      }
      scheduleOneClickMoviePoll(taskId, next.run_id, next.status);
    } catch (exc) {
      if (isSelectedTask(taskId)) {
        setError(exc instanceof Error ? exc.message : String(exc));
        oneClickMovieTimer = window.setTimeout(() => void pollOneClickMovie(taskId, runId), 3000);
      }
    }
  }

  async function openOneClickMoviePanel() {
    const currentTask = task();
    if (!currentTask?.id) return;
    setOneClickMovieOpen(true);
    try {
      const latest = await analysisV1Api.oneClickMovieStatus(currentTask.id);
      if (!isSelectedTask(currentTask.id)) return;
      setOneClickMovieProgress(latest);
      if (isActiveRunStatus(latest.status)) scheduleOneClickMoviePoll(currentTask.id, latest.run_id, latest.status);
    } catch {
      // The backend will return an idle state once the route is available; ignore transient load misses.
    }
  }

  function buildOneClickMoviePayload(overrides = {}) {
    return {
      force: overrides.force !== false,
      resume: Boolean(overrides.resume),
      run_only_step_id: overrides.run_only_step_id || "",
      run_from_step_id: overrides.run_from_step_id || "",
      run_model_provider: draft().run_model_provider,
      run_model_id: draft().run_model_id,
      asr_mode: runAsrMode(),
      allow_cloud_asr_data_transfer: runAllowCloudAsr(),
      tts_builder_mode: runTtsBuilderMode() === "skip" ? "quick" : runTtsBuilderMode(),
      rewrite_mode: runRewriteMode(),
      storyboard_mode: "quick",
      options: {
        source: "analysis_v1_one_click_movie",
      },
      video_plan_settings: {
        max_video_seconds: 4,
        min_video_seconds: 2,
        split_tolerance_seconds: 2,
      },
      composer_settings: {
        subtitle_mode: "hyperframe",
        watermark_mode: "never",
      },
    };
  }

  async function startOneClickMovie(overrides = {}) {
    const currentTask = task();
    if (!currentTask) return;
    const taskId = currentTask.id;
    if (!isSelectedTask(taskId)) return;
    if (isAdmin() && asrModeNeedsCloudConsent(runAsrMode()) && !runAllowCloudAsr() && !overrides.run_only_step_id && !overrides.run_from_step_id) {
      setError("当前一键成片会执行 02_01 音频识别。请勾选“允许云端 ASR 传输音频”，或改选 ASR=local。");
      return;
    }
    const configPayload = normalizePromptBundle({ ...currentTask, ...draft() });
    const runPayload = buildOneClickMoviePayload(overrides);
    beginBusy();
    setError("");
    try {
      await analysisV1Api.saveConfig(taskId, configPayload);
      if (!isSelectedTask(taskId)) return;
      const started = await analysisV1Api.oneClickMovie(taskId, runPayload);
      if (!isSelectedTask(taskId)) return;
      setOneClickMovieProgress(started);
      setOneClickMovieOpen(true);
      scheduleOneClickMoviePoll(taskId, started.run_id, started.status);
    } catch (exc) {
      setTaskError(taskId, exc);
    } finally {
      endBusy();
    }
  }

  function oneClickMovieSyncLabel(segment) {
    const syncMode = String(segment?.lipsync?.sync_mode || "").toLowerCase();
    return segment?.lipsync?.need_lipsync === false || syncMode === "audio_replace_retime" ? "音频合成" : "音频匹配";
  }

  function buildRunPayload(overrides = {}) {
    const mode = String(overrides.mode || runMode() || "run_all");
    const payload = {
      mode,
      run_model_provider: draft().run_model_provider,
      run_model_id: draft().run_model_id,
    };
    if (isAdmin()) {
      payload.asr_mode = runAsrMode();
      payload.allow_cloud_asr_data_transfer = runAllowCloudAsr();
      payload.include_tts_builder = runTtsBuilderMode() !== "skip";
      payload.tts_builder_mode = runTtsBuilderMode();
      payload.rewrite_mode = runRewriteMode();
      payload.storyboard_mode = runStoryboardMode();
      payload.pause_before_step_id = runPauseBeforeStepId();
    }
    if (mode.startsWith("rerun")) {
      payload.previous_attempt_id = overrides.previous_attempt_id ?? runProgress()?.attempt_id ?? task()?.latest_attempt_id;
    }
    if (mode === "run_range") {
      payload.start_step_id = overrides.start_step_id || runStartStepId();
      payload.end_step_id = overrides.end_step_id || runEndStepId();
    }
    if (mode === "run_from_step" || mode === "rerun_from_step") {
      payload.start_step_id = overrides.start_step_id || runStartStepId();
    }
    if (mode === "run_only_step") {
      payload.run_only_step_id = overrides.run_only_step_id || runOnlyStepId();
    }
    return { ...payload, ...overrides, mode };
  }

  async function runAnalysis(overrides = {}) {
    const currentTask = task();
    if (!currentTask) return;
    const taskId = currentTask.id;
    if (!isSelectedTask(taskId)) return;
    const payload = normalizePromptBundle({ ...currentTask, ...draft() });
    const runPayload = buildRunPayload(overrides);
    if (String(runPayload.mode || "").startsWith("rerun") && !runPayload.previous_attempt_id) {
      setError("没有可重跑的历史运行，请改用全量运行");
      return;
    }
    const executesAsr = runPayloadExecutesStep(runPayload, "02_01");
    if (isAdmin() && executesAsr && asrModeNeedsCloudConsent(runAsrMode()) && !runAllowCloudAsr()) {
      setError("当前 ASR 模式可能把任务音频发送到数据库配置的云端 ASR provider。请勾选“允许云端 ASR 传输音频”，或改选 ASR=local。");
      return;
    }
    beginBusy();
    setError("");
    try {
      await analysisV1Api.saveConfig(taskId, payload);
      if (!isSelectedTask(taskId)) return;
      const started = await analysisV1Api.runToStoryBoard(taskId, runPayload);
      if (!isSelectedTask(taskId)) return;
      setRunProgress(started);
      setSelectedRunStepId(started.steps?.[0]?.id ? String(started.steps[0].id) : "");
      setRunProgressOpen(true);
      closeRunModelDialog();
      scheduleRunProgressPoll(taskId, started.attempt_id, started.status);
    } catch (exc) {
      setTaskError(taskId, exc);
    } finally {
      endBusy();
    }
  }

  async function startQuickTTSBuilderRun(builderPayload = {}) {
    const currentTask = task();
    if (!currentTask) return null;
    const taskId = currentTask.id;
    if (!isSelectedTask(taskId)) return null;
    const payload = normalizePromptBundle({ ...currentTask, ...draft() });
    const referenceStart = Number(builderPayload.reference_start || 0);
    const referenceDuration = Number(builderPayload.reference_duration || 16);
    const builderMode = String(builderPayload.builder_mode || builderPayload.tts_builder_mode || "quick");
    const builderConfig = TTS_BUILDER_RUN_CONFIG[builderMode] || TTS_BUILDER_RUN_CONFIG.quick;
    const runPayload = {
      mode: "run_only_step",
      run_only_step_id: builderConfig.run_only_step_id,
      run_model_provider: draft().run_model_provider,
      run_model_id: draft().run_model_id,
      asr_mode: runAsrMode(),
      allow_cloud_asr_data_transfer: runAllowCloudAsr(),
      include_tts_builder: true,
      tts_builder_mode: builderConfig.tts_builder_mode,
      storyboard_mode: "quick",
      force: builderPayload.force !== false,
      options: {
        source: "tts_builder_dialog",
        reference_start: Number.isFinite(referenceStart) ? Math.max(0, referenceStart) : 0,
        reference_duration: Number.isFinite(referenceDuration) ? Math.max(0.1, referenceDuration) : 16,
        stage1_count: Number.isFinite(Number(builderPayload.stage1_count)) ? Math.max(1, Math.round(Number(builderPayload.stage1_count))) : 24,
        stage2_count: Number.isFinite(Number(builderPayload.stage2_count)) ? Math.max(1, Math.round(Number(builderPayload.stage2_count))) : 6,
        final_count: Number.isFinite(Number(builderPayload.final_count)) ? Math.max(1, Math.round(Number(builderPayload.final_count))) : 3,
        enable_speechbrain: Boolean(builderPayload.enable_speechbrain),
        providers: String(builderPayload.providers || ""),
        model: String(builderPayload.model || ""),
      },
    };
    beginBusy();
    setError("");
    try {
      await analysisV1Api.saveConfig(taskId, payload);
      if (!isSelectedTask(taskId)) return null;
      const started = await analysisV1Api.runToStoryBoard(taskId, runPayload);
      if (!isSelectedTask(taskId)) return null;
      setRunProgress(started);
      const executingStep = started.steps?.find((step) => step.will_execute);
      setSelectedRunStepId(executingStep?.id ? String(executingStep.id) : started.steps?.[0]?.id ? String(started.steps[0].id) : "");
      setRunProgressOpen(true);
      closeRunModelDialog();
      scheduleRunProgressPoll(taskId, started.attempt_id, started.status);
      return started;
    } catch (exc) {
      setTaskError(taskId, exc);
      throw exc;
    } finally {
      endBusy();
    }
  }

  async function saveFreeRewriteConfigAndOpenRunPanel() {
    const currentTask = task();
    if (!currentTask) return;
    const taskId = currentTask.id;
    if (!isSelectedTask(taskId)) return;
    const payload = normalizePromptBundle({ ...currentTask, ...draft() });
    beginBusy();
    setError("");
    try {
      const res = await analysisV1Api.saveConfig(taskId, payload);
      if (!isSelectedTask(taskId)) return;
      setDetail(res);
      setDraft(createDraftFromDetail(res));
      setRunProgress(null);
      setPendingRunOverrides(freeRewriteStoryboardRunOverrides());
      setSelectedRunStepId(FREE_REWRITE_STORYBOARD_STEP_IDS[0]);
      closeRunModelDialog();
      setRunProgressOpen(true);
    } catch (exc) {
      setTaskError(taskId, exc);
    } finally {
      endBusy();
    }
  }

  async function saveFullTaskConfigAndOpenRunPanel() {
    const currentTask = task();
    if (!currentTask) return;
    const taskId = currentTask.id;
    if (!isSelectedTask(taskId)) return;
    const payload = normalizePromptBundle({ ...currentTask, ...draft() });
    beginBusy();
    setError("");
    try {
      const res = await analysisV1Api.saveConfig(taskId, payload);
      if (!isSelectedTask(taskId)) return;
      setDetail(res);
      setDraft(createDraftFromDetail(res));
      setRunProgress(null);
      setPendingRunOverrides(fullTaskRunOverrides());
      setSelectedRunStepId(runPlanSteps()[0]?.id ? String(runPlanSteps()[0].id) : "");
      closeRunModelDialog();
      setRunProgressOpen(true);
    } catch (exc) {
      setTaskError(taskId, exc);
    } finally {
      endBusy();
    }
  }

  async function enterTask() {
    const currentTask = task();
    if (!currentTask) return;
    const taskId = currentTask.id;
    if (!isSelectedTask(taskId)) return;
    const payload = normalizePromptBundle({ ...currentTask, ...draft() });
    beginBusy();
    setError("");
    try {
      await analysisV1Api.saveConfig(taskId, payload);
      if (!isSelectedTask(taskId)) return;
      closeRunModelDialog();
      setRunProgressOpen(true);
    } catch (exc) {
      setTaskError(taskId, exc);
    } finally {
      endBusy();
    }
  }

  async function runStepAction(mode, stepId) {
    setStepMenu(null);
    const previous_attempt_id = runProgress()?.attempt_id;
    if (mode === "run_range") return runAnalysis({ mode, end_step_id: stepId });
    if (mode === "run_from_step") return runAnalysis({ mode, start_step_id: stepId });
    if (mode === "run_only_step") return runAnalysis({ mode, run_only_step_id: stepId });
    if (mode === "rerun_from_step") return runAnalysis({ mode, start_step_id: stepId, previous_attempt_id });
    return runAnalysis({ mode });
  }

  async function refreshRunProgress() {
    const progress = runProgress();
    if (!progress?.task_id || !progress?.attempt_id) return;
    await pollRunProgress(progress.task_id, progress.attempt_id);
  }

  async function stopRunProgress() {
    const progress = runProgress();
    if (!progress?.task_id || !progress?.attempt_id) return;
    const taskId = progress.task_id;
    const commandToken = beginRunCommand("stop");
    try {
      const next = await analysisV1Api.stopRunToStoryBoard(taskId, progress.attempt_id);
      if (!isSelectedTask(taskId)) return;
      setRunProgress(next);
      scheduleRunProgressPoll(next.task_id, next.attempt_id, next.status);
    } catch (exc) {
      setTaskError(taskId, exc);
    } finally {
      endRunCommand(commandToken);
    }
  }

  async function cancelPausePoint() {
    setStepMenu(null);
    const progress = runProgress();
    if (!progress?.capabilities?.can_cancel_pause_point) {
      setRunPauseBeforeStepId("");
      return;
    }
    if (!progress?.task_id || !progress?.attempt_id) {
      setRunPauseBeforeStepId("");
      return;
    }
    const taskId = progress.task_id;
    const commandToken = beginRunCommand("cancelPause");
    try {
      const next = await analysisV1Api.cancelPauseBeforeRunToStoryBoard(taskId, progress.attempt_id);
      if (!isSelectedTask(taskId)) return;
      setRunProgress(next);
    } catch (exc) {
      setTaskError(taskId, exc);
    } finally {
      endRunCommand(commandToken);
    }
  }

  async function setPauseBeforeStep(stepId) {
    setStepMenu(null);
    const progress = runProgress();
    if (!progress?.capabilities?.can_set_pause_point) {
      setRunPauseBeforeStepId(stepId);
      return;
    }
    if (!progress?.task_id || !progress?.attempt_id) {
      setRunPauseBeforeStepId(stepId);
      return;
    }
    const taskId = progress.task_id;
    const commandToken = beginRunCommand(`pause:${stepId}`);
    try {
      const next = await analysisV1Api.pauseBeforeRunToStoryBoard(taskId, progress.attempt_id, stepId);
      if (!isSelectedTask(taskId)) return;
      setRunProgress(next);
    } catch (exc) {
      setTaskError(taskId, exc);
    } finally {
      endRunCommand(commandToken);
    }
  }

  async function loadStepDetail(stepId) {
    const progress = runProgress();
    if (!progress?.task_id || !progress?.attempt_id || !stepId) return;
    const taskId = progress.task_id;
    setSelectedRunStepId(stepId);
    setStepMenu(null);
    try {
      const [quickWatch, logs] = await Promise.all([
        analysisV1Api.runToStoryBoardQuickWatch(taskId, progress.attempt_id, stepId),
        analysisV1Api.runToStoryBoardLogs(taskId, progress.attempt_id, stepId).catch(() => null),
      ]);
      if (!isSelectedTask(taskId)) return;
      setSelectedRunStepId(stepId);
      setStepQuickWatch({ ...quickWatch, quick_watch: { ...(quickWatch.quick_watch || {}), logs: logs || quickWatch.quick_watch?.logs || {} } });
    } catch (exc) {
      setTaskError(taskId, exc);
    }
  }

  async function openRunStepDetail(stepId) {
    setRunStepDetailOpen(true);
    await loadStepDetail(stepId);
  }

  onMount(() => {
    void loadPromptModels().catch((exc) => setError(exc instanceof Error ? exc.message : String(exc)));
    void loadTasks().then(() => {
      const hashTaskId = taskIdFromAnalysisV1Hash(window.location.hash);
      if (hashTaskId) selectTask(hashTaskId, false);
    });
    const listHandler = () => {
      setTaskListOpen(true);
      void loadTasks();
    };
    const newTaskHandler = () => void createTask();
    window.addEventListener("analysis-v1:task-list", listHandler);
    window.addEventListener("analysis-v1:new-task", newTaskHandler);
    onCleanup(() => {
      window.removeEventListener("analysis-v1:task-list", listHandler);
      window.removeEventListener("analysis-v1:new-task", newTaskHandler);
      clearRunProgressTimer();
      clearOneClickMovieTimer();
    });
  });

  createEffect(() => {
    const hashTaskId = taskIdFromAnalysisV1Hash(props.routeHash);
    if (hashTaskId && hashTaskId !== selectedTaskId()) selectTask(hashTaskId, false);
  });

  const handleDialogueSelected = (item) => {
    props.onMediaItemChange?.(item || null);
  };

  function openStoryBoard() {
    if (!task()?.id || !hasStoryBoardFile()) return;
    window.location.hash = `#/koubo-storyboard/tasks/${task().id}`;
  }

  onCleanup(() => props.onMediaItemChange?.(null));

  return <>
    <div class="openflow-page openclip-flow-page analysis-v1-flow-page">
      <Show when={error()}>
        <div class="analysis-v1-banner bad">{error()}</div>
      </Show>
      <section class="card step-panel">
        <div class="step-card-head">
          <div class="step-title-wrap">
            <span class="step-badge">1</span>
            <h2>Analysis</h2>
            <StatusBadge status={task()?.status || "draft"} />
            <Show when={task()}>
              <span class="analysis-v1-task-session-tag">
                <span>任务 #{task()?.id || "-"}</span>
                <span>/</span>
                <Show when={task()?.session_id} fallback={<span>会话 -</span>}>
                  <span>会话 #{task()?.session_id}</span>
                </Show>
              </span>
            </Show>
          </div>
          <div class="step-actions openflow-step-actions">
            <button class="primary analysis-v1-one-click-movie-entry" type="button" title="打开口播一键成片面板" disabled={!task()} onClick={() => void openOneClickMoviePanel()}><PlayClipIcon /><span>一键成片</span></button>
            <button class="secondary ocrebuild-param-toggle analysis-v1-prompt-builder-entry" type="button" title="提示词构建器" disabled={!task()} onClick={openPromptBuilderDrawer}><SlidersIcon /><span>视频分析</span></button>
            <button class="secondary ocrebuild-param-toggle analysis-v1-srt-rewriter-entry" type="button" title="脚本重写" disabled={!task()} onClick={openSrtRewriterDrawer}><SlidersIcon /><span>脚本重写</span></button>
            <button class="secondary ocrebuild-param-toggle analysis-v1-tts-builder-entry" type="button" title="TTS 构建器" disabled={!task()} onClick={() => setTtsBuilderOpen(true)}><WaveformIcon /><span>音色选择</span></button>
            <button class="secondary ocrebuild-param-toggle analysis-v1-tts-clone-entry" type="button" title="音色克隆" disabled={!task()} onClick={() => setTtsCloneOpen(true)}><SpeechIcon /><span>音色克隆</span></button>
            <button class="secondary ocrebuild-param-toggle analysis-v1-storyboard-entry" type="button" disabled={!task() || !hasStoryBoardFile()} title={hasStoryBoardFile() ? "打开故事版（口播）" : "StoryBoard 跑完后打开故事版（口播）"} onClick={openStoryBoard}><StoryboardIcon /><span>故事板</span></button>
            <button class="icon-action analysis-v1-param-icon" type="button" title={paramsCollapsed() ? "展开参数" : "收起参数"} aria-label={paramsCollapsed() ? "展开参数" : "收起参数"} aria-pressed={!paramsCollapsed()} onClick={() => setParamsCollapsed((value) => !value)}>
              <SimpleChevronIcon direction={paramsCollapsed() ? "down" : "up"} />
            </button>
          </div>
        </div>
        <Show when={!paramsCollapsed()}>
          <div class="step-card-body openflow-summary-body">
            <div class="openflow-summary-fields">
              <div class="openflow-summary-item">
                <div class="openflow-summary-label">视频</div>
                <div class="openflow-summary-value"><span class="inline-code">{task()?.reference_video_path || "无参考视频（脚本创建）"}</span></div>
              </div>
              <div class="openflow-summary-item">
                <div class="openflow-summary-label">行业</div>
                <div class="openflow-summary-value">{task()?.industry || "-"}</div>
              </div>
              <div class="openflow-summary-item">
                <div class="openflow-summary-label">人设</div>
                <div class="openflow-summary-value">{task()?.persona || "-"}</div>
              </div>
              <div class="openflow-summary-item">
                <div class="openflow-summary-label">目标受众</div>
                <div class="openflow-summary-value">{task()?.target_audience || "-"}</div>
              </div>
              <div class="openflow-summary-item">
                <div class="openflow-summary-label">视频公式</div>
                <div class="openflow-summary-value">{task()?.video_formula || "口播脚本改写"}</div>
              </div>
              <div class="openflow-summary-item">
                <div class="openflow-summary-label">产品信息</div>
                <div class="openflow-summary-value openflow-prompt-preview">{task()?.product_info || "-"}</div>
              </div>
              <div class="openflow-summary-item">
                <div class="openflow-summary-label">约束条件</div>
                <div class="openflow-summary-value openflow-prompt-preview">{task()?.constraints || "-"}</div>
              </div>
            </div>
          </div>
        </Show>
      </section>

      <section class="jobs-panel analysis-v1-workspace-output-panel">
        <AnalysisV1DialogueView
          items={dialogueItems()}
          editing={dialogueEditMode()}
          saving={savingDialogue()}
          drafts={dialogueDrafts()}
          saveStatus={dialogueSaveStatus()}
          onStartEdit={startDialogueEdit}
          onCancelEdit={cancelDialogueEdit}
          onDraftChange={updateDialogueDraft}
          onSaveEdit={() => void saveDialogueEdits()}
          onSelectedChange={handleDialogueSelected}
        />
      </section>
    </div>

    <Show when={taskListOpen()}>
      <div class="drawer-backdrop" onClick={() => setTaskListOpen(false)} />
      <section class="verify-dialog openflow-session-dialog">
        <div class="env-dialog-head">
          <div>
            <h3>口播视频分析任务</h3>
            <p>选择一个任务查看，或删除旧任务。</p>
          </div>
          <button class="secondary" type="button" onClick={() => setTaskListOpen(false)}>关闭</button>
        </div>
        <div class="openflow-session-dialog-list">
          <div class="field-row openflow-session-dialog-toolbar">
            <button type="button" disabled={creatingTask()} onClick={() => void createTask()}>{creatingTask() ? "创建中..." : "新建任务"}</button>
            <button class="secondary" type="button" disabled={creatingTask()} onClick={() => void loadTasks()}>刷新</button>
          </div>
          <For each={tasks()}>{(item) => (
            <div class={`openflow-session-dialog-item ${task()?.id === item.id ? "is-active" : ""} ${item._creating ? "is-pending" : ""}`}>
              <button class="openflow-session-dialog-main" type="button" disabled={item._creating} onClick={() => { selectTask(item.id); setTaskListOpen(false); }}>
                <strong>{item._creating ? "新任务" : `#${item.id}`}</strong>
                <span>{item._creating ? "正在创建任务..." : item.status || "draft"}</span>
                <span>{item._creating ? "准备工作区和会话" : formatDateTime(item.updated_at)}</span>
              </button>
              <button class="openflow-session-dialog-delete" type="button" title="删除任务" disabled={item._creating} onClick={() => void deleteTask(item.id)}><TrashIcon /></button>
            </div>
          )}</For>
        </div>
      </section>
    </Show>

    <Show when={ttsBuilderOpen() && task()}>
      <div class="drawer-backdrop" />
      <AnalysisV1TTSBuilder
        taskId={task()?.id}
        sessionId={task()?.session_id}
        ttsPayload={ttsBuilderPayload()}
        ttsModelConfig={ttsModelConfig()}
        api={analysisV1Api}
        cacheKey={Date.now()}
        onReload={() => loadTask(task()?.id)}
        onRunQuickBuilder={(payload) => startQuickTTSBuilderRun(payload)}
        onClose={() => setTtsBuilderOpen(false)}
      />
    </Show>

    <Show when={ttsCloneOpen() && task()}>
      <div class="drawer-backdrop" />
      <AnalysisV1TTSBuilder
        mode="clone"
        taskId={task()?.id}
        sessionId={task()?.session_id}
        ttsPayload={ttsBuilderPayload()}
        ttsModelConfig={ttsModelConfig()}
        api={analysisV1Api}
        cacheKey={Date.now()}
        onReload={() => loadTask(task()?.id)}
        onRunQuickBuilder={(payload) => startQuickTTSBuilderRun(payload)}
        onClose={() => setTtsCloneOpen(false)}
      />
    </Show>

    <OneClickMovieDialog
      open={oneClickMovieOpen() && task()}
      taskId={task()?.id}
      sessionId={task()?.session_id}
      runId={oneClickMovieProgress()?.run_id}
      status={oneClickMovieProgress()?.status || "idle"}
      steps={oneClickMovieProgress()?.steps || []}
      segments={oneClickMovieProgress()?.segments || []}
      storyboardItems={dialogueItems()}
      message={oneClickMovieMessage()}
      startedAt={oneClickMovieProgress()?.started_at}
      finishedAt={oneClickMovieProgress()?.finished_at}
      totalDurationSeconds={oneClickMovieProgress()?.duration_seconds}
      startTitle="重新一键成片"
      startDisabled={busy() || Boolean(activeRunProgress()) || Boolean(activeOneClickMovieProgress())}
      storyboardDisabled={!hasStoryBoardFile()}
      storyboardTitle={hasStoryBoardFile() ? "故事板" : "StoryBoard 跑完后可打开"}
      stepDisplayName={runStepDisplayName}
      syncLabel={oneClickMovieSyncLabel}
      onClose={() => setOneClickMovieOpen(false)}
      onStart={() => void startOneClickMovie({ force: true })}
      onResumeVideoStep={() => void startOneClickMovie({ force: false, resume: true, run_only_step_id: "05_02" })}
      onResumeVideoStepAndFollowing={() => void startOneClickMovie({ force: false, resume: true, run_from_step_id: "05_02" })}
      onOpenStoryBoard={openStoryBoard}
    />

    <Show when={promptDrawerOpen() && task()}>
      <div class="drawer-backdrop" onClick={() => setPromptDrawerOpen(false)} />
      <section class="skill-drawer openflow-config-drawer openflow-prompt-drawer analysis-v1-prompt-drawer">
        <div class="skill-drawer-head">
          <div class="ocrebuild-drawer-title-row">
            <h3>{promptDrawerMode() === "srt_rewriter" ? "脚本重写" : "提示词构建器"}</h3>
          </div>
          <div class="openflow-dialog-head-actions analysis-v1-builder-actions">
            <button class="icon-action openflow-icon-action primary analysis-v1-builder-icon" type="button" title={PROMPT_TABS[activePromptTab()]?.generateLabel || "生成最终提示词"} data-tooltip={PROMPT_TABS[activePromptTab()]?.generateLabel || "生成最终提示词"} disabled={busy()} onClick={() => void openPromptModelDialog()}><CodeIcon /></button>
            <Show when={promptDrawerMode() === "srt_rewriter"}>
              <button class="icon-action openflow-icon-action analysis-v1-builder-icon analysis-v1-builder-run-selected" type="button" title="运行选中步骤：00 Variable、04_01 SRT Rewrite、04_02 StoryBoard" data-tooltip="运行选中步骤" disabled={!task() || busy() || Boolean(activeRunProgress())} onClick={() => void openFreeRewriteFromRewriter()}><PlayClipIcon /></button>
            </Show>
            <Show when={promptDrawerMode() === "prompt_builder"}>
              <button class="icon-action openflow-icon-action analysis-v1-builder-icon analysis-v1-builder-run-config" type="button" title="运行设置" aria-label="运行设置" data-tooltip="运行设置" disabled={!task() || busy() || Boolean(activeRunProgress())} onClick={() => void openPromptBuilderFullTaskRunDialog()}><PlayClipIcon /></button>
            </Show>
            <button class="icon-action openflow-icon-action analysis-v1-builder-icon" type="button" title={PROMPT_TABS[activePromptTab()]?.saveLabel || "保存提示词"} data-tooltip={PROMPT_TABS[activePromptTab()]?.saveLabel || "保存提示词"} disabled={busy()} onClick={() => void saveDraft()}><SaveIcon /></button>
            <button class="icon-action openflow-icon-action analysis-v1-builder-icon" type="button" title="关闭" data-tooltip="关闭" onClick={() => setPromptDrawerOpen(false)}><CloseIcon /></button>
          </div>
        </div>
        <Show when={error()}>
          <div class="analysis-v1-banner bad">{error()}</div>
        </Show>
        <div class="openflow-config-drawer-body">
          <AnalysisV1PromptBuilder task={task()} draft={draft()} options={detail()?.options || {}} activeTab={activePromptTab()} busy={busy() || uploadingVideo()} uploading={uploadingVideo()} hideTargetVideo={promptDrawerMode() === "srt_rewriter"} onTabChange={setActivePromptTab} onChange={(next) => setDraft(normalizePromptBundle(next))} onUploadVideo={(file) => void uploadTargetVideo(file)} onSaveDraft={() => void saveDraft()} />
        </div>
      </section>
    </Show>

    <Show when={promptModelDialogOpen() && task()}>
      <div class="drawer-backdrop openclip-model-overlay" onClick={() => setPromptModelDialogOpen(false)} />
      <section class="verify-dialog openflow-model-dialog openclip-prompt-model-dialog analysis-v1-compact-model-dialog">
        <div class="openflow-prompt-model-grid openclip-model-dialog-body model-preset-dialog-body">
          <ModelPresetCards
            items={promptModels().items}
            provider={draft().prompt_model_provider}
            model={draft().prompt_model_id}
            onSelect={selectPromptModelPreset}
            aria-label="提示词模型预设"
          />
        </div>
        <div class="field-row openflow-model-dialog-actions openclip-model-dialog-actions">
          <button class="secondary openclip-model-cancel" type="button" onClick={() => setPromptModelDialogOpen(false)}>取消</button>
          <button class="openclip-model-confirm" type="button" disabled={!draft().prompt_model_provider || !draft().prompt_model_id || busy()} onClick={() => void generateFinalPrompt()}>{busy() ? "运行中..." : "运行"}</button>
        </div>
      </section>
    </Show>

    <Show when={runModelDialogOpen() && task()}>
      <div class="drawer-backdrop openclip-model-overlay" onClick={closeRunModelDialog} />
      <section class="verify-dialog openflow-model-dialog openclip-prompt-model-dialog openclip-run-model-dialog" style={runModelDialogStyle()}>
        <div class="env-dialog-head openclip-model-dialog-head" onPointerDown={startRunModelDialogDrag}>
          <div class="analysis-v1-run-context-tags">
            <span>任务 #{task()?.id || "-"}</span>
            <span>会话 #{task()?.session_id || "-"}</span>
            <Show when={isFreeRewriteRunDialog()}>
              <span>重写</span>
            </Show>
            <Show when={isPromptBuilderFullTaskRunDialog()}>
              <span>全任务</span>
            </Show>
          </div>
        </div>
        <div class="openflow-prompt-model-grid openclip-model-dialog-body">
          <div class="openflow-field-span-2">
            <ModelPresetCards
              items={promptModels().items}
              provider={draft().run_model_provider}
              model={draft().run_model_id}
              onSelect={selectRunModelPreset}
              aria-label="运行模型预设"
            />
          </div>
          <Show when={isAdmin() && ["run_range", "run_from_step", "rerun_from_step"].includes(runMode())}>
            <label class="openflow-field openclip-model-form-group"><span>起始步骤</span><div class="openclip-model-select-wrap"><select value={runStartStepId()} onChange={(event) => setRunStartStepId(event.currentTarget.value)}><For each={runPlanSteps()}>{(step) => <option value={step.id}>{step.id} {step.name}</option>}</For></select></div></label>
          </Show>
          <Show when={isAdmin() && runMode() === "run_range"}>
            <label class="openflow-field openclip-model-form-group"><span>结束步骤</span><div class="openclip-model-select-wrap"><select value={runEndStepId()} onChange={(event) => setRunEndStepId(event.currentTarget.value)}><For each={runPlanSteps()}>{(step) => <option value={step.id}>{step.id} {step.name}</option>}</For></select></div></label>
          </Show>
          <Show when={isAdmin() && runMode() === "run_only_step"}>
            <label class="openflow-field openclip-model-form-group"><span>单步运行</span><div class="openclip-model-select-wrap"><select value={runOnlyStepId()} onChange={(event) => setRunOnlyStepId(event.currentTarget.value)}><For each={runPlanSteps()}>{(step) => <option value={step.id}>{step.id} {step.name}</option>}</For></select></div></label>
          </Show>
          <Show when={isAdmin()}>
            <label class="openflow-field openclip-model-form-group"><span>SRT 改写</span><div class="openclip-model-select-wrap"><select value={runRewriteMode()} onChange={(event) => setRunRewriteMode(event.currentTarget.value)}><option value="strict">04_01 SRT 改写</option><option value="free">04_01 SRT 自由改写</option></select></div></label>
          </Show>
          <Show when={isAdmin() && !isThreeStepStoryboardRunDialog()}>
            <label class="openflow-field openclip-model-form-group"><span>TTS 构建器</span><div class="openclip-model-select-wrap"><select value={runTtsBuilderMode()} onChange={(event) => setRunTtsBuilderMode(event.currentTarget.value)}><option value="quick">03_02 快速声音匹配</option><option value="quick_adv">03_03 高级声音匹配</option><option value="builder_g">03_01 全量声音匹配</option><option value="skip">跳过该步骤</option></select></div></label>
          </Show>
          <Show when={isAdmin()}>
            <label class="openflow-field openclip-model-form-group"><span>StoryBoard</span><div class="openclip-model-select-wrap"><select value={isThreeStepStoryboardRunDialog() ? "model" : runStoryboardMode()} onChange={(event) => setRunStoryboardMode(event.currentTarget.value)}><option value="model">04_02 全量分组</option><Show when={!isThreeStepStoryboardRunDialog()}><option value="quick">04_03 快速分组</option></Show></select></div></label>
          </Show>
        </div>
        <div class="field-row openflow-model-dialog-actions openclip-model-dialog-actions openclip-run-model-actions">
          <button class="secondary openclip-model-cancel" type="button" onClick={closeRunModelDialog}>取消</button>
          <button
            class="openclip-model-confirm"
            type="button"
            disabled={!draft().run_model_provider || !draft().run_model_id || busy()}
            onClick={() => void (isThreeStepStoryboardRunDialog() ? saveFreeRewriteConfigAndOpenRunPanel() : isPromptBuilderFullTaskRunDialog() ? saveFullTaskConfigAndOpenRunPanel() : enterTask())}
          >
            {busy() ? "进入中..." : "进入任务"}
          </button>
        </div>
      </section>
    </Show>

    <Show when={runProgressOpen() && task()}>
      <div class="drawer-backdrop analysis-v1-run-progress-backdrop" onClick={() => (!runProgress() || isTerminalRunStatus(runProgress()?.status)) && setRunProgressOpen(false)} />
      <section class="verify-dialog analysis-v1-run-progress-dialog" style={runProgressDialogStyle()}>
        <div class="analysis-v1-run-progress-head" onPointerDown={startRunProgressDialogDrag}>
          <div>
            <div class="analysis-v1-run-context-tags analysis-v1-run-progress-tags">
              <span>任务 #{task()?.id || "-"}</span>
              <span>会话 #{task()?.session_id || "-"}</span>
              <Show when={runProgress()?.attempt_id}>
                <span>尝试 #{runProgress()?.attempt_id}</span>
              </Show>
            </div>
          </div>
          <div class="analysis-v1-run-commandbar">
            <Show when={!isTtsBuilderDialogRunProgress()}>
              <button class="primary icon-action analysis-v1-run-start" type="button" title={primaryRunActionLabel()} aria-label={primaryRunActionLabel()} disabled={!draft().run_model_provider || !draft().run_model_id || busy() || Boolean(activeRunProgress())} onClick={() => void runAnalysis(defaultRunOverrides())}><PlayClipIcon /></button>
            </Show>
            <button class="secondary icon-action" type="button" disabled={!hasStoryBoardFile()} title={hasStoryBoardFile() ? "故事板" : "StoryBoard 跑完后可打开"} aria-label="故事板" onClick={openStoryBoard}><StoryboardIcon /></button>
            <button class="icon-action" type="button" title="关闭" aria-label="关闭" onClick={() => setRunProgressOpen(false)}><CloseIcon /></button>
          </div>
        </div>
        <div class="analysis-v1-run-progress-body">
          <div class="analysis-v1-run-indicator-grid">
            <Show when={runProgress()} fallback={
              <div class="analysis-v1-run-step-list">
                <Show when={visibleRunPlanSteps().length} fallback={<div class="analysis-v1-empty">尚未运行</div>}>
                  <For each={visibleRunPlanSteps()}>{(step) => {
                    const stepId = String(step.id || "");
                    return (
                      <div
                        class={`analysis-v1-run-step is-idle ${selectedRunStepId() === stepId ? "is-selected" : ""}`}
                        onClick={() => {
                          setStepMenu(null);
                          setSelectedRunStepId(stepId);
                        }}
                        onContextMenu={(event) => openStepContextMenu(event, stepId)}
                      >
                        <div class="analysis-v1-run-step-main">
                          <strong>{runStepDisplayName(step)}</strong>
                        </div>
                        <span class="analysis-v1-run-step-status tag-idle">等待</span>
                      </div>
                    );
                  }}</For>
                </Show>
              </div>
            }>
              <div class="analysis-v1-run-step-list">
                <For each={visibleRunProgressSteps()}>{(step) => {
                  const stepId = String(step.id || "");
                  return (
                    <div
                      class={`analysis-v1-run-step is-${statusTone(step.status)} ${selectedRunStepId() === stepId ? "is-selected" : ""}`}
                      onClick={() => {
                        setStepMenu(null);
                        setSelectedRunStepId(stepId);
                      }}
                      onContextMenu={(event) => openStepContextMenu(event, stepId)}
                    >
                      <div class="analysis-v1-run-step-main">
                        <strong>{runStepDisplayName(step)}</strong>
                      </div>
                      <span class={`analysis-v1-run-step-status tag-${statusTone(step.status) === "ready" ? "ready" : statusTone(step.status) === "running" ? "available" : statusTone(step.status) === "failed" ? "failed" : "idle"}`}>{stepStatusLabel(step.status)}</span>
                      <span class="analysis-v1-run-step-duration">{stepDuration(step)}</span>
                      <Show when={step.message && !runProgressMessage()}>
                        <small>{step.message}</small>
                      </Show>
                    </div>
                  );
                }}</For>
              </div>
            </Show>
          </div>
          <Portal>
            <Show when={stepMenuStep()}>
              {(menuStep) => {
                const step = menuStep();
                const stepId = String(step.id || "");
                const hasActiveRun = isActiveRunStatus(runProgress()?.status);
                const isPendingStep = String(step.status || "").toLowerCase() === "pending";
                const canPause = isPendingStep && (runProgress()?.capabilities?.can_set_pause_point || !hasActiveRun);
                const canCancelPause = Boolean(plannedPauseBeforeStepId()) || runProgress()?.capabilities?.can_cancel_pause_point;
                return (
                  <div class="analysis-v1-step-menu" style={{ left: `${stepMenu()?.x || 0}px`, top: `${stepMenu()?.y || 0}px` }} onClick={(event) => event.stopPropagation()}>
                    <button type="button" disabled={hasActiveRun} onClick={() => void runStepAction("run_range", stepId)}>运行至此步</button>
                    <button type="button" disabled={hasActiveRun} onClick={() => void runStepAction("run_from_step", stepId)}>从此步开始运行</button>
                    <button type="button" disabled={hasActiveRun || !step.capabilities?.supports_run_only} onClick={() => void runStepAction("run_only_step", stepId)}>单独运行此步</button>
                    <button type="button" disabled={!isTerminalRunStatus(runProgress()?.status)} onClick={() => void runStepAction("rerun_from_step", stepId)}>重跑此步及后续</button>
                    <button type="button" disabled={!runProgress()?.capabilities?.can_stop || runCommandBusy() === "stop"} onClick={() => void stopRunProgress()}>{runProgress()?.status === "stopping" ? "正在停止..." : "当前步骤结束后停止"}</button>
                    <button type="button" disabled={!canPause || runCommandBusy() === `pause:${stepId}`} onClick={() => void setPauseBeforeStep(stepId)}>运行到此步前暂停</button>
                    <button type="button" disabled={!canCancelPause || runCommandBusy() === "cancelPause"} onClick={() => void cancelPausePoint()}>取消暂停点</button>
                    <button type="button" disabled={!runProgress()?.attempt_id} onClick={() => void refreshRunProgress()}>刷新</button>
                    <button type="button" onClick={() => void openRunStepDetail(stepId)}>查看详情</button>
                  </div>
                );
              }}
            </Show>
          </Portal>
          <Show when={runStepDetailOpen()}>
            <div class="analysis-v1-run-detail-popover-backdrop" onClick={() => setRunStepDetailOpen(false)} />
            <aside class="analysis-v1-run-step-detail analysis-v1-run-step-detail-popover">
                <div class="analysis-v1-run-detail-head">
                  <div>
                    <strong>{runStepDisplayName(selectedRunStep())}</strong>
                    <span>{stepStatusLabel(selectedRunStep()?.status)} / {stepDuration(selectedRunStep())}</span>
                  </div>
                  <div class="analysis-v1-run-detail-head-actions">
                    <button class="secondary" type="button" onClick={() => void loadStepDetail(selectedRunStep()?.id)}>刷新详情</button>
                    <button class="icon-action" type="button" title="关闭详情" aria-label="关闭详情" onClick={() => setRunStepDetailOpen(false)}><CloseIcon /></button>
                  </div>
                </div>
                <div class="analysis-v1-run-detail-tabs">
                  <For each={RUN_DETAIL_TABS}>{(tab) => <button type="button" class={runDetailTab() === tab.id ? "is-active" : ""} onClick={() => setRunDetailTab(tab.id)}>{tab.label}</button>}</For>
                </div>
                <div class="analysis-v1-run-detail-body">
                  <Show when={runDetailTab() === "overview"}>
                    <dl class="analysis-v1-run-detail-list">
                      <dt>状态</dt><dd>{stepStatusLabel(selectedRunStep()?.status)}</dd>
                      <dt>开始时间</dt><dd>{formatDateTime(selectedRunStep()?.started_at)}</dd>
                      <dt>结束时间</dt><dd>{formatDateTime(selectedRunStep()?.finished_at)}</dd>
                      <dt>退出码</dt><dd>{selectedRunStep()?.exit_code ?? "-"}</dd>
                      <dt>消息</dt><dd>{selectedRunStep()?.message || "-"}</dd>
                    </dl>
                    <StepMeteringOverview metering={selectedRunStep()?.metering} />
                  </Show>
                  <Show when={runDetailTab() === "metering"}>
                    <StepMeteringBreakdown metering={selectedRunStep()?.metering} />
                  </Show>
                  <Show when={runDetailTab() === "parameters"}>
                    <pre>{prettyJson(selectedStepQuickWatch()?.parameters || runProgress()?.plan || {})}</pre>
                  </Show>
                  <Show when={runDetailTab() === "command"}>
                    <pre>{prettyJson(selectedStepQuickWatch()?.command || {})}</pre>
                  </Show>
                  <Show when={runDetailTab() === "files"}>
                    <div class="analysis-v1-run-file-list">
                      <For each={quickWatchFileRows(selectedStepQuickWatch())}>{(file) => (
                        <div class="analysis-v1-run-file-row">
                          <span>{file.group}</span>
                          <code>{file.path}</code>
                          <em>{file.exists ? `${Number(file.size || 0).toLocaleString()} 字节` : "未生成"}</em>
                        </div>
                      )}</For>
                    </div>
                  </Show>
                  <Show when={runDetailTab() === "logs"}>
                    <div class="analysis-v1-run-log-grid">
                      <div><strong>stdout</strong><pre>{selectedStepQuickWatch()?.logs?.stdout_tail || selectedRunStep()?.stdout_tail || "-"}</pre></div>
                      <div><strong>stderr</strong><pre>{selectedStepQuickWatch()?.logs?.stderr_tail || selectedRunStep()?.stderr_tail || "-"}</pre></div>
                    </div>
                  </Show>
                </div>
            </aside>
          </Show>
          <Show when={ttsBuilderDialogRunHint()}>
            <div class="analysis-v1-run-progress-message is-failed">
              {ttsBuilderDialogRunHint()}
            </div>
          </Show>
          <Show when={runProgressMessage() && !ttsBuilderDialogRunHint()}>
            <div class={`analysis-v1-run-progress-message is-${statusTone(runProgress()?.status)}`}>
              {runProgressMessage()}
            </div>
          </Show>
          <div class="analysis-v1-run-total-duration">总耗时 {formatRunDuration(runProgressTotalDuration())}</div>
        </div>
      </section>
    </Show>
  </>;
}
