import { For, Show, createEffect, createMemo, createSignal, onCleanup } from "solid-js";
import { CloseIcon, EditIcon, PlayClipIcon, RefreshIcon, SaveIcon, SimpleChevronIcon, SlidersIcon } from "../AnalysisV1/analysisV1Icons.jsx";
import RunProgressDialog from "../shared/RunProgressDialog.jsx";
import StoryboardIcon from "../shared/StoryboardIcon.jsx";
import OneClickMovieDialog from "../DanceMimicV1/OneClickMovieDialog.jsx";
import { DiffText } from "../AnalysisV1/components/AnalysisV1DialogueView.jsx";
import KouboTaskCreateFromScriptModal from "../KouboTaskList/KouboTaskCreateFromScriptModal.jsx";
import { kouboTaskListApi } from "../KouboTaskList/kouboTaskListApi.js";
import { normalizeTask } from "../KouboTaskList/kouboTaskListModel.js";
import { talkingHeadV1Api } from "./talkingHeadV1Api.js";
import "../DanceMimicV1/danceMimicV1.css";
import "../styles/analysis-v1-output.css";
import "../KouboTaskList/styles/taskCreateModal.css";

const ERROR_STATUSES = new Set(["failed", "blocked", "cancelled"]);

const STEP_LABELS = {
  "00": "运行变量准备",
  "01": "故事版生成",
  "02": "故事版分镜生成",
  "03": "故事版配置",
  "04_01": "口播脚本改写",
  "05_01": "生成视频计划",
  "05_02": "逐句生成视频",
  "06_01": "合并成片",
};

const STORYBOARD_PLAN_STEPS = [
  { id: "00", name: "00_PrepareSessionVariables", display_name_zh: "运行变量准备", status: "pending" },
  { id: "04_01", name: "04_01_TalkingHeadSRTRewrite", display_name_zh: "口播脚本改写", status: "pending" },
  { id: "01", name: "01_StoryBoardGenerate", display_name_zh: "故事版生成", status: "pending" },
  { id: "02", name: "02_StoryBoardStructure", display_name_zh: "故事版分镜生成", status: "pending" },
  { id: "03", name: "03_StoryBoardConfig", display_name_zh: "故事版配置", status: "pending" },
];

const ONE_CLICK_PLAN_STEPS = [
  ...STORYBOARD_PLAN_STEPS,
  { id: "05_01", name: "05_01_VideoPlanGenerator", display_name_zh: "生成视频计划", status: "pending" },
  { id: "05_02", name: "05_02_VideoPlanExecutor", display_name_zh: "逐句生成视频", status: "pending" },
  { id: "06_01", name: "06_01_VideoPlanComposer", display_name_zh: "合并成片", status: "pending" },
];

function taskIdFromHash(hash) {
  const value = String(hash || window.location.hash || "");
  const match = value.match(/#\/talking-head\/tasks\/(\d+)/);
  return match ? Number(match[1]) : null;
}

function statusTone(status) {
  const value = String(status || "");
  if (value === "completed") return "good";
  if (value === "running" || value === "queued") return "busy";
  if (value === "blocked") return "warn";
  if (value === "failed" || value === "cancelled") return "bad";
  return "neutral";
}

function statusBadgeTone(status) {
  const tone = statusTone(status);
  if (tone === "good") return "ready";
  if (tone === "busy") return "available";
  if (tone === "bad") return "failed";
  return "idle";
}

function runDialogTone(status) {
  const tone = statusTone(status);
  if (tone === "good") return "ready";
  if (tone === "busy") return "running";
  if (tone === "bad") return "failed";
  return "idle";
}

function stepStatusLabel(status) {
  const value = String(status || "").toLowerCase();
  return {
    pending: "等待",
    queued: "排队中",
    running: "运行中",
    completed: "完成",
    blocked: "阻断",
    failed: "失败",
    skipped: "跳过",
  }[value] || value || "-";
}

function oneClickMovieSyncLabel(segment) {
  const syncMode = String(segment?.lipsync?.sync_mode || "").toLowerCase();
  return segment?.lipsync?.need_lipsync === false || syncMode === "audio_replace_retime"
    ? "音频合成"
    : "音频匹配";
}

function formatSeconds(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "0s";
  return `${number.toFixed(2).replace(/\.?0+$/, "")}s`;
}

function formatTimeRange(item) {
  return `${formatSeconds(item?.start)} - ${formatSeconds(item?.end)}`;
}

function rawFileUrl(sessionId, filePath) {
  const value = String(filePath || "").trim();
  if (!sessionId || !value) return "";
  const base = window.location.href.split("#")[0];
  const root = base.endsWith("/") ? base : `${base}/`;
  return new URL(`api/session-tasks/${sessionId}/raw/${value.split("/").map(encodeURIComponent).join("/")}`, root).toString();
}

function sidebarItemFromTalkingHead({ taskId, sessionId, selectedItem, movieOutputVideo, movieOutputVideoUrl, movieStatus }) {
  if (!taskId && !movieOutputVideoUrl) return null;
  return {
    id: selectedItem?.srt_id || selectedItem?.dialogue_id || "",
    taskId,
    sessionId,
    dialogue: selectedItem?.text || "",
    start: selectedItem?.start,
    end: selectedItem?.end,
    movieOutputVideo,
    movieOutputVideoUrl,
    movieStatus,
  };
}

export function TalkingHeadV1MediaSidebar(props) {
  return <section class="panel dmv1-media-sidebar">
    <Show when={props.item} fallback={<div class="dmv1-reference-empty">一键成片完成后在这里预览</div>}>
      <div class="dmv1-reference-card dmv1-movie-output-card">
        <div class="dmv1-reference-card-title">
          <span>一键成片结果</span>
          <Show when={props.item.movieOutputVideo}><em>{props.item.movieStatus === "completed" ? "完成" : "处理中"}</em></Show>
        </div>
        <Show when={props.item.movieOutputVideoUrl} fallback={<div class="dmv1-reference-empty">等待合并成片产出</div>}>
          <video controls preload="metadata" src={props.item.movieOutputVideoUrl} />
          <small>{props.item.movieOutputVideo}</small>
        </Show>
      </div>
    </Show>
  </section>;
}

function storyboardPayload(force = false) {
  return {
    mode: "run_selected_steps",
    selected_step_ids: ["00", "04_01", "01", "02", "03"],
    force,
    tts_builder_mode: "skip",
    rewrite_mode: "strict",
    storyboard_mode: "quick",
    options: {
      workflow_profile: "person_talking_head_v1",
      resource_strategy: { kind: "talking_head_only", allow_cutaway: false },
    },
  };
}

function oneClickPayload(detail, runOptions = {}) {
  const hasStoryboard = Boolean(detail?.storyboard?.exists && detail?.storyboard?.configured);
  const talkingHead = detail?.talking_head || {};
  const segmentPlanning = talkingHead.segment_planning || {};
  const srtSeconds = Number(segmentPlanning.srt_target_seconds) || 8;
  const options = typeof runOptions === "boolean" ? { force: runOptions } : (runOptions || {});
  return {
    force: options.force ?? true,
    resume: Boolean(options.resume),
    run_only_step_id: String(options.run_only_step_id || ""),
    run_from_step_id: String(options.run_from_step_id || (options.run_only_step_id ? "" : (hasStoryboard ? "05_01" : "00"))),
    tts_builder_mode: "skip",
    rewrite_mode: "strict",
    storyboard_mode: "quick",
    video_plan_settings: {
      max_video_seconds: srtSeconds,
      min_video_seconds: Math.min(2, srtSeconds),
      split_tolerance_seconds: 0,
      resource_strategy: { kind: "talking_head_only", allow_cutaway: false },
      first_frame_policy: "portrait_then_previous_tail",
      portrait_segments_per_image: Number(segmentPlanning.portrait_segments_per_image) || 2,
    },
    composer_settings: { subtitle_mode: "none", watermark_mode: "never" },
    options: {
      workflow_profile: "person_talking_head_v1",
      talking_head: talkingHead,
    },
  };
}

export default function TalkingHeadV1Module(props) {
  const [taskId, setTaskId] = createSignal(taskIdFromHash(props.routeHash));
  const [detail, setDetail] = createSignal(null);
  const [error, setError] = createSignal("");
  const [busy, setBusy] = createSignal("");
  const [runDialogOpen, setRunDialogOpen] = createSignal(false);
  const [movieDialogOpen, setMovieDialogOpen] = createSignal(false);
  const [paramsCollapsed, setParamsCollapsed] = createSignal(false);
  const [storyboardCollapsed, setStoryboardCollapsed] = createSignal(false);
  const [selectedSrtId, setSelectedSrtId] = createSignal("");
  const [dialogueEditMode, setDialogueEditMode] = createSignal(false);
  const [dialogueDrafts, setDialogueDrafts] = createSignal({});
  const [dialogueSaveStatus, setDialogueSaveStatus] = createSignal("");
  const [savingDialogue, setSavingDialogue] = createSignal(false);
  const [configWizardOpen, setConfigWizardOpen] = createSignal(false);
  const [configWizardTask, setConfigWizardTask] = createSignal(null);
  const [configWizardOptions, setConfigWizardOptions] = createSignal({});
  const [configWizardModels, setConfigWizardModels] = createSignal({ items: [], default_model: { providerID: "", modelID: "" } });
  const [configWizardBusy, setConfigWizardBusy] = createSignal("");
  const [latestRun, setLatestRun] = createSignal(null);
  const [latestMovieRun, setLatestMovieRun] = createSignal(null);
  const [runDialogView, setRunDialogView] = createSignal("preview");
  const [movieDialogView, setMovieDialogView] = createSignal("preview");
  const [pendingRunForce, setPendingRunForce] = createSignal(false);
  let pollTimer = 0;

  createEffect(() => {
    setTaskId(taskIdFromHash(props.routeHash));
  });

  const srtItems = createMemo(() => detail()?.srt?.items || []);
  const isAiRewrite = createMemo(() => detail()?.script_creation_mode === "ai_rewrite");
  const isAiCreate = createMemo(() => detail()?.script_creation_mode === "ai_create");
  const selectedItem = createMemo(() => srtItems().find((item) => item.srt_id === selectedSrtId()) || srtItems()[0] || null);
  const runStatus = createMemo(() => latestRun()?.status || detail()?.task?.status || "draft");
  const movieRunStatus = createMemo(() => latestMovieRun()?.status || "idle");
  const isActiveRun = createMemo(() => ["queued", "running"].includes(String(runStatus())));
  const isActiveMovieRun = createMemo(() => ["queued", "running"].includes(String(movieRunStatus())));
  const showRunAttempt = createMemo(() => runDialogView() === "attempt" || isActiveRun());
  const showMovieAttempt = createMemo(() => movieDialogView() === "attempt" || isActiveMovieRun());
  const runDialogStatus = createMemo(() => showRunAttempt() ? runStatus() : "idle");
  const movieDialogStatus = createMemo(() => showMovieAttempt() ? movieRunStatus() : "idle");
  const runDialogSteps = createMemo(() => showRunAttempt() ? (latestRun()?.steps || []) : STORYBOARD_PLAN_STEPS);
  const movieDialogSteps = createMemo(() => showMovieAttempt() ? (latestMovieRun()?.steps || []) : ONE_CLICK_PLAN_STEPS);
  const runDialogMessage = createMemo(() => showRunAttempt() ? (latestRun()?.summary || "") : "将执行以下人物口播 StoryBoard 任务，确认后点击运行。");
  const movieDialogMessage = createMemo(() => showMovieAttempt() ? (latestMovieRun()?.summary || "") : "将执行以下一键成片任务，确认后点击运行。");
  const portrait = createMemo(() => detail()?.talking_head?.portrait || {});
  const voice = createMemo(() => detail()?.talking_head?.voice_timing || {});
  const segmentPlanning = createMemo(() => detail()?.talking_head?.segment_planning || {});
  const movieOutputVideo = createMemo(() => String(latestMovieRun()?.compose?.output_video || "").trim());
  const movieOutputVideoUrl = createMemo(() => {
    const output = movieOutputVideo();
    if (!output || !detail()?.session_id) return "";
    const cacheKey = latestMovieRun()?.run_id || output;
    return `${rawFileUrl(detail().session_id, output)}?v=${encodeURIComponent(String(cacheKey))}`;
  });

  function startDialogueEdit() {
    setDialogueDrafts(Object.fromEntries(srtItems().map((item) => [item.srt_id, String(item.rewritten_text || "")])));
    setDialogueSaveStatus("");
    setDialogueEditMode(true);
  }

  function cancelDialogueEdit() {
    setDialogueDrafts({});
    setDialogueSaveStatus("");
    setDialogueEditMode(false);
  }

  function updateDialogueDraft(srtId, value) {
    setDialogueDrafts((previous) => ({ ...previous, [srtId]: value }));
  }

  async function saveDialogueEdits() {
    if (!taskId() || !dialogueEditMode()) return;
    setSavingDialogue(true);
    setDialogueSaveStatus("");
    setError("");
    try {
      await talkingHeadV1Api.saveRewrittenSrt(taskId(), {
        items: srtItems().map((item) => ({
          srt_id: item.srt_id,
          dialogue: String(dialogueDrafts()[item.srt_id] ?? item.rewritten_text ?? ""),
        })),
      });
      await load({ silent: true });
      setDialogueDrafts({});
      setDialogueEditMode(false);
      setDialogueSaveStatus("保存成功");
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存改写 SRT 失败");
    } finally {
      setSavingDialogue(false);
    }
  }

  async function openConfigWizard() {
    if (!taskId()) return;
    setConfigWizardBusy("load");
    setError("");
    try {
      const [taskPayload, options] = await Promise.all([
        kouboTaskListApi.detail(taskId()),
        kouboTaskListApi.options(),
      ]);
      setConfigWizardTask(normalizeTask(taskPayload?.item || {}));
      setConfigWizardOptions(options || {});
      setConfigWizardOpen(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载人物口播配置失败");
    } finally {
      setConfigWizardBusy("");
    }
  }

  async function saveConfigWizard(payload, options = {}) {
    if (!taskId()) return;
    setConfigWizardBusy("save");
    setError("");
    try {
      await kouboTaskListApi.updateTalkingHead(taskId(), payload);
      setConfigWizardOpen(false);
      await load({ silent: true });
      if (options.action === "run_all") openRunDialogPreview(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存人物口播配置失败");
      throw err;
    } finally {
      setConfigWizardBusy("");
    }
  }

  async function generateConfigPrompt(payload, model, scope = "rewrite") {
    if (!taskId()) return null;
    setConfigWizardBusy("prompt");
    setError("");
    try {
      await kouboTaskListApi.updateTalkingHead(taskId(), payload);
      const request = {
        prompt_model_provider: model?.providerID || "",
        prompt_model_id: model?.modelID || "",
      };
      if (scope === "all") {
        await kouboTaskListApi.generatePrompt(taskId(), { ...request, prompt_kind: "rewrite" });
        return await kouboTaskListApi.generatePrompt(taskId(), { ...request, prompt_kind: "storyboard" });
      }
      return await kouboTaskListApi.generatePrompt(taskId(), { ...request, prompt_kind: "rewrite" });
    } catch (err) {
      setError(err instanceof Error ? err.message : "生成人物口播提示词失败");
      throw err;
    } finally {
      setConfigWizardBusy("");
    }
  }

  function openRunDialogPreview(force = false) {
    setPendingRunForce(Boolean(force));
    if (!isActiveRun()) setLatestRun(null);
    setRunDialogView("preview");
    setRunDialogOpen(true);
    void load({ silent: true });
  }

  function openMovieDialogPreview() {
    setMovieDialogView(latestMovieRun()?.run_id ? "attempt" : "preview");
    setMovieDialogOpen(true);
    void load({ silent: true });
  }

  async function load(options = {}) {
    const id = taskId();
    if (!id) return;
    if (!options.silent) setBusy("load");
    setError("");
    try {
      const next = await talkingHeadV1Api.detail(id);
      setDetail(next);
      if (!selectedSrtId() && next?.srt?.items?.[0]?.srt_id) setSelectedSrtId(next.srt.items[0].srt_id);
      if (latestRun()?.attempt_id && ["queued", "running"].includes(String(latestRun()?.status))) {
        setLatestRun(await talkingHeadV1Api.runStoryboardStatus(id, latestRun().attempt_id));
      }
      const movie = await talkingHeadV1Api.oneClickMovieStatus(id, latestMovieRun()?.run_id || "");
      setLatestMovieRun(movie);
      if (movie?.run_id) setMovieDialogView("attempt");
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载人物口播任务失败");
    } finally {
      if (!options.silent) setBusy("");
    }
  }

  async function startRun(force = false) {
    const id = taskId();
    if (!id) return;
    setBusy("run");
    setError("");
    try {
      const run = await talkingHeadV1Api.runStoryboard(id, storyboardPayload(force));
      setLatestRun(run);
      setRunDialogView("attempt");
      setRunDialogOpen(true);
      void load({ silent: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : "启动人物口播 StoryBoard 失败");
    } finally {
      setBusy("");
    }
  }

  async function startOneClickMovie(runOptions = { force: true }) {
    const id = taskId();
    if (!id) return;
    setBusy("one-click-movie");
    setError("");
    try {
      const movie = await talkingHeadV1Api.oneClickMovie(id, oneClickPayload(detail(), runOptions));
      setLatestMovieRun(movie);
      setMovieDialogView("attempt");
      setMovieDialogOpen(true);
      void load({ silent: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : "启动人物口播一键成片失败");
    } finally {
      setBusy("");
    }
  }

  createEffect(() => {
    taskId();
    void load();
  });

  createEffect(() => {
    window.clearInterval(pollTimer);
    pollTimer = 0;
    if (isActiveRun() || isActiveMovieRun()) {
      pollTimer = window.setInterval(() => void load({ silent: true }), 2500);
    }
  });

  createEffect(() => {
    props.onMediaItemChange?.(sidebarItemFromTalkingHead({
      taskId: taskId(),
      sessionId: detail()?.session_id,
      selectedItem: selectedItem(),
      movieOutputVideo: movieOutputVideo(),
      movieOutputVideoUrl: movieOutputVideoUrl(),
      movieStatus: movieRunStatus(),
    }));
  });

  onCleanup(() => {
    window.clearInterval(pollTimer);
    props.onMediaItemChange?.(null);
  });

  return (
    <div class="dmv1-page">
      <section class="dmv1-params-panel">
        <div class="dmv1-panel-head">
          <div class="dmv1-title-wrap">
            <span class="dmv1-step-badge">1</span>
            <h2>人物口播</h2>
            <span class={`dmv1-status-tag tag-${statusBadgeTone(runStatus())}`}>{String(runStatus() || "draft").toUpperCase()}</span>
            <Show when={taskId()}>
              <span class="dmv1-task-session-tag">
                <span>任务 #{taskId()}</span>
                <span>/</span>
                <span>会话 #{detail()?.session_id || "-"}</span>
              </span>
            </Show>
          </div>
          <div class="dmv1-header-actions">
            <button type="button" class="secondary" onClick={() => { window.location.hash = "#/koubo-tasks"; }}>任务列表</button>
            <button class="secondary" type="button" disabled={!taskId() || configWizardBusy() === "load"} onClick={() => void openConfigWizard()} title="打开人物口播配置向导" aria-label="参数设置">
              <SlidersIcon />
              <span>参数设置</span>
            </button>
            <button type="button" disabled={!taskId()} onClick={() => openRunDialogPreview(false)} title="打开人物口播 StoryBoard 运行弹窗">
              <PlayClipIcon />
              <span>{latestRun() ? "重新运行" : "运行"}</span>
            </button>
            <button type="button" disabled={!taskId()} onClick={openMovieDialogPreview} title={detail()?.storyboard?.exists ? "打开一键成片运行弹窗" : "将先生成故事版再一键成片"}>
              <PlayClipIcon />
              <span>一键成片</span>
            </button>
            <button type="button" class="secondary" disabled={!taskId() || isActiveRun() || isActiveMovieRun()} onClick={() => openRunDialogPreview(true)} title="打开强制重建运行弹窗">
              <RefreshIcon />
              <span>强制重建</span>
            </button>
            <button type="button" class="secondary" disabled={!taskId()} onClick={() => { window.location.hash = `#/koubo-storyboard/tasks/${taskId()}`; }} title="打开故事板">
              <StoryboardIcon />
              <span>故事板</span>
            </button>
            <button class="dmv1-icon-action" type="button" title={paramsCollapsed() ? "展开参数" : "收起参数"} aria-label={paramsCollapsed() ? "展开参数" : "收起参数"} aria-pressed={!paramsCollapsed()} onClick={() => setParamsCollapsed((value) => !value)}>
              <SimpleChevronIcon direction={paramsCollapsed() ? "down" : "up"} />
            </button>
          </div>
        </div>
        <Show when={detail() && !paramsCollapsed()}>
          <div class="dmv1-panel-body">
            <div class="dmv1-param-fields">
              <div class="dmv1-param-item"><div class="dmv1-param-label">人物形象</div><div class="dmv1-param-value"><span class="dmv1-inline-code">{portrait().portrait_image_path || "-"}</span></div></div>
              <div class="dmv1-param-item">
                <div class="dmv1-param-label">声音</div>
                <div class="dmv1-param-value dmv1-voice-param-value">
                  <span>{voice().voice_label || voice().voice_id || "StoryBoard 中选择"}</span>
                  <span class="dmv1-voice-tempo">Tempo（语速）：{voice().tempo || 1}</span>
                </div>
              </div>
              <div class="dmv1-param-item"><div class="dmv1-param-label">分镜秒</div><div class="dmv1-param-value">{segmentPlanning().srt_target_seconds || 8}s</div></div>
              <div class="dmv1-param-item"><div class="dmv1-param-label">StoryBoard</div><div class="dmv1-param-value">{detail()?.storyboard?.exists ? "已生成" : "未生成"}</div></div>
            </div>
          </div>
        </Show>
      </section>

      <Show when={error()}>
        <div class="dmv1-banner bad">{error()}</div>
      </Show>
      <Show when={!detail()}>
        <div class="dmv1-empty">{taskId() ? "正在加载人物口播任务..." : "未选择人物口播任务。"}</div>
      </Show>

      <Show when={detail()}>
        <section class="dmv1-storyboard-panel">
          <div class="dmv1-storyboard-head">
            <div class="dmv1-storyboard-title-row">
              <h3>人物口播脚本</h3>
              <span class="dmv1-storyboard-count-tag">{srtItems().length} 句对白</span>
            </div>
            <div class="dmv1-storyboard-head-actions">
              <Show when={isAiCreate()}>
                <span class="dmv1-storyboard-save-status">{dialogueSaveStatus()}</span>
                <button class="analysis-v1-dialogue-icon-button" type="button" title={dialogueEditMode() ? "取消修改" : "修改人物口播脚本"} aria-label={dialogueEditMode() ? "取消修改" : "修改人物口播脚本"} disabled={savingDialogue()} onClick={() => dialogueEditMode() ? cancelDialogueEdit() : startDialogueEdit()}>
                  <Show when={dialogueEditMode()} fallback={<EditIcon />}><CloseIcon /></Show>
                </button>
                <button class="analysis-v1-dialogue-icon-button primary" type="button" title="保存人物口播脚本" aria-label="保存人物口播脚本" disabled={!dialogueEditMode() || savingDialogue()} onClick={() => void saveDialogueEdits()}>
                  <SaveIcon />
                </button>
              </Show>
              <button class="dmv1-icon-action dmv1-storyboard-collapse" type="button" title={storyboardCollapsed() ? "展开人物口播脚本" : "收起人物口播脚本"} aria-label={storyboardCollapsed() ? "展开人物口播脚本" : "收起人物口播脚本"} aria-pressed={!storyboardCollapsed()} onClick={() => setStoryboardCollapsed((value) => !value)}>
                <SimpleChevronIcon direction={storyboardCollapsed() ? "down" : "up"} />
              </button>
            </div>
          </div>
          <Show when={!storyboardCollapsed()}>
            <Show when={srtItems().length} fallback={<div class="dmv1-empty">当前任务还没有 SRT。</div>}>
              <Show
                when={isAiRewrite()}
                fallback={
                  <div class="dmv1-storyboard-grid">
                    <div class="dmv1-dialogue-list" role="listbox" aria-label="逐句对白">
                      <For each={srtItems()}>{(item) => (
                        <div class={`dmv1-dialogue-item ${selectedItem()?.srt_id === item.srt_id ? "is-selected" : ""}`} onClick={() => setSelectedSrtId(item.srt_id)}>
                          <span class="dmv1-dialogue-index">{item.index}</span>
                          <Show when={isAiCreate() && dialogueEditMode()} fallback={<span class="dmv1-dialogue-text">{item.text || "无对白"}</span>}>
                            <textarea class="dmv1-dialogue-text dmv1-dialogue-edit" value={dialogueDrafts()[item.srt_id] ?? item.rewritten_text ?? item.text ?? ""} disabled={savingDialogue()} onInput={(event) => updateDialogueDraft(item.srt_id, event.currentTarget.value)} onClick={(event) => event.stopPropagation()} />
                          </Show>
                          <em>{formatTimeRange(item)}</em>
                        </div>
                      )}</For>
                    </div>
                  </div>
                }
              >
                <div class="dmv1-storyboard-grid dmv1-rewrite-compare">
                  <div class="analysis-v1-dialogue-compare">
                    <div class="analysis-v1-dialogue-compare-head dmv1-rewrite-compare-head">
                      <span>参考脚本</span>
                      <span>改写脚本</span>
                      <div class="analysis-v1-dialogue-toolbar">
                        <span>{dialogueSaveStatus()}</span>
                        <div class="analysis-v1-dialogue-actions">
                          <button
                            class="analysis-v1-dialogue-icon-button"
                            type="button"
                            title={dialogueEditMode() ? "取消修改" : "修改改写 SRT"}
                            aria-label={dialogueEditMode() ? "取消修改" : "修改改写 SRT"}
                            disabled={savingDialogue()}
                            onClick={() => dialogueEditMode() ? cancelDialogueEdit() : startDialogueEdit()}
                          >
                            <Show when={dialogueEditMode()} fallback={<EditIcon />}><CloseIcon /></Show>
                          </button>
                          <button
                            class="analysis-v1-dialogue-icon-button primary"
                            type="button"
                            title="保存改写 SRT"
                            aria-label="保存改写 SRT"
                            disabled={!dialogueEditMode() || savingDialogue()}
                            onClick={() => void saveDialogueEdits()}
                          >
                            <SaveIcon />
                          </button>
                        </div>
                      </div>
                    </div>
                    <div class="analysis-v1-dialogue-list" role="listbox" aria-label="原 SRT 与改写 SRT 对比">
                      <For each={srtItems()}>{(item) => (
                        <div class={`analysis-v1-dialogue-row ${selectedItem()?.srt_id === item.srt_id ? "is-active" : ""}`} onClick={() => setSelectedSrtId(item.srt_id)}>
                          <span class="analysis-v1-dialogue-index">{item.index}</span>
                          <p class="analysis-v1-dialogue-text">
                            <DiffText original={item.original_text || ""} rewritten={dialogueDrafts()[item.srt_id] ?? item.rewritten_text ?? ""} side="original" />
                          </p>
                          <Show
                            when={dialogueEditMode()}
                            fallback={
                              <p class="analysis-v1-dialogue-text">
                                <Show when={item.rewritten_text} fallback={<span class="analysis-v1-dialogue-missing">未生成改写</span>}>
                                  <DiffText original={item.original_text || ""} rewritten={item.rewritten_text || ""} side="rewrite" />
                                </Show>
                              </p>
                            }
                          >
                            <textarea
                              class="analysis-v1-dialogue-text analysis-v1-dialogue-edit"
                              value={dialogueDrafts()[item.srt_id] ?? item.rewritten_text ?? ""}
                              disabled={savingDialogue()}
                              onInput={(event) => updateDialogueDraft(item.srt_id, event.currentTarget.value)}
                              onClick={(event) => event.stopPropagation()}
                            />
                          </Show>
                          <em>{formatTimeRange(item)}</em>
                        </div>
                      )}</For>
                    </div>
                  </div>
                </div>
              </Show>
            </Show>
          </Show>
        </section>
      </Show>

      <KouboTaskCreateFromScriptModal
        open={configWizardOpen}
        task={configWizardTask}
        busy={() => configWizardBusy() === "save"}
        promptBusy={() => configWizardBusy() === "prompt"}
        promptModels={configWizardModels}
        promptOptions={configWizardOptions}
        profile={() => ({ id: "person_talking_head_v1", createMode: "person_talking_head" })}
        onLoadPromptModels={async () => {
          if (configWizardModels().items?.length) return configWizardModels();
          const models = await kouboTaskListApi.promptModels();
          setConfigWizardModels(models);
          return models;
        }}
        onListVoiceClones={() => kouboTaskListApi.listTalkingHeadVoiceClones()}
        onPreviewVoiceClone={(payload) => kouboTaskListApi.previewTalkingHeadVoiceClone(payload)}
        onDeleteVoiceClone={(voiceId) => kouboTaskListApi.deleteTalkingHeadVoiceClone(voiceId)}
        onGeneratePrompts={(payload, model) => generateConfigPrompt(payload, model, "all")}
        onGenerateScriptFinalPrompt={(payload, model) => generateConfigPrompt(payload, model, "rewrite")}
        onClose={() => setConfigWizardOpen(false)}
        onCreate={(payload, options) => saveConfigWizard(payload, options)}
      />

      <RunProgressDialog
        open={runDialogOpen()}
        taskId={taskId()}
        sessionId={detail()?.session_id}
        attemptId={showRunAttempt() ? (latestRun()?.attempt_no || latestRun()?.attempt_id) : ""}
        status={runDialogStatus()}
        steps={runDialogSteps()}
        planSteps={STORYBOARD_PLAN_STEPS}
        startTitle={showRunAttempt() ? "重新运行人物口播" : pendingRunForce() ? "强制重建人物口播" : "运行人物口播"}
        startDisabled={!taskId() || busy() === "run" || isActiveRun()}
        storyboardDisabled={!taskId()}
        storyboardTitle={detail()?.storyboard?.exists ? "故事板" : "故事板生成后可打开"}
        message={runDialogMessage()}
        startedAt={showRunAttempt() ? latestRun()?.started_at : 0}
        finishedAt={showRunAttempt() ? latestRun()?.finished_at : 0}
        totalDurationSeconds={showRunAttempt() ? latestRun()?.duration_seconds : 0}
        statusTone={runDialogTone}
        stepStatusLabel={stepStatusLabel}
        stepDisplayName={(step) => STEP_LABELS[step.id] || step.display_name_zh || step.name || step.id}
        onStart={() => void startRun(pendingRunForce())}
        onOpenStoryBoard={() => { window.location.hash = `#/koubo-storyboard/tasks/${taskId()}`; }}
        onClose={() => setRunDialogOpen(false)}
      />

      <OneClickMovieDialog
        open={movieDialogOpen()}
        taskId={taskId()}
        sessionId={detail()?.session_id}
        runId={showMovieAttempt() ? latestMovieRun()?.run_id : ""}
        status={movieDialogStatus()}
        steps={movieDialogSteps()}
        planSteps={ONE_CLICK_PLAN_STEPS}
        segments={showMovieAttempt() ? (latestMovieRun()?.segments || []) : []}
        storyboardItems={detail()?.storyboard?.items || []}
        startTitle="重新一键成片"
        startDisabled={!taskId() || busy() === "one-click-movie" || isActiveRun() || isActiveMovieRun()}
        storyboardDisabled={!taskId()}
        storyboardTitle="故事板"
        message={movieDialogMessage()}
        outputVideo={showMovieAttempt() ? movieOutputVideo() : ""}
        outputVideoUrl={showMovieAttempt() ? movieOutputVideoUrl() : ""}
        startedAt={showMovieAttempt() ? latestMovieRun()?.started_at : 0}
        finishedAt={showMovieAttempt() ? latestMovieRun()?.finished_at : 0}
        totalDurationSeconds={showMovieAttempt() ? latestMovieRun()?.duration_seconds : 0}
        stepDisplayName={(step) => STEP_LABELS[step.id] || step.display_name_zh || step.name || step.id}
        syncLabel={oneClickMovieSyncLabel}
        onStart={() => void startOneClickMovie({ force: true })}
        onResumeVideoStep={() => void startOneClickMovie({ force: false, resume: true, run_only_step_id: "05_02" })}
        onResumeVideoStepAndFollowing={() => void startOneClickMovie({ force: false, resume: true, run_from_step_id: "05_02" })}
        onOpenStoryBoard={() => { window.location.hash = `#/koubo-storyboard/tasks/${taskId()}`; }}
        onClose={() => setMovieDialogOpen(false)}
      />
    </div>
  );
}
