import { For, Show, createEffect, createSignal, onCleanup } from "solid-js";
import { CloseIcon, RunIcon } from "../components/MediaLibraryIcons.jsx";
import {
  compositeRunState,
  analysisSchemeStatusMeta,
  isNoAudioDialogueResult,
  openCutSchemeById,
} from "./mediaLibraryDetailModel.js";

function formatElapsed(milliseconds) {
  const totalSeconds = Math.max(0, Math.floor(Number(milliseconds || 0) / 1000));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  return hours ? `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}` : `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

export default function MediaLibraryToolDrawer(props) {
  const scheme = () => openCutSchemeById(props.schemeId);
  const result = () => props.openCut.schemes[scheme().id];
  const status = () => analysisSchemeStatusMeta(scheme().id, result());
  const active = () => ["queued", "running"].includes(result()?.status);
  const noAudio = () => scheme().id === "dialogue" && isNoAudioDialogueResult(result());
  const cloudAsrConsentMissing = () => scheme().id === "dialogue" && !noAudio() && !props.allowCloudAsrDataTransfer;
  const compositeState = () => compositeRunState(props.openCut);
  const runnable = () => scheme().id === "composite"
    ? compositeState().runnable
    : ["dialogue", "visual"].includes(scheme().id) && !active() && !noAudio() && !cloudAsrConsentMissing();
  const runTitle = () => {
    if (active()) return "运行中";
    if (noAudio()) return "源视频没有音轨，对白分析不可用";
    if (scheme().id === "composite") return compositeState().disabledReason || compositeState().label;
    if (cloudAsrConsentMissing()) return "请先确认本次运行的云端 ASR 音频传输授权";
    return ["ready", "blocked", "stale", "failed"].includes(result()?.status) ? "重新运行" : "运行工具集";
  };
  const [clock, setClock] = createSignal(Date.now());
  createEffect(() => {
    if (!props.open || !active()) return;
    setClock(Date.now());
    const timer = window.setInterval(() => setClock(Date.now()), 1000);
    onCleanup(() => window.clearInterval(timer));
  });
  const elapsed = () => {
    const progress = result()?.progress || {};
    const startedAt = Number(progress.started_at || 0);
    if (active() && startedAt) return formatElapsed(clock() - startedAt);
    if (Number.isFinite(Number(progress.elapsed_ms))) return formatElapsed(Number(progress.elapsed_ms));
    return "--:--";
  };
  const runDescription = () => {
    if (props.runError) return props.runError;
    if (result()?.error) return result().error;
    if (active()) return result()?.progress?.label || `${scheme().label}正在运行`;
    if (result()?.status === "ready") return `${scheme().label}已完成${result()?.count !== null && result()?.count !== undefined ? `，共生成 ${result().count} 个片段` : ""}`;
    if (scheme().id === "composite" && compositeState().disabledReason) return compositeState().disabledReason;
    return scheme().description;
  };
  const stepState = (index) => {
    if (noAudio()) return index < 2 ? "已完成" : "不适用";
    if (result()?.status === "ready") return "已完成";
    if (result()?.status === "failed") return "未完成";
    const completed = Number(result()?.progress?.completed || 0);
    if (index < completed) return "已完成";
    if (active() && index === completed) return "运行中";
    return "待运行";
  };
  return <Show when={props.open}>
    <div class="media-library-tool-backdrop" onClick={props.onClose} />
    <aside class="media-library-tool-drawer" role="dialog" aria-modal="true" aria-label={scheme().title}>
      <header><div><h3>{scheme().title}</h3><p>{scheme().description}</p></div><div class="media-library-tool-header-actions"><button type="button" class="media-library-tool-run-icon" title={runTitle()} aria-label={runTitle()} disabled={!runnable() || props.runBusy} onClick={() => props.onRun(scheme().id)}><RunIcon /></button><button type="button" title="关闭" aria-label="关闭" onClick={props.onClose}><CloseIcon /></button></div></header>
      <Show when={scheme().id === "dialogue"}>
        <Show when={!noAudio()} fallback={
          <div class="media-library-tool-cloud-asr-consent is-unavailable">
            <span><strong>此素材没有音轨，无需 ASR 授权</strong><small>对白识别不可用，但画面分析和视频剪辑仍可使用。完成合格的四帧画面分析后，可以按已发布的视觉描述检索。</small></span>
          </div>
        }>
          <label class="media-library-tool-cloud-asr-consent">
            <input
              type="checkbox"
              checked={Boolean(props.allowCloudAsrDataTransfer)}
              disabled={active() || props.runBusy}
              onChange={(event) => props.onAllowCloudAsrDataTransferChange?.(event.currentTarget.checked)}
            />
            <span><strong>允许本次运行使用云端 ASR</strong><small>如果系统配置的是云端 ASR，视频音频将发送给已配置的服务商；使用本地 ASR 时不会外发。本次勾选会在关闭面板后重置。</small></span>
          </label>
        </Show>
      </Show>
      <section class={`media-library-tool-run-summary ${noAudio() ? "is-unavailable" : result()?.error || props.runError ? "is-error" : ""}`}>
        <div class="media-library-tool-run-metrics"><div><span>运行状态</span><strong class={`media-library-status ${status().tone}`}>{status().label}</strong></div><div><span>总运行时间</span><strong>{elapsed()}</strong></div></div>
        <div class="media-library-tool-run-description"><span>运行描述</span><p>{runDescription()}</p></div>
      </section>
      <section class="media-library-tool-steps"><h4>工具步骤</h4><ol><For each={scheme().steps}>{(step, index) => <li><span>{step}</span><small>{stepState(index())}</small></li>}</For></ol></section>
    </aside>
  </Show>;
}
