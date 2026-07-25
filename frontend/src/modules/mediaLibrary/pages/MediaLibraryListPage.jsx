import { For, Show, createEffect, createMemo, createSignal, onCleanup } from "solid-js";
import { api } from "../../../lib/api.ts";
import MediaLibraryFilters from "../components/MediaLibraryFilters.jsx";
import MediaLibraryPagination from "../components/MediaLibraryPagination.jsx";
import MediaLibraryCardGrid from "../components/MediaLibraryCardGrid.jsx";
import MediaLibraryTable from "../components/MediaLibraryTable.jsx";
import MediaLibraryTagEditor from "../components/MediaLibraryTagEditor.jsx";
import MediaLibraryViewControls from "../components/MediaLibraryViewControls.jsx";
import MediaPreviewDrawer from "../components/MediaPreviewDrawer.jsx";
import { VideoLibraryGlyph } from "../components/MediaLibraryIcons.jsx";
import MediaLibraryUploadDialog from "../upload/MediaLibraryUploadDialog.jsx";
import MediaLibraryDeleteDialog from "../delete/MediaLibraryDeleteDialog.jsx";
import { mediaLibraryHasQueryFilters, resetMediaLibraryFilters } from "../mediaLibraryFilterModel.js";
import {
  mediaLibraryApiParams,
  mediaLibraryListHash,
  mediaLibraryQueryFromHash,
  normalizeMediaAsset,
} from "../mediaLibraryModel.js";
import {
  mediaLibraryPatchErrorMessage,
  readMediaLibraryViewPreferences,
  saveMediaLibraryCardColumns,
  saveMediaLibraryViewMode,
} from "../mediaLibraryViewModel.js";

export default function MediaLibraryListPage(props) {
  const initialViewPreferences = readMediaLibraryViewPreferences();
  const [items, setItems] = createSignal([]);
  const [total, setTotal] = createSignal(0);
  const [filters, setFilters] = createSignal(mediaLibraryQueryFromHash(props.routeHash));
  const [availableTags, setAvailableTags] = createSignal([]);
  const [busy, setBusy] = createSignal(true);
  const [error, setError] = createSignal("");
  const [previewAsset, setPreviewAsset] = createSignal(null);
  const [openMenuId, setOpenMenuId] = createSignal("");
  const [uploadOpen, setUploadOpen] = createSignal(false);
  const [deleteAsset, setDeleteAsset] = createSignal(null);
  const [deleteBusy, setDeleteBusy] = createSignal(false);
  const [deleteError, setDeleteError] = createSignal("");
  const [viewMode, setViewMode] = createSignal(initialViewPreferences.viewMode);
  const [cardColumns, setCardColumns] = createSignal(initialViewPreferences.cardColumns);
  const [tagAsset, setTagAsset] = createSignal(null);
  const [tagSaveBusy, setTagSaveBusy] = createSignal(false);
  const [tagSaveError, setTagSaveError] = createSignal("");
  let requestId = 0;
  let hashTimer = null;

  const closeMenus = (event) => {
    if (event?.target?.closest?.(".media-library-more-wrap")) return;
    setOpenMenuId("");
  };
  document.addEventListener("click", closeMenus);
  onCleanup(() => {
    document.removeEventListener("click", closeMenus);
    if (hashTimer) window.clearTimeout(hashTimer);
  });

  async function load(nextFilters) {
    const currentRequest = ++requestId;
    setBusy(true);
    setError("");
    try {
      const payload = await api.mediaLibraryList(mediaLibraryApiParams(nextFilters));
      if (currentRequest !== requestId) return;
      const nextItems = (payload.items || []).map(normalizeMediaAsset).filter((item) => item.assetId);
      setItems(nextItems);
      setTotal(Number(payload.total ?? nextItems.length));
      const facetTags = payload.facets?.tags || nextItems.flatMap((item) => item.tags);
      setAvailableTags([...new Set(facetTags.map((item) => String(item || "").trim()).filter(Boolean))].sort((a, b) => a.localeCompare(b, "zh-CN")));
    } catch (err) {
      if (currentRequest === requestId) setError(err instanceof Error ? err.message : "加载素材库失败");
    } finally {
      if (currentRequest === requestId) setBusy(false);
    }
  }

  createEffect(() => {
    const next = mediaLibraryQueryFromHash(props.routeHash);
    setFilters(next);
    void load(next);
  });

  function updateFilters(patch, debounce = false) {
    const next = { ...filters(), ...patch, page: patch.page ?? 1 };
    setFilters(next);
    if (hashTimer) window.clearTimeout(hashTimer);
    const commit = () => {
      const nextHash = mediaLibraryListHash(next);
      if (window.location.hash !== nextHash) window.location.hash = nextHash;
      else void load(next);
    };
    if (debounce) hashTimer = window.setTimeout(commit, 300);
    else commit();
  }

  function clearFilters() {
    updateFilters({ ...resetMediaLibraryFilters(filters()), q: "", pageSize: filters().pageSize });
  }

  async function patchAsset(asset, input, successMessage = "") {
    setError("");
    try {
      await api.mediaLibraryUpdate(asset.assetId, input);
      if (successMessage) window.dispatchEvent(new CustomEvent("opencrew:notice", { detail: successMessage }));
      await load(filters());
    } catch (err) {
      setError(mediaLibraryPatchErrorMessage(err));
    }
  }

  function renameAsset(asset) {
    setOpenMenuId("");
    const value = window.prompt("输入新的素材名称", asset.displayName);
    if (value === null || !value.trim() || value.trim() === asset.displayName) return;
    void patchAsset(asset, { display_name: value.trim() });
  }

  function editTags(asset) {
    setOpenMenuId("");
    setTagSaveError("");
    setTagAsset(asset);
  }

  function closeTagEditor() {
    if (tagSaveBusy()) return;
    setTagAsset(null);
    setTagSaveError("");
  }

  async function saveTags(asset, tags) {
    if (!asset || tagSaveBusy()) return;
    setTagSaveBusy(true);
    setTagSaveError("");
    try {
      const payload = await api.mediaLibraryUpdate(asset.assetId, { tags });
      const updated = normalizeMediaAsset(payload.item || {});
      setItems((current) => current.map((item) => item.assetId === updated.assetId ? updated : item));
      setTagAsset(null);
      window.dispatchEvent(new CustomEvent("opencrew:notice", { detail: "素材标签已保存" }));
      await load(filters());
    } catch (err) {
      setTagSaveError(mediaLibraryPatchErrorMessage(err, "保存标签失败"));
    } finally {
      setTagSaveBusy(false);
    }
  }

  function changeViewMode(nextMode) {
    setOpenMenuId("");
    setViewMode(saveMediaLibraryViewMode(nextMode));
  }

  function changeCardColumns(nextColumns) {
    setCardColumns(saveMediaLibraryCardColumns(nextColumns));
  }

  async function lifecycleAction(asset, kind) {
    setOpenMenuId("");
    if (kind === "archive" && !window.confirm(`确认归档“${asset.displayName}”？`)) return;
    setError("");
    try {
      if (kind === "archive") await api.mediaLibraryArchive(asset.assetId);
      else await api.mediaLibraryRestore(asset.assetId);
      await load(filters());
    } catch (err) {
      setError(err instanceof Error ? err.message : "更新素材失败");
    }
  }

  function requestDelete(asset) {
    setOpenMenuId("");
    setDeleteError("");
    setDeleteAsset(asset);
  }

  function closeDeleteDialog() {
    if (deleteBusy()) return;
    setDeleteAsset(null);
    setDeleteError("");
  }

  async function confirmDelete(asset) {
    if (!asset || deleteBusy() || asset.referencedByCount) return;
    const sessionText = asset.sessionId ? `Session #${asset.sessionId}` : "对应 Session";
    setDeleteBusy(true);
    setDeleteError("");
    try {
      await api.mediaLibraryDelete(asset.assetId);
      setDeleteAsset(null);
      window.dispatchEvent(new CustomEvent("opencrew:notice", { detail: `素材“${asset.displayName}”及 ${sessionText} 已删除` }));
      await load(filters());
    } catch (err) {
      setDeleteError(err instanceof Error ? err.message : "删除素材失败");
    } finally {
      setDeleteBusy(false);
    }
  }

  const noFilters = createMemo(() => !mediaLibraryHasQueryFilters(filters()));
  async function handleUploadComplete() {
    window.dispatchEvent(new CustomEvent("opencrew:notice", { detail: "素材上传完成" }));
    await load(filters());
  }
  const emptyFallback = () => (
    <div class="media-library-empty-state">
      <div class="media-library-empty-icon"><VideoLibraryGlyph /></div>
      <h3>{noFilters() ? "素材库还是空的" : "没有找到符合条件的素材"}</h3>
      <p>{noFilters() ? "以后加入的视频会统一显示在这里，方便检索、分析和挑选片段。" : "请尝试修改关键词或清除筛选条件。"}</p>
      <Show when={!noFilters()}><button type="button" onClick={clearFilters}>清除筛选</button></Show>
    </div>
  );

  return (
    <div class="media-library-page media-library-list-page">
      <header class="media-library-page-header"><div class="media-library-title-row"><h2>素材库</h2><span>共 {total()} 条视频</span></div><button type="button" class="media-library-upload-button" onClick={() => setUploadOpen(true)}>上传素材</button></header>
      <Show when={error()}><div class="media-library-banner bad"><span>{error()}</span><button type="button" onClick={() => void load(filters())}>重新加载</button></div></Show>
      <MediaLibraryFilters filters={filters()} tags={availableTags()} onSearch={(q) => updateFilters({ q }, true)} onApply={(next) => updateFilters(next)} />
      <MediaLibraryViewControls viewMode={viewMode()} cardColumns={cardColumns()} onViewMode={changeViewMode} onCardColumns={changeCardColumns} />
      <div class="media-library-list-region">
        <Show when={busy() && !items().length} fallback={
          <Show when={viewMode() === "cards"} fallback={<MediaLibraryTable items={items()} emptyFallback={emptyFallback()} openMenuId={openMenuId()} onToggleMenu={(assetId) => setOpenMenuId((current) => current === assetId ? "" : assetId)} onPreview={setPreviewAsset} onRename={renameAsset} onEditTags={editTags} onArchive={(asset) => void lifecycleAction(asset, "archive")} onRestore={(asset) => void lifecycleAction(asset, "restore")} onDelete={requestDelete} />}>
            <MediaLibraryCardGrid items={items()} cardColumns={cardColumns()} emptyFallback={emptyFallback()} openMenuId={openMenuId()} onToggleMenu={(assetId) => setOpenMenuId((current) => current === assetId ? "" : assetId)} onPreview={setPreviewAsset} onRename={renameAsset} onEditTags={editTags} onArchive={(asset) => void lifecycleAction(asset, "archive")} onRestore={(asset) => void lifecycleAction(asset, "restore")} onDelete={requestDelete} />
          </Show>
        }>
          <div class="media-library-skeleton" aria-label="正在加载素材库"><For each={[1, 2, 3, 4, 5]}>{() => <div><span/><span/><span/><span/></div>}</For></div>
        </Show>
      </div>
      <Show when={busy() && items().length}><div class="media-library-refreshing">正在刷新…</div></Show>
      <MediaLibraryPagination total={total()} filters={filters()} onPage={(page) => updateFilters({ page })} onPageSize={(pageSize) => updateFilters({ pageSize, page: 1 })} />
      <MediaPreviewDrawer asset={previewAsset()} onClose={() => setPreviewAsset(null)} />
      <MediaLibraryUploadDialog open={uploadOpen()} onClose={() => setUploadOpen(false)} onComplete={handleUploadComplete} />
      <MediaLibraryDeleteDialog asset={deleteAsset()} busy={deleteBusy()} error={deleteError()} onClose={closeDeleteDialog} onConfirm={confirmDelete} />
      <Show when={tagAsset()}>{(asset) => <MediaLibraryTagEditor asset={asset()} suggestions={availableTags()} busy={tagSaveBusy()} error={tagSaveError()} onClose={closeTagEditor} onSave={(tags) => void saveTags(asset(), tags)} />}</Show>
    </div>
  );
}
