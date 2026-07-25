import { For, Show, createEffect, createMemo, createSignal } from "solid-js";

const DEFAULT_HOST_PRODUCT_DRAFTS = {
  host: {
    simplePrompt: "生成一个新的任务级女主播一致性参考板。参考图只作为口播/带货角色、家庭直播环境、坐姿构图和手部讲解姿势参考，不复制原人物身份。新主播为年轻东亚女性，粉蓝色纯色宽松圆领 T 恤，黑色圆形领夹麦，低马尾或紧凑低发髻，自然淡妆，和原主播脸型、眼睛、发型、服装明显不同。需要三视图、表情变化、细节拆解和一个竖屏口播姿势小窗。",
    finalPrompt: "",
    references: [],
    phase: "idle",
    error: "",
  },
  product: {
    simplePrompt: "生成一页化橘红和润膏的纯产品多角度一致性参考图。参考图用于锁定产品身份：和润膏、MICRO FERRAL 中朝、亮绿色标签、银灰包装、植物图形、15 标识、UP 字样、绿色条包。不要人物、手、厨房、直播画面或旧黄色 Zirkulin 产品。需要正面、左右 3/4、侧面、俯视、组件排列和关键细节特写。",
    finalPrompt: "",
    references: [],
    phase: "idle",
    error: "",
  },
};

export default function OCRebuildHostProductBuilder(props) {
  const {
    ArrowsClockwiseIcon,
    CloseIcon,
    CodeIcon,
    DocumentIcon,
    PlayIcon,
    PlusIcon,
    TrashIcon,
  } = props.icons;

  const [hostProductBuilderTab, setHostProductBuilderTab] = createSignal("host");
  const [hostProductBuilderState, setHostProductBuilderState] = createSignal(null);
  const [hostProductImageRefreshToken, setHostProductImageRefreshToken] = createSignal(0);
  const [hostProductPromptModelDialogOpen, setHostProductPromptModelDialogOpen] = createSignal(false);
  const [hostProductPromptPreviewOpen, setHostProductPromptPreviewOpen] = createSignal(false);
  const [hostProductPromptModelFilter, setHostProductPromptModelFilter] = createSignal("");
  const [hostProductBuilderDrafts, setHostProductBuilderDrafts] = createSignal(DEFAULT_HOST_PRODUCT_DRAFTS);

  const filteredPromptModels = createMemo(() => {
    const providerID = props.draft()?.prompt_model_provider;
    const keyword = hostProductPromptModelFilter().trim().toLowerCase();
    return props.runModels().filter((item) => {
      if (providerID && item.providerID !== providerID) return false;
      if (!keyword) return true;
      return `${item.providerName} ${item.modelName} ${item.modelID}`.toLowerCase().includes(keyword);
    });
  });

  async function loadHostProductBuilder() {
    const taskId = props.task()?.id;
    if (!taskId) return null;
    const state = await props.api.hostProductBuilder(taskId);
    setHostProductBuilderState(state);
    setHostProductImageRefreshToken(Date.now());
    setHostProductBuilderDrafts((prev) => ({
      host: { ...prev.host, simplePrompt: state.host?.simple_prompt || prev.host.simplePrompt, finalPrompt: hostProductStoredFinalPrompt("host", state.host?.final_prompt || prev.host.finalPrompt), references: state.host?.reference_images || prev.host.references, phase: "idle", error: "" },
      product: { ...prev.product, simplePrompt: state.product?.simple_prompt || prev.product.simplePrompt, finalPrompt: hostProductStoredFinalPrompt("product", state.product?.final_prompt || prev.product.finalPrompt), references: state.product?.reference_images || prev.product.references, phase: "idle", error: "" },
    }));
    return state;
  }

  function hostProductDraft(kind = hostProductBuilderTab()) { return hostProductBuilderDrafts()[kind] || hostProductBuilderDrafts().host; }
  function hostProductSection(kind = hostProductBuilderTab()) { return hostProductBuilderState()?.[kind] || {}; }
  function hostProductPromptStorageKey(kind) {
    return `opencrew:host-product-builder:${props.task()?.session_id || "session"}:${props.task()?.id || props.selectedTaskId() || "task"}:${kind}:finalPrompt`;
  }
  function isProviderErrorPagePrompt(value) {
    const lower = String(value || "").trim().slice(0, 8000).toLowerCase();
    return (lower.includes("<!doctype html") || lower.includes("<html"))
      && (lower.includes("cloudflare") || lower.includes("5xx-error-landing") || lower.includes("a timeout occurred") || lower.includes("error code 524"));
  }
  function hostProductStoredFinalPrompt(kind, fallback = "") {
    try {
      const stored = window.localStorage.getItem(hostProductPromptStorageKey(kind));
      const value = stored === null ? fallback : stored;
      if (isProviderErrorPagePrompt(value)) {
        window.localStorage.removeItem(hostProductPromptStorageKey(kind));
        return isProviderErrorPagePrompt(fallback) ? "" : fallback;
      }
      return value;
    } catch {
      return isProviderErrorPagePrompt(fallback) ? "" : fallback;
    }
  }
  function saveHostProductFinalPrompt(kind, value) {
    const next = value || "";
    try {
      window.localStorage.setItem(hostProductPromptStorageKey(kind), next);
    } catch {
      // LocalStorage may be unavailable in restricted browser contexts.
    }
    updateHostProductDraft(kind, { finalPrompt: next });
  }
  function updateHostProductDraft(kind, patch) {
    setHostProductBuilderDrafts((prev) => ({ ...prev, [kind]: { ...(prev[kind] || {}), ...patch } }));
  }
  async function uploadHostProductRefs(kind, files) {
    if (!files?.length) return;
    updateHostProductDraft(kind, { phase: "uploading", error: "" });
    try {
      const res = await props.api.uploadHostProductRefs(props.selectedTaskId(), kind, files);
      setHostProductBuilderState(res.state);
      setHostProductImageRefreshToken(Date.now());
      updateHostProductDraft(kind, { references: res.section?.reference_images || [], phase: "idle", error: "" });
    } catch (err) {
      updateHostProductDraft(kind, { phase: "idle", error: err instanceof Error ? err.message : String(err) });
      throw err;
    }
  }
  async function generateHostProductPrompt(kind) {
    await props.saveConfig();
    const item = hostProductDraft(kind);
    updateHostProductDraft(kind, { phase: "prompting", error: "" });
    try {
      const res = await props.api.generateHostProductPrompt(props.selectedTaskId(), { kind, simple_prompt: item.simplePrompt || "", reference_images: item.references || [], provider: props.draft()?.prompt_model_provider || "", model: props.draft()?.prompt_model_id || "" });
      saveHostProductFinalPrompt(kind, res.prompt || "");
      updateHostProductDraft(kind, { references: res.section?.reference_images || item.references || [], phase: "prompt_ready", error: "" });
      setHostProductPromptModelDialogOpen(false);
      setHostProductPromptPreviewOpen(true);
      await loadHostProductBuilder();
    } catch (err) {
      updateHostProductDraft(kind, { phase: "idle", error: err instanceof Error ? err.message : String(err) });
      throw err;
    }
  }
  async function deleteHostProductRef(kind, path) {
    if (!path) return;
    const res = await props.api.deleteHostProductRef(props.selectedTaskId(), { kind, path });
    setHostProductBuilderState(res.state);
    setHostProductImageRefreshToken(Date.now());
    updateHostProductDraft(kind, { references: res.section?.reference_images || [], error: "" });
  }
  async function uploadHostProductOutput(kind, files) {
    const file = Array.from(files || [])[0];
    if (!file) return;
    updateHostProductDraft(kind, { phase: "uploading-output", error: "" });
    try {
      const res = await props.api.uploadHostProductOutput(props.selectedTaskId(), kind, file);
      setHostProductBuilderState(res.state);
      setHostProductImageRefreshToken(Date.now());
      updateHostProductDraft(kind, { phase: "generated", error: "" });
    } catch (err) {
      updateHostProductDraft(kind, { phase: "idle", error: err instanceof Error ? err.message : String(err) });
      throw err;
    }
  }
  async function deleteHostProductOutput(kind) {
    const section = hostProductSection(kind);
    if (!section.output || !window.confirm(`删除当前${kind === "host" ? "人物" : "产品"}参考图？`)) return;
    const res = await props.api.deleteHostProductOutput(props.selectedTaskId(), { kind, path: section.output });
    setHostProductBuilderState(res.state);
    setHostProductImageRefreshToken(Date.now());
    updateHostProductDraft(kind, { phase: "idle", error: "" });
  }
  async function generateHostProductImage(kind) {
    const item = hostProductDraft(kind);
    const finalPrompt = hostProductStoredFinalPrompt(kind, item.finalPrompt || "");
    updateHostProductDraft(kind, { phase: "generating", error: "" });
    await props.api.streamHostProductImage(props.selectedTaskId(), { kind, prompt: finalPrompt, reference_images: item.references || [], provider: "", model: "" }, (event) => {
      props.emitStreamDebug?.(event, { family: "host_product_builder", workflow_id: kind });
      if (event.type === "completed") updateHostProductDraft(kind, { phase: "generated", error: "" });
      if (event.type === "failed") updateHostProductDraft(kind, { phase: "idle", error: event.detail || "Generation failed" });
    });
    await loadHostProductBuilder();
  }

  createEffect(() => {
    if (!props.open()) return;
    void props.runAction("hostProductBuilderLoad", loadHostProductBuilder);
  });

  function renderHostProductBuilderPanel() {
    if (!props.open()) return null;
    const kind = hostProductBuilderTab();
    const item = hostProductDraft(kind);
    const section = hostProductSection(kind);
    const outputUrl = section.output ? props.rebuildAssetUrl(section.output) : "";
    return <>
      <div class="drawer-backdrop host-product-builder-backdrop" onClick={() => props.setOpen(false)} />
      <section class="skill-drawer openflow-config-drawer host-product-builder-drawer">
        <div class="skill-drawer-head">
          <div class="host-product-title-row">
            <h3>Host & Product Builder</h3>
            <div class="host-product-builder-tabs">
              <button class={kind === "host" ? "is-active" : ""} type="button" aria-pressed={kind === "host"} onClick={() => setHostProductBuilderTab("host")}>人物参考</button>
              <button class={kind === "product" ? "is-active" : ""} type="button" aria-pressed={kind === "product"} onClick={() => setHostProductBuilderTab("product")}>产品参考</button>
            </div>
          </div>
          <div class="openflow-dialog-head-actions">
            <button class="icon-action openflow-icon-action primary" title="选择模型并生成最终提示词" aria-label="选择模型并生成最终提示词" disabled={!item.simplePrompt?.trim() || item.phase === "prompting"} onClick={() => setHostProductPromptModelDialogOpen(true)}><CodeIcon /></button>
            <button class="icon-action openflow-icon-action" title="查看最终提示词" aria-label="查看最终提示词" disabled={!item.finalPrompt?.trim()} onClick={() => setHostProductPromptPreviewOpen(true)}><DocumentIcon /></button>
            <button class="icon-action openflow-icon-action success" title={item.phase === "generating" ? "生成图片中..." : kind === "host" ? "生成人物参考" : "生成产品参考"} aria-label={item.phase === "generating" ? "生成图片中" : kind === "host" ? "生成人物参考" : "生成产品参考"} disabled={!item.finalPrompt?.trim() || item.phase === "generating"} onClick={() => void props.runAction(`hostProductImage-${kind}`, () => generateHostProductImage(kind))}><PlayIcon /></button>
            <button class="icon-action openflow-icon-action" title="Reload" aria-label="Reload" onClick={() => void props.runAction("hostProductBuilderReload", loadHostProductBuilder)}><ArrowsClockwiseIcon /></button>
            <button class="icon-action openflow-icon-action close" title="Close" aria-label="Close" onClick={() => props.setOpen(false)}><CloseIcon /></button>
          </div>
        </div>
        <div class="host-product-builder-body">
          <div class="host-product-builder-grid">
            <section class="host-product-builder-column">
              <div class="host-product-section-label">
                <span>多图参考上传</span>
                <label class="host-product-plus-upload" title="上传多图参考" aria-label="上传多图参考">
                  <PlusIcon />
                  <input type="file" accept="image/*" multiple onChange={(event) => { const input = event.currentTarget; void props.runAction(`hostProductUpload-${kind}`, () => uploadHostProductRefs(kind, input.files)).finally(() => { input.value = ""; }); }} />
                </label>
              </div>
              <div class="host-product-reference-list">
                <Show when={item.references?.length} fallback={<p>还没有上传参考图</p>}>
                  <For each={item.references || []}>{(path) => <article class="host-product-reference-item"><button type="button" onClick={() => props.openAssetImageViewer(props.rebuildAssetUrl(path), `${kind} reference`)}><img src={props.rebuildAssetUrl(path)} loading="lazy" /><span>{path.split("/").pop()}</span></button><button class="host-product-file-delete" type="button" title="删除参考图" aria-label="删除参考图" onClick={(event) => { event.stopPropagation(); void props.runAction(`hostProductRefDelete-${kind}-${path}`, () => deleteHostProductRef(kind, path)); }}><TrashIcon /></button></article>}</For>
                </Show>
              </div>
              <label class="openflow-field host-product-prompt-field">
                <span>简单提示词</span>
                <textarea value={item.simplePrompt || ""} onInput={(event) => updateHostProductDraft(kind, { simplePrompt: event.currentTarget.value })} />
              </label>
            </section>
            <section class="host-product-builder-column">
              <div class="host-product-output-head">
                <div class="host-product-output-title-row">
                  <h4>{kind === "host" ? "人物一致性参考" : "产品一致性参考"}</h4>
                  <label class="host-product-plus-upload" title="直接上传最终参考图" aria-label="直接上传最终参考图">
                    <PlusIcon />
                    <input type="file" accept="image/*" onChange={(event) => { const input = event.currentTarget; void props.runAction(`hostProductOutputUpload-${kind}`, () => uploadHostProductOutput(kind, input.files)).finally(() => { input.value = ""; }); }} />
                  </label>
                </div>
              </div>
              <div class="host-product-output">
                <Show when={outputUrl} fallback={<p>生成结果会保存到当前 Session workspace</p>}>
                  <article class="host-product-output-item">
                    <button type="button" onClick={() => props.openAssetImageViewer(outputUrl, kind === "host" ? "HOST.png" : "PRODUCT.png")}><Show keyed when={hostProductImageRefreshToken()}>{() => <img src={outputUrl} loading="eager" />}</Show></button>
                    <button class="host-product-file-delete" type="button" title="删除最终参考图" aria-label="删除最终参考图" onClick={(event) => { event.stopPropagation(); void props.runAction(`hostProductOutputDelete-${kind}`, () => deleteHostProductOutput(kind)); }}><TrashIcon /></button>
                  </article>
                  <span>{section.output}</span>
                </Show>
              </div>
              <Show when={item.error}><p class="host-product-error">{item.error}</p></Show>
            </section>
          </div>
        </div>
      </section>
    </>;
  }

  function renderHostProductPromptModelDialog() {
    if (!hostProductPromptModelDialogOpen() || !props.draft() || !props.open()) return null;
    const kind = hostProductBuilderTab();
    const item = hostProductDraft(kind);
    const label = kind === "host" ? "Host" : "Product";
    return <>
      <div class="drawer-backdrop openclip-model-overlay" onClick={() => setHostProductPromptModelDialogOpen(false)} />
      <section class="verify-dialog openflow-model-dialog openclip-prompt-model-dialog host-product-prompt-model-dialog">
        <div class="env-dialog-head openclip-model-dialog-head">
          <div class="openclip-model-header-text">
            <h3>Select Builder Prompt Model</h3>
            <p>{label} · Task #{props.task()?.id || "-"} / Session #{props.task()?.session_id || "-"}</p>
          </div>
        </div>
        <div class="openflow-prompt-model-grid openclip-model-dialog-body">
          <label class="openflow-field openflow-field-span-2 openclip-model-form-group">
            <input value={hostProductPromptModelFilter()} onInput={(e) => setHostProductPromptModelFilter(e.currentTarget.value)} placeholder="搜索模型 / provider / model id" />
          </label>
          <label class="openflow-field openclip-model-form-group">
            <span>Provider</span>
            <div class="openclip-model-select-wrap">
              <select value={props.draft().prompt_model_provider || ""} onChange={(e) => props.updatePromptModelProvider(e.currentTarget.value)}>
                <For each={props.providerItems()}>{(modelItem) => <option value={modelItem.providerID}>{modelItem.providerName}</option>}</For>
              </select>
            </div>
          </label>
          <label class="openflow-field openclip-model-form-group">
            <span>Model</span>
            <div class="openclip-model-select-wrap">
              <select value={props.draft().prompt_model_id || ""} onChange={(e) => props.updateDraft("prompt_model_id", e.currentTarget.value)}>
                <For each={filteredPromptModels()}>{(modelItem) => <option value={modelItem.modelID}>{modelItem.modelName}</option>}</For>
              </select>
            </div>
          </label>
        </div>
        <div class="openflow-model-dialog-summary openclip-model-selection">
          <div class="openflow-model-dialog-summary-body openclip-selection-card">
            <div class="openclip-selection-content">
              <em>{props.selectedPromptModel() ? props.modelDetail(props.selectedPromptModel()) : "No model available"}</em>
            </div>
          </div>
        </div>
        <div class="field-row openflow-model-dialog-actions openclip-model-dialog-actions">
          <button class="secondary openclip-model-cancel" onClick={() => setHostProductPromptModelDialogOpen(false)}>Cancel</button>
          <button class="openclip-model-confirm" disabled={!props.draft().prompt_model_provider || !props.draft().prompt_model_id || !item.simplePrompt?.trim() || item.phase === "prompting"} onClick={() => void props.runAction(`hostProductPrompt-${kind}`, () => generateHostProductPrompt(kind))}>Confirm & Generate</button>
        </div>
      </section>
    </>;
  }

  function renderHostProductPromptPreviewDialog() {
    if (!hostProductPromptPreviewOpen() || !props.open()) return null;
    const kind = hostProductBuilderTab();
    const item = hostProductDraft(kind);
    return <>
      <div class="drawer-backdrop" onClick={() => setHostProductPromptPreviewOpen(false)} />
      <section class="verify-dialog openclip-prompt-preview-dialog host-product-prompt-preview-dialog">
        <div class="env-dialog-head">
          <div>
            <h3>{kind === "host" ? "Host Final Prompt" : "Product Final Prompt"}</h3>
            <p>{(item.finalPrompt || "").length.toLocaleString()} characters</p>
          </div>
          <button class="secondary" onClick={() => setHostProductPromptPreviewOpen(false)}>Close</button>
        </div>
        <textarea class="skill-editor openclip-prompt-preview-textarea" value={item.finalPrompt || ""} onInput={(event) => saveHostProductFinalPrompt(kind, event.currentTarget.value)} />
      </section>
    </>;
  }

  return <>
    {renderHostProductBuilderPanel()}
    {renderHostProductPromptModelDialog()}
    {renderHostProductPromptPreviewDialog()}
  </>;
}
