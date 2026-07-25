import { For, Show, createEffect, createMemo, createSignal } from "solid-js";
import { api } from "../../../../lib/api.ts";
import {
  MEDIA_LIBRARY_SEARCH_CAPABILITY_TEXT,
  MEDIA_LIBRARY_ZERO_RESULT_SUGGESTIONS,
  buildMediaLibraryEditorHash,
  candidateSupportsAction,
  formatSearchRange,
  mediaLibraryFragmentKindLabel,
  normalizeMediaLibrarySearchResponse,
  storyboardDialogueSearchContext,
} from "../../mediaLibrarySearchModel.js";
import {
  mediaLibraryCapabilityView,
  normalizeMediaLibraryCapabilities,
} from "../../../mediaLibrary/mediaLibraryCapabilities.js";
import { XIcon } from "../kouboStoryboardIcons.jsx";

function errorText(error) {
  const raw = error instanceof Error ? error.message : String(error || "");
  try {
    const payload = JSON.parse(raw);
    const detail = payload?.detail;
    return String(detail?.user_message || detail?.message || detail?.code || detail || raw);
  } catch {
    return raw || "素材检索失败，请稍后重试。";
  }
}

function importIdempotencyKey(taskId, assetId) {
  const token = globalThis.crypto?.randomUUID?.() || `${Date.now()}_${Math.random().toString(16).slice(2)}`;
  return `mlui_${taskId}_${String(assetId || "").replace(/[^A-Za-z0-9._:-]+/g, "_")}_${token}`.slice(0, 128);
}

function CandidatePreview(props) {
  const candidate = () => props.candidate;
  const previewing = () => props.previewCandidateId() === candidate().candidate_id;
  return <div class="kbsp-ml-search-preview">
    <Show when={previewing() && candidate().preview_url} fallback={
      <Show when={candidate().thumbnail_url} fallback={<div class="kbsp-ml-search-preview-empty">{candidate().candidate_kind === "derived_clip" ? "可复用片段" : "原视频"}</div>}>
        <img src={candidate().thumbnail_url} alt="" loading="lazy" />
      </Show>
    }>
      <video src={candidate().preview_url} poster={candidate().thumbnail_url || ""} controls autoplay playsinline preload="metadata" />
    </Show>
  </div>;
}

function SearchCandidateCard(props) {
  const candidate = () => props.candidate;
  const firstFragment = () => candidate().matched_fragments?.[0] || null;
  const matchedCount = () => candidate().matched_fragments?.length || 0;
  const canPreview = () => candidateSupportsAction(candidate(), "preview") && Boolean(candidate().preview_url);
  const canOpenEditor = () => candidateSupportsAction(candidate(), "open_editor") && Boolean(candidate().asset_id);
  const canImportOriginal = () => candidateSupportsAction(candidate(), "import_original") && Boolean(candidate().asset_id);
  const canImportClip = () => candidateSupportsAction(candidate(), "import_clip") && Boolean(candidate().source_clip_id);
  const imported = () => props.importedIds().has(candidate().candidate_id);
  const importBusy = () => props.importingId() === candidate().candidate_id;
  return <article class="kbsp-ml-search-card">
    <CandidatePreview candidate={candidate()} previewCandidateId={props.previewCandidateId} />
    <div class="kbsp-ml-search-card-body">
      <div class="kbsp-ml-search-card-title">
        <div>
          <strong>{candidate().display_name}</strong>
          <span>{candidate().candidate_kind === "derived_clip" ? "全局素材库 · 可复用片段" : `全局素材库 · 原视频 · ${matchedCount()} 个命中片段`}</span>
        </div>
        <Show when={candidate().score != null}><b>{Math.round(candidate().score * 100)}%</b></Show>
      </div>
      <div class="kbsp-ml-search-meta">
        <span>{candidate().orientation === "portrait" ? "竖屏" : candidate().orientation === "landscape" ? "横屏" : "方向未知"}</span>
        <Show when={candidate().duration_ms != null}><span>{candidate().candidate_kind === "derived_clip" ? "片段时长" : "总时长"} {formatSearchRange(0, candidate().duration_ms).split(" – ")[1]}</span></Show>
        <Show when={candidate().candidate_kind === "derived_clip" && candidate().tags?.length}><span>标签：{candidate().tags.join("、")}</span></Show>
      </div>
      <Show when={candidate().score_reasons?.length}>
        <div class="kbsp-ml-search-reasons">
          <For each={candidate().score_reasons}>{(reason) => <span>{reason}</span>}</For>
        </div>
      </Show>
      <div class="kbsp-ml-search-fragments">
        <Show when={candidate().matched_fragments?.length} fallback={<p>{candidate().candidate_kind === "derived_clip" ? "按人工名称和标签命中；预览时间使用片段本地坐标。" : "后端未返回可解释的命中范围。"}</p>}>
          <For each={candidate().matched_fragments}>{(fragment, index) => <section>
            <div class="kbsp-ml-search-fragment-head">
              <span>{mediaLibraryFragmentKindLabel(fragment)} {index() + 1}</span>
              <strong>{formatSearchRange(fragment.start_ms, fragment.end_ms)}</strong>
            </div>
            <Show when={fragment.dialogue_text || fragment.summary}>
              <p>{fragment.dialogue_text || fragment.summary}</p>
            </Show>
            <Show when={canOpenEditor()}>
              <button type="button" onClick={() => props.openEditor(candidate(), fragment)}>剪切这个片段</button>
            </Show>
          </section>}</For>
        </Show>
      </div>
      <div class="kbsp-ml-search-card-actions">
        <Show when={canPreview()}>
          <button type="button" onClick={() => props.setPreviewCandidateId(props.previewCandidateId() === candidate().candidate_id ? "" : candidate().candidate_id)}>
            {props.previewCandidateId() === candidate().candidate_id ? "关闭预览" : candidate().candidate_kind === "derived_clip" ? "预览片段" : "预览原视频"}
          </button>
        </Show>
        <Show when={canOpenEditor()}>
          <button type="button" onClick={() => props.openEditor(candidate(), firstFragment())}>打开剪辑（首个命中）</button>
        </Show>
        <Show when={canImportOriginal()}>
          <button class="is-primary" type="button" disabled={importBusy() || imported()} onClick={() => props.importCandidate(candidate())}>
            {importBusy() ? "正在加入整条视频..." : imported() ? "整条视频已加入当前 Task" : "加入当前 Task（整条视频）"}
          </button>
        </Show>
        <Show when={canImportClip()}>
          <button class="is-primary" type="button" disabled={importBusy() || imported()} onClick={() => props.importCandidate(candidate())}>
            {importBusy() ? "正在加入片段..." : imported() ? "片段已加入当前 Task" : "加入当前 Task"}
          </button>
        </Show>
      </div>
    </div>
  </article>;
}

export default function MediaLibrarySearchDialog(props) {
  const task = () => props.task?.();
  const dialogue = () => props.dialogue?.();
  const context = createMemo(() => storyboardDialogueSearchContext(task(), dialogue()));
  const [open, setOpen] = createSignal(false);
  const [userText, setUserText] = createSignal("");
  const [orientation, setOrientation] = createSignal("any");
  const [busy, setBusy] = createSignal(false);
  const [error, setError] = createSignal("");
  const [searchResult, setSearchResult] = createSignal(null);
  const [hasSearched, setHasSearched] = createSignal(false);
  const [previewCandidateId, setPreviewCandidateId] = createSignal("");
  const [importingId, setImportingId] = createSignal("");
  const [importedIds, setImportedIds] = createSignal(new Set());
  const [capabilities, setCapabilities] = createSignal(
    normalizeMediaLibraryCapabilities(null),
  );
  const capabilityView = createMemo(
    () => mediaLibraryCapabilityView(capabilities()),
  );
  let requestToken = 0;
  let previousContextKey;

  const resetSearch = () => {
    requestToken += 1;
    setOpen(false);
    setUserText("");
    setOrientation("any");
    setBusy(false);
    setError("");
    setSearchResult(null);
    setHasSearched(false);
    setPreviewCandidateId("");
    setImportingId("");
    setImportedIds(new Set());
  };

  createEffect(() => {
    const nextKey = context().key;
    if (previousContextKey !== undefined && previousContextKey !== nextKey) resetSearch();
    previousContextKey = nextKey;
  });

  createEffect(() => {
    const taskId = context().taskId;
    setCapabilities(normalizeMediaLibraryCapabilities(null));
    void api.mediaLibraryCapabilities()
      .then((payload) => {
        if (context().taskId === taskId) {
          setCapabilities(
            normalizeMediaLibraryCapabilities(payload),
          );
        }
      })
      .catch(() => {
        if (context().taskId === taskId) {
          setCapabilities(normalizeMediaLibraryCapabilities(null));
        }
      });
  });

  createEffect(() => {
    if (!capabilityView().searchEntryVisible) setOpen(false);
  });

  const runSearch = async () => {
    const snapshot = context();
    if (!snapshot.enabled || busy()) return;
    const token = ++requestToken;
    setBusy(true);
    setError("");
    setSearchResult(null);
    setHasSearched(false);
    setPreviewCandidateId("");
    try {
      const payload = await api.storyboardMediaLibrarySearchRun(snapshot.taskId, snapshot.dialogueAssetKey, {
        user_text: userText().trim(),
        orientation: orientation(),
        limit: 12,
      });
      if (token !== requestToken || context().key !== snapshot.key) return;
      setSearchResult(normalizeMediaLibrarySearchResponse(payload));
      setHasSearched(true);
    } catch (err) {
      if (token !== requestToken || context().key !== snapshot.key) return;
      setError(errorText(err));
      setHasSearched(true);
    } finally {
      if (token === requestToken) setBusy(false);
    }
  };

  const openEditor = (candidate, fragment) => {
    const snapshot = context();
    const hash = buildMediaLibraryEditorHash({
      assetId: candidate?.asset_id,
      startMs: fragment?.start_ms,
      endMs: fragment?.end_ms,
      targetTaskId: snapshot.taskId,
      dialogueAssetKey: snapshot.dialogueAssetKey,
      searchId: searchResult()?.searchId,
      matchedFragmentId: fragment?.fragment_id,
    });
    if (hash) window.location.hash = hash;
  };

  const importCandidate = async (candidate) => {
    const snapshot = context();
    const sourceId = String(candidate?.candidate_kind === "derived_clip" ? candidate?.source_clip_id : candidate?.asset_id || "");
    const searchId = String(searchResult()?.searchId || "");
    if (!snapshot.enabled || !sourceId || !searchId || importingId()) return;
    setImportingId(String(candidate?.candidate_id || sourceId));
    setError("");
    try {
      const result = await api.storyboardMediaLibraryImport(snapshot.taskId, {
        source_kind: candidate?.candidate_kind === "derived_clip" ? "media_library_clip" : "media_library_original",
        source_id: sourceId,
        target_task_id: snapshot.taskId,
        requested_name: candidate?.display_name || "",
        search_id: searchId,
        dialogue_asset_key: snapshot.dialogueAssetKey,
        idempotency_key: importIdempotencyKey(snapshot.taskId, sourceId),
      });
      if (context().key !== snapshot.key) return;
      setImportedIds((current) => new Set([...current, String(candidate?.candidate_id || sourceId)]));
      try {
        await props.onImported?.(result);
      } catch (refreshError) {
        setError(`素材已加入当前 Task，但 Asset Pool 刷新失败：${errorText(refreshError)}`);
      }
    } catch (err) {
      if (context().key === snapshot.key) setError(errorText(err));
    } finally {
      if (context().key === snapshot.key) setImportingId("");
    }
  };

  return <>
    <Show when={capabilityView().searchEntryVisible}>
      <div class="kbsp-ml-search-entry">
        <button
          class="kbsp-ml-search-trigger"
          type="button"
          disabled={!context().enabled}
          aria-describedby={!context().enabled ? "kbsp-ml-search-disabled-reason" : undefined}
          title={context().enabled ? MEDIA_LIBRARY_SEARCH_CAPABILITY_TEXT : context().disabledReason}
          onClick={() => setOpen(true)}
        >
          检索素材
        </button>
        <Show when={!context().enabled}>
          <small id="kbsp-ml-search-disabled-reason" role="status">{context().disabledReason}</small>
        </Show>
      </div>
    </Show>
    <Show when={open()}>
      <div class="kbsp-ml-search-backdrop" onClick={() => setOpen(false)} />
      <section class="kbsp-ml-search-dialog" role="dialog" aria-modal="true" aria-label="检索全局素材库">
        <header>
          <div>
            <strong>检索全局素材库</strong>
            <span>当前对白：{context().dialogueText}</span>
          </div>
          <button type="button" aria-label="关闭检索素材" title="关闭" onClick={() => setOpen(false)}><XIcon /></button>
        </header>
        <div class="kbsp-ml-search-controls">
          <p>{MEDIA_LIBRARY_SEARCH_CAPABILITY_TEXT}</p>
          <label>
            <span>补充画面或关键词（可选）</span>
            <textarea rows="2" value={userText()} placeholder="例如：玻璃碗、产品近景、竖屏；留空则按当前对白检索" onInput={(event) => setUserText(event.currentTarget.value)} />
          </label>
          <label>
            <span>画幅</span>
            <select value={orientation()} onInput={(event) => setOrientation(event.currentTarget.value)}>
              <option value="any">不限</option>
              <option value="portrait">竖屏</option>
              <option value="landscape">横屏</option>
            </select>
          </label>
          <button class="is-primary" type="button" disabled={busy()} onClick={() => void runSearch()}>{busy() ? "正在检索..." : "开始检索"}</button>
        </div>
        <Show when={error()}><div class="kbsp-ml-search-message is-error">{error()}</div></Show>
        <Show when={searchResult()?.plannerDegraded}>
          <div class="kbsp-ml-search-message is-warning">查询规划暂时不可用，系统已使用对白原文和补充关键词继续完成确定性检索；结果仍可预览、导入和剪辑。</div>
        </Show>
        <div class="kbsp-ml-search-results">
          <Show when={searchResult()?.items?.length} fallback={
            <Show when={hasSearched() && !busy() && !error()}>
              <div class="kbsp-ml-search-zero">
                <strong>没有找到符合条件的素材</strong>
                <p>系统不会静默放宽四帧视觉分析、派生片段显式加入或父素材未归档等资格条件。你可以：</p>
                <ul><For each={MEDIA_LIBRARY_ZERO_RESULT_SUGGESTIONS}>{(item) => <li>{item}</li>}</For></ul>
              </div>
            </Show>
          }>
            <div class="kbsp-ml-search-result-head"><strong>素材结果</strong><span>{searchResult()?.items?.length || 0} 项素材</span></div>
            <For each={searchResult()?.items || []}>{(candidate) => <SearchCandidateCard
              candidate={candidate}
              previewCandidateId={previewCandidateId}
              setPreviewCandidateId={setPreviewCandidateId}
              importingId={importingId}
              importedIds={importedIds}
              openEditor={openEditor}
              importCandidate={importCandidate}
            />}</For>
          </Show>
        </div>
      </section>
    </Show>
  </>;
}
