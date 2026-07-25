import { For, Show, createEffect, createMemo, createSignal } from "solid-js";
import { CloseIcon, EditIcon, ImageIcon, PlayClipIcon, SaveIcon } from "../analysisV1Icons.jsx";
import { formatSeconds } from "../analysisV1Model";

function pushPart(parts, text, changed) {
  if (!text) return;
  const previous = parts[parts.length - 1];
  if (previous && previous.changed === changed) {
    previous.text += text;
    return;
  }
  parts.push({ text, changed });
}

function diffText(leftText, rightText) {
  const left = Array.from(String(leftText || ""));
  const right = Array.from(String(rightText || ""));
  const rows = left.length + 1;
  const cols = right.length + 1;
  const table = Array.from({ length: rows }, () => Array(cols).fill(0));
  for (let i = left.length - 1; i >= 0; i -= 1) {
    for (let j = right.length - 1; j >= 0; j -= 1) {
      table[i][j] = left[i] === right[j] ? table[i + 1][j + 1] + 1 : Math.max(table[i + 1][j], table[i][j + 1]);
    }
  }
  const leftParts = [];
  const rightParts = [];
  let i = 0;
  let j = 0;
  while (i < left.length || j < right.length) {
    if (i < left.length && j < right.length && left[i] === right[j]) {
      pushPart(leftParts, left[i], false);
      pushPart(rightParts, right[j], false);
      i += 1;
      j += 1;
    } else if (j < right.length && (i >= left.length || table[i][j + 1] >= table[i + 1]?.[j])) {
      pushPart(rightParts, right[j], true);
      j += 1;
    } else if (i < left.length) {
      pushPart(leftParts, left[i], true);
      i += 1;
    }
  }
  return { leftParts, rightParts };
}

export function DiffText(props) {
  const parts = createMemo(() => diffText(props.original, props.rewritten));
  const sideParts = createMemo(() => props.side === "rewrite" ? parts().rightParts : parts().leftParts);
  return <span class="analysis-v1-diff-text">
    <For each={sideParts()}>{(part) => <span class={part.changed ? "analysis-v1-diff-changed" : ""}>{part.text}</span>}</For>
  </span>;
}

function SegmentVideo(props) {
  let videoEl;
  createEffect(() => {
    const item = props.item;
    if (!videoEl || !item) return;
    const start = Number(item.start);
    if (Number.isFinite(start)) videoEl.currentTime = start;
  });
  const onPlay = () => {
    const start = Number(props.item?.start);
    if (Number.isFinite(start) && videoEl && Math.abs(videoEl.currentTime - start) > 0.25) {
      videoEl.currentTime = start;
    }
  };
  const onTimeUpdate = () => {
    const end = Number(props.item?.end);
    if (videoEl && Number.isFinite(end) && videoEl.currentTime >= end) videoEl.pause();
  };
  return <video ref={videoEl} class="analysis-v1-video" controls preload="metadata" src={props.item?.videoUrl || ""} onPlay={onPlay} onTimeUpdate={onTimeUpdate} />;
}

export function AnalysisV1MediaSidebar(props) {
  return <section class="panel analysis-v1-media-sidebar">
    <Show when={props.item} fallback={<div class="analysis-v1-empty">选择一句 SRT 后查看媒体</div>}>
      <div class="analysis-v1-preview-card">
        <div class="analysis-v1-preview-meta">
          <strong>{props.item.id}</strong>
          <span>{formatSeconds(props.item.start)} - {formatSeconds(props.item.end)}</span>
        </div>
        <p>{props.item.originalDialogue || props.item.dialogue}</p>
        <p>{props.item.rewrittenDialogue || "未生成改写"}</p>
      </div>
      <div class="analysis-v1-media-card">
        <div class="analysis-v1-preview-title"><PlayClipIcon /><span>原视频片段</span></div>
        <SegmentVideo item={props.item} />
      </div>
      <div class="analysis-v1-media-card">
        <div class="analysis-v1-preview-title"><ImageIcon /><span>最终帧图</span></div>
        <Show when={props.item.imageUrl} fallback={<div class="analysis-v1-image-missing">无图片</div>}>
          <img class="analysis-v1-frame" src={props.item.imageUrl} alt={props.item.id} />
        </Show>
      </div>
    </Show>
  </section>;
}

export default function AnalysisV1DialogueView(props) {
  const [selectedId, setSelectedId] = createSignal("");
  createEffect(() => {
    const items = props.items || [];
    if (!items.length) {
      setSelectedId("");
      return;
    }
    if (!items.some((item) => item.id === selectedId())) setSelectedId(items[0].id);
  });
  const selected = createMemo(() => (props.items || []).find((item) => item.id === selectedId()) || (props.items || [])[0]);
  createEffect(() => props.onSelectedChange?.(selected() || null));

  return <section class="analysis-v1-panel analysis-v1-output-panel">
    <Show when={(props.items || []).length > 0} fallback={<div class="analysis-v1-empty">还没有读取到 SessionOutput/subtitle/final_srt_frame_items.json</div>}>
      <div class="analysis-v1-dialogue-layout">
        <div class="analysis-v1-dialogue-compare">
          <div class="analysis-v1-dialogue-compare-head">
            <span>原 SRT</span>
            <span>改写 SRT</span>
            <div class="analysis-v1-dialogue-toolbar">
              <span>{props.saveStatus || ""}</span>
              <div class="analysis-v1-dialogue-actions">
                <button
                  class="analysis-v1-dialogue-icon-button"
                  type="button"
                  title={props.editing ? "取消编辑" : "编辑改写 SRT"}
                  aria-label={props.editing ? "取消编辑" : "编辑改写 SRT"}
                  disabled={props.saving}
                  onClick={() => props.editing ? props.onCancelEdit?.() : props.onStartEdit?.()}
                >
                  <Show when={props.editing} fallback={<EditIcon />}>
                    <CloseIcon />
                  </Show>
                </button>
                <button
                  class="analysis-v1-dialogue-icon-button primary"
                  type="button"
                  title="保存改写 SRT"
                  aria-label="保存改写 SRT"
                  disabled={!props.editing || props.saving}
                  onClick={() => props.onSaveEdit?.()}
                >
                  <SaveIcon />
                </button>
              </div>
            </div>
          </div>
          <div class="analysis-v1-dialogue-list">
            <For each={props.items}>{(item, index) => (
              <Show
                when={props.editing}
                fallback={
                  <button type="button" class={`analysis-v1-dialogue-row ${selectedId() === item.id ? "is-active" : ""}`} onClick={() => setSelectedId(item.id)}>
                    <span class="analysis-v1-dialogue-index">{index() + 1}</span>
                    <p class="analysis-v1-dialogue-text">
                      <DiffText original={item.originalDialogue || item.dialogue} rewritten={item.rewrittenDialogue || ""} side="original" />
                    </p>
                    <p class="analysis-v1-dialogue-text">
                      <Show when={item.rewrittenDialogue} fallback={<span class="analysis-v1-dialogue-missing">未生成改写</span>}>
                        <DiffText original={item.originalDialogue || item.dialogue} rewritten={item.rewrittenDialogue || ""} side="rewrite" />
                      </Show>
                    </p>
                    <em>{formatSeconds(item.start)} - {formatSeconds(item.end)}</em>
                  </button>
                }
              >
                <div class={`analysis-v1-dialogue-row ${selectedId() === item.id ? "is-active" : ""}`} onClick={() => setSelectedId(item.id)}>
                  <span class="analysis-v1-dialogue-index">{index() + 1}</span>
                  <p class="analysis-v1-dialogue-text">
                    <DiffText original={item.originalDialogue || item.dialogue} rewritten={props.drafts?.[item.id] ?? item.rewrittenDialogue ?? ""} side="original" />
                  </p>
                  <textarea
                    class="analysis-v1-dialogue-text analysis-v1-dialogue-edit"
                    value={props.drafts?.[item.id] ?? item.rewrittenDialogue ?? ""}
                    disabled={props.saving}
                    onInput={(event) => props.onDraftChange?.(item.id, event.currentTarget.value)}
                    onClick={(event) => event.stopPropagation()}
                  />
                  <em>{formatSeconds(item.start)} - {formatSeconds(item.end)}</em>
                </div>
              </Show>
            )}</For>
          </div>
        </div>
      </div>
    </Show>
  </section>;
}
