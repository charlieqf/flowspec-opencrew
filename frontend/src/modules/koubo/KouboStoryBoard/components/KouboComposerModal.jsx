import { For, Show, createEffect, createMemo, createSignal, onCleanup } from "solid-js";
import { emitDebugError } from "../../../../debug/debugAdapter.js";
import { DownloadIcon, FilmIcon, LayersIcon, MessageIcon, PlayIcon, WorkflowIcon, XIcon } from "../kouboStoryboardIcons.jsx";
import KouboAgentDrawer from "./KouboAgentDrawer.jsx";

function text(value) {
  return String(value || "").trim();
}

function sameTarget(left = {}, right = {}) {
  return text(left.target_type) === text(right.target_type)
    && text(left.shot_id) === text(right.shot_id)
    && text(left.scene_id) === text(right.scene_id);
}

function targetType(target = {}) {
  return text(target.target_type || target.scope);
}

function isTaskTarget(target = {}) {
  return ["task", "all"].includes(targetType(target));
}

function targetLabel(item = {}) {
  if (item.kind === "task") return "整片";
  if (item.kind === "shot") return item.label || item.target?.shot_id || "镜头";
  return item.target?.scene_id || item.label || "场景";
}

function targetScopeLabel(target = {}) {
  const targetType = text(target.target_type || target.scope);
  if (targetType === "task" || targetType === "all") return "整片";
  if (targetType === "shot") return text(target.shot_id) || "镜头";
  if (targetType === "scene") return [text(target.shot_id), text(target.scene_id)].filter(Boolean).join(" / ") || "场景";
  return "未指定";
}

function shortHash(value) {
  const hash = text(value);
  return hash ? hash.slice(0, 8) : "-";
}

function statusLabel(item = {}) {
  if (!item.ready) return item.status?.exists ? "需更新" : "未就绪";
  if (item.status?.status === "completed" && item.status?.exists) return "已合成";
  return "可合成";
}

function downloadName(path) {
  const name = text(path).split(/[\\/]/).pop().replace(/[^\w.\-\u4e00-\u9fa5]+/g, "_");
  return name || "composed-video.mp4";
}

function fileName(path) {
  return text(path).split(/[\\/]/).pop() || "";
}

function downloadUrl(href) {
  const value = text(href);
  if (!value) return "";
  try {
    const url = new URL(value, window.location.href);
    url.searchParams.set("download", "1");
    return url.toString();
  } catch {
    const separator = value.includes("?") ? "&" : "?";
    return `${value}${separator}download=1`;
  }
}

function countLabels(item = {}) {
  if (item.kind === "scene") {
    const composeLabel = `合成 ${item.ready_segment_count || 0}/${item.segment_count || 0} 段`;
    const storyboardCount = Number(item.storyboard_segment_count || 0);
    const readyStoryboardCount = Number(item.ready_storyboard_segment_count || 0);
    const showSource = storyboardCount > 0 && storyboardCount !== Number(item.segment_count || 0);
    return {
      primary: composeLabel,
      secondary: showSource ? `原片段 ${readyStoryboardCount}/${storyboardCount}` : "",
    };
  }
  if (item.kind === "shot") return { primary: `${item.ready_scene_count || 0}/${item.scene_count || 0} 场景`, secondary: "" };
  return { primary: `${item.ready_shot_count || 0}/${item.shot_count || 0} 镜头`, secondary: "" };
}

function elapsedLabel(ms) {
  const seconds = Math.max(0, Math.floor(Number(ms || 0) / 1000));
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  return minutes ? `${minutes}m ${String(remainder).padStart(2, "0")}s` : `${seconds}s`;
}

export default function KouboComposerModal(props) {
  const [selectedId, setSelectedId] = createSignal("");
  const [agentOpen, setAgentOpen] = createSignal(false);
  const [now, setNow] = createSignal(Date.now());
  const [templates, setTemplates] = createSignal([]);
  const [templateId, setTemplateId] = createSignal("");
  const [templateNote, setTemplateNote] = createSignal("");
  const [templating, setTemplating] = createSignal(false);
  const [templatedUrl, setTemplatedUrl] = createSignal("");
  const [selectedDownloadUrl, setSelectedDownloadUrl] = createSignal("");
  const [selectedDownloadError, setSelectedDownloadError] = createSignal("");
  const activeTemplate = createMemo(() => templates().find((tpl) => tpl.id === templateId()) || null);
  let runningTimer = 0;
  let downloadLinkRequestId = 0;
  const candidates = createMemo(() => props.result?.()?.candidates || []);
  const scenes = createMemo(() => candidates().filter((item) => item.kind === "scene"));
  const shots = createMemo(() => candidates().filter((item) => item.kind === "shot"));
  const shotPlan = createMemo(() => candidates().find((item) => item.kind === "task") || null);
  const requestedTarget = createMemo(() => props.result?.()?.current_target || props.result?.()?.requested_target || props.result?.()?.target || {});
  const planTarget = createMemo(() => props.result?.()?.plan_target || {});
  const traceItems = createMemo(() => {
    const result = props.result?.() || {};
    const task = result.task || {};
    return [
      ["Task", task.id ? `#${task.id}` : "-"],
      ["Session", task.session_id ? `#${task.session_id}` : "-"],
      ["Req", targetScopeLabel(requestedTarget())],
      ["Plan", targetScopeLabel(planTarget())],
      ["Hash", shortHash(result.plan_hash)],
    ];
  });
  const requestedCandidate = createMemo(() => candidates().find((item) => sameTarget(item.target, requestedTarget())) || null);
  const needsTaskVideoPlan = createMemo(() => isTaskTarget(requestedTarget()) && Boolean(targetType(planTarget())) && !isTaskTarget(planTarget()));
  const groupedShots = createMemo(() => shots().map((shot) => ({
    shot,
    scenes: scenes().filter((scene) => scene.target?.shot_id === shot.target?.shot_id),
  })).filter((group) => group.scenes.length || group.shot));
  const defaultSelection = createMemo(() => {
    const requested = requestedCandidate();
    if (requested) return requested;
    const plan = shotPlan();
    if (plan) return plan;
    const readyShot = shots().find((item) => item.ready);
    if (readyShot) return readyShot;
    const readyScene = scenes().find((item) => item.ready);
    if (readyScene) return readyScene;
    return plan || shots()[0] || scenes()[0] || null;
  });
  const selected = createMemo(() => candidates().find((item) => item.id === selectedId()) || defaultSelection());
  const busy = () => ["checking", "executing"].includes(text(props.state?.().phase));
  const summary = createMemo(() => ({
    scene_count: scenes().length,
    shot_count: shots().length,
    ready_count: candidates().filter((item) => item.ready).length,
    segment_count: scenes().reduce((total, item) => total + Number(item.segment_count || 0), 0),
    ready_segment_count: scenes().reduce((total, item) => total + Number(item.ready_segment_count || 0), 0),
  }));
  const selectedVideoPath = createMemo(() => text(selected()?.status?.video_path));
  const selectedVideoUrl = createMemo(() => {
    const path = selectedVideoPath();
    return path && props.assetUrl ? `${props.assetUrl(path)}?v=${encodeURIComponent(text(selected()?.status?.updated_at) || text(props.result?.()?.elapsed_seconds) || "composer")}` : "";
  });
  const selectedVideoDownloadUrl = createMemo(() => selectedDownloadUrl() || downloadUrl(selectedVideoUrl()));
  const runningTitle = createMemo(() => text(props.state?.().phase) === "checking" ? "正在检查目标" : "HyperFrame 渲染中");
  const runningStatus = createMemo(() => text(props.state?.().status) || (text(props.state?.().phase) === "checking" ? "正在准备合成目标..." : "正在渲染合成视频..."));
  const runningElapsed = createMemo(() => elapsedLabel(now() - (Number(props.state?.().startedAt) || now())));
  const segmentVideoItems = (scene = {}) => (scene.segments || []).filter((item) => item?.exists && text(item?.video_path)).map((item, index) => ({
    ...item,
    label: text(item.label) || text(item.segment_id) || `Segment ${index + 1}`,
    file_name: fileName(item.video_path),
    url: props.assetUrl ? `${props.assetUrl(text(item.video_path))}?v=${encodeURIComponent(text(item.segment_id) || String(index + 1))}` : "",
  })).filter((item) => item.url);

  function taskIdForDebug() {
    const taskObj = typeof props.task === "function" ? props.task() : props.task;
    return taskObj?.id || null;
  }

  function composerAgentContext() {
    return {
      requestedTarget: requestedTarget(),
      planTarget: planTarget(),
      selected_candidate: selected(),
      needsTaskVideoPlan: needsTaskVideoPlan(),
      state: props.state?.() || {},
      result: props.result?.() || {},
    };
  }

  function composerAgentChips() {
    return [
      { label: "Req", value: targetScopeLabel(requestedTarget()) },
      { label: "Plan", value: targetScopeLabel(planTarget()) },
      { label: "Ready", value: String(summary().ready_count) },
      { label: "State", value: text(props.state?.().phase) || "-" },
    ];
  }

  function renderComposerAgentCandidate(candidate) {
    const payload = candidate.payload || {};
    if (candidate.kind !== "composer_diagnosis") {
      return <article class="kbsp-agent-candidate"><strong>{candidate.title}</strong><pre>{JSON.stringify(payload, null, 2)}</pre></article>;
    }
    const actions = Array.isArray(payload.next_actions) ? payload.next_actions : [];
    return <article class="kbsp-agent-candidate">
      <strong>{payload.title || "Composer 诊断"}</strong>
      <Show when={Array.isArray(payload.findings) && payload.findings.length}>
        <ul>
          <For each={payload.findings}>{(finding) => <li>{finding.message || JSON.stringify(finding)}</li>}</For>
        </ul>
      </Show>
      <div class="kbsp-agent-candidate-actions">
        <For each={actions}>{(action) => {
          const actionName = text(action.action);
          if (actionName === "generate_task_video_plan") {
            return <button type="button" onClick={() => {
              if (action.confirm !== false && !window.confirm("生成整片计划？")) return;
              props.regenerateVideoPlan?.({ target_type: "task", shot_id: "", scene_id: "", action_source: "composer_agent_candidate" });
            }}>{action.label || "生成整片计划"}</button>;
          }
          if (actionName === "execute_composer") {
            return <button type="button" onClick={() => {
              if (action.confirm !== false && !window.confirm("执行当前合成目标？")) return;
              props.executeComposer?.(action.target || selected()?.target);
            }}>{action.label || "执行 Composer"}</button>;
          }
          if (actionName === "select_candidate") {
            return <button type="button" class="secondary" onClick={() => setSelectedId(text(action.candidate_id || action.id))}>{action.label || "选择候选"}</button>;
          }
          if (actionName === "refresh_composer_execution") {
            return <button type="button" class="secondary" onClick={() => void props.refreshExecution?.()}>{action.label || "刷新状态"}</button>;
          }
          return <button type="button" class="secondary" onClick={() => void navigator.clipboard?.writeText(JSON.stringify(action, null, 2))}>{action.label || actionName || "复制动作"}</button>;
        }}</For>
        <button type="button" class="secondary" onClick={() => void props.refreshExecution?.()}>刷新状态</button>
      </div>
    </article>;
  }

  createEffect(() => {
    if (!props.open?.()) return;
    const preferred = requestedCandidate();
    setSelectedId((preferred || defaultSelection() || {}).id || "");
  });

  createEffect(() => {
    if (!props.open?.()) {
      setSelectedDownloadUrl("");
      setSelectedDownloadError("");
      return;
    }
    const path = selectedVideoPath();
    const taskObj = typeof props.task === "function" ? props.task() : props.task;
    const sessionId = taskObj?.session_id;
    const api = props.api;
    const requestId = ++downloadLinkRequestId;
    setSelectedDownloadUrl("");
    setSelectedDownloadError("");
    if (!path || !sessionId || !api?.rawDownloadLink) return;
    api.rawDownloadLink(sessionId, path)
      .then((result) => {
        if (requestId !== downloadLinkRequestId) return;
        setSelectedDownloadUrl(text(result?.url));
      })
      .catch((error) => {
        if (requestId !== downloadLinkRequestId) return;
        setSelectedDownloadError(error instanceof Error ? error.message : String(error || "下载链接生成失败"));
      });
  });

  createEffect(() => {
    if (!props.open?.()) return;
    if (templates().length) return;
    fetch("/hyperframe_templates/templates.json")
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        const list = Array.isArray(data?.templates) ? data.templates : [];
        setTemplates(list);
        if (list.length && !templateId()) setTemplateId(text(list[0].id));
      })
      .catch((err) => emitDebugError(err, { family: "hyperframe_template", task_id: taskIdForDebug(), detail: "Load HyperFrame templates failed" }));
  });

  async function runApplyTemplate() {
    const tplId = templateId();
    const videoPath = selectedVideoPath();
    if (!tplId || !videoPath || templating()) return;
    const taskObj = typeof props.task === "function" ? props.task() : props.task;
    const taskId = taskObj?.id;
    if (!taskId || !props.api?.applyHyperframeTemplate) {
      setTemplateNote("无法发起渲染：缺少任务或接口。");
      return;
    }
    setTemplateNote("");
    setTemplatedUrl("");
    setTemplating(true);
    try {
      const res = await props.api.applyHyperframeTemplate(taskId, {
        template_id: tplId,
        video_path: videoPath,
        params: activeTemplate()?.params || {},
      });
      const outPath = text(res?.video_path);
      if (outPath && props.assetUrl) {
        setTemplatedUrl(`${props.assetUrl(outPath)}?v=${Date.now()}`);
      } else {
        setTemplateNote("渲染完成但未返回视频路径。");
      }
    } catch (err) {
      setTemplateNote(`套用失败：${err?.message || err}`);
    } finally {
      setTemplating(false);
    }
  }

  createEffect(() => {
    if (!busy()) {
      if (runningTimer) {
        clearInterval(runningTimer);
        runningTimer = 0;
      }
      return;
    }
    setNow(Date.now());
    if (!runningTimer) runningTimer = setInterval(() => setNow(Date.now()), 1000);
  });

  onCleanup(() => {
    if (runningTimer) clearInterval(runningTimer);
  });

  const CandidateButton = (item, extraClass = "") => {
    const labels = countLabels(item);
    return <button type="button" class={`kbsp-cpm-item ${extraClass}`} classList={{ "is-active": item.id === selectedId(), "is-completed": item.status?.status === "completed" && item.status?.exists }} onClick={() => setSelectedId(item.id)}>
      <span class="kbsp-cpm-kind">{item.kind === "scene" ? <LayersIcon /> : <FilmIcon />}{item.kind === "scene" ? "场景" : item.kind === "shot" ? "镜头" : "整片"}</span>
      <strong>{targetLabel(item)}</strong>
      <em>{statusLabel(item)}</em>
      <small class="kbsp-cpm-count">
        <span>{labels.primary}</span>
        <Show when={labels.secondary}><span>{labels.secondary}</span></Show>
      </small>
    </button>;
  };

  const SegmentRow = (segment, index) => <div class="kbsp-cpm-segment-row">
    <span class="kbsp-cpm-segment-index">{segment.position_label || `S${index() + 1}`}</span>
    <div class="kbsp-cpm-segment-main">
      <span>{segment.label}</span>
      <small>{segment.file_name}</small>
    </div>
    <video
      src={segment.url}
      muted
      playsinline
      preload="none"
      onMouseEnter={(event) => event.currentTarget.play?.()}
      onMouseLeave={(event) => {
        event.currentTarget.pause?.();
        event.currentTarget.currentTime = 0;
      }}
    />
  </div>;

  return <Show when={props.open?.()}>
    <div class="kbsp-vpm-backdrop kbsp-cpm-backdrop" role="dialog" aria-modal="true" aria-label="视频合成">
      <section class="kbsp-vpm-shell kbsp-cpm-shell">
        <header class="kbsp-vpm-header">
          <div class="kbsp-vpm-title-wrap">
            <div class="kbsp-vpm-mark kbsp-cpm-mark"><LayersIcon /></div>
            <div class="kbsp-vpm-title-copy">
              <h2>视频合成</h2>
              <div class="kbsp-vpm-summary-line">
                <span>场景 {summary().scene_count}</span>
                <span>镜头 {summary().shot_count}</span>
                <span>Segment {summary().ready_segment_count}/{summary().segment_count}</span>
                <span>目标 {summary().ready_count}</span>
              </div>
            </div>
          </div>
          <div class="kbsp-vpm-actions">
            <button type="button" aria-label="合成诊断 Agent" title="合成诊断 Agent" onClick={() => setAgentOpen(true)}><MessageIcon /></button>
            <button type="button" aria-label="关闭视频合成" onClick={() => props.setOpen?.(false)}><XIcon /></button>
          </div>
        </header>

        <Show when={props.result?.()?.warnings?.length}>
          <div class="kbsp-vpm-notice">{props.result().warnings[0]?.message || "合成目标尚未就绪。"}</div>
        </Show>

        <div class="kbsp-trace-strip" aria-label="视频合成追踪标记">
          <For each={traceItems()}>{(item) => <span><b>{item[0]}</b>{item[1]}</span>}</For>
        </div>

        <div class="kbsp-cpm-body">
          <Show when={candidates().length} fallback={<div class="kbsp-vpm-empty">没有可合成的视频目标。</div>}>
            <div class="kbsp-cpm-list">
              <Show when={shotPlan()}>
                <section class="kbsp-cpm-shot-plan">
                  {CandidateButton(shotPlan(), "is-shot-plan")}
                </section>
              </Show>
              <For each={groupedShots()}>{(group) => <section class="kbsp-cpm-shot-group">
                {CandidateButton(group.shot, "is-shot")}
                <div class="kbsp-cpm-scene-list">
                  <For each={group.scenes}>{(scene) => <section class="kbsp-cpm-scene-block">
                    {CandidateButton(scene, "is-scene")}
                    <div class="kbsp-cpm-segment-list">
                      <For each={segmentVideoItems(scene)}>{(segment, index) => SegmentRow(segment, index)}</For>
                    </div>
                  </section>}</For>
                </div>
              </section>}</For>
            </div>

            <aside class="kbsp-cpm-preview" style={{ width: "100%" }}>
              <div class="kbsp-cpm-preview-actions">
                <Show when={selectedVideoUrl()}>
                  <a class="kbsp-cpm-download" href={selectedVideoDownloadUrl()} download={downloadName(selectedVideoPath())} title={selectedDownloadError() || "下载合成视频"} aria-label="下载合成视频">
                    <DownloadIcon />
                    <span>下载</span>
                  </a>
                </Show>
                <button type="button" class="kbsp-vpm-execute kbsp-cpm-run" disabled={busy() || !selected()?.ready} classList={{ "is-running": busy() }} onClick={() => props.executeComposer?.(selected()?.target)}>
                  <Show when={busy()} fallback={<PlayIcon />}>
                    <span class="kbsp-cpm-button-spinner" aria-hidden="true" />
                  </Show>
                  <span>{busy() ? "渲染中" : "合成视频"}</span>
                </button>
              </div>
              <Show when={busy()}>
                <div class="kbsp-cpm-running" role="status" aria-live="polite">
                  <span class="kbsp-cpm-spinner" aria-hidden="true" />
                  <div>
                    <strong>{runningTitle()}</strong>
                    <span>{runningStatus()}</span>
                  </div>
                  <em>{runningElapsed()}</em>
                </div>
              </Show>
              <Show when={templates().length}>
                <section class="kbsp-cpm-templates">
                  <div class="kbsp-cpm-templates-head">
                    <strong>字幕标题模板</strong>
                    <span>选择样式套用到成片</span>
                  </div>
                  <div class="kbsp-cpm-template-grid">
                    <For each={templates()}>{(tpl) => (
                      <button type="button" class="kbsp-cpm-template-card" classList={{ "is-active": text(tpl.id) === templateId() }} onClick={() => { setTemplateId(text(tpl.id)); setTemplateNote(""); }}>
                        <img class="kbsp-cpm-template-preview" src={`/hyperframe_templates/${text(tpl.preview?.gif) || `previews/${text(tpl.id)}.gif`}`} alt={`${tpl.name}预览`} loading="lazy" />
                        <span class="kbsp-cpm-template-name">{tpl.name}</span>
                      </button>
                    )}</For>
                  </div>
                  <Show when={activeTemplate()}>
                    <div class="kbsp-cpm-template-actions">
                      <span class="kbsp-cpm-template-current">已选：{activeTemplate()?.name}</span>
                      <button
                        type="button"
                        class="kbsp-vpm-execute kbsp-cpm-template-apply"
                        classList={{ "is-running": templating() }}
                        disabled={!selectedVideoUrl() || busy() || templating()}
                        onClick={() => void runApplyTemplate()}
                      >
                        <Show when={templating()}><span class="kbsp-cpm-button-spinner" aria-hidden="true" /></Show>
                        <span>{templating() ? "套用渲染中" : "套用到成片"}</span>
                      </button>
                    </div>
                    <Show when={templating()}><p class="kbsp-cpm-status">正在把模板叠加到成片上渲染…（约 20–40 秒，请勿关闭）</p></Show>
                    <Show when={templateNote()}><p class="kbsp-cpm-status">{templateNote()}</p></Show>
                    <Show when={templatedUrl()}>
                      <div class="kbsp-cpm-template-result">
                        <div class="kbsp-cpm-preview-actions">
                          <a class="kbsp-cpm-download" href={downloadUrl(templatedUrl())} download={`templated_${templateId()}.mp4`} title="下载模板成片">
                            <DownloadIcon />
                            <span>下载</span>
                          </a>
                        </div>
                        <video class="kbsp-cpm-video" src={templatedUrl()} controls playsinline preload="none" />
                        <p class="kbsp-cpm-status is-done">模板已套用，已生成新版本。</p>
                      </div>
                    </Show>
                  </Show>
                </section>
              </Show>
              <Show when={selectedVideoUrl()} fallback={<div class="kbsp-cpm-video-empty" classList={{ "is-running": busy() }}>
                <Show when={busy()} fallback={<FilmIcon />}>
                  <i class="kbsp-cpm-empty-spinner" aria-hidden="true" />
                </Show>
                <strong>{busy() ? "正在渲染" : "暂无合成视频"}</strong>
                <span>{busy() ? "HyperFrame 正在合成当前目标。" : selected()?.ready ? "点击合成视频后生成预览。" : "当前目标尚未就绪。"}</span>
                <Show when={!busy() && needsTaskVideoPlan()}>
                  <button type="button" class="kbsp-cpm-plan-action" onClick={() => props.regenerateVideoPlan?.({ target_type: "task", shot_id: "", scene_id: "", action_source: "composer_scope_mismatch_cta" })}>
                    <WorkflowIcon />
                    <span>生成整片计划</span>
                  </button>
                </Show>
              </div>}>
                <video class="kbsp-cpm-video" src={selectedVideoUrl()} controls playsinline preload="none" />
              </Show>
              <Show when={props.state?.().status && !busy()}>
                <p class="kbsp-cpm-status">{props.state().status}</p>
              </Show>
              <Show when={props.result?.()?.status === "completed"}>
                <p class="kbsp-cpm-status is-done">合成已完成。</p>
              </Show>
            </aside>
          </Show>
        </div>
      </section>
      <KouboAgentDrawer
        open={agentOpen}
        setOpen={setAgentOpen}
        task={props.task}
        api={props.api}
        agentKey="composer"
        title="合成诊断 Agent"
        subtitle="范围、候选和合成状态排查"
        greeting="我可以帮你解释为什么当前目标不能合成、为什么只覆盖部分内容，以及下一步应先生成哪个生成计划。"
        placeholder="例如：为什么现在合成不了整片？"
        contextChips={composerAgentChips}
        buildClientContext={composerAgentContext}
        renderCandidate={renderComposerAgentCandidate}
      />
    </div>
  </Show>;
}
