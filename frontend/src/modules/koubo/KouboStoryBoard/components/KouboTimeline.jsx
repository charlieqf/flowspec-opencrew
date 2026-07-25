import { For, Show, createEffect, createSignal } from "solid-js";
import { Portal } from "solid-js/web";
import { fmtSeconds, fmtTime, sceneDuration, shotDuration } from "../kouboStoryboardModel.js";
import { VIDEO_PLAN_MAX_OPTIONS } from "../kouboStoryboardVideoPlan.js";
import { FilmIcon, ImageIcon, LayersIcon, PanelToggleIcon, PauseIcon, PlayIcon, SlidersIcon, SpeedIcon, WorkflowIcon } from "../kouboStoryboardIcons.jsx";

const TASK_TARGET = { target_type: "task", shot_id: "", scene_id: "" };
const TIMELINE_GLOBAL_TARGET = { ...TASK_TARGET, action_source: "timeline_global_button" };

export default function KouboTimeline(props) {
  const [speedDraft, setSpeedDraft] = createSignal("1.00");
  const [videoPlanDraft, setVideoPlanDraft] = createSignal({ max_video_seconds: "4", min_video_seconds: "2.00", split_tolerance_seconds: "2.00" });
  const [composerDraft, setComposerDraft] = createSignal({ subtitle_mode: "hyperframe", watermark_mode: "never", force: true });
  const [collapsed, setCollapsed] = createSignal(false);
  const sceneKey = () => {
    const selected = props.selectedDialogueId();
    for (const shot of props.shots()) {
      for (const scene of shot.scenes || []) {
        if ((scene.dialogues || []).some((dialogue) => dialogue.dialogue_id === selected)) return scene.scene_id;
      }
    }
    return "";
  };
  const currentShotId = () => props.playbackCurrentShotId?.() || "";
  const currentSceneId = () => props.playbackCurrentSceneId?.() || "";
  const isPlaying = () => ["generating", "playing"].includes(props.playbackPhase?.());
  const playbackBarPhase = () => ["generating", "playing"].includes(props.playbackPhase?.()) ? props.playbackPhase() : "";
  const playbackSceneClass = (scene) => playbackBarPhase() && currentSceneId() === scene.scene_id ? `is-${playbackBarPhase()}` : "";
  const playbackShotClass = (shot) => playbackBarPhase() && currentShotId() === shot.shot_id ? `is-${playbackBarPhase()}` : "";
  createEffect(() => {
    if (props.playbackSpeedOpen?.()) setSpeedDraft(Number(props.playbackSpeed?.() || 1).toFixed(2));
  });
  createEffect(() => {
    if (!props.videoPlanParamsOpen?.()) return;
    const settings = props.videoPlanSettings?.() || {};
    setVideoPlanDraft({
      max_video_seconds: String(Number(settings.max_video_seconds || 4)),
      min_video_seconds: Number(settings.min_video_seconds || 2).toFixed(2),
      split_tolerance_seconds: Number(settings.split_tolerance_seconds ?? 2).toFixed(2),
    });
  });
  createEffect(() => {
    if (!props.composerParamsOpen?.()) return;
    const settings = props.composerSettings?.() || {};
    setComposerDraft({
      subtitle_mode: settings.subtitle_mode || "hyperframe",
      watermark_mode: settings.watermark_mode || "never",
      force: settings.force !== false,
    });
  });
  const timelineVideoPlanTarget = () => ({
    ...(props.currentVideoPlanTarget?.() || TASK_TARGET),
    action_source: "timeline_scope_button",
  });
  const videoPlanDisabledReason = () => props.videoPlanDisabledReason?.(timelineVideoPlanTarget()) || "";
  const imagePlanDisabledReason = () => props.imagePlanDisabledReason?.() || "";
  const videoOnlyPlanDisabledReason = () => props.videoOnlyPlanDisabledReason?.() || "";
  const videoPlanDisabled = () => Boolean(videoPlanDisabledReason());
  const imagePlanDisabled = () => Boolean(imagePlanDisabledReason());
  const videoOnlyPlanDisabled = () => Boolean(videoOnlyPlanDisabledReason());
  const composerDisabled = () => Boolean(props.composerDisabledReason?.());
  const updateVideoPlanDraft = (key, value) => setVideoPlanDraft((previous) => ({ ...previous, [key]: value }));
  const selectMaxVideoDraft = (seconds) => setVideoPlanDraft((previous) => ({
    ...previous,
    max_video_seconds: String(seconds),
    split_tolerance_seconds: seconds === 10 ? "0.00" : previous.split_tolerance_seconds,
  }));
  const updateComposerDraft = (key, value) => setComposerDraft((previous) => ({ ...previous, [key]: value }));
  const currentMaxVideo = () => Number(props.videoPlanSettings?.().max_video_seconds || 4);
  const maxVideoDraftSeconds = () => Number(videoPlanDraft().max_video_seconds || 4);
  const currentComposerSubtitle = () => props.composerSettings?.().subtitle_mode === "none" ? "NoSub" : "HF";
  return <footer class={`kbsp-timeline ${collapsed() ? "is-collapsed" : ""} ${props.scope() === "all" ? "is-all-selected" : ""}`} onClick={(event) => {
    if (event.target?.closest?.("button, input, .kbsp-audio-popover, .kbsp-video-plan-popover, .kbsp-composer-popover")) return;
    props.setScope("all");
    props.setSelectedDialogueId("");
  }}>
    <button class="kbsp-timeline-collapse-toggle" type="button" title={collapsed() ? "展开时间轴面板" : "收起时间轴面板"} aria-label={collapsed() ? "展开时间轴面板" : "收起时间轴面板"} onClick={(event) => {
      event.stopPropagation();
      setCollapsed((value) => !value);
    }}><PanelToggleIcon collapsed={collapsed()} /></button>
    <div class="kbsp-time-label">
      <span>00:00</span>
      <div class="kbsp-timeline-center">
        <div class="kbsp-timeline-audio-controls">
          <button class={isPlaying() ? "is-playing" : ""} type="button" title={isPlaying() ? "暂停音频生成" : "音频生成"} aria-label="音频生成" onClick={(event) => {
            event.stopPropagation();
            props.toggleTimelinePlayback?.();
          }}>{isPlaying() ? <PauseIcon /> : <PlayIcon />}</button>
          <button type="button" title="音频生成配置" aria-label="音频生成配置" onClick={(event) => {
            event.stopPropagation();
            props.setPlaybackSpeedOpen?.(!props.playbackSpeedOpen?.());
          }}><SpeedIcon />{Number(props.playbackSpeed?.() || 1).toFixed(2)}x</button>
          <button class={`kbsp-video-plan-button ${props.videoPlanBusy?.() ? "is-generating" : ""}`} type="button" title={videoPlanDisabledReason() || "生成计划"} aria-label="生成计划" disabled={videoPlanDisabled()} onClick={(event) => {
            event.stopPropagation();
            props.openVideoPlan?.(timelineVideoPlanTarget());
          }}><WorkflowIcon /></button>
          <button class={`kbsp-image-plan-button ${props.imagePlanBusy?.() ? "is-generating" : ""}`} type="button" title={imagePlanDisabledReason() || "图像计划"} aria-label="图像计划" disabled={imagePlanDisabled()} onClick={(event) => {
            event.stopPropagation();
            props.generateImagePlan?.({ action_source: "timeline_scope_button" });
          }}><ImageIcon /></button>
          <button class={`kbsp-video-only-plan-button ${props.videoOnlyPlanBusy?.() ? "is-generating" : ""}`} type="button" title={videoOnlyPlanDisabledReason() || "视频计划"} aria-label="视频计划" disabled={videoOnlyPlanDisabled()} onClick={(event) => {
            event.stopPropagation();
            props.generateVideoOnlyPlan?.({ action_source: "timeline_scope_button" });
          }}><FilmIcon /></button>
          <button class="kbsp-video-plan-param-button" type="button" title="生成计划配置" aria-label="生成计划配置" disabled={Boolean(props.videoPlanBusy?.())} onClick={(event) => {
            event.stopPropagation();
            props.setVideoPlanParamsOpen?.(!props.videoPlanParamsOpen?.());
          }}><SpeedIcon /><strong>{Number(currentMaxVideo()).toFixed(2)}s</strong></button>
          <button class={`kbsp-composer-button ${props.composerBusy?.() ? "is-generating" : ""}`} type="button" title={props.composerDisabledReason?.() || "视频合成"} aria-label="视频合成" disabled={composerDisabled()} onClick={(event) => {
            event.stopPropagation();
            props.setScope("all");
            props.setSelectedDialogueId("");
            props.openComposer?.(TIMELINE_GLOBAL_TARGET);
          }}><LayersIcon /></button>
          <button class="kbsp-composer-param-button" type="button" title="视频合成配置" aria-label="视频合成配置" disabled={Boolean(props.composerBusy?.())} onClick={(event) => {
            event.stopPropagation();
            props.setComposerParamsOpen?.(!props.composerParamsOpen?.());
          }}><SlidersIcon /><strong>{currentComposerSubtitle()}</strong></button>
        </div>
        <Show when={props.playbackStatus?.()}>
          <span class="kbsp-playback-status">{props.playbackStatus()}</span>
        </Show>
        <Show when={props.playbackSpeedOpen?.()}>
          <Portal>
            <div class="kbsp-audio-popover kbsp-speed-popover" onClick={(event) => event.stopPropagation()}>
              <div class="kbsp-audio-popover-head">
                <strong>音频生成配置</strong>
              </div>
              <label class="kbsp-speed-control">
                <span>播放速度</span>
                <div><input type="number" min="0.25" max="4" step="0.05" value={speedDraft()} onInput={(event) => setSpeedDraft(event.currentTarget.value)} /><em>x</em></div>
              </label>
              <div class="kbsp-speed-presets">
                <For each={[0.5, 1, 1.5]}>{(speed) => <button class={Math.abs(Number(speedDraft() || 1) - speed) < 0.001 ? "is-active" : ""} type="button" onClick={() => setSpeedDraft(speed.toFixed(2))}>{speed.toFixed(2)}x</button>}</For>
              </div>
              <div class="kbsp-audio-popover-actions">
                <button type="button" onClick={() => props.setPlaybackSpeedOpen?.(false)}>取消</button>
                <button type="button" onClick={() => props.applyPlaybackSpeed?.(speedDraft())}>应用</button>
              </div>
            </div>
          </Portal>
        </Show>
        <Show when={props.composerParamsOpen?.()}>
          <Portal>
            <div class="kbsp-audio-popover kbsp-composer-popover" onClick={(event) => event.stopPropagation()}>
              <div class="kbsp-audio-popover-head">
                <strong>视频合成配置</strong>
              </div>
              <label class="kbsp-speed-control kbsp-composer-mode-control">
                <span>Subtitle</span>
                <div class="kbsp-video-plan-max-options">
                  <For each={[["hyperframe", "HyperFrame"], ["none", "None"]]}>{(option) => <button class={composerDraft().subtitle_mode === option[0] ? "is-active" : ""} type="button" onClick={() => updateComposerDraft("subtitle_mode", option[0])}>{option[1]}</button>}</For>
                </div>
              </label>
              <label class="kbsp-speed-control kbsp-composer-mode-control">
                <span>Watermark</span>
                <div class="kbsp-video-plan-max-options">
                  <For each={[["never", "Never"], ["auto", "Auto"], ["always", "Always"]]}>{(option) => <button class={composerDraft().watermark_mode === option[0] ? "is-active" : ""} type="button" onClick={() => updateComposerDraft("watermark_mode", option[0])}>{option[1]}</button>}</For>
                </div>
              </label>
              <label class="kbsp-composer-checkbox">
                <input type="checkbox" checked={composerDraft().force} onChange={(event) => updateComposerDraft("force", event.currentTarget.checked)} />
                <span>Force rerun</span>
              </label>
              <div class="kbsp-audio-popover-actions">
                <button type="button" onClick={() => props.setComposerParamsOpen?.(false)}>取消</button>
                <button type="button" disabled={Boolean(props.composerBusy?.())} onClick={() => {
                  props.applyComposerSettings?.(composerDraft());
                  props.setComposerParamsOpen?.(false);
                }}>应用</button>
              </div>
            </div>
          </Portal>
        </Show>
        <Show when={props.videoPlanParamsOpen?.()}>
          <Portal>
            <div class="kbsp-audio-popover kbsp-video-plan-popover" onClick={(event) => event.stopPropagation()}>
              <div class="kbsp-audio-popover-head">
                <strong>生成计划配置</strong>
              </div>
              <label class="kbsp-speed-control kbsp-video-plan-max-control">
                <span>Max Video</span>
                <div class="kbsp-video-plan-max-options">
                  <For each={VIDEO_PLAN_MAX_OPTIONS}>{(seconds) => <button class={maxVideoDraftSeconds() === seconds ? "is-active" : ""} type="button" onClick={() => selectMaxVideoDraft(seconds)}>{seconds}s</button>}</For>
                </div>
              </label>
              <label class="kbsp-speed-control">
                <span>最短视频</span>
                <div><input type="number" min="2" max="30" step="0.1" value={videoPlanDraft().min_video_seconds} onInput={(event) => updateVideoPlanDraft("min_video_seconds", event.currentTarget.value)} /><em>s</em></div>
              </label>
              <label class="kbsp-speed-control">
                <span>分段容忍度</span>
                <div><input type="number" min="0" max="10" step="0.1" disabled={maxVideoDraftSeconds() === 10} value={maxVideoDraftSeconds() === 10 ? "0.00" : videoPlanDraft().split_tolerance_seconds} onInput={(event) => updateVideoPlanDraft("split_tolerance_seconds", event.currentTarget.value)} /><em>s</em></div>
              </label>
              <div class="kbsp-audio-popover-actions">
                <button type="button" onClick={() => props.setVideoPlanParamsOpen?.(false)}>取消</button>
                <button type="button" disabled={Boolean(props.videoPlanBusy?.())} onClick={async () => {
                  await props.applyVideoPlanSettings?.(videoPlanDraft());
                  props.setVideoPlanParamsOpen?.(false);
                }}>应用</button>
              </div>
            </div>
          </Portal>
        </Show>
      </div>
      <span>{fmtTime(props.totalDuration())}</span>
    </div>
    <div class="kbsp-scene-bars">
      <For each={props.shots()}>{(shot, shotIndex) => <For each={shot.scenes || []}>{(scene) => <button class={`${sceneKey() === scene.scene_id ? "is-active" : ""} ${playbackSceneClass(scene)}`} type="button" style={{ width: `${Math.max(2, (Number(scene.duration || 0) / Math.max(1, props.totalDuration())) * 100)}%` }} title={`${scene.scene_id} ${Number(scene.duration || 0).toFixed(2)}s`} onClick={(event) => {
        event.stopPropagation();
        props.setSelectedShotIndex(shotIndex());
        props.setSelectedDialogueId(scene.dialogues?.[0]?.dialogue_id || "");
        props.setScope("scene");
        props.scrollToNode(`kbsp-scene-${scene.scene_id}`);
      }}><span class="kbsp-scene-bar-duration">{fmtSeconds(scene.duration || 0)}</span></button>}</For>}</For>
    </div>
    <div class="kbsp-shot-bars">
      <For each={props.shots()}>{(shot, shotIndex) => <button class={`${props.selectedShotIndex() === shotIndex() && !props.selectedDialogueId() ? "is-active" : ""} ${playbackShotClass(shot)}`} type="button" style={{ width: `${Math.max(5, (shotDuration(shot) / Math.max(1, props.totalDuration())) * 100)}%` }} onClick={(event) => {
        event.stopPropagation();
        props.setSelectedShotIndex(shotIndex());
        props.setSelectedDialogueId("");
        props.setScope("shot");
        props.scrollToNode(`kbsp-shot-${shot.shot_id}`);
      }}>
        <span class="kbsp-shot-bar-name">{shot.shot_name || shot.shot_id}</span>
        <span class="kbsp-shot-bar-duration">{fmtSeconds(shotDuration(shot))}</span>
      </button>}</For>
    </div>
  </footer>;
}
