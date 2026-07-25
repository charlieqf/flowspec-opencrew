import { For, Show } from "solid-js";
import { formatTimelineMs } from "./timelineModel.js";

function fragmentKindLabel(fragment) {
  return fragment.analysisScheme === "visual_semantic" ? "视觉命中" : fragment.analysisScheme === "dialogue" ? "对白命中" : "片段命中";
}

function importSucceeded(message) {
  return String(message || "").includes("已导入目标 StoryBoard");
}

function scoreReasonLabel(reason, source) {
  const text = String(reason || "").trim();
  if (source !== "external") return text;
  const normalized = text.toLowerCase().replace(/[_-]+/g, " ").replace(/\s+/g, " ");
  if (normalized === "text embedding rerank" || normalized === "embedding rerank") {
    return "文本关键词相关";
  }
  if (normalized === "provider relevance rank" || normalized === "provider relevance") {
    return "来源站点相关性排序";
  }
  if (normalized === "provider rank") return "来源站点排序";
  if (normalized === "portrait ratio") return "竖屏比例匹配";
  if (normalized === "landscape ratio") return "横屏比例匹配";
  if (normalized === "square ratio") return "方形比例匹配";
  if (normalized === "unknown ratio") return "画幅比例信息";
  if (normalized === "license metadata confirmed") return "授权信息已确认";
  if (normalized === "provider specific fallback query") return "已使用来源站点兼容关键词";
  return /[a-z]/i.test(text) ? "来源提供的相关性依据" : text;
}

function scoreReasonLabels(candidate) {
  return Array.from(new Set(
    candidate.scoreReasons.map((reason) => scoreReasonLabel(reason, candidate.source)).filter(Boolean),
  ));
}

export default function EditorSearchPanel(props) {
  return <section class="ml-editor-side-section" aria-label="跨页面对白与关键词检索">
    <header>
      <div>
        <h3>跨页面对白/关键词检索</h3>
        <p>原视频可按命中范围打开剪辑页；派生片段精确预览并直接复用；外部候选只能整条导入。</p>
      </div>
    </header>
    <p class="ml-editor-search-capability">支持对白、关键词和已发布的四帧视觉描述检索，当前优先精确率；暂不包含图像或视频向量相似度检索。</p>
    <label class="ml-editor-field">目标 StoryBoard
      <select value={props.targetTaskId || ""} onChange={(event) => props.onTargetTaskChange?.(Number(event.currentTarget.value) || 0)}>
        <option value="">未选择（仅可检索全局素材库）</option>
        <For each={props.importTargets}>{(target) =>
          <option value={target.taskId}>Task #{target.taskId} · {target.title}</option>
        }</For>
      </select>
    </label>
    <div class="ml-editor-source-options">
      <label><input
        type="checkbox"
        checked={props.sources.includes("media_library")}
        onChange={(event) => props.onSourceChange?.("media_library", event.currentTarget.checked)}
      />全局素材库</label>
      <label title={!props.targetTaskId ? "外部检索需要先选择有效 StoryBoard 目标" : ""}><input
        type="checkbox"
        disabled={!props.targetTaskId}
        checked={props.sources.includes("external")}
        onChange={(event) => props.onSourceChange?.("external", event.currentTarget.checked)}
      />外部素材</label>
    </div>
    <label class="ml-editor-field">补充关键词/要求
      <textarea
        rows="2"
        value={props.userText}
        placeholder="例如：产品防水能力、近景、竖屏"
        onInput={(event) => props.onUserTextChange?.(event.currentTarget.value)}
      />
    </label>
    <div class="ml-editor-search-context">
      <span>已选分析片段 {props.searchFragmentRefs.length}</span>
      <Show when={props.searchFragmentRefs.length}>
        <button type="button" onClick={() => props.onClearRefs?.()}>清空</button>
      </Show>
    </div>
    <Show when={props.error}><div
      class={importSucceeded(props.error) ? "ml-editor-inline-success" : "ml-editor-inline-error"}
      role="status"
    >{props.error}</div></Show>
    <button
      type="button"
      class="ml-editor-primary-button"
      disabled={props.busy || !props.sources.length}
      onClick={() => props.onRun?.()}
    >{props.busy ? "正在检索…" : "开始检索"}</button>
    <Show when={props.searchRun}>
      <div class="ml-editor-search-summary">
        <span>检索完成 · {props.searchRun.items.length} 个候选</span>
        <Show when={props.searchRun.plannerDegraded}><strong>查询规划已降级</strong></Show>
        <details>
          <summary>技术详情</summary>
          <span>Search ID {props.searchRun.searchId}</span>
        </details>
      </div>
      <For each={Object.entries(props.searchRun.sourceErrors || {})}>{([source, detail]) =>
        <div class="ml-editor-inline-error">
          {source}：{detail?.code || "source_failed"} · {detail?.user_message || detail?.message || "该来源未返回结果"}
        </div>
      }</For>
      <div class="ml-editor-candidate-list">
        <For each={props.searchRun.items}>{(candidate) =>
          <article class={`ml-editor-candidate ${candidate.source}`}>
            <div class="ml-editor-candidate-preview">
              <span>无缩略图</span>
              <Show when={candidate.thumbnailUrl}>
                <img
                  src={candidate.thumbnailUrl}
                  alt=""
                  onError={(event) => {
                    event.currentTarget.style.display = "none";
                  }}
                />
              </Show>
            </div>
            <div>
              <h4>{candidate.displayName}</h4>
              <p>
                {candidate.source === "external"
                  ? `外部素材 · ${candidate.provider || "未知 Provider"}`
                  : candidate.candidateKind === "derived_clip" ? "全局素材库 · 可复用片段" : "全局素材库 · 原视频"}
                {" · "}{candidate.durationMs === null ? "时长未知" : formatTimelineMs(candidate.durationMs)}
                {" · "}{candidate.aspect || candidate.orientation || "画幅未知"}
              </p>
              <Show when={candidate.source === "external"}>
                <dl class="ml-editor-external-meta">
                  <div><dt>Provider</dt><dd>{candidate.provider || "未提供"}</dd></div>
                  <div><dt>Creator</dt><dd>
                    <Show when={candidate.creator.url} fallback={candidate.creator.name || "未提供"}>
                      <a href={candidate.creator.url} target="_blank" rel="noreferrer">{candidate.creator.name || "来源页面"}</a>
                    </Show>
                  </dd></div>
                  <div><dt>License</dt><dd>
                    <Show when={candidate.license.url} fallback={candidate.license.name || "未知"}>
                      <a href={candidate.license.url} target="_blank" rel="noreferrer">{candidate.license.name || "查看许可"}</a>
                    </Show>
                    {" · "}{candidate.license.status}
                    <Show when={candidate.license.requiresAttribution}> · 需要署名</Show>
                  </dd></div>
                  <Show when={candidate.license.attributionText}><div><dt>署名</dt><dd>{candidate.license.attributionText}</dd></div></Show>
                </dl>
                <label class="ml-editor-license-confirm">
                  <input
                    type="checkbox"
                    checked={props.confirmedExternalLicenses.has(candidate.candidateId)}
                    onChange={(event) => props.onExternalLicenseConfirmation?.(candidate.candidateId, event.currentTarget.checked)}
                  />
                  我已阅读并确认该候选的来源与 license 条款
                </label>
              </Show>
              <Show when={candidate.candidateKind === "derived_clip" && candidate.tags.length}>
                <small>标签：{candidate.tags.join("、")}</small>
              </Show>
              <Show when={candidate.scoreReasons.length}><small>{scoreReasonLabels(candidate).join(" · ")}</small></Show>
              <div class="ml-editor-candidate-actions">
                <Show when={candidate.allowedActions.includes("preview") && candidate.previewUrl}>
                  <a href={candidate.previewUrl} target="_blank" rel="noreferrer" onClick={() => props.onPreview?.(candidate)}>预览</a>
                </Show>
                <Show when={candidate.allowedActions.includes("open_editor") && candidate.source === "media_library" && candidate.assetId}>
                  <button type="button" onClick={() => props.onOpenEditor?.(candidate, candidate.matchedFragments[0])}>打开其剪辑页</button>
                </Show>
                <Show when={candidate.allowedActions.includes("import_original") && candidate.source === "media_library"}>
                  <button type="button" disabled={!props.targetTaskId || props.actionBusy} onClick={() => props.onImport?.(candidate, "import_original")}>导入原视频</button>
                </Show>
                <Show when={candidate.allowedActions.includes("import_clip") && candidate.source === "media_library" && candidate.candidateKind === "derived_clip"}>
                  <button type="button" disabled={!props.targetTaskId || props.actionBusy} onClick={() => props.onImport?.(candidate, "import_clip")}>导入此片段</button>
                </Show>
                <Show when={candidate.allowedActions.includes("import_whole") && candidate.source === "external"}>
                  <button
                    type="button"
                    disabled={!props.targetTaskId || props.actionBusy || !candidate.importSupported || !props.confirmedExternalLicenses.has(candidate.candidateId)}
                    title={!candidate.importSupported ? candidate.importUnsupportedReason || "该候选不支持导入" : !props.confirmedExternalLicenses.has(candidate.candidateId) ? "请先显式确认 license" : ""}
                    onClick={() => props.onImport?.(candidate, "import_whole")}
                  >整条导入</button>
                </Show>
              </div>
              <Show when={candidate.matchedFragments.length}>
                <div class="ml-editor-match-list">
                  <strong>命中 {candidate.matchedFragments.length} 个片段</strong>
                  <For each={candidate.matchedFragments}>{(fragment) => <section>
                    <div><b>{fragmentKindLabel(fragment)}</b><span>{formatTimelineMs(fragment.startMs)} – {formatTimelineMs(fragment.endMs)}</span></div>
                    <p>{fragment.dialogueText || fragment.summary || "已返回可解释的命中范围"}</p>
                    <Show when={candidate.allowedActions.includes("open_editor") && candidate.source === "media_library" && candidate.assetId}>
                      <button type="button" onClick={() => props.onOpenEditor?.(candidate, fragment)}>以此范围打开剪辑</button>
                    </Show>
                  </section>}</For>
                </div>
              </Show>
            </div>
          </article>
        }</For>
      </div>
    </Show>
  </section>;
}
