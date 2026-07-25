import { For, Show, createEffect, createMemo, createSignal, onCleanup } from "solid-js";
import { ImageIcon, PlusIcon, RefreshIcon, XIcon } from "../kouboStoryboardIcons.jsx";

const DEFAULT_SIZE = "1536x1024";
const SIZE_OPTIONS = ["1536x1024", "1024x1024", "1024x1536", "1792x1024", "1024x1792"];
const MAX_REFERENCE_IMAGES = 8;

function text(value, fallback = "") {
  return String(value ?? fallback).trim();
}

function filenameFromPath(path) {
  return String(path || "").split(/[\\/]/).filter(Boolean).pop() || String(path || "");
}

function generationBadges(item) {
  const promotions = Array.isArray(item?.promotions) ? item.promotions : [];
  const badges = [];
  if (!promotions.length) badges.push("未入库");
  if (promotions.some((entry) => entry?.target === "asset_library")) badges.push("已加入素材库");
  if (promotions.some((entry) => entry?.target === "dialogue_image")) badges.push("已绑定对白");
  if (promotions.some((entry) => entry?.target === "consistency")) badges.push("已设为一致性参考");
  return badges;
}

export default function CleanImagePanel(props) {
  let referenceFileInput;
  let lastTaskId = "";
  const [prompt, setPrompt] = createSignal("");
  const [negativePrompt, setNegativePrompt] = createSignal("");
  const [size, setSize] = createSignal(DEFAULT_SIZE);
  const [referenceItems, setReferenceItems] = createSignal([]);
  const [referencePickerOpen, setReferencePickerOpen] = createSignal(false);
  const [draggingReferences, setDraggingReferences] = createSignal(false);
  const [items, setItems] = createSignal([]);
  const [activeId, setActiveId] = createSignal("");
  const [phase, setPhase] = createSignal("idle");
  const [status, setStatus] = createSignal("");
  const [error, setError] = createSignal("");
  const [dialogueId, setDialogueId] = createSignal("");
  const [consistencyKind, setConsistencyKind] = createSignal("host");

  const activeItem = createMemo(() => items().find((item) => item.generation_id === activeId()) || items()[0] || null);
  const dialogueOptions = createMemo(() => props.dialogueOptions?.() || []);
  const taskId = createMemo(() => props.task?.()?.id || "");
  const referencePathSet = createMemo(() => new Set(referenceItems().map((item) => item.path).filter(Boolean)));
  const referenceOptions = createMemo(() => {
    const byPath = new Map();
    for (const item of props.referenceImageOptions?.() || []) {
      const path = text(item?.path);
      if (!path || byPath.has(path)) continue;
      byPath.set(path, {
        ...item,
        path,
        label: text(item?.label) || text(item?.filename) || filenameFromPath(path),
        filename: text(item?.filename) || filenameFromPath(path),
      });
    }
    return Array.from(byPath.values());
  });
  const imageUrl = (item = activeItem()) => {
    const taskId = props.task?.()?.id;
    const generationId = item?.generation_id;
    const base = taskId && generationId && props.api.cleanImageUrl ? props.api.cleanImageUrl(taskId, generationId) : item?.image_url || "";
    return base ? `${base}?v=${item?.promotions?.length || 0}` : "";
  };
  const isBusy = () => phase() !== "idle";
  const target = () => props.target?.() || null;

  function planPayload() {
    return props.plan?.() || null;
  }

  async function loadGenerations() {
    const currentTaskId = taskId();
    if (!currentTaskId) return;
    const res = await props.api.cleanImageGenerations(currentTaskId);
    setItems(res.items || []);
    if (!activeId() && res.items?.[0]) setActiveId(res.items[0].generation_id);
  }

  function referencePaths() {
    return referenceItems().map((item) => text(item.path)).filter(Boolean);
  }

  function referenceUrl(item) {
    if (item?.image_url) return item.image_url;
    if (item?.url) return item.url;
    const taskId = props.task?.()?.id;
    if (taskId && item?.path?.startsWith("SessionScratch/CleanImageGenerations/References/") && props.api.cleanImageReferenceUrl) {
      return props.api.cleanImageReferenceUrl(taskId, filenameFromPath(item.path));
    }
    return item?.path && props.assetUrl ? props.assetUrl(item.path) : "";
  }

  function normalizedReferenceItem(item) {
    const path = text(item?.path);
    return {
      ...item,
      path,
      label: text(item?.label) || text(item?.original_filename) || text(item?.filename) || filenameFromPath(path),
      filename: text(item?.filename) || filenameFromPath(path),
      image_url: item?.image_url || item?.url || "",
    };
  }

  function addReferenceItems(nextItems) {
    const incoming = (nextItems || []).map(normalizedReferenceItem).filter((item) => item.path);
    if (!incoming.length) return;
    let overflow = false;
    setReferenceItems((previous) => {
      const byPath = new Map(previous.map((item) => [item.path, item]));
      for (const item of incoming) {
        if (byPath.has(item.path)) continue;
        if (byPath.size >= MAX_REFERENCE_IMAGES) {
          overflow = true;
          continue;
        }
        byPath.set(item.path, item);
      }
      return Array.from(byPath.values());
    });
    if (overflow) setError(`参考图最多 ${MAX_REFERENCE_IMAGES} 张`);
  }

  function removeReference(path) {
    setReferenceItems((previous) => previous.filter((item) => item.path !== path));
  }

  function toggleReferenceOption(item) {
    const path = text(item?.path);
    if (!path) return;
    if (referencePathSet().has(path)) removeReference(path);
    else addReferenceItems([item]);
  }

  async function uploadReferences(files) {
    const currentTaskId = taskId();
    const picked = Array.from(files || []).filter((file) => file?.type?.startsWith("image/") || /\.(png|jpe?g|webp|gif)$/i.test(file?.name || ""));
    if (!currentTaskId || !picked.length || isBusy()) return;
    if (referenceItems().length + picked.length > MAX_REFERENCE_IMAGES) {
      setError(`参考图最多 ${MAX_REFERENCE_IMAGES} 张`);
      return;
    }
    setPhase("uploading_refs");
    setStatus("正在上传参考图");
    setError("");
    try {
      const res = await props.runAction("cleanImageUploadReferences", () => props.api.uploadCleanImageReferences(currentTaskId, picked));
      addReferenceItems(res.items || []);
      setStatus(`已添加 ${res.items?.length || 0} 张参考图`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setPhase("idle");
    }
  }

  function handleReferenceDrop(event) {
    event.preventDefault();
    setDraggingReferences(false);
    void uploadReferences(event.dataTransfer?.files);
  }

  async function initialize() {
    setError("");
    setStatus("");
    await loadGenerations();
  }

  async function generate() {
    const currentTaskId = taskId();
    if (!currentTaskId || isBusy()) return;
    setPhase("generating");
    setStatus("生成中");
    setError("");
    try {
      const res = await props.runAction("cleanImageGenerate", () => props.api.generateCleanImage(currentTaskId, {
        prompt: prompt(),
        negative_prompt: negativePrompt(),
        size: size(),
        reference_paths: referencePaths(),
      }));
      const item = res.generation;
      setItems((previous) => [item, ...previous.filter((entry) => entry.generation_id !== item.generation_id)]);
      setActiveId(item.generation_id);
      setStatus("已生成，未入库");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setPhase("idle");
    }
  }

  function replaceGeneration(next) {
    if (!next?.generation_id) return;
    setItems((previous) => previous.map((item) => item.generation_id === next.generation_id ? next : item));
    setActiveId(next.generation_id);
  }

  async function promoteAssetLibrary() {
    const currentTaskId = taskId();
    const item = activeItem();
    if (!currentTaskId || !item || isBusy()) return;
    setPhase("promoting");
    setStatus("正在加入素材库");
    setError("");
    try {
      const res = await props.runAction("cleanImagePromoteAsset", () => props.api.promoteCleanImageToAssetLibrary(currentTaskId, item.generation_id, { plan: planPayload() }));
      replaceGeneration(res.generation);
      props.applyAssetPayload?.(res);
      props.openUploadAssets?.();
      setStatus("已加入右侧素材库 / 上传素材");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setPhase("idle");
    }
  }

  async function promoteDialogue() {
    const currentTaskId = taskId();
    const item = activeItem();
    const targetDialogueId = dialogueId();
    if (!currentTaskId || !item || !targetDialogueId || isBusy()) return;
    setPhase("promoting");
    setStatus("正在绑定对白");
    setError("");
    try {
      const res = await props.runAction("cleanImagePromoteDialogue", () => props.api.promoteCleanImageToDialogue(currentTaskId, item.generation_id, { dialogue_id: targetDialogueId, plan: planPayload() }));
      replaceGeneration(res.generation);
      props.applyTaskPayload?.(res);
      props.scrollToDialogue?.(targetDialogueId);
      setStatus("已绑定到当前对白 / 新图");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setPhase("idle");
    }
  }

  async function promoteConsistency() {
    const currentTaskId = taskId();
    const item = activeItem();
    if (!currentTaskId || !item || isBusy()) return;
    if (!window.confirm(consistencyKind() === "host" ? "覆盖当前人物一致性参考图？" : "覆盖当前产品一致性参考图？")) return;
    setPhase("promoting");
    setStatus("正在设置一致性参考");
    setError("");
    try {
      const res = await props.runAction("cleanImagePromoteConsistency", () => props.api.promoteCleanImageToConsistency(currentTaskId, item.generation_id, { kind: consistencyKind() }));
      replaceGeneration(res.generation);
      props.openHostProductBuilder?.();
      setStatus(consistencyKind() === "host" ? "已设为人物一致性参考" : "已设为产品一致性参考");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setPhase("idle");
    }
  }

  createEffect(() => {
    if (!props.open?.()) return;
    const nextTarget = target();
    setDialogueId(nextTarget?.dialogue_id || props.selectedDialogueId?.() || "");
    void initialize();
  });

  createEffect(() => {
    const currentTaskId = String(taskId() || "");
    if (currentTaskId === lastTaskId) return;
    lastTaskId = currentTaskId;
    setReferenceItems([]);
    setReferencePickerOpen(false);
    setDraggingReferences(false);
  });

  createEffect(() => {
    const nextTarget = target();
    if (props.open?.() && nextTarget?.target_type === "dialogue_image") setDialogueId(nextTarget.dialogue_id || "");
  });

  createEffect(() => {
    if (!props.open?.()) return;
    const onKey = (event) => {
      if (event.key === "Escape") props.setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    onCleanup(() => window.removeEventListener("keydown", onKey));
  });

  return <Show when={props.open?.()}>
    <div class="kbsp-clean-backdrop" onClick={() => props.setOpen(false)} />
    <section class="kbsp-clean-dialog" role="dialog" aria-modal="true" aria-label="干净单次生图">
      <header class="kbsp-clean-head">
        <div>
          <h3>干净单次生图</h3>
          <span>Clean Image</span>
        </div>
        <div class="kbsp-clean-head-actions">
          <button class="kbsp-clean-icon" type="button" title="刷新历史" aria-label="刷新历史" onClick={() => void loadGenerations()}><RefreshIcon /></button>
          <button class="kbsp-clean-icon close" type="button" title="关闭" aria-label="关闭" onClick={() => props.setOpen(false)}><XIcon /></button>
        </div>
      </header>
      <div class="kbsp-clean-body">
        <section class="kbsp-clean-compose">
          <label class="kbsp-clean-field">
            <span>Prompt</span>
            <textarea value={prompt()} onInput={(event) => setPrompt(event.currentTarget.value)} />
          </label>
          <label class="kbsp-clean-field">
            <span>Negative</span>
            <textarea class="is-compact" value={negativePrompt()} onInput={(event) => setNegativePrompt(event.currentTarget.value)} />
          </label>
          <div class="kbsp-clean-grid">
            <label><span>Size</span><select value={size()} onChange={(event) => setSize(event.currentTarget.value)}><For each={SIZE_OPTIONS}>{(item) => <option value={item}>{item}</option>}</For></select></label>
          </div>
          <section class={`kbsp-clean-ref-panel ${draggingReferences() ? "is-dragging" : ""}`} onDragOver={(event) => { event.preventDefault(); setDraggingReferences(true); }} onDragLeave={(event) => { if (!event.currentTarget.contains(event.relatedTarget)) setDraggingReferences(false); }} onDrop={handleReferenceDrop}>
            <div class="kbsp-clean-ref-head">
              <span>参考图</span>
              <div>
                <button class="kbsp-clean-ref-upload" type="button" disabled={isBusy()} onClick={() => referenceFileInput?.click()}><PlusIcon />上传</button>
                <button class="kbsp-clean-ref-choose" type="button" disabled={isBusy()} onClick={() => setReferencePickerOpen((value) => !value)}>选择</button>
              </div>
              <input ref={referenceFileInput} class="kbsp-clean-ref-upload-input" type="file" accept="image/*" multiple hidden onChange={(event) => { const input = event.currentTarget; void uploadReferences(input.files).finally(() => { input.value = ""; }); }} />
            </div>
            <Show when={referenceItems().length} fallback={<div class="kbsp-clean-ref-empty">未添加参考图</div>}>
              <div class="kbsp-clean-ref-list">
                <For each={referenceItems()}>{(item) => <article class="kbsp-clean-ref-item" title={item.path}>
                  <img src={referenceUrl(item)} loading="lazy" />
                  <span>{item.label}</span>
                  <button type="button" title="移除参考图" aria-label="移除参考图" disabled={isBusy()} onClick={() => removeReference(item.path)}><XIcon /></button>
                </article>}</For>
              </div>
            </Show>
            <Show when={referencePickerOpen()}>
              <div class="kbsp-clean-ref-picker">
                <Show when={referenceOptions().length} fallback={<div class="kbsp-clean-ref-empty">暂无可选图片</div>}>
                  <For each={referenceOptions()}>{(item) => <button class={`kbsp-clean-ref-option ${referencePathSet().has(item.path) ? "is-selected" : ""}`} type="button" title={item.path} onClick={() => toggleReferenceOption(item)}>
                    <img src={referenceUrl(item)} loading="lazy" />
                    <span>{item.label}</span>
                  </button>}</For>
                </Show>
              </div>
            </Show>
          </section>
          <button class="kbsp-clean-primary" type="button" disabled={isBusy() || !prompt().trim()} onClick={() => void generate()}><ImageIcon />{phase() === "generating" ? "生成中" : "生成"}</button>
          <Show when={status()}><p class="kbsp-clean-status">{status()}</p></Show>
          <Show when={error()}><p class="kbsp-clean-error">{error()}</p></Show>
        </section>
        <section class="kbsp-clean-preview">
          <div class="kbsp-clean-preview-main">
            <Show when={activeItem()} fallback={<div class="kbsp-clean-empty"><ImageIcon /></div>}>
              <div class="kbsp-clean-image-wrap">
                <img src={imageUrl()} />
                <div class="kbsp-clean-badges"><For each={generationBadges(activeItem())}>{(badge) => <span>{badge}</span>}</For></div>
              </div>
              <div class="kbsp-clean-promote">
                <button type="button" disabled={isBusy()} onClick={() => void promoteAssetLibrary()}>加入素材库</button>
                <div>
                  <select value={dialogueId()} onChange={(event) => setDialogueId(event.currentTarget.value)}>
                    <option value="">选择对白</option>
                    <For each={dialogueOptions()}>{(item) => <option value={item.dialogue_id}>{item.label}</option>}</For>
                  </select>
                  <button type="button" disabled={isBusy() || !dialogueId()} onClick={() => void promoteDialogue()}>绑定新图</button>
                </div>
                <div>
                  <select value={consistencyKind()} onChange={(event) => setConsistencyKind(event.currentTarget.value)}>
                    <option value="host">人物参考</option>
                    <option value="product">产品参考</option>
                  </select>
                  <button type="button" disabled={isBusy()} onClick={() => void promoteConsistency()}>设为一致性参考</button>
                </div>
              </div>
            </Show>
          </div>
          <div class="kbsp-clean-history">
            <For each={items()}>{(item) => <button class={activeId() === item.generation_id ? "is-active" : ""} type="button" onClick={() => setActiveId(item.generation_id)}>
              <img src={imageUrl(item)} loading="lazy" />
              <span>{generationBadges(item)[0]}</span>
            </button>}</For>
          </div>
        </section>
      </div>
    </section>
  </Show>;
}
