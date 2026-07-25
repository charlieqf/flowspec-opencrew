import { For, Show, createEffect, createSignal } from "solid-js";
import { formatDurationSeconds, formatTime, shotDuration } from "../storyboardTiming.js";
import { storyboardScenes } from "../storyboardModel.js";

function PlayIcon() { return <svg viewBox="0 0 24 24" fill="currentColor"><path d="M8 5.5a1 1 0 0 1 1.5-.86l8 5a1 1 0 0 1 0 1.72l-8 5A1 1 0 0 1 8 15.5z"/></svg>; }
function PauseIcon() { return <svg viewBox="0 0 24 24" fill="currentColor"><path d="M7 5h3v14H7z"/><path d="M14 5h3v14h-3z"/></svg>; }
function SpeedIcon() { return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M5 19a9 9 0 1 1 14 0"/><path d="M12 12l4-4"/><path d="M8 19h8"/></svg>; }

export function StoryboardTimeline(props) {
  const selectedSceneKey = () => String(props.selectedSceneId?.() || "").replace(/_dialogue_\d+$/, "");
  const playbackBarPhase = () => ["generating", "playing"].includes(props.playbackPhase?.()) ? props.playbackPhase() : "";
  const playbackSceneClass = (scene) => playbackBarPhase() && props.playbackCurrentSceneId?.() === scene.scene_id ? `is-${playbackBarPhase()}` : "";
  const playbackShotClass = (shot) => playbackBarPhase() && props.playbackCurrentShotId?.() === shot.shot_id ? `is-${playbackBarPhase()}` : "";
  const [speedDraft, setSpeedDraft] = createSignal(1);

  createEffect(() => {
    if (!props.playbackSpeedOpen?.()) return;
    setSpeedDraft(props.playbackSpeed?.() || 1);
  });

  return <footer class={`ocsb-timeline ${props.timelineSelectionScope?.() === "all" ? "is-all-selected" : ""}`} onClick={(event) => {
    if (event.target?.closest?.("button, input, textarea, select, .ocsb-audio-popover")) return;
    props.selectFullTimeline?.();
  }}>
    <div class="ocsb-time-label">
      <span>00:00</span>
      <div class="ocsb-timeline-center">
        <div class="ocsb-timeline-audio-controls">
          <button class={props.playbackPhase?.() === "playing" || props.playbackPhase?.() === "generating" ? "is-playing" : ""} type="button" title={props.playbackPhase?.() === "playing" ? "暂停" : "播放"} aria-label="播放或暂停" onClick={(event) => { event.stopPropagation(); void props.toggleTimelinePlayback?.(); }}>
            {props.playbackPhase?.() === "playing" || props.playbackPhase?.() === "generating" ? <PauseIcon /> : <PlayIcon />}
          </button>
          <button type="button" title="播放速度" aria-label="播放速度" onClick={(event) => { event.stopPropagation(); props.setPlaybackSpeedOpen?.(true); }}><SpeedIcon /><span>{Number(props.playbackSpeed?.() || 1).toFixed(2)}x</span></button>
        </div>
        <Show when={props.playbackStatus?.()}><span class="ocsb-playback-status" title={props.playbackPhase?.()}>{props.playbackStatus()}</span></Show>
      </div>
      <span>{formatTime(props.totalDuration())}</span>
    </div>
    <div class="ocsb-scene-bars">
      <For each={props.shots()}>{(shot, shotIndex) => <For each={storyboardScenes(shot)}>{(scene) => <button class={`${selectedSceneKey() === scene.scene_id ? "is-active" : ""} ${playbackSceneClass(scene)}`} type="button" style={{ width: `${Math.max(2, (Number(scene.duration || 0) / Math.max(1, props.totalDuration())) * 100)}%` }} onClick={() => {
        props.setSelectedShotIndex(shotIndex());
        props.setSelectedSceneId(scene.marks[0]?.scene_mark_id || "");
        props.setTimelineSelectionScope?.("scene");
        props.scrollToStoryboardNode(`ocsb-scene-${scene.scene_id}`);
      }} title={`${scene.scene_id} ${Number(scene.duration || 0).toFixed(2)}s`}>
        <span class="ocsb-scene-bar-duration">{formatDurationSeconds(scene.duration || 0)}</span>
      </button>}</For>}</For>
    </div>
    <div class="ocsb-shot-bars">
      <For each={props.shots()}>{(shot, shotIndex) => <button class={`${props.selectedShotIndex() === shotIndex() && !props.selectedSceneId?.() ? "is-active" : ""} ${playbackShotClass(shot)}`} type="button" style={{ width: `${Math.max(5, (shotDuration(shot) / Math.max(1, props.totalDuration())) * 100)}%` }} onClick={() => {
        props.setSelectedShotIndex(shotIndex());
        props.setSelectedSceneId("");
        props.setTimelineSelectionScope?.("shot");
        props.scrollToStoryboardNode(`ocsb-shot-${shot.shot_id}`);
      }}>
        <span class="ocsb-shot-bar-name">{props.shotDisplayName?.(shot) || shot.shot_id}</span>
        <span class="ocsb-shot-bar-duration">{formatDurationSeconds(shotDuration(shot))}</span>
      </button>}</For>
    </div>
    <Show when={props.playbackSpeedOpen?.()}>
      <div class="ocsb-audio-popover ocsb-speed-popover">
        <div class="ocsb-audio-popover-head"><strong>Playback Speed</strong><button type="button" onClick={() => props.setPlaybackSpeedOpen?.(false)}>Close</button></div>
        <label class="ocsb-speed-control"><span>Playback speed</span><div><input type="number" min="0.25" max="4" step="0.05" value={speedDraft()} onInput={(event) => setSpeedDraft(event.currentTarget.value)} /><em>x</em></div></label>
        <div class="ocsb-speed-presets"><For each={[0.5, 0.75, 1, 1.25, 1.5, 2]}>{(value) => <button class={Number(speedDraft()) === value ? "is-active" : ""} type="button" onClick={() => setSpeedDraft(value)}>{value}x</button>}</For></div>
        <div class="ocsb-audio-popover-actions"><button type="button" onClick={() => props.setPlaybackSpeedOpen?.(false)}>Cancel</button><button type="button" onClick={() => props.applyPlaybackSpeed?.(speedDraft())}>Apply</button></div>
      </div>
    </Show>
  </footer>;
}
