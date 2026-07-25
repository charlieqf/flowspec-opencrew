import { For, Show, batch, createEffect, createMemo, createSignal, onCleanup } from "solid-js";
import { ModelPresetCards, findModelPresetItem } from "../../../../components/ModelPresetCards.jsx";

const DEFAULT_DRAFTS = {
  host: {
    simplePrompt: "生成一个新的任务级女主播一致性参考板。参考图只作为口播/带货角色、家庭直播环境、坐姿构图和手部讲解姿势参考，不复制原人物身份。新主播为年轻东亚女性，浅蓝色或粉蓝色纯色上衣，黑色圆形领夹麦，自然淡妆，和原主播脸型、眼睛、发型、服装明显不同。需要三视图、表情变化、细节拆解和一个竖屏口播姿势小窗。",
    finalPrompt: "",
    references: [],
    phase: "idle",
    status: "",
    error: "",
  },
  product: {
    simplePrompt: "生成一页纯产品多角度一致性参考图。参考图用于锁定新产品的包装身份、品牌文字、主色、辅助色、材质、图案、组件、条包或瓶身结构。不要人物、手、厨房、直播画面或旧产品。需要正面、左右 3/4、侧面、俯视、组件排列和关键细节特写。",
    finalPrompt: "",
    references: [],
    phase: "idle",
    status: "",
    error: "",
  },
};

const QUICK_TAGS = {
  host: ["三视图", "写实照片级", "直播带货背景", "自然淡妆"],
  product: ["多角度展示", "包装细节", "材质特写", "干净白底"],
};

export default function KouboHostProductBuilder(props) {
  const {
    CodeIcon,
    DocumentIcon,
    PlayIcon,
    PlusIcon,
    RefreshIcon,
    TrashIcon,
    XIcon,
  } = props.icons;

  const [tab, setTab] = createSignal("host");
  const [state, setState] = createSignal(null);
  const [drafts, setDrafts] = createSignal(DEFAULT_DRAFTS);
  const [refreshToken, setRefreshToken] = createSignal(0);
  const [modelDialogOpen, setModelDialogOpen] = createSignal(false);
  const [previewOpen, setPreviewOpen] = createSignal(false);
  const [promptProvider, setPromptProvider] = createSignal("");
  const [promptModel, setPromptModel] = createSignal("");

  function savedRunModel(nextState = state()) {
    return {
      providerID: String(nextState?.run_model?.providerID || props.task()?.run_model_provider || "").trim(),
      modelID: String(nextState?.run_model?.modelID || props.task()?.run_model_id || "").trim(),
    };
  }
  function modelLabel(value) {
    return String(value || "").trim();
  }
  function normalizedPromptModels(nextState = state()) {
    const base = nextState?.prompt_models || { items: [], default_model: { providerID: "", modelID: "" } };
    const items = [...(base.items || [])];
    const runModel = savedRunModel(nextState);
    if (runModel.providerID && runModel.modelID && !items.some((item) => item.providerID === runModel.providerID && item.modelID === runModel.modelID)) {
      items.push({
        providerID: runModel.providerID,
        providerName: modelLabel(runModel.providerID),
        modelID: runModel.modelID,
        modelName: modelLabel(runModel.modelID),
        contextLimit: 0,
        inputModalities: [],
        source: "task_run_model",
      });
    }
    return {
      ...base,
      items,
      default_model: runModel.providerID && runModel.modelID ? runModel : (base.default_model || { providerID: "", modelID: "" }),
    };
  }

  const promptModels = createMemo(() => normalizedPromptModels());
  const modelItems = createMemo(() => promptModels().items || []);

  function kindLabel(kind = tab()) {
    return kind === "host" ? "人物" : "产品";
  }
  function draft(kind = tab()) {
    return drafts()[kind] || drafts().host;
  }
  function section(kind = tab()) {
    return state()?.[kind] || {};
  }
  function outputUrl(kind = tab()) {
    const output = section(kind).output;
    return output ? `${props.assetUrl(output)}?v=${refreshToken()}` : "";
  }
  function updateDraft(kind, patch) {
    setDrafts((prev) => ({ ...prev, [kind]: { ...(prev[kind] || {}), ...patch } }));
  }
  function syncModelDefaults(nextState) {
    const models = normalizedPromptModels(nextState);
    const items = models.items || [];
    const defaults = models.default_model || {};
    const current = items.find((item) => item.providerID === promptProvider() && item.modelID === promptModel()) || null;
    const defaultItem = items.find((item) => item.providerID === defaults.providerID && item.modelID === defaults.modelID) || null;
    const preferred = current ?? defaultItem ?? findModelPresetItem(items, "max") ?? findModelPresetItem(items, "flash") ?? items[0] ?? null;
    batch(() => {
      setPromptProvider(preferred?.providerID || defaults.providerID || "");
      setPromptModel(preferred?.modelID || defaults.modelID || "");
    });
  }
  function selectModelPreset(selection) {
    batch(() => {
      setPromptProvider(selection.providerID);
      setPromptModel(selection.modelID);
    });
  }
  function appendQuickTag(kind, tag) {
    const current = draft(kind).simplePrompt?.trim() || "";
    if (current.includes(tag)) return;
    updateDraft(kind, { simplePrompt: current ? `${current}，${tag}` : tag });
  }
  function isWorking(kind = tab()) {
    return ["prompting", "generating", "uploading", "uploading-output"].includes(draft(kind).phase);
  }
  function primaryActionLabel(kind = tab()) {
    if (draft(kind).phase === "prompting") return "正在生成提示词";
    if (draft(kind).phase === "generating") return `正在生成${kindLabel(kind)}参考`;
    return draft(kind).finalPrompt?.trim() ? `生成${kindLabel(kind)}参考图` : "生成最终提示词";
  }
  function cleanFinalPrompt(value) {
    const raw = String(value || "");
    const lower = raw.trim().slice(0, 8000).toLowerCase();
    if ((lower.includes("<!doctype html") || lower.includes("<html"))
      && (lower.includes("cloudflare") || lower.includes("5xx-error-landing") || lower.includes("a timeout occurred") || lower.includes("error code 524"))) {
      return "";
    }
    return raw;
  }
  function runPrimaryAction(kind = tab()) {
    if (draft(kind).finalPrompt?.trim()) {
      void generateImage(kind);
      return;
    }
    setModelDialogOpen(true);
  }
  function handleRefDrop(kind, event) {
    event.preventDefault();
    void uploadRefs(kind, event.dataTransfer?.files);
  }
  function handleOutputDrop(kind, event) {
    event.preventDefault();
    void uploadOutput(kind, event.dataTransfer?.files);
  }

  async function loadBuilder() {
    const taskId = props.task()?.id;
    if (!taskId) return null;
    const nextState = await props.runAction("hostProductBuilderLoad", () => props.api.hostProductBuilder(taskId));
    setState(nextState);
    setRefreshToken(Date.now());
    setDrafts((prev) => ({
      host: { ...prev.host, simplePrompt: nextState.host?.simple_prompt || prev.host.simplePrompt, finalPrompt: cleanFinalPrompt(nextState.host?.final_prompt || prev.host.finalPrompt), references: nextState.host?.reference_images || [], phase: "idle", status: "", error: "" },
      product: { ...prev.product, simplePrompt: nextState.product?.simple_prompt || prev.product.simplePrompt, finalPrompt: cleanFinalPrompt(nextState.product?.final_prompt || prev.product.finalPrompt), references: nextState.product?.reference_images || [], phase: "idle", status: "", error: "" },
    }));
    syncModelDefaults(nextState);
    return nextState;
  }

  async function uploadRefs(kind, files) {
    if (!files?.length) return;
    updateDraft(kind, { phase: "uploading", status: "正在上传参考图", error: "" });
    try {
      const res = await props.runAction(`hostProductUpload-${kind}`, () => props.api.uploadHostProductRefs(props.task().id, kind, files));
      setState(res.state);
      setRefreshToken(Date.now());
      updateDraft(kind, { references: res.section?.reference_images || [], phase: "idle", status: "", error: "" });
    } catch (err) {
      updateDraft(kind, { phase: "idle", status: "", error: err instanceof Error ? err.message : String(err) });
      throw err;
    }
  }

  async function deleteRef(kind, path) {
    if (!path) return;
    const res = await props.runAction(`hostProductRefDelete-${kind}`, () => props.api.deleteHostProductRef(props.task().id, { kind, path }));
    setState(res.state);
    setRefreshToken(Date.now());
    updateDraft(kind, { references: res.section?.reference_images || [], status: "", error: "" });
  }

  async function uploadOutput(kind, files) {
    const file = Array.from(files || [])[0];
    if (!file) return;
    updateDraft(kind, { phase: "uploading-output", status: "正在上传最终参考图", error: "" });
    try {
      const res = await props.runAction(`hostProductOutputUpload-${kind}`, () => props.api.uploadHostProductOutput(props.task().id, kind, file));
      setState(res.state);
      setRefreshToken(Date.now());
      updateDraft(kind, { phase: "generated", status: "", error: "" });
    } catch (err) {
      updateDraft(kind, { phase: "idle", status: "", error: err instanceof Error ? err.message : String(err) });
      throw err;
    }
  }

  async function deleteOutput(kind) {
    const output = section(kind).output;
    if (!output || !window.confirm(`删除当前${kindLabel(kind)}一致性参考图？`)) return;
    const res = await props.runAction(`hostProductOutputDelete-${kind}`, () => props.api.deleteHostProductOutput(props.task().id, { kind, path: output }));
    setState(res.state);
    setRefreshToken(Date.now());
    updateDraft(kind, { phase: "idle", status: "", error: "" });
  }

  async function generatePrompt(kind) {
    const item = draft(kind);
    updateDraft(kind, { phase: "prompting", status: "正在请求模型生成最终提示词", error: "" });
    try {
      let completed = null;
      let failed = "";
      await props.runAction(`hostProductPrompt-${kind}`, () => props.api.streamHostProductPrompt(props.task().id, { kind, simple_prompt: item.simplePrompt || "", reference_images: item.references || [], provider: promptProvider(), model: promptModel() }, (event) => {
        if (event.type === "started") updateDraft(kind, { status: "已开始生成最终提示词" });
        if (event.type === "heartbeat") updateDraft(kind, { status: `模型仍在生成，已等待 ${Math.round(Number(event.elapsed_seconds || 0))} 秒` });
        if (event.type === "failed") {
          failed = event.detail || "提示词生成失败";
          updateDraft(kind, { phase: "idle", status: "", error: failed });
        }
        if (event.type === "completed") completed = event;
      }));
      if (failed) throw new Error(failed);
      const res = completed;
      if (!res) throw new Error("提示词生成结束但没有返回结果");
      setState(res.state);
      updateDraft(kind, { finalPrompt: cleanFinalPrompt(res.prompt || ""), references: res.section?.reference_images || item.references || [], phase: "prompt_ready", status: "", error: "" });
      setModelDialogOpen(false);
      setPreviewOpen(true);
    } catch (err) {
      updateDraft(kind, { phase: "idle", status: "", error: err instanceof Error ? err.message : String(err) });
      throw err;
    }
  }

  async function generateImage(kind) {
    const item = draft(kind);
    updateDraft(kind, { phase: "generating", status: `正在生成${kindLabel(kind)}一致性参考`, error: "" });
    try {
      await props.runAction(`hostProductImage-${kind}`, () => props.api.streamHostProductImage(props.task().id, { kind, prompt: item.finalPrompt || "", reference_images: item.references || [], provider: "", model: "" }, (event) => {
        if (event.type === "heartbeat") updateDraft(kind, { status: `图片仍在生成，已等待 ${Math.round(Number(event.elapsed_seconds || 0))} 秒` });
        if (event.type === "failed") updateDraft(kind, { phase: "idle", status: "", error: event.detail || "生成失败" });
        if (event.type === "completed") updateDraft(kind, { phase: "generated", status: "", error: "" });
      }));
      await loadBuilder();
    } catch (err) {
      updateDraft(kind, { phase: "idle", status: "", error: err instanceof Error ? err.message : String(err) });
      throw err;
    }
  }

  createEffect(() => {
    if (!props.open()) return;
    void loadBuilder();
  });

  createEffect(() => {
    if (!modelDialogOpen()) return;
    const onKey = (event) => {
      if (event.key === "Escape") setModelDialogOpen(false);
    };
    window.addEventListener("keydown", onKey);
    onCleanup(() => window.removeEventListener("keydown", onKey));
  });

  createEffect(() => {
    if (!props.open()) return;
    const onKey = (event) => {
      if (event.key === "Escape") props.setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    onCleanup(() => window.removeEventListener("keydown", onKey));
  });

  function renderReferenceItem(kind, path) {
    return <article class="kbsp-hpb-ref-item">
      <button type="button" title={path} onClick={() => props.openImage({ path, label: `${kindLabel(kind)}参考图` })}>
        <img src={`${props.assetUrl(path)}?v=${refreshToken()}`} loading="lazy" />
        <span>{path.split("/").pop()}</span>
      </button>
      <button class="kbsp-hpb-delete" type="button" title="删除参考图" aria-label="删除参考图" onClick={(event) => { event.stopPropagation(); void deleteRef(kind, path); }}><TrashIcon /></button>
    </article>;
  }

  return <Show when={props.open()}>
    <div class="kbsp-hpb-backdrop" onClick={() => props.setOpen(false)} />
    <section class="kbsp-hpb-dialog" role="dialog" aria-modal="true" aria-label="人物与产品参考生成">
      <header class="kbsp-hpb-head">
        <div class="kbsp-hpb-title-row">
          <h3>人物与产品参考生成</h3>
          <div class="kbsp-hpb-tabs">
            <button class={tab() === "host" ? "is-active" : ""} type="button" aria-pressed={tab() === "host"} onClick={() => setTab("host")}>人物参考</button>
            <button class={tab() === "product" ? "is-active" : ""} type="button" aria-pressed={tab() === "product"} onClick={() => setTab("product")}>产品参考</button>
          </div>
        </div>
        <div class="kbsp-hpb-actions">
          <button class="kbsp-hpb-icon primary" type="button" title="选择模型并生成最终提示词" aria-label="选择模型并生成最终提示词" disabled={!draft().simplePrompt?.trim() || draft().phase === "prompting"} onClick={() => setModelDialogOpen(true)}><CodeIcon /></button>
          <button class="kbsp-hpb-icon" type="button" title="查看最终提示词" aria-label="查看最终提示词" disabled={!draft().finalPrompt?.trim()} onClick={() => setPreviewOpen(true)}><DocumentIcon /></button>
          <button class="kbsp-hpb-icon success" type="button" title={draft().phase === "generating" ? "生成图片中" : `生成${kindLabel()}参考`} aria-label={draft().phase === "generating" ? "生成图片中" : `生成${kindLabel()}参考`} disabled={!draft().finalPrompt?.trim() || draft().phase === "generating"} onClick={() => void generateImage(tab())}><PlayIcon /></button>
          <button class="kbsp-hpb-icon" type="button" title="刷新" aria-label="刷新" onClick={() => void loadBuilder()}><RefreshIcon /></button>
          <button class="kbsp-hpb-icon close" type="button" title="关闭" aria-label="关闭" onClick={() => props.setOpen(false)}><XIcon /></button>
        </div>
      </header>
      <div class="kbsp-hpb-body">
        <section class="kbsp-hpb-column">
          <div class="kbsp-hpb-input-block">
            <div class="kbsp-hpb-section-label">
              <span>多图参考上传</span>
              <label class="kbsp-hpb-plus" title="上传多图参考" aria-label="上传多图参考">
                <PlusIcon />
                <input type="file" accept="image/*" multiple onChange={(event) => { const input = event.currentTarget; void uploadRefs(tab(), input.files).finally(() => { input.value = ""; }); }} />
              </label>
            </div>
            <label class="kbsp-hpb-upload-box" onDragOver={(event) => event.preventDefault()} onDrop={(event) => handleRefDrop(tab(), event)}>
              <span class="kbsp-hpb-upload-icon"><PlusIcon /></span>
              <span class="kbsp-hpb-upload-copy">拖拽图片到这里，或<strong>点击上传</strong></span>
              <input type="file" accept="image/*" multiple onChange={(event) => { const input = event.currentTarget; void uploadRefs(tab(), input.files).finally(() => { input.value = ""; }); }} />
            </label>
            <div class="kbsp-hpb-ref-list">
              <Show when={draft().references?.length} fallback={<p>还没有上传参考图</p>}>
                <For each={draft().references || []}>{(path) => renderReferenceItem(tab(), path)}</For>
              </Show>
            </div>
          </div>
          <label class="kbsp-hpb-field">
            <span>智能提示词 Prompt</span>
            <textarea value={draft().simplePrompt || ""} onInput={(event) => updateDraft(tab(), { simplePrompt: event.currentTarget.value })} />
          </label>
          <div class="kbsp-hpb-tags" aria-label={`${kindLabel()}快捷提示词`}>
            <For each={QUICK_TAGS[tab()] || []}>{(tag) => <button type="button" onClick={() => appendQuickTag(tab(), tag)}>+ {tag}</button>}</For>
          </div>
          <button class="kbsp-hpb-generate" type="button" disabled={!draft().simplePrompt?.trim() || isWorking()} onClick={() => runPrimaryAction(tab())}>
            <PlayIcon />
            <span>{primaryActionLabel()}</span>
          </button>
          <Show when={draft().status && isWorking()}><p class="kbsp-hpb-status">{draft().status}</p></Show>
        </section>
        <section class="kbsp-hpb-column kbsp-hpb-output-panel">
          <div class="kbsp-hpb-output-head">
            <h4>{kindLabel()}一致性参考预览</h4>
            <label class="kbsp-hpb-plus" title="直接上传最终参考图" aria-label="直接上传最终参考图">
              <PlusIcon />
              <input type="file" accept="image/*" onChange={(event) => { const input = event.currentTarget; void uploadOutput(tab(), input.files).finally(() => { input.value = ""; }); }} />
            </label>
          </div>
          <div class="kbsp-hpb-output">
            <Show when={outputUrl()} fallback={
              <label class="kbsp-hpb-output-upload" onDragOver={(event) => event.preventDefault()} onDrop={(event) => handleOutputDrop(tab(), event)}>
                <span class="kbsp-hpb-upload-icon"><PlusIcon /></span>
                <span class="kbsp-hpb-upload-copy">拖拽图片到这里，或<strong>点击上传</strong></span>
                <input type="file" accept="image/*" onChange={(event) => { const input = event.currentTarget; void uploadOutput(tab(), input.files).finally(() => { input.value = ""; }); }} />
              </label>
            }>
              <article class="kbsp-hpb-output-item">
                <button type="button" onClick={() => props.openImage({ path: section().output, label: kindLabel() === "人物" ? "HOST.png" : "PRODUCT.png" })}>
                  <img src={outputUrl()} loading="eager" />
                </button>
                <button class="kbsp-hpb-delete" type="button" title="删除最终参考图" aria-label="删除最终参考图" onClick={(event) => { event.stopPropagation(); void deleteOutput(tab()); }}><TrashIcon /></button>
              </article>
              <span>{section().output}</span>
            </Show>
            <Show when={draft().phase === "generating" || draft().phase === "uploading-output"}>
              <div class="kbsp-hpb-loading">
                <span />
                <p>{draft().phase === "generating" ? `正在生成${kindLabel()}一致性参考` : "正在上传最终参考图"}</p>
              </div>
            </Show>
          </div>
          <Show when={draft().status && isWorking()}><p class="kbsp-hpb-status">{draft().status}</p></Show>
          <Show when={draft().error}><p class="kbsp-hpb-error">{draft().error}</p></Show>
        </section>
      </div>
    </section>

    <Show when={modelDialogOpen()}>
      <div class="kbsp-hpb-modal-backdrop" onClick={() => setModelDialogOpen(false)} />
      <section class="kbsp-hpb-model-dialog" role="dialog" aria-modal="true" aria-label="选择提示词模型">
        <header><h3>选择提示词模型</h3><p>{kindLabel()} · 任务 #{props.task()?.id || "-"} / 会话 #{props.task()?.session_id || "-"}</p></header>
        <div class="kbsp-hpb-model-grid model-preset-dialog-body">
          <ModelPresetCards
            items={modelItems()}
            provider={promptProvider()}
            model={promptModel()}
            onSelect={selectModelPreset}
            aria-label="Host product prompt model preset"
          />
        </div>
        <Show when={draft().status && draft().phase === "prompting"}><p class="kbsp-hpb-status">{draft().status}</p></Show>
        <footer><button type="button" class="secondary" disabled={draft().phase === "prompting"} onClick={() => setModelDialogOpen(false)}>取消</button><button type="button" disabled={!promptProvider() || !promptModel() || draft().phase === "prompting"} onClick={() => void generatePrompt(tab())}>{draft().phase === "prompting" ? "生成中..." : "确认并生成"}</button></footer>
      </section>
    </Show>

    <Show when={previewOpen()}>
      <div class="kbsp-hpb-modal-backdrop" onClick={() => setPreviewOpen(false)} />
      <section class="kbsp-hpb-prompt-dialog" role="dialog" aria-modal="true" aria-label="最终提示词">
        <header><h3>{kindLabel()}最终提示词</h3><button class="kbsp-hpb-icon close" type="button" title="关闭" aria-label="关闭" onClick={() => setPreviewOpen(false)}><XIcon /></button></header>
        <textarea value={draft().finalPrompt || ""} onInput={(event) => updateDraft(tab(), { finalPrompt: event.currentTarget.value })} />
        <footer><button type="button" onClick={() => setPreviewOpen(false)}>完成</button></footer>
      </section>
    </Show>
  </Show>;
}
