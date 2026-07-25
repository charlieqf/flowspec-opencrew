import { Show, createEffect, createMemo, createSignal, onCleanup } from "solid-js";
import { api } from "../../../lib/api.ts";
import { BackIcon } from "../components/MediaLibraryIcons.jsx";
import MediaLibraryAnalysisTabs from "../detail/MediaLibraryAnalysisTabs.jsx";
import MediaLibraryAssetInfo from "../detail/MediaLibraryAssetInfo.jsx";
import MediaLibraryCompositePanel from "../detail/MediaLibraryCompositePanel.jsx";
import MediaLibraryDetailHeader from "../detail/MediaLibraryDetailHeader.jsx";
import MediaLibraryFragmentList from "../detail/MediaLibraryFragmentList.jsx";
import MediaLibraryToolDrawer from "../detail/MediaLibraryToolDrawer.jsx";
import MediaLibraryVisualPanel from "../detail/MediaLibraryVisualPanel.jsx";
import MediaLibraryVisualSemanticPanel from "../detail/MediaLibraryVisualSemanticPanel.jsx";
import {
  compositeRunState,
  normalizeCompositeCurrent,
  normalizeOpenCutDetail,
  normalizeVisualCurrent,
  resolveCompositeDisplayResult,
  resolveVisualDisplayResult,
  visualSemanticRunState,
} from "../detail/mediaLibraryDetailModel.js";
import {
  mediaLibraryCapabilityView,
  normalizeMediaLibraryCapabilities,
} from "../mediaLibraryCapabilities.js";
import { mediaLibraryAssetCanOpenEditor, mediaOrientation, normalizeMediaAsset } from "../mediaLibraryModel.js";

export default function MediaLibraryDetailPage(props) {
  const [asset, setAsset] = createSignal(null);
  const [openCut, setOpenCut] = createSignal(null);
  const [busy, setBusy] = createSignal(true);
  const [error, setError] = createSignal("");
  const [activeTab, setActiveTab] = createSignal("dialogue");
  const [selectedId, setSelectedId] = createSignal("");
  const [infoExpanded, setInfoExpanded] = createSignal(false);
  const [drawerScheme, setDrawerScheme] = createSignal("");
  const [runBusy, setRunBusy] = createSignal(false);
  const [runError, setRunError] = createSignal("");
  const [allowCloudAsrDataTransfer, setAllowCloudAsrDataTransfer] = createSignal(false);
  const [visualCurrent, setVisualCurrent] = createSignal(null);
  const [visualCurrentLoadError, setVisualCurrentLoadError] = createSignal("");
  const [visualSemanticRunBusy, setVisualSemanticRunBusy] = createSignal(false);
  const [visualSemanticRunError, setVisualSemanticRunError] = createSignal("");
  const [allowCloudVisualDataTransfer, setAllowCloudVisualDataTransfer] = createSignal(false);
  const [compositeCurrent, setCompositeCurrent] = createSignal(null);
  const [compositeCurrentLoadError, setCompositeCurrentLoadError] = createSignal("");
  const [compositeRunBusy, setCompositeRunBusy] = createSignal(false);
  const [compositeRunError, setCompositeRunError] = createSignal("");
  const [capabilities, setCapabilities] = createSignal(
    normalizeMediaLibraryCapabilities(null),
  );
  const capabilityView = createMemo(
    () => mediaLibraryCapabilityView(capabilities()),
  );

  const loadDetail = async (assetId, showBusy = false) => {
    if (showBusy) setBusy(true);
    setError("");
    setVisualCurrentLoadError("");
    setCompositeCurrentLoadError("");
    const [detailResult, visualCurrentResult, compositeCurrentResult] = await Promise.allSettled([
      api.mediaLibraryDetail(assetId),
      api.mediaLibraryVisualCurrent(assetId),
      api.mediaLibraryCompositeCurrent(assetId),
    ]);
    if (detailResult.status === "fulfilled") {
      const raw = detailResult.value.item || detailResult.value;
      const normalized = normalizeMediaAsset(raw);
      setAsset(normalized);
      setOpenCut(normalizeOpenCutDetail(raw, normalized));
    } else {
      const err = detailResult.reason;
      setError(err instanceof Error ? err.message : "加载素材详情失败");
    }
    if (visualCurrentResult.status === "fulfilled") {
      setVisualCurrent(normalizeVisualCurrent(visualCurrentResult.value));
    } else {
      const err = visualCurrentResult.reason;
      setVisualCurrent(null);
      setVisualCurrentLoadError(err instanceof Error ? err.message : "视觉语义 current 结果读取失败");
    }
    if (compositeCurrentResult.status === "fulfilled") {
      setCompositeCurrent(normalizeCompositeCurrent(compositeCurrentResult.value));
    } else {
      const err = compositeCurrentResult.reason;
      setCompositeCurrent(null);
      setCompositeCurrentLoadError(err instanceof Error ? err.message : "综合分析 current 结果读取失败");
    }
    if (showBusy) setBusy(false);
  };

  createEffect(() => {
    const assetId = props.assetId;
    setCapabilities(normalizeMediaLibraryCapabilities(null));
    void api.mediaLibraryCapabilities()
      .then((payload) => {
        if (props.assetId === assetId) {
          setCapabilities(
            normalizeMediaLibraryCapabilities(payload),
          );
        }
      })
      .catch(() => {
        if (props.assetId === assetId) {
          setCapabilities(normalizeMediaLibraryCapabilities(null));
        }
      });
    setAllowCloudVisualDataTransfer(false);
    setVisualSemanticRunError("");
    setCompositeRunError("");
    void loadDetail(assetId, true);
  });

  createEffect(() => {
    const visual = openCut()?.schemes?.visual;
    const active = ["queued", "running"].includes(openCut()?.schemes?.dialogue?.status)
      || ["queued", "running"].includes(visual?.status)
      || ["queued", "running"].includes(visual?.structureStatus)
      || ["queued", "running"].includes(visual?.semanticStatus)
      || ["queued", "running"].includes(openCut()?.schemes?.composite?.status);
    if (!active) return;
    const timer = window.setInterval(() => loadDetail(props.assetId), 1500);
    onCleanup(() => window.clearInterval(timer));
  });

  const runTool = async (schemeId) => {
    if (
      !capabilityView().analysisActionsEnabled
      || !["dialogue", "visual"].includes(schemeId)
    ) return;
    setRunBusy(true);
    setRunError("");
    try {
      if (schemeId === "dialogue") {
        const force = ["blocked", "ready", "stale", "failed"].includes(openCut()?.schemes?.dialogue?.status);
        await api.mediaLibraryRunDialogue(props.assetId, {
          force,
          allow_cloud_asr_data_transfer: allowCloudAsrDataTransfer(),
        });
      }
      else await api.mediaLibraryRunVisual(props.assetId, {
        force_structure: ["ready", "stale", "failed"].includes(openCut()?.schemes?.visual?.structureStatus),
        force_semantic: false,
        allow_cloud_visual_data_transfer: false,
      });
      await loadDetail(props.assetId);
    } catch (err) {
      setRunError(err instanceof Error ? err.message : `启动${schemeId === "visual" ? "画面" : "对白"}分析失败`);
    } finally {
      setRunBusy(false);
    }
  };

  const runVisualSemantic = async () => {
    if (!capabilityView().visualSemanticActionEnabled) return;
    const visual = openCut()?.schemes?.visual;
    const state = visualSemanticRunState(visual, allowCloudVisualDataTransfer());
    if (!state.runnable) return;
    setVisualSemanticRunBusy(true);
    setVisualSemanticRunError("");
    try {
      await api.mediaLibraryRunVisual(props.assetId, {
        force_structure: false,
        force_semantic: state.retry,
        allow_cloud_visual_data_transfer: allowCloudVisualDataTransfer(),
      });
      setAllowCloudVisualDataTransfer(false);
      await loadDetail(props.assetId);
    } catch (err) {
      setVisualSemanticRunError(err instanceof Error ? err.message : "启动视觉语义分析失败");
    } finally {
      setVisualSemanticRunBusy(false);
    }
  };

  const runComposite = async () => {
    if (!capabilityView().compositeActionEnabled) return;
    const state = compositeRunState(openCut());
    if (!state.runnable) return;
    setCompositeRunBusy(true);
    setCompositeRunError("");
    try {
      await api.mediaLibraryRunComposite(props.assetId, state.retry);
      await loadDetail(props.assetId);
    } catch (err) {
      setCompositeRunError(err instanceof Error ? err.message : "启动综合分析失败");
    } finally {
      setCompositeRunBusy(false);
    }
  };

  const activeResult = createMemo(() => {
    const result = openCut()?.schemes?.[activeTab()] || { items: [], error: "" };
    if (activeTab() === "visual") return resolveVisualDisplayResult(result, visualCurrent());
    if (activeTab() === "composite") return resolveCompositeDisplayResult(result, compositeCurrent());
    return result;
  });
  const selectedItem = createMemo(() => activeResult().items.find((item) => item.id === selectedId()) || activeResult().items[0] || null);

  createEffect(() => {
    const items = activeResult().items;
    if (!items.some((item) => item.id === selectedId())) setSelectedId(items[0]?.id || "");
  });

  return <div class="media-library-page media-library-detail-page">
    <button type="button" class="media-library-back" onClick={() => { window.location.hash = "#/media-library"; }}><BackIcon />返回素材库</button>
    <Show when={error()}><div class="media-library-banner bad"><span>{error()}</span><button type="button" onClick={() => { window.location.hash = "#/media-library"; }}>返回列表</button></div></Show>
    <Show when={busy()}><div class="media-library-detail-loading">正在加载素材详情…</div></Show>
    <Show when={asset() && openCut()}>{() => <>
      <div class="media-library-detail-shell">
        <main class="media-library-detail-main">
          <MediaLibraryDetailHeader
            asset={asset()}
            openCut={openCut()}
            canOpenEditor={mediaLibraryAssetCanOpenEditor(asset())}
            analysisRunsEnabled={capabilityView().analysisActionsEnabled}
            compositeEnabled={capabilityView().compositeActionEnabled}
            editorEntryVisible={capabilityView().editorEntryVisible}
            infoExpanded={infoExpanded()}
            onToggleInfo={() => setInfoExpanded((value) => !value)}
            onOpenTool={(schemeId) => { setDrawerScheme(schemeId); setAllowCloudAsrDataTransfer(false); }}
            onOpenEditor={() => { window.location.hash = `#/media-library/${encodeURIComponent(asset().assetId)}/editor?return_to=media_library_detail`; }}
          />
          <MediaLibraryAssetInfo
            asset={asset()}
            openCut={openCut()}
            visualRun={visualCurrent()?.run}
            compositeRun={compositeCurrent()?.run}
            expanded={infoExpanded()}
          />
          <section class="media-library-analysis-workspace">
            <MediaLibraryAnalysisTabs activeTab={activeTab()} openCut={openCut()} onChange={setActiveTab} />
            <Show when={activeTab() === "visual"}>
              <MediaLibraryVisualSemanticPanel
                visual={openCut().schemes.visual}
                current={visualCurrent()}
                currentLoadError={visualCurrentLoadError()}
                runBusy={visualSemanticRunBusy()}
                runError={visualSemanticRunError()}
                allowCloudVisualDataTransfer={allowCloudVisualDataTransfer()}
                onAllowCloudVisualDataTransferChange={setAllowCloudVisualDataTransfer}
                onRun={() => void runVisualSemantic()}
                featureEnabled={capabilityView().visualSemanticActionEnabled}
              />
            </Show>
            <Show when={activeTab() === "composite"}>
              <MediaLibraryCompositePanel
                openCut={openCut()}
                current={compositeCurrent()}
                currentLoadError={compositeCurrentLoadError()}
                runBusy={compositeRunBusy()}
                runError={compositeRunError()}
                onRun={() => void runComposite()}
                featureEnabled={capabilityView().compositeActionEnabled}
              />
            </Show>
            <div class="media-library-analysis-layout">
              <MediaLibraryFragmentList schemeId={activeTab()} items={activeResult().items} error={activeResult().error} errorCode={activeResult().errorCode} selectedId={selectedItem()?.id || ""} onSelect={setSelectedId} />
            </div>
          </section>
        </main>
        <div class="media-library-detail-visual-rail">
          <MediaLibraryVisualPanel
            asset={asset()}
            item={selectedItem()}
            schemeId={activeTab()}
            orientation={mediaOrientation(asset())}
            semanticStatus={openCut().schemes.visual.semanticStatus}
            semanticRun={activeResult().semanticRun}
          />
        </div>
      </div>
      <MediaLibraryToolDrawer
        open={Boolean(drawerScheme())}
        schemeId={drawerScheme() || "dialogue"}
        asset={asset()}
        openCut={openCut()}
        runBusy={drawerScheme() === "composite" ? compositeRunBusy() : runBusy()}
        runError={drawerScheme() === "composite" ? compositeRunError() : runError()}
        allowCloudAsrDataTransfer={allowCloudAsrDataTransfer()}
        onAllowCloudAsrDataTransferChange={setAllowCloudAsrDataTransfer}
        onRun={(schemeId) => schemeId === "composite" ? void runComposite() : void runTool(schemeId)}
        onClose={() => {
          setDrawerScheme("");
          setRunError("");
          setAllowCloudAsrDataTransfer(false);
        }}
      />
    </>}</Show>
  </div>;
}
