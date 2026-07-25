import { For } from "solid-js";
import { dialogueBoundAsset } from "../storyboardAssets.js";
import { cleanDialogueText, sceneMarks, storyboardScenes } from "../storyboardModel.js";
import { ClapperIcon, MessageIcon, MoonIcon, SunIcon } from "../storyboardIcons.jsx";
import StoryboardIcon from "../../shared/StoryboardIcon.jsx";

function sceneText(mark) {
  return cleanDialogueText(mark?.srt_text || "");
}

export function StoryboardSidebar(props) {
  return <aside class="ocsb-left" style={{ width: `${props.leftPanelWidth()}px` }}>
    <div class="ocsb-brand">
      <div><StoryboardIcon /><span>Storyboard Pro</span></div>
      <button class="ocsb-theme-toggle" type="button" title={props.storyTheme() === "dark" ? "Light Mode" : "Dark Mode"} aria-label={props.storyTheme() === "dark" ? "Light Mode" : "Dark Mode"} onClick={() => props.setStoryTheme(props.storyTheme() === "dark" ? "light" : "dark")}>{props.storyTheme() === "dark" ? <SunIcon /> : <MoonIcon />}</button>
    </div>
    <div class="ocsb-tree">
      <For each={props.shots()}>{(shot, shotIndex) => <section>
        <div class={`ocsb-tree-shot ${props.selectedShotIndex() === shotIndex() ? "is-active" : ""}`}>
          <button class="ocsb-tree-shot-select" type="button" title={shot.shot_id} onClick={() => {
            props.setSelectedShotIndex(shotIndex());
            props.setSelectedSceneId("");
            props.scrollToStoryboardNode(`ocsb-shot-${shot.shot_id}`);
          }}>
            <ClapperIcon />
          </button>
          <input class="ocsb-shot-name-input ocsb-tree-shot-name" value={props.shotDisplayName(shot)} title={shot.shot_id} aria-label={`Shot name ${shot.shot_id}`} onFocus={() => {
            props.setSelectedShotIndex(shotIndex());
            props.setSelectedSceneId("");
          }} onInput={(event) => props.updateShotField(shot.shot_id, "shot_name", event.currentTarget.value)} onBlur={(event) => {
            if (!event.currentTarget.value.trim()) props.updateShotField(shot.shot_id, "shot_name", shot.shot_id);
          }} />
        </div>
        <div class="ocsb-tree-scenes">
          <For each={storyboardScenes(shot)}>{(scene) => <div class="ocsb-tree-scene">
            <button class={scene.marks.some((mark) => props.selectedSceneId() === mark.scene_mark_id) ? "is-active" : ""} type="button" onClick={() => {
              props.setSelectedShotIndex(shotIndex());
              props.setSelectedSceneId(scene.marks[0]?.scene_mark_id || "");
              props.scrollToStoryboardNode(`ocsb-scene-${scene.scene_id}`);
            }}>
              <span class="ocsb-tree-name">{scene.scene_id}</span>
            </button>
            <For each={scene.marks}>{(mark) => {
              const hasDialogueImage = Boolean(dialogueBoundAsset(mark));
              return <button class={`ocsb-tree-dialogue ${hasDialogueImage ? "has-image" : ""} ${props.selectedSceneId() === mark.scene_mark_id ? "is-active" : ""}`} type="button" title={hasDialogueImage ? "Dialogue has image" : ""} onClick={() => {
                props.setSelectedShotIndex(shotIndex());
                props.setSelectedSceneId(mark.scene_mark_id);
                props.scrollToStoryboardNode(`ocsb-dialogue-${mark.scene_mark_id}`);
              }}>
                <span class="ocsb-tree-dialogue-icon"><MessageIcon /></span>
                <span class="ocsb-tree-dialogue-text">{sceneText(mark) || "Empty line..."}</span>
              </button>;
            }}</For>
          </div>}</For>
        </div>
      </section>}</For>
    </div>
    <button class="ocsb-left-resize-grip" type="button" title="Resize left navigation" aria-label="Resize left navigation" onMouseDown={props.startLeftResize} />
  </aside>;
}
