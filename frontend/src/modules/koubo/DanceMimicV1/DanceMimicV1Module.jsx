import { For, Show, createEffect, createMemo, createSignal, onCleanup } from "solid-js";
import { PlayClipIcon, RefreshIcon, SimpleChevronIcon } from "../AnalysisV1/analysisV1Icons.jsx";
import RunProgressDialog from "../shared/RunProgressDialog.jsx";
import StoryboardIcon from "../shared/StoryboardIcon.jsx";
import OneClickMovieDialog from "./OneClickMovieDialog.jsx";
import { danceMimicV1Api } from "./danceMimicV1Api.js";
import "./danceMimicV1.css";

const ERROR_STATUSES = new Set(["failed", "blocked", "cancelled"]);

const STEP_LABELS = {
  "00": "准备运行参数",
  "01": "拆解参考视频",
  "02": "处理人脸合规",
  "03": "构建故事版",
};

const ONE_CLICK_STEP_LABELS = {
  "05_01": "生成视频计划",
  "05_02": "逐句生成视频",
  "06_01": "合并成片",
};

const ARTIFACT_LABELS = {
  variables_json: "变量文件",
  source_reference_video: "参考源视频",
  target_identity_image: "目标人物图片",
  reference_media_manifest: "参考媒体清单",
  reference_segments_manifest: "人脸合规清单",
  srt_storyboard: "故事版对白",
  storyboard_seed: "分配故事版记录",
  stale_manifest: "素材失效清单",
};

const HIDDEN_RUN_ARTIFACT_KEYS = new Set(["variables_json", "source_reference_video", "target_identity_image"]);
const RUN_ARTIFACT_ORDER = ["reference_media_manifest", "reference_segments_manifest", "srt_storyboard", "storyboard_seed", "stale_manifest"];

function taskIdFromHash(hash) {
  const value = String(hash || window.location.hash || "");
  const match = value.match(/#\/dance-mimic\/tasks\/(\d+)/);
  return match ? Number(match[1]) : null;
}

function statusLabel(status) {
  const value = String(status || "draft");
  const labels = {
    draft: "草稿",
    queued: "排队中",
    running: "运行中",
    completed: "已完成",
    blocked: "已阻断",
    failed: "失败",
    cancelled: "已取消",
  };
  return labels[value] || value;
}

function statusTone(status) {
  const value = String(status || "");
  if (value === "completed") return "good";
  if (value === "running" || value === "queued") return "busy";
  if (value === "blocked") return "warn";
  if (value === "failed" || value === "cancelled") return "bad";
  return "neutral";
}

function runDialogTone(status) {
  const tone = statusTone(status);
  if (tone === "good") return "ready";
  if (tone === "busy") return "running";
  if (tone === "bad") return "failed";
  return "idle";
}

function statusBadgeTone(status) {
  const tone = statusTone(status);
  if (tone === "good") return "ready";
  if (tone === "busy") return "available";
  if (tone === "bad") return "failed";
  return "idle";
}

function stepStatusLabel(status) {
  const value = String(status || "").toLowerCase();
  const labels = {
    pending: "等待",
    queued: "排队中",
    running: "运行中",
    completed: "完成",
    blocked: "阻断",
    failed: "失败",
    cancelled: "已取消",
    skipped: "跳过",
  };
  return labels[value] || statusLabel(value);
}

function formatSize(bytes) {
  const value = Number(bytes || 0);
  if (!value) return "-";
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
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

function sidebarItemFromDialogue(item, movie = {}) {
  if (!item && !movie?.outputVideo) return null;
  return {
    id: item?.srt_id || item?.dialogue_id || "",
    dialogue: item?.text || "无对白",
    start: item?.start,
    end: item?.end,
    segmentId: item?.source_segment_id || "",
    videoUrl: item?.reference_video?.preview_url || "",
    movieOutputVideo: movie.outputVideo || "",
    movieOutputVideoUrl: movie.outputVideoUrl || "",
  };
}

export function DanceMimicV1MediaSidebar(props) {
  return <section class="panel dmv1-media-sidebar">
    <Show when={props.item} fallback={<div class="dmv1-reference-empty">选择一句对白后查看遮脸参考视频</div>}>
      <div class="dmv1-reference-card">
        <div class="dmv1-reference-title">
          <strong>{props.item.id}</strong>
          <span>{formatSeconds(props.item.start)} - {formatSeconds(props.item.end)}</span>
        </div>
        <p>{props.item.dialogue}</p>
      </div>
      <div class="dmv1-reference-card">
        <div class="dmv1-reference-card-title">
          <span>遮脸参考视频</span>
          <Show when={props.item.segmentId}><em>{props.item.segmentId}</em></Show>
        </div>
        <Show when={props.item.videoUrl} fallback={<div class="dmv1-reference-empty">未找到匹配视频片段</div>}>
          <video controls preload="metadata" src={props.item.videoUrl} />
        </Show>
      </div>
      <div class="dmv1-reference-card dmv1-movie-output-card">
        <div class="dmv1-reference-card-title">
          <span>一键成片结果</span>
          <Show when={props.item.movieOutputVideo}><em>final</em></Show>
        </div>
        <Show when={props.item.movieOutputVideoUrl} fallback={<div class="dmv1-reference-empty">等待合并成片产出</div>}>
          <video controls preload="metadata" src={props.item.movieOutputVideoUrl} />
        </Show>
      </div>
    </Show>
  </section>;
}

function privacyPreviewStatusLabel(status) {
  return ({
    ready: "已生成",
    pending: "等待生成",
    stale: "需重新生成",
    blocked: "生成失败",
  })[String(status || "")] || "不可用";
}

function privacyPreviewScopeLabel(scope) {
  return ({
    both: "参考视频和目标图",
    reference_video: "参考视频",
    target_identity: "目标人物图",
    none: "未应用",
  })[String(scope || "")] || String(scope || "");
}

function PrivacyGridPreview(props) {
  const preview = () => props.preview || {};
  const status = () => String(preview().status || "pending");
  const reference = () => preview().reference_video || {};
  const target = () => preview().target_identity || {};
  return <section class="dmv1-privacy-preview-panel">
    <div class="dmv1-privacy-preview-head">
      <div>
        <h3>隐私网格预览</h3>
        <span>{privacyPreviewScopeLabel(preview().effective_grid_scope)}</span>
      </div>
      <span class={`dmv1-privacy-preview-status is-${status()}`}>{privacyPreviewStatusLabel(status())}</span>
    </div>
    <Show when={status() === "ready"} fallback={<div class={`dmv1-privacy-preview-message is-${status()}`}>{preview().message || "步骤 02 完成后生成预览。"}</div>}>
      <div class="dmv1-privacy-preview-grid">
        <article class="dmv1-privacy-preview-item">
          <div class="dmv1-privacy-preview-item-head">
            <strong>参考视频代表帧</strong>
            <span>{reference().grid_applied ? `${formatSeconds(reference().preview_timestamp_seconds)} 代表帧` : "未应用"}</span>
          </div>
          <Show when={reference().grid_applied && reference().preview_url} fallback={<div class="dmv1-privacy-preview-empty">未应用隐私网格</div>}>
            <img src={reference().preview_url} alt="参考视频隐私网格代表帧" loading="lazy" />
          </Show>
        </article>
        <article class="dmv1-privacy-preview-item">
          <div class="dmv1-privacy-preview-item-head">
            <strong>目标人物图</strong>
            <span>{target().grid_applied ? "已应用" : "未应用"}</span>
          </div>
          <Show when={target().grid_applied && target().preview_url} fallback={<div class="dmv1-privacy-preview-empty">未应用隐私网格</div>}>
            <img src={target().preview_url} alt="目标人物隐私网格预览" loading="lazy" />
          </Show>
        </article>
      </div>
    </Show>
  </section>;
}

function commandPayload(detail, runOptions = {}) {
  const danceOptions = detail?.meta?.dance_mimic || {};
  return {
    target_video_seconds: Number(danceOptions.target_video_seconds) || 8,
    minimum_video_seconds: Number(danceOptions.minimum_video_seconds) || 4,
    face_detections_manifest: String(danceOptions.face_detections_manifest || ""),
    reference_privacy_mode: String(danceOptions.reference_privacy_mode || detail?.reference_privacy_mode || "face_mask_only"),
    apply_privacy_grid_to_reference_video: danceOptions.apply_privacy_grid_to_reference_video ?? detail?.apply_privacy_grid_to_reference_video ?? true,
    apply_privacy_grid_to_target_identity_image: danceOptions.apply_privacy_grid_to_target_identity_image ?? detail?.apply_privacy_grid_to_target_identity_image ?? true,
    block_on_face_not_detected: Boolean(danceOptions.block_on_face_not_detected),
    force: Boolean(runOptions.force),
  };
}

function oneClickMoviePayload(detail, runOptions = {}) {
  const movieOptions = detail?.movie_plan?.options || {};
  return {
    max_video_seconds: Number(movieOptions.max_video_seconds) || 4,
    min_video_seconds: Number(movieOptions.min_video_seconds) || 2,
    split_tolerance_seconds: Number(movieOptions.split_tolerance_seconds) || 2,
    force: runOptions.force !== false,
    resume: Boolean(runOptions.resume),
    run_only_step_id: String(runOptions.run_only_step_id || ""),
    run_from_step_id: String(runOptions.run_from_step_id || ""),
    subtitle_mode: movieOptions.subtitle_mode || "hyperframe",
    watermark_mode: movieOptions.watermark_mode || "never",
  };
}

export default function DanceMimicV1Module(props) {
  const [taskId, setTaskId] = createSignal(taskIdFromHash(props.routeHash));
  const [detail, setDetail] = createSignal(null);
  const [busy, setBusy] = createSignal("");
  const [error, setError] = createSignal("");
  const [paramsCollapsed, setParamsCollapsed] = createSignal(true);
  const [storyboardCollapsed, setStoryboardCollapsed] = createSignal(true);
  const [runDialogOpen, setRunDialogOpen] = createSignal(false);
  const [movieDialogOpen, setMovieDialogOpen] = createSignal(false);
  const [selectedDialogueId, setSelectedDialogueId] = createSignal("");
  let pollTimer = 0;

  const latestRun = createMemo(() => detail()?.latest_run || null);
  const latestMovieRun = createMemo(() => detail()?.latest_movie_run || null);
  const task = createMemo(() => detail()?.task || {});
  const runStatus = createMemo(() => latestRun()?.status || task().status || "draft");
  const movieRunStatus = createMemo(() => latestMovieRun()?.status || "idle");
  const isActiveRun = createMemo(() => runStatus() === "queued" || runStatus() === "running");
  const isActiveMovieRun = createMemo(() => movieRunStatus() === "queued" || movieRunStatus() === "running");
  const steps = createMemo(() => {
    const runSteps = latestRun()?.steps;
    if (Array.isArray(runSteps) && runSteps.length) return runSteps;
    const planSteps = detail()?.run_plan?.steps;
    return Array.isArray(planSteps) ? planSteps : [];
  });
  const movieSteps = createMemo(() => {
    const runSteps = latestMovieRun()?.steps;
    if (Array.isArray(runSteps) && runSteps.length) return runSteps;
    const planSteps = detail()?.movie_plan?.steps;
    return Array.isArray(planSteps) ? planSteps : [];
  });
  const fullMovieSteps = createMemo(() => [...steps(), ...movieSteps()]);
  const movieSegments = createMemo(() => Array.isArray(latestMovieRun()?.segments) ? latestMovieRun().segments : []);
  const artifactItems = createMemo(() => {
    const files = detail()?.artifacts?.files || {};
    const visibleItems = new Map(
      Object.entries(files)
        .filter(([key]) => !HIDDEN_RUN_ARTIFACT_KEYS.has(key))
        .map(([key, value]) => [key, { key, label: ARTIFACT_LABELS[key] || key, ...(value || {}) }])
    );
    return RUN_ARTIFACT_ORDER.map((key) => visibleItems.get(key) || { key, label: ARTIFACT_LABELS[key] || key, exists: false, size: 0 });
  });
  const readyForStoryBoard = createMemo(() => Boolean(detail()?.artifacts?.storyboard_ready));
  const storyboardItems = createMemo(() => {
    const items = detail()?.storyboard?.items;
    return Array.isArray(items) ? items : [];
  });
  const selectedDialogue = createMemo(() => storyboardItems().find((item) => (item.srt_id || item.dialogue_id) === selectedDialogueId()) || storyboardItems()[0] || null);
  const movieOutputVideo = createMemo(() => String(latestMovieRun()?.compose?.output_video || "").trim());
  const movieOutputVideoUrl = createMemo(() => {
    const output = movieOutputVideo();
    if (!output || !detail()?.session_id) return "";
    const cacheKey = latestMovieRun()?.updated_at || latestMovieRun()?.run_id || "";
    return `${rawFileUrl(detail().session_id, output)}?v=${encodeURIComponent(String(cacheKey))}`;
  });
  const runDialogMessage = createMemo(() => latestRun()?.summary && ERROR_STATUSES.has(String(latestRun()?.status || "")) ? latestRun()?.summary : "");
  const movieDialogMessage = createMemo(() => latestMovieRun()?.summary && ERROR_STATUSES.has(String(latestMovieRun()?.status || "")) ? latestMovieRun()?.summary : "");

  async function load(options = {}) {
    const id = taskId();
    if (!id) return;
    if (!options.silent) setBusy("load");
    setError("");
    try {
      setDetail(await danceMimicV1Api.detail(id));
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载 DanceMimic 任务失败");
    } finally {
      if (!options.silent) setBusy("");
    }
  }

  async function startRun(options = {}) {
    const id = taskId();
    if (!id) return;
    setRunDialogOpen(true);
    setBusy("run");
    setError("");
    try {
      const run = await danceMimicV1Api.run(id, commandPayload(detail(), options));
      setDetail((prev) => ({ ...(prev || {}), latest_run: run }));
      await load({ silent: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : "启动 DanceMimic 执行失败");
    } finally {
      setBusy("");
    }
  }

  async function startOneClickMovie(options = {}) {
    const id = taskId();
    if (!id) return;
    setMovieDialogOpen(true);
    setBusy("one-click-movie");
    setError("");
    try {
      const run = await danceMimicV1Api.oneClickMovie(id, oneClickMoviePayload(detail(), options));
      setDetail((prev) => ({ ...(prev || {}), latest_movie_run: run }));
      await load({ silent: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : "启动一键成片失败");
    } finally {
      setBusy("");
    }
  }

  async function resumeOneClickMovieVideoStep() {
    await startOneClickMovie({ force: false, resume: true, run_only_step_id: "05_02" });
  }

  async function resumeOneClickMovieFromVideoStep() {
    await startOneClickMovie({ force: false, resume: true, run_from_step_id: "05_02" });
  }

  createEffect(() => {
    const nextTaskId = taskIdFromHash(props.routeHash);
    if (nextTaskId !== taskId()) {
      setTaskId(nextTaskId);
      setDetail(null);
      setError("");
      setParamsCollapsed(true);
      setStoryboardCollapsed(true);
    }
  });

  createEffect(() => {
    const id = taskId();
    if (id) void load();
  });

  createEffect(() => {
    const items = storyboardItems();
    const selected = selectedDialogueId();
    if (!items.length) {
      if (selected) setSelectedDialogueId("");
      return;
    }
    if (!items.some((item) => (item.srt_id || item.dialogue_id) === selected)) {
      setSelectedDialogueId(items[0].srt_id || items[0].dialogue_id || "");
    }
  });

  createEffect(() => {
    props.onMediaItemChange?.(sidebarItemFromDialogue(selectedDialogue(), { outputVideo: movieOutputVideo(), outputVideoUrl: movieOutputVideoUrl() }));
  });

  createEffect(() => {
    window.clearInterval(pollTimer);
    pollTimer = 0;
    if (isActiveRun() || isActiveMovieRun()) {
      pollTimer = window.setInterval(() => void load({ silent: true }), 2500);
    }
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
            <h2>动作模拟</h2>
            <span class={`dmv1-status-tag tag-${statusBadgeTone(runStatus())}`}>{String(runStatus() || "draft").toUpperCase()}</span>
            <Show when={taskId()}>
              <span class="dmv1-task-session-tag">
                <span>任务 #{taskId()}</span>
                <span>/</span>
                <Show when={detail()?.session_id} fallback={<span>会话 -</span>}>
                  <span>会话 #{detail()?.session_id}</span>
                </Show>
              </span>
            </Show>
          </div>
          <div class="dmv1-header-actions">
            <button type="button" class="secondary" onClick={() => { window.location.hash = "#/koubo-tasks"; }}>任务列表</button>
            <button type="button" disabled={!taskId()} onClick={() => setRunDialogOpen(true)} title="打开动作模拟执行弹窗">
              <PlayClipIcon />
              <span>{latestRun() ? "重新运行" : "运行"}</span>
            </button>
            <button type="button" disabled={!taskId()} onClick={() => setMovieDialogOpen(true)} title={readyForStoryBoard() ? "打开一键成片运行弹窗" : "故事版生成后可一键成片"}>
              <PlayClipIcon />
              <span>一键成片</span>
            </button>
            <button type="button" class="secondary" disabled={!taskId() || busy() === "run" || isActiveRun() || isActiveMovieRun()} onClick={() => void startRun({ force: true })} title="强制重建">
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
              <div class="dmv1-param-item">
                <div class="dmv1-param-label">参考视频</div>
                <div class="dmv1-param-value"><span class="dmv1-inline-code">{detail()?.reference_video_path || "-"}</span></div>
              </div>
              <div class="dmv1-param-item">
                <div class="dmv1-param-label">目标人物图片</div>
                <div class="dmv1-param-value"><span class="dmv1-inline-code">{detail()?.target_identity_image_path || "-"}</span></div>
              </div>
              <div class="dmv1-param-item">
                <div class="dmv1-param-label">参考隐私</div>
                <div class="dmv1-param-value">{detail()?.reference_privacy_mode || detail()?.meta?.dance_mimic?.reference_privacy_mode || "-"}</div>
              </div>
              <Show when={(detail()?.reference_privacy_mode || detail()?.meta?.dance_mimic?.reference_privacy_mode) === "red_grid_guide"}>
                <div class="dmv1-param-item">
                  <div class="dmv1-param-label">隐私网格范围</div>
                  <div class="dmv1-param-value">{detail()?.effective_grid_scope || detail()?.meta?.dance_mimic?.effective_grid_scope || "-"}</div>
                </div>
              </Show>
              <div class="dmv1-param-item">
                <div class="dmv1-param-label">StoryBoard</div>
                <div class="dmv1-param-value">{readyForStoryBoard() ? "已生成" : "未生成"}</div>
              </div>
            </div>
          </div>
        </Show>
      </section>

      <Show when={error()}>
        <div class="dmv1-banner bad">{error()}</div>
      </Show>

      <Show when={!detail()}>
        <div class="dmv1-empty">{taskId() ? "正在加载 DanceMimic 任务..." : "未选择 DanceMimic 任务。"}</div>
      </Show>

      <Show when={detail()?.privacy_grid_preview?.status && detail()?.privacy_grid_preview?.status !== "not_applicable"}>
        <PrivacyGridPreview preview={detail()?.privacy_grid_preview} />
      </Show>

      <Show when={detail()?.storyboard?.exists}>
        <section class="dmv1-storyboard-panel">
          <div class="dmv1-storyboard-head">
            <div>
              <h3>逐句动作模拟拆解</h3>
              <span>{storyboardItems().length} 句对白</span>
            </div>
            <button class="dmv1-icon-action dmv1-storyboard-collapse" type="button" title={storyboardCollapsed() ? "展开逐句动作模拟拆解" : "收起逐句动作模拟拆解"} aria-label={storyboardCollapsed() ? "展开逐句动作模拟拆解" : "收起逐句动作模拟拆解"} aria-pressed={!storyboardCollapsed()} onClick={() => setStoryboardCollapsed((value) => !value)}>
              <SimpleChevronIcon direction={storyboardCollapsed() ? "down" : "up"} />
            </button>
          </div>
          <Show when={!storyboardCollapsed()}>
            <Show when={storyboardItems().length} fallback={<div class="dmv1-empty">故事版已生成，但没有找到逐句对白。</div>}>
              <div class="dmv1-storyboard-grid">
                <div class="dmv1-dialogue-list" role="listbox" aria-label="逐句对白">
                  <For each={storyboardItems()}>{(item) => {
                    const itemId = item.srt_id || item.dialogue_id || "";
                    return (
                      <button
                        type="button"
                        class={`dmv1-dialogue-item ${selectedDialogue()?.srt_id === item.srt_id ? "is-selected" : ""}`}
                        onClick={() => setSelectedDialogueId(itemId)}
                      >
                        <span class="dmv1-dialogue-index">{item.index}</span>
                        <span class="dmv1-dialogue-text">{item.text || "无对白"}</span>
                        <em>{formatTimeRange(item)}</em>
                      </button>
                    );
                  }}</For>
                </div>
              </div>
            </Show>
          </Show>
        </section>
      </Show>

      <RunProgressDialog
        open={runDialogOpen()}
        taskId={taskId()}
        sessionId={detail()?.session_id}
        attemptId={latestRun()?.attempt_no || latestRun()?.attempt_id}
        status={runStatus()}
        steps={steps()}
        startTitle={latestRun() ? "重新运行动作模拟" : "运行动作模拟"}
        startDisabled={!taskId() || busy() === "run" || isActiveRun()}
        storyboardDisabled={!taskId()}
        storyboardTitle={readyForStoryBoard() ? "故事板" : "故事板生成后可打开"}
        message={runDialogMessage()}
        startedAt={latestRun()?.started_at}
        finishedAt={latestRun()?.finished_at}
        totalDurationSeconds={latestRun()?.duration_seconds}
        statusTone={runDialogTone}
        stepStatusLabel={stepStatusLabel}
        stepDisplayName={(step) => STEP_LABELS[step.id] || step.display_name_zh || step.name || step.id}
        onStart={() => void startRun()}
        onOpenStoryBoard={() => { window.location.hash = `#/koubo-storyboard/tasks/${taskId()}`; }}
        onClose={() => setRunDialogOpen(false)}
      >
        <section class="dmv1-run-artifacts">
          <h3>产物</h3>
          <div class="dmv1-artifact-grid">
            <For each={artifactItems()}>{(item, index) => (
              <div class={`dmv1-artifact ${item.exists ? "exists" : ""}`}>
                <div class="dmv1-artifact-head">
                  <span class="dmv1-artifact-index">{index() + 1}</span>
                  <span class="dmv1-artifact-label">{item.label}</span>
                  <strong class={`dmv1-artifact-status ${item.exists ? "exists" : "missing"}`}>{item.exists ? "存在" : "缺失"}</strong>
                </div>
                <Show when={item.exists}>
                  <small>{formatSize(item.size)}</small>
                </Show>
              </div>
            )}</For>
          </div>
        </section>
      </RunProgressDialog>

      <OneClickMovieDialog
        open={movieDialogOpen()}
        taskId={taskId()}
        sessionId={detail()?.session_id}
        runId={latestMovieRun()?.run_id}
        status={movieRunStatus()}
        steps={fullMovieSteps()}
        segments={movieSegments()}
        storyboardItems={storyboardItems()}
        startTitle="重新一键成片"
        startDisabled={!taskId() || busy() === "one-click-movie" || isActiveRun() || isActiveMovieRun()}
        storyboardDisabled={!taskId()}
        storyboardTitle="故事板"
        message={movieDialogMessage()}
        outputVideo={latestMovieRun()?.compose?.output_video}
        startedAt={latestMovieRun()?.started_at}
        finishedAt={latestMovieRun()?.finished_at}
        totalDurationSeconds={latestMovieRun()?.duration_seconds}
        stepDisplayName={(step) => STEP_LABELS[step.id] || ONE_CLICK_STEP_LABELS[step.id] || step.display_name_zh || step.name || step.id}
        onStart={() => void startOneClickMovie({ force: true })}
        onResumeVideoStep={() => void resumeOneClickMovieVideoStep()}
        onResumeVideoStepAndFollowing={() => void resumeOneClickMovieFromVideoStep()}
        onOpenStoryBoard={() => { window.location.hash = `#/koubo-storyboard/tasks/${taskId()}`; }}
        onClose={() => setMovieDialogOpen(false)}
      />
    </div>
  );
}
