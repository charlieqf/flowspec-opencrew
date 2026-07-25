import { Show, createEffect, createMemo, createSignal } from "solid-js";
import FlowIcon from "./FlowIcon.jsx";

function text(value) {
  return String(value || "").trim();
}

function buildPromptText(positive, negative) {
  const pos = text(positive);
  const neg = text(negative);
  return neg ? `${pos}\n\nNegative prompt:\n${neg}` : pos;
}

export default function PromptBuilderModal(props) {
  const [positivePrompt, setPositivePrompt] = createSignal("");
  const [negativePrompt, setNegativePrompt] = createSignal("");
  const [insertMode, setInsertMode] = createSignal("append");
  const [savingMode, setSavingMode] = createSignal("");
  const builder = () => props.builder?.() || {};
  const supported = createMemo(() => builder().supported !== false);
  const hasExistingDraft = createMemo(() => Boolean(text(props.currentDraft?.())));
  const providerLabel = createMemo(() => [builder().provider, builder().model].filter(Boolean).join(" / ") || "未选择模型");
  const templateLabel = createMemo(() => builder().template_path || builder().template_source || "");

  createEffect(() => {
    const payload = builder();
    setPositivePrompt(text(payload.positive_prompt));
    setNegativePrompt(text(payload.negative_prompt));
    setInsertMode(hasExistingDraft() ? "append" : "replace");
  });

  async function apply(mode) {
    if (!supported() || !text(positivePrompt()) || savingMode()) return;
    const prompt = mode === "positive"
      ? text(positivePrompt())
      : mode === "negative"
        ? text(negativePrompt())
        : buildPromptText(positivePrompt(), negativePrompt());
    setSavingMode(mode);
    try {
      await props.onApply?.({
        applyMode: mode,
        insertMode: insertMode(),
        positivePrompt: positivePrompt(),
        negativePrompt: negativePrompt(),
        prompt,
      });
    } finally {
      setSavingMode("");
    }
  }

  return <Show when={props.open?.()}>
    <div class="ual-prompt-builder-backdrop" role="dialog" aria-modal="true" aria-label="提示词构建器">
      <section class="ual-prompt-builder-shell">
        <header>
          <div>
            <h3>提示词构建器</h3>
            <p>{providerLabel()}</p>
          </div>
          <button type="button" aria-label="关闭提示词构建器" onClick={props.onClose}><FlowIcon name="close" /></button>
        </header>

        <Show when={props.loading?.()}>
          <div class="ual-prompt-builder-state">正在加载提示词...</div>
        </Show>

        <Show when={!props.loading?.() && props.error?.()}>
          <div class="ual-prompt-builder-error">{props.error?.()}</div>
        </Show>

        <Show when={!props.loading?.() && !props.error?.() && !supported()}>
          <div class="ual-prompt-builder-state">
            <strong>{builder().reason || "当前 Builder 不支持所选模型。"}</strong>
            <span>{builder().warnings?.[0] || "请在当前设置中选择可用模型后再打开。"}</span>
            <div class="ual-prompt-builder-inline-actions">
              <Show when={props.onSwitchToGrok}>
                <button type="button" onClick={props.onSwitchToGrok}>切换到 Grok</button>
              </Show>
              <button type="button" class="secondary" onClick={props.onClose}>继续普通输入</button>
            </div>
          </div>
        </Show>

        <Show when={!props.loading?.() && !props.error?.() && supported()}>
          <div class="ual-prompt-builder-meta">
            <span title={templateLabel()}>{templateLabel() || "当前会话提示词草稿"}</span>
          </div>
          <section class="ual-prompt-builder-fields">
            <label>
              <span>正向提示词</span>
              <textarea value={positivePrompt()} spellcheck={false} onInput={(event) => setPositivePrompt(event.currentTarget.value)} />
            </label>
            <label>
              <span>负向提示词</span>
              <textarea value={negativePrompt()} spellcheck={false} onInput={(event) => setNegativePrompt(event.currentTarget.value)} />
            </label>
          </section>
          <Show when={builder().warnings?.length}>
            <p class="ual-prompt-builder-warning">{builder().warnings.join(" ")}</p>
          </Show>
          <Show when={hasExistingDraft()}>
            <div class="ual-prompt-builder-insert-mode" aria-label="Insert mode">
              <button type="button" class={insertMode() === "append" ? "is-active" : ""} onClick={() => setInsertMode("append")}>追加</button>
              <button type="button" class={insertMode() === "replace" ? "is-active" : ""} onClick={() => setInsertMode("replace")}>替换</button>
            </div>
          </Show>
        </Show>

        <footer>
          <button type="button" class="secondary" disabled={Boolean(savingMode())} onClick={props.onClose}>取消</button>
          <Show when={supported() && !props.loading?.() && !props.error?.()}>
            <button type="button" disabled={Boolean(savingMode()) || !text(positivePrompt())} onClick={() => void apply("positive")}>{savingMode() === "positive" ? "添加中..." : "添加正向"}</button>
            <button type="button" disabled={Boolean(savingMode()) || !text(negativePrompt())} onClick={() => void apply("negative")}>{savingMode() === "negative" ? "添加中..." : "添加负向"}</button>
            <button type="button" class="primary" disabled={Boolean(savingMode()) || !text(positivePrompt())} onClick={() => void apply("full")}>{savingMode() === "full" ? "添加中..." : "添加完整提示词"}</button>
          </Show>
        </footer>
      </section>
    </div>
  </Show>;
}
