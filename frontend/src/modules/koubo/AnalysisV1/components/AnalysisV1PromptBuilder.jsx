import { For, Show, createMemo } from "solid-js";
import { PROMPT_TABS, buildRewriteSimplePrompt, buildStoryboardSimplePrompt, normalizeStoryboardQuickConfig } from "../analysisV1Model.js";

const SIMPLE_PROMPT_SOURCE_FIELDS = new Set([
  "industry",
  "persona",
  "target_audience",
  "video_formula",
  "product_info",
  "constraints",
]);

function optionValue(options, current) {
  return (options || []).includes(current) ? current : "__custom__";
}

export default function AnalysisV1PromptBuilder(props) {
  let fileInput;
  const tabs = [PROMPT_TABS.rewrite, PROMPT_TABS.storyboard];
  const activeTab = createMemo(() => PROMPT_TABS[props.activeTab] || PROMPT_TABS.rewrite);
  const activeSimpleValue = createMemo(() => props.draft[activeTab().simpleField] || "");
  const activeFinalValue = createMemo(() => props.draft[activeTab().finalField] || "");
  const storyboardQuickConfig = createMemo(() => normalizeStoryboardQuickConfig(props.draft.storyboard_quick_config));
  const options = createMemo(() => props.options || {});
  const update = (field, value) => {
    const next = { ...props.draft, [field]: value };
    if (SIMPLE_PROMPT_SOURCE_FIELDS.has(field)) {
      next.rewrite_simple_prompt = buildRewriteSimplePrompt(next);
      next.storyboard_simple_prompt = buildStoryboardSimplePrompt(next);
      next.simple_prompt = next.rewrite_simple_prompt;
    }
    if (field === "rewrite_simple_prompt") next.simple_prompt = value;
    if (field === "rewrite_final_prompt") next.final_prompt = value;
    props.onChange(next);
  };
  const updateStoryboardQuickConfig = (field, value) => {
    const nextConfig = normalizeStoryboardQuickConfig({ ...storyboardQuickConfig(), [field]: value });
    const next = {
      ...props.draft,
      storyboard_quick_config: nextConfig,
      storyboard_final_prompt: "",
    };
    next.storyboard_simple_prompt = buildStoryboardSimplePrompt(next);
    props.onChange(next);
  };
  const productLength = () => String(props.draft.product_info || "").length;
  const constraintsLength = () => String(props.draft.constraints || "").length;
  const renderOptionField = (field, label, labelSmall, items, placeholder) => {
    const currentOption = () => optionValue(items || [], props.draft[field] || "");
    return (
      <label class="analysis-v1-field">
        <span>{label} <small>/ {labelSmall}</small></span>
        <select value={currentOption()} onChange={(event) => update(field, event.currentTarget.value === "__custom__" ? "" : event.currentTarget.value)}>
          <For each={items || []}>{(item) => <option value={item}>{item}</option>}</For>
          <option value="__custom__">自定义</option>
        </select>
        <Show when={currentOption() === "__custom__"}>
          <input value={props.draft[field] || ""} onInput={(event) => update(field, event.currentTarget.value)} placeholder={placeholder} />
        </Show>
      </label>
    );
  };
  return <section class="analysis-v1-panel analysis-v1-prompt-panel">
    <Show when={props.task} fallback={<div class="analysis-v1-empty">选择一个任务后编辑口播改写提示词</div>}>
      <div class="analysis-v1-prompt-grid">
        <Show when={!props.hideTargetVideo}>
          <label class="analysis-v1-field analysis-v1-video-field">
            <span>目标视频 <small>/ TARGET VIDEO</small></span>
            <div class="analysis-v1-video-input-row">
              <input value={props.draft.reference_video_path || ""} onInput={(event) => update("reference_video_path", event.currentTarget.value)} placeholder="上传目标视频或粘贴本地路径" />
              <input ref={(el) => { fileInput = el; }} class="analysis-v1-hidden-file" type="file" accept="video/*,.mp4,.mov,.m4v" onChange={(event) => { const file = event.currentTarget.files?.[0]; if (file) void props.onUploadVideo?.(file); event.currentTarget.value = ""; }} />
              <button type="button" class="analysis-v1-upload-button" disabled={props.busy} onClick={() => fileInput?.click()}>{props.uploading ? "上传中" : "上传"}</button>
            </div>
          </label>
        </Show>
        {renderOptionField("industry", "行业", "INDUSTRY", options().industry, "自定义行业")}
        {renderOptionField("persona", "人设", "PERSONA", options().persona, "自定义人设")}
        {renderOptionField("target_audience", "目标受众", "TARGET AUDIENCE", options().target_audience, "自定义目标受众")}
        {renderOptionField("video_formula", "视频公式", "VIDEO FORMULA", options().video_formula, "自定义视频公式")}
        <label class="analysis-v1-field analysis-v1-half-field">
          <span>产品信息 <small>/ PRODUCT INFO</small></span>
          <textarea maxlength="1000" value={props.draft.product_info} onInput={(event) => update("product_info", event.currentTarget.value)} />
          <em>{productLength()} / 1000</em>
        </label>
        <label class="analysis-v1-field analysis-v1-half-field">
          <span>约束条件 <small>/ CONSTRAINTS</small></span>
          <textarea maxlength="1000" value={props.draft.constraints} onInput={(event) => update("constraints", event.currentTarget.value)} placeholder="输入必须遵守的要求、限制条件、合规说明等..." />
          <em>{constraintsLength()} / 1000</em>
        </label>
        <div class="analysis-v1-prompt-tabs analysis-v1-field-span">
          <For each={tabs}>{(tab) => (
            <button
              type="button"
              class={props.activeTab === tab.id ? "is-active" : ""}
              onClick={() => props.onTabChange?.(tab.id)}
            >
              {tab.label}
            </button>
          )}</For>
        </div>
        <Show when={activeTab().id === "storyboard"}>
          <div class="analysis-v1-field analysis-v1-field-span analysis-v1-storyboard-quick">
            <span>故事版快速参数 <small>/ STRUCTURE</small></span>
            <div class="analysis-v1-storyboard-quick-grid">
              <label>
                <small>场景</small>
                <input type="number" min="1" max="60" step="0.5" value={storyboardQuickConfig().target_scene_seconds} onInput={(event) => updateStoryboardQuickConfig("target_scene_seconds", event.currentTarget.value)} />
                <em>s</em>
              </label>
              <label>
                <small>分镜</small>
                <input type="number" min="1" max="120" step="0.5" value={storyboardQuickConfig().target_shot_seconds} onInput={(event) => updateStoryboardQuickConfig("target_shot_seconds", event.currentTarget.value)} />
                <em>s</em>
              </label>
              <label>
                <small>容忍度</small>
                <input type="number" min="0" max="20" step="0.5" value={storyboardQuickConfig().split_tolerance_seconds} onInput={(event) => updateStoryboardQuickConfig("split_tolerance_seconds", event.currentTarget.value)} />
                <em>s</em>
              </label>
              <label>
                <small>分组方式</small>
                <select value={storyboardQuickConfig().language_boundary_mode} onChange={(event) => updateStoryboardQuickConfig("language_boundary_mode", event.currentTarget.value)}>
                  <option value="balanced">均衡</option>
                  <option value="strict">严格</option>
                  <option value="loose">宽松</option>
                </select>
              </label>
            </div>
          </div>
        </Show>
        <label class="analysis-v1-field analysis-v1-field-span">
          <span>{activeTab().id === "rewrite" ? "SRT 改写简单提示词" : "StoryBoard 简单提示词"}</span>
          <textarea class="analysis-v1-simple-prompt" value={activeSimpleValue()} onInput={(event) => update(activeTab().simpleField, event.currentTarget.value)} />
        </label>
        <label class="analysis-v1-field analysis-v1-field-span">
          <span>{activeTab().id === "rewrite" ? "SRT 改写最终提示词" : "StoryBoard 最终提示词"}</span>
          <textarea class="analysis-v1-final-prompt" value={activeFinalValue()} onInput={(event) => update(activeTab().finalField, event.currentTarget.value)} />
        </label>
      </div>
    </Show>
  </section>;
}
