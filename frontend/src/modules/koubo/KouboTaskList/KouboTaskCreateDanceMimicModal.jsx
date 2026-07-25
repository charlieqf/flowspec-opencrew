import { For, Show, createEffect, createMemo, createSignal, onCleanup } from "solid-js";
import { CloseIcon, PlayClipIcon, SaveIcon } from "../AnalysisV1/analysisV1Icons.jsx";

const DEFAULT_DRAFT = {
  title: "",
  reference_video_path: "",
  target_identity_image_path: "",
  target_video_seconds: 8,
  minimum_video_seconds: 4,
  face_detections_manifest: "",
  reference_privacy_mode: "face_mask_only",
  apply_privacy_grid_to_reference_video: true,
  apply_privacy_grid_to_target_identity_image: true,
  block_on_face_not_detected: true,
  auto_run: false,
};

function propValue(value) {
  return typeof value === "function" ? value() : value;
}

function UploadPlusIcon() {
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 5v14"/><path d="M5 12h14"/></svg>;
}

function draftFromTask(task) {
  if (!task) return { ...DEFAULT_DRAFT };
  const config = task.storyboardQuickConfig || {};
  const referencePath = task.referenceVideo || config.reference_video_path || "";
  const targetPath = task.targetIdentityImage || config.target_identity_image_path || "";
  return {
    ...DEFAULT_DRAFT,
    title: task.title || "",
    reference_video_path: referencePath,
    target_identity_image_path: targetPath,
    target_video_seconds: config.target_video_seconds || DEFAULT_DRAFT.target_video_seconds,
    minimum_video_seconds: config.minimum_video_seconds || DEFAULT_DRAFT.minimum_video_seconds,
    face_detections_manifest: config.face_detections_manifest || "",
    reference_privacy_mode: config.reference_privacy_mode || task.referencePrivacyMode || DEFAULT_DRAFT.reference_privacy_mode,
    apply_privacy_grid_to_reference_video: config.apply_privacy_grid_to_reference_video ?? true,
    apply_privacy_grid_to_target_identity_image: config.apply_privacy_grid_to_target_identity_image ?? true,
    block_on_face_not_detected: config.block_on_face_not_detected ?? DEFAULT_DRAFT.block_on_face_not_detected,
    auto_run: false,
  };
}

export default function KouboTaskCreateDanceMimicModal(props) {
  const [draft, setDraft] = createSignal({ ...DEFAULT_DRAFT });
  const [referenceVideos, setReferenceVideos] = createSignal([]);
  const [referencePickerMode, setReferencePickerMode] = createSignal("upload");
  const [referenceUploadFile, setReferenceUploadFile] = createSignal(null);
  const [referenceBusy, setReferenceBusy] = createSignal("");
  const [referenceError, setReferenceError] = createSignal("");
  const [targetImages, setTargetImages] = createSignal([]);
  const [targetPickerMode, setTargetPickerMode] = createSignal("upload");
  const [targetUploadFile, setTargetUploadFile] = createSignal(null);
  const [targetBusy, setTargetBusy] = createSignal("");
  const [targetError, setTargetError] = createSignal("");
  const [modalPosition, setModalPosition] = createSignal(null);
  let modalEl;
  let dragState = null;
  let hydrateKey = "";

  const currentTask = createMemo(() => props.task?.() || null);
  const libraryEnabled = createMemo(() => Boolean(currentTask()?.taskId || currentTask()?.workspaceDir || propValue(props.taskId) || propValue(props.workspaceDir)));

  const selectedReference = createMemo(() => {
    const path = draft().reference_video_path.trim();
    return referenceVideos().find((item) => item.path === path || item.absolute_path === path) || null;
  });

  const selectedTarget = createMemo(() => {
    const path = draft().target_identity_image_path.trim();
    return targetImages().find((item) => item.path === path || item.absolute_path === path) || null;
  });

  function update(key, value) {
    setDraft((prev) => ({ ...prev, [key]: value }));
  }

  async function loadReferenceVideos() {
    if (!libraryEnabled() || !props.onListReferenceVideos) return;
    setReferenceBusy("list");
    setReferenceError("");
    try {
      const payload = await props.onListReferenceVideos();
      setReferenceVideos(Array.isArray(payload?.items) ? payload.items : []);
    } catch (err) {
      setReferenceError(err instanceof Error ? err.message : "加载参考视频素材失败");
    } finally {
      setReferenceBusy("");
    }
  }

  function chooseReferenceVideo(file) {
    if (!file) return;
    setReferenceError("");
    setReferenceUploadFile(file);
    update("reference_video_path", "");
  }

  function dropReferenceVideo(event) {
    event.preventDefault();
    const file = event.dataTransfer?.files?.[0];
    if (file) chooseReferenceVideo(file);
  }

  async function loadTargetImages() {
    if (!libraryEnabled() || !props.onListTargetImages) return;
    setTargetBusy("list");
    setTargetError("");
    try {
      const payload = await props.onListTargetImages();
      setTargetImages(Array.isArray(payload?.items) ? payload.items : []);
    } catch (err) {
      setTargetError(err instanceof Error ? err.message : "加载目标人物素材失败");
    } finally {
      setTargetBusy("");
    }
  }

  function chooseTargetImage(file) {
    if (!file) return;
    setTargetError("");
    setTargetUploadFile(file);
    update("target_identity_image_path", "");
  }

  function dropTargetImage(event) {
    event.preventDefault();
    const file = event.dataTransfer?.files?.[0];
    if (file) chooseTargetImage(file);
  }

  createEffect(() => {
    if (props.open() && libraryEnabled()) {
      void loadReferenceVideos();
      void loadTargetImages();
    }
  });

  createEffect(() => {
    if (!props.open()) {
      hydrateKey = "";
      setModalPosition(null);
      stopDrag();
      return;
    }
    const task = currentTask();
    const nextKey = task ? `task-${task.taskId}-${task.updatedAt || 0}` : "new";
    if (nextKey === hydrateKey) return;
    hydrateKey = nextKey;
    const nextDraft = draftFromTask(task);
    setDraft(nextDraft);
    setReferenceUploadFile(null);
    setTargetUploadFile(null);
    setReferenceError("");
    setTargetError("");
    setReferencePickerMode(nextDraft.reference_video_path ? "path" : "upload");
    setTargetPickerMode(nextDraft.target_identity_image_path ? "path" : "upload");
  });

  function clampModalPosition(left, top) {
    const rect = modalEl?.getBoundingClientRect?.();
    const width = rect?.width || 980;
    const height = rect?.height || 520;
    const margin = 12;
    return {
      left: Math.min(Math.max(margin, left), Math.max(margin, window.innerWidth - width - margin)),
      top: Math.min(Math.max(margin, top), Math.max(margin, window.innerHeight - Math.min(height, window.innerHeight - margin * 2) - margin)),
    };
  }

  function stopDrag() {
    window.removeEventListener("pointermove", dragModal);
    window.removeEventListener("pointerup", stopDrag);
    window.removeEventListener("pointercancel", stopDrag);
    dragState = null;
  }

  function dragModal(event) {
    if (!dragState) return;
    setModalPosition(clampModalPosition(event.clientX - dragState.offsetX, event.clientY - dragState.offsetY));
  }

  function startDrag(event) {
    if (event.button !== 0 || event.target?.closest?.("button")) return;
    const rect = modalEl?.getBoundingClientRect?.();
    if (!rect) return;
    event.preventDefault();
    dragState = {
      offsetX: event.clientX - rect.left,
      offsetY: event.clientY - rect.top,
    };
    setModalPosition({ left: rect.left, top: rect.top });
    window.addEventListener("pointermove", dragModal);
    window.addEventListener("pointerup", stopDrag);
    window.addEventListener("pointercancel", stopDrag);
  }

  function modalStyle() {
    const position = modalPosition();
    return position ? { left: `${position.left}px`, top: `${position.top}px`, transform: "none" } : undefined;
  }

  function close() {
    if (props.busy?.()) return;
    setDraft({ ...DEFAULT_DRAFT });
    setReferencePickerMode("upload");
    setReferenceUploadFile(null);
    setReferenceError("");
    setTargetPickerMode("upload");
    setTargetUploadFile(null);
    setTargetError("");
    setModalPosition(null);
    stopDrag();
    props.onClose?.();
  }

  function hasReferenceInput() {
    return referencePickerMode() === "upload" ? Boolean(referenceUploadFile()) : Boolean(draft().reference_video_path.trim());
  }

  function hasTargetInput() {
    return targetPickerMode() === "upload" ? Boolean(targetUploadFile()) : Boolean(draft().target_identity_image_path.trim());
  }

  function canSave() {
    return !props.busy?.();
  }

  function canRun() {
    return !props.busy?.() && hasReferenceInput() && hasTargetInput();
  }

  function submit(autoRunOverride = null) {
    props.onCreate?.({
      ...draft(),
      reference_video_file: referencePickerMode() === "upload" ? referenceUploadFile() : null,
      target_identity_image_file: targetPickerMode() === "upload" ? targetUploadFile() : null,
      auto_run: autoRunOverride === null ? Boolean(draft().auto_run) : Boolean(autoRunOverride),
      target_video_seconds: Number(draft().target_video_seconds || 8),
      minimum_video_seconds: Number(draft().minimum_video_seconds || 4),
    }, { taskId: currentTask()?.taskId || null });
  }

  onCleanup(stopDrag);

  return (
    <Show when={props.open()}>
      <div class="koubo-task-list-modal-backdrop" onClick={close} />
      <section ref={(el) => { modalEl = el; }} class="koubo-task-list-modal" style={modalStyle()}>
        <header class="koubo-task-list-draggable-header" onPointerDown={startDrag}>
          <div>
            <h3>{currentTask() ? "动作模拟编辑" : "动作模拟创建"}</h3>
          </div>
          <div class="koubo-task-list-modal-head-actions">
            <button type="button" class="koubo-task-list-icon-action" disabled={!canSave()} title="保存" aria-label="保存" onClick={() => submit()}>
              <SaveIcon />
            </button>
            <button type="button" class="koubo-task-list-icon-action" disabled={!canRun()} title="创建并运行" aria-label="创建并运行" onClick={() => submit(true)}>
              <PlayClipIcon />
            </button>
            <button type="button" class="koubo-task-list-close-action" title="关闭" aria-label="关闭" onClick={close}>
              <CloseIcon />
            </button>
          </div>
        </header>
        <div class="koubo-task-list-modal-body">
          <div class="koubo-task-list-dance-title-row">
            <label class="koubo-task-list-dance-title-field">
              任务名称
              <input value={draft().title} onInput={(event) => update("title", event.currentTarget.value)} placeholder="动作模拟任务" />
            </label>
            <div class="koubo-task-list-toggle-row koubo-task-list-dance-title-toggles">
              <label class="koubo-task-list-checkbox-tight">
                <input type="checkbox" checked={draft().block_on_face_not_detected} onChange={(event) => update("block_on_face_not_detected", event.currentTarget.checked)} />
                <span>无人脸时阻断</span>
              </label>
              <label class="koubo-task-list-checkbox-tight">
                <input type="checkbox" checked={draft().auto_run} onChange={(event) => update("auto_run", event.currentTarget.checked)} />
                <span>创建后运行</span>
              </label>
            </div>
          </div>
          <div class="koubo-task-list-form-grid koubo-task-list-dance-grid">
            <label class="koubo-task-list-dance-param-field">
              目标分段秒数
              <input type="number" min="1" step="0.5" value={draft().target_video_seconds} onInput={(event) => update("target_video_seconds", event.currentTarget.value)} />
            </label>
            <label class="koubo-task-list-dance-param-field">
              最小分段秒数
              <input type="number" min="1" step="0.5" value={draft().minimum_video_seconds} onInput={(event) => update("minimum_video_seconds", event.currentTarget.value)} />
            </label>
            <label class="koubo-task-list-dance-param-field">
              参考隐私模式
              <select value={draft().reference_privacy_mode} onChange={(event) => update("reference_privacy_mode", event.currentTarget.value)}>
                <option value="face_mask_only">仅遮脸</option>
                <option value="provider_safe_outline">强隐私轮廓</option>
                <option value="provider_safe_pose">强隐私骨架</option>
                <option value="red_grid_guide">隐私网格（身份可见）</option>
              </select>
            </label>
            <label class="koubo-task-list-dance-param-field">
              参考人脸位置
              <input value={draft().face_detections_manifest} onInput={(event) => update("face_detections_manifest", event.currentTarget.value)} placeholder="高级控制文件，可留空" />
            </label>
          </div>
          <Show when={draft().reference_privacy_mode === "red_grid_guide" && !draft().apply_privacy_grid_to_reference_video && !draft().apply_privacy_grid_to_target_identity_image}>
            <div class="koubo-task-list-target-error">参考视频和目标人物图均不会添加隐私网格，身份内容将按原始视觉输入发送给模型。</div>
          </Show>
          <div class="koubo-task-list-dance-media-grid">
            <section class="koubo-task-list-target-picker koubo-task-list-reference-picker">
              <div class="koubo-task-list-target-picker-head">
                <span>参考舞蹈视频</span>
                <Show when={draft().reference_privacy_mode === "red_grid_guide"}>
                  <label class="koubo-task-list-checkbox-tight">
                    <input type="checkbox" checked={draft().apply_privacy_grid_to_reference_video} onChange={(event) => update("apply_privacy_grid_to_reference_video", event.currentTarget.checked)} />
                    <span>应用隐私网格</span>
                  </label>
                </Show>
                <div class="koubo-task-list-target-picker-tabs">
                  <button type="button" class={referencePickerMode() === "upload" ? "is-active" : ""} onClick={() => setReferencePickerMode("upload")}>上传</button>
                  <button type="button" class={referencePickerMode() === "path" ? "is-active" : ""} onClick={() => { setReferenceUploadFile(null); setReferencePickerMode("path"); }}>路径</button>
                  <button type="button" class={referencePickerMode() === "library" ? "is-active" : ""} disabled={!libraryEnabled()} title={libraryEnabled() ? "素材库" : "创建后可用"} onClick={() => { if (libraryEnabled()) { setReferenceUploadFile(null); setReferencePickerMode("library"); } }}>素材库</button>
                </div>
              </div>
              <Show when={referencePickerMode() === "library"}>
                <div class="koubo-task-list-target-library-actions">
                  <button type="button" class="secondary" disabled={referenceBusy() === "list"} onClick={() => void loadReferenceVideos()}>{referenceBusy() === "list" ? "刷新中..." : "刷新素材库"}</button>
                </div>
                <div class="koubo-task-list-target-grid koubo-task-list-reference-grid">
                  <For each={referenceVideos()}>
                    {(item) => (
                      <button
                        type="button"
                        class={`koubo-task-list-target-card koubo-task-list-reference-card ${draft().reference_video_path === (item.path || item.absolute_path) ? "is-selected" : ""}`}
                        title={item.path || item.absolute_path || item.filename}
                        onClick={() => { setReferenceUploadFile(null); update("reference_video_path", item.path || item.absolute_path || ""); }}
                      >
                        <Show when={item.preview_url}>
                          <video src={item.preview_url} muted preload="metadata" />
                        </Show>
                        <span>{item.filename || item.label || "参考视频"}</span>
                        <small>{item.task_id ? `Task #${item.task_id}` : item.source || "library"}</small>
                      </button>
                    )}
                  </For>
                  <Show when={!referenceVideos().length && referenceBusy() !== "list"}>
                    <div class="koubo-task-list-target-empty">暂无可选参考视频</div>
                  </Show>
                </div>
              </Show>
              <Show when={referencePickerMode() === "upload"}>
                <label class="koubo-task-list-upload-box" onDragOver={(event) => event.preventDefault()} onDrop={dropReferenceVideo}>
                  <span class="koubo-task-list-upload-icon"><UploadPlusIcon /></span>
                  <span class="koubo-task-list-upload-copy">
                    <Show when={referenceUploadFile()} fallback={<>拖拽视频到这里，或<strong>点击上传</strong></>}>
                      {(file) => <>已选择 {file().name}，可<strong>点击上传</strong>替换</>}
                    </Show>
                  </span>
                  <input type="file" accept="video/mp4,video/quicktime,video/webm" onChange={(event) => { const file = event.currentTarget.files?.[0]; if (file) chooseReferenceVideo(file); event.currentTarget.value = ""; }} />
                </label>
              </Show>
              <Show when={referencePickerMode() === "path"}>
                <label>
                  参考视频路径
                  <input value={draft().reference_video_path} onInput={(event) => { setReferenceUploadFile(null); update("reference_video_path", event.currentTarget.value); }} placeholder="/Users/xxx/reference.mp4" />
                </label>
              </Show>
              <Show when={selectedReference()}>
                <div class="koubo-task-list-target-selected koubo-task-list-reference-selected">
                  <video src={selectedReference().preview_url} muted preload="metadata" />
                  <span>{selectedReference().filename || selectedReference().path}</span>
                </div>
              </Show>
              <Show when={referenceError()}>
                <div class="koubo-task-list-target-error">{referenceError()}</div>
              </Show>
            </section>
            <section class="koubo-task-list-target-picker koubo-task-list-identity-picker">
              <div class="koubo-task-list-target-picker-head">
                <span>目标人物图片</span>
                <Show when={draft().reference_privacy_mode === "red_grid_guide"}>
                  <label class="koubo-task-list-checkbox-tight">
                    <input type="checkbox" checked={draft().apply_privacy_grid_to_target_identity_image} onChange={(event) => update("apply_privacy_grid_to_target_identity_image", event.currentTarget.checked)} />
                    <span>应用隐私网格</span>
                  </label>
                </Show>
                <div class="koubo-task-list-target-picker-tabs">
                  <button type="button" class={targetPickerMode() === "upload" ? "is-active" : ""} onClick={() => setTargetPickerMode("upload")}>上传</button>
                  <button type="button" class={targetPickerMode() === "path" ? "is-active" : ""} onClick={() => { setTargetUploadFile(null); setTargetPickerMode("path"); }}>路径</button>
                  <button type="button" class={targetPickerMode() === "library" ? "is-active" : ""} disabled={!libraryEnabled()} title={libraryEnabled() ? "素材库" : "创建后可用"} onClick={() => { if (libraryEnabled()) { setTargetUploadFile(null); setTargetPickerMode("library"); } }}>素材库</button>
                </div>
              </div>
              <Show when={targetPickerMode() === "library"}>
                <div class="koubo-task-list-target-library-actions">
                  <button type="button" class="secondary" disabled={targetBusy() === "list"} onClick={() => void loadTargetImages()}>{targetBusy() === "list" ? "刷新中..." : "刷新素材库"}</button>
                </div>
                <div class="koubo-task-list-target-grid">
                  <For each={targetImages()}>
                    {(item) => (
                      <button
                        type="button"
                        class={`koubo-task-list-target-card ${draft().target_identity_image_path === (item.path || item.absolute_path) ? "is-selected" : ""}`}
                        title={item.path || item.absolute_path || item.filename}
                        onClick={() => { setTargetUploadFile(null); update("target_identity_image_path", item.path || item.absolute_path || ""); }}
                      >
                        <Show when={item.preview_url}>
                          <img src={item.preview_url} alt={item.label || item.filename || "target"} />
                        </Show>
                        <span>{item.is_ai_generated ? "AI 生成人物" : "图片素材"}</span>
                        <small>{item.task_id ? `Task #${item.task_id}` : item.source || "library"}</small>
                      </button>
                    )}
                  </For>
                  <Show when={!targetImages().length && targetBusy() !== "list"}>
                    <div class="koubo-task-list-target-empty">暂无可选人物图</div>
                  </Show>
                </div>
              </Show>
              <Show when={targetPickerMode() === "upload"}>
                <label class="koubo-task-list-upload-box" onDragOver={(event) => event.preventDefault()} onDrop={dropTargetImage}>
                  <span class="koubo-task-list-upload-icon"><UploadPlusIcon /></span>
                  <span class="koubo-task-list-upload-copy">
                    <Show when={targetUploadFile()} fallback={<>拖拽图片到这里，或<strong>点击上传</strong></>}>
                      {(file) => <>已选择 {file().name}，可<strong>点击上传</strong>替换</>}
                    </Show>
                  </span>
                  <input type="file" accept="image/png,image/jpeg,image/webp" onChange={(event) => { const file = event.currentTarget.files?.[0]; if (file) chooseTargetImage(file); event.currentTarget.value = ""; }} />
                </label>
              </Show>
              <Show when={targetPickerMode() === "path"}>
                <label>
                  目标人物图片路径
                  <input value={draft().target_identity_image_path} onInput={(event) => { setTargetUploadFile(null); update("target_identity_image_path", event.currentTarget.value); }} placeholder="/Users/xxx/target-person.png" />
                </label>
              </Show>
              <Show when={selectedTarget()}>
                <div class="koubo-task-list-target-selected">
                  <img src={selectedTarget().preview_url} alt="selected target" />
                  <span>{selectedTarget().filename || selectedTarget().path}</span>
                </div>
              </Show>
              <Show when={targetError()}>
                <div class="koubo-task-list-target-error">{targetError()}</div>
              </Show>
            </section>
          </div>
        </div>
      </section>
    </Show>
  );
}
