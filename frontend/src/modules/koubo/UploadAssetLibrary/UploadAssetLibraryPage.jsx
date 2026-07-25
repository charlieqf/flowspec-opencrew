import { Show, createEffect, createMemo, createSignal } from "solid-js";
import { getCachedKouboStoryboardDetail, kbApi, rememberKouboStoryboardDetail } from "../KouboStoryBoard/kouboStoryboardApi.js";
import { assetKind } from "../KouboStoryBoard/kouboStoryboardAssets.js";
import ImagePreview from "../KouboStoryBoard/components/ImagePreview.jsx";
import UploadAssetLibraryOverlay from "./UploadAssetLibraryOverlay.jsx";

const LIBRARY_VIEWS = new Set(["images", "images-agent", "videos", "videos-agent", "tts-agent", "digital-human-agent", "prompt-agent", "search-agent", "history"]);

function taskIdFromHash(hash) {
  const match = String(hash || "").match(/^#\/koubo-asset-library\/tasks\/(\d+)/);
  return match ? Number(match[1]) : 0;
}

function viewFromHash(hash) {
  const match = String(hash || "").match(/^#\/koubo-asset-library\/tasks\/\d+\/([^/?#]+)/);
  const view = match ? decodeURIComponent(match[1]) : "";
  return LIBRARY_VIEWS.has(view) ? view : "";
}

export default function UploadAssetLibraryPage(props) {
  const [state, setState] = createSignal(null);
  const [busy, setBusy] = createSignal("");
  const [error, setError] = createSignal("");
  const [previewAsset, setPreviewAsset] = createSignal(null);
  const taskId = createMemo(() => taskIdFromHash(props.routeHash));
  const routeView = createMemo(() => viewFromHash(props.routeHash));
  const task = createMemo(() => state()?.task || null);
  const meta = createMemo(() => state()?.meta || {});
  const plan = createMemo(() => state()?.plan || null);
  const sessionId = createMemo(() => Number(task()?.session_id || meta()?.analysis_session_id || 0));
  const manualAssets = createMemo(() => Array.isArray(meta()?.manual_assets) ? meta().manual_assets : []);
  const uploadedImages = createMemo(() => Array.isArray(meta()?.uploaded_images) ? meta().uploaded_images : manualAssets().filter((item) => assetKind(item) === "image"));
  const uploadedAudios = createMemo(() => Array.isArray(meta()?.uploaded_audios) ? meta().uploaded_audios : manualAssets().filter((item) => assetKind(item) === "audio"));
  const uploadedVideos = createMemo(() => Array.isArray(meta()?.uploaded_videos) ? meta().uploaded_videos : manualAssets().filter((item) => assetKind(item) === "video"));
  const historyVersions = createMemo(() => Array.isArray(meta()?.history_versions) ? meta().history_versions : []);

  createEffect(() => {
    const id = taskId();
    if (!id) {
      setError("Missing Asset Library task id.");
      return;
    }
    const cached = getCachedKouboStoryboardDetail(id);
    if (cached) setState({ task: cached.task, meta: cached.meta, plan: cached.plan });
    setBusy(cached ? "refresh" : "load");
    setError("");
    kbApi.detail(id)
      .then((result) => setState({ task: result.task, meta: result.meta, plan: result.plan }))
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
      .finally(() => setBusy(""));
  });

  function applyAssetLibraryResult(result) {
    if (!result?.task || !result?.meta) return;
    rememberKouboStoryboardDetail(result);
    setState({ task: result.task, meta: result.meta, plan: result.plan || state()?.plan || null });
  }

  function closePage() {
    const id = taskId();
    window.location.hash = id ? `#/koubo-storyboard/tasks/${id}` : "#/koubo-storyboard/tasks";
  }

  function openPreview(asset) {
    if (!asset) return;
    setPreviewAsset(asset);
  }

  return <div class="ual-page">
    <Show when={error()}><div class="banner bad">{error()}</div></Show>
    <Show when={busy() === "load" && !state()}><div class="kbsp-empty">Loading Asset Library...</div></Show>
    <Show when={state()}>
      <UploadAssetLibraryOverlay
        mode="page"
        task={task}
        meta={meta}
        plan={plan}
        sessionId={sessionId}
        images={uploadedImages}
        videos={uploadedVideos}
        audios={uploadedAudios}
        sourceImages={() => []}
        sourceAssetGroups={() => []}
        historyVersions={historyVersions}
        routeView={routeView}
        onAssetLibraryResult={applyAssetLibraryResult}
        onClose={closePage}
        onPreview={openPreview}
      />
      <ImagePreview image={previewAsset} setImage={setPreviewAsset} sessionId={sessionId} />
    </Show>
  </div>;
}
