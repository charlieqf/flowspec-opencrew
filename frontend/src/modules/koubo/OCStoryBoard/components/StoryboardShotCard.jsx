import { For, Show } from "solid-js";
import { assetUrl, dialogueBoundAsset, frameSessionForPath } from "../storyboardAssets.js";
import { sceneMarks, storyboardScenes } from "../storyboardModel.js";
import { formatTime, shotDuration } from "../storyboardTiming.js";
import { GripIcon, ImageIcon, PlusIcon, ScissorsIcon, XIcon } from "../storyboardIcons.jsx";

function DialogueImageSlot(props) {
  const boundAsset = () => dialogueBoundAsset(props.mark);
  const path = () => boundAsset()?.path || "";
  const item = () => path() ? { ...boundAsset(), resource_session_id: boundAsset().resource_session_id || frameSessionForPath(path(), props.task()?.session_id, props.meta()) } : null;
  const previewImage = (event) => {
    const image = item();
    if (!image || props.selectedAsset()) return;
    event.preventDefault();
    event.stopPropagation();
    props.openImagePreview?.(image, props.mark.srt_text || props.mark.scene_mark_id);
  };
  const bindSelectedAsset = (event) => {
    const asset = props.selectedAsset();
    if (!asset) return;
    event.preventDefault();
    event.stopPropagation();
    props.assignAssetToScene(asset, props.mark.scene_mark_id, "display");
    props.setSelectedAsset(null);
  };
  return <div class={`ocsb-frame-drop ocsb-dialogue-image ${path() ? "has-image" : ""} ${props.selectedAsset() ? "can-bind" : ""}`} data-storyboard-drop="true" data-scene-id={props.mark.scene_mark_id} data-role="display" onClick={(event) => {
    const asset = props.selectedAsset();
    if (!asset && path()) {
      previewImage(event);
      return;
    }
    if (!asset) return;
    props.assignAssetToScene(asset, props.mark.scene_mark_id, "display");
    props.setSelectedAsset(null);
  }} onPointerUp={bindSelectedAsset} onMouseUp={bindSelectedAsset} onDragEnter={props.allowAssetDrop} onDragOver={props.allowAssetDrop} onDrop={(event) => props.dropAsset(event, props.mark.scene_mark_id, "display")}>
    <Show when={path()} fallback={<div class="ocsb-drop-empty"><ImageIcon /></div>}>
      <img src={assetUrl(item(), props.task()?.session_id)} loading="lazy" draggable="false" />
      <button type="button" title="Remove Image" onClick={(event) => { event.stopPropagation(); props.clearSceneImage(props.mark.scene_mark_id); }}><XIcon /></button>
    </Show>
  </div>;
}

function DurationControl(props) {
  const changeBy = (delta) => {
    const current = Number(props.mark.duration || 0);
    const next = Math.max(0, Number((current + delta).toFixed(2)));
    props.updateSceneField(props.mark.scene_mark_id, "duration", next);
  };
  return <div class="ocsb-duration-control" title={`Builder-G: ${props.timingInfoForDialogue(props.mark).chars}字 × ${props.timingInfoForDialogue(props.mark).secPerChar.toFixed(3)}s/字 = ${props.timingInfoForDialogue(props.mark).duration.toFixed(2)}s`}>
    <span>DURATION</span>
    <div>
      <input type="number" min="0" step="0.1" value={Number(props.mark.duration || 0).toFixed(2)} onInput={(event) => props.updateSceneField(props.mark.scene_mark_id, "duration", event.currentTarget.value)} />
      <em>s</em>
      <div class="ocsb-duration-steppers" aria-label="Adjust Duration">
        <button type="button" title="Increase Duration" aria-label="Increase Duration" onClick={(event) => { event.stopPropagation(); changeBy(0.1); }}><span></span></button>
        <button type="button" title="Decrease Duration" aria-label="Decrease Duration" onClick={(event) => { event.stopPropagation(); changeBy(-0.1); }}><span></span></button>
      </div>
    </div>
  </div>;
}

function DialogueCard(props) {
  const markIndex = () => Number(props.mark.__mark_index ?? -1);
  const previous = () => sceneMarks(props.shot)[markIndex() - 1];
  const isActive = () => props.selectedSceneId?.() === props.mark.scene_mark_id;
  const selectDialogue = () => {
    props.setSelectedShotIndex(props.shotIndex);
    props.setSelectedSceneId(props.mark.scene_mark_id);
  };
  const focusDialogueText = () => {
    selectDialogue();
    props.setEditingSceneId?.(props.mark.scene_mark_id);
  };
  const blurDialogueText = () => {
    if (props.editingSceneId?.() === props.mark.scene_mark_id) props.setEditingSceneId?.("");
  };
  const canMergeDialogue = () => {
    const prev = previous();
    if (!prev) return false;
    const currentScene = props.mark.scene_id || String(props.mark.scene_mark_id || "").replace(/_dialogue_\d+$/, "");
    const previousScene = prev.scene_id || String(prev.scene_mark_id || "").replace(/_dialogue_\d+$/, "");
    return Boolean(currentScene && currentScene === previousScene);
  };
  const selectAndMaybeBindAsset = (event) => {
    const target = event.target;
    if (target?.closest?.(".ocsb-duration-control, .ocsb-dialogue-delete, .ocsb-dialogue-merge, .ocsb-frame-drop button")) return;
    selectDialogue();
    event.stopPropagation();
    const asset = props.selectedAsset();
    if (!asset) return;
    event.preventDefault();
    props.assignAssetToScene(asset, props.mark.scene_mark_id, "display");
    props.setSelectedAsset(null);
  };
  return <div class={`ocsb-dialogue-card ${props.selectedAsset() ? "can-bind" : ""} ${isActive() ? "is-active" : ""}`} id={`ocsb-dialogue-${props.mark.scene_mark_id}`} data-storyboard-drop="true" data-scene-id={props.mark.scene_mark_id} data-role="display" onClick={selectAndMaybeBindAsset}>
    <div class="ocsb-grip"><GripIcon /></div>
    <div class="ocsb-dialogue-meta">
      <DurationControl mark={props.mark} timingInfoForDialogue={props.timingInfoForDialogue} updateSceneField={props.updateSceneField} />
    </div>
    <textarea rows="2" value={props.mark.srt_text || ""} placeholder="Action lines or dialogue..." onFocus={focusDialogueText} onBlur={blurDialogueText} onInput={(event) => props.updateSceneField(props.mark.scene_mark_id, "srt_text", event.currentTarget.value)} />
    <div class="ocsb-frame-strip ocsb-frame-strip-single">
      <DialogueImageSlot {...props} />
    </div>
    <Show when={canMergeDialogue()}>
      <button class="ocsb-dialogue-merge" type="button" title="向上合并对话" onClick={(event) => { event.stopPropagation(); props.mergeDialogueUp(props.shot.shot_id, props.mark.scene_mark_id); }}>向上合并</button>
    </Show>
    <button class="ocsb-dialogue-delete" type="button" title="Delete Dialogue" disabled={sceneMarks(props.shot).length <= 1} onClick={(event) => { event.stopPropagation(); props.deleteDialogue(props.shot.shot_id, props.mark.scene_mark_id); }}><XIcon /></button>
  </div>;
}

function DialogueBreakActions(props) {
  const hasFollowingInScene = () => props.scene.marks.some((mark, index) => index > props.markIndex() && mark.scene_id === props.mark.scene_id);
  const hasFollowingInShot = () => sceneMarks(props.shot).some((mark, index) => index > Number(props.mark.__mark_index ?? -1));
  const isEditing = () => props.editingSceneId?.() === props.mark.scene_mark_id;
  const isSceneBoundary = () => !hasFollowingInScene() && hasFollowingInShot();
  return <div class={`ocsb-dialogue-break ${isEditing() ? "is-editing" : ""} ${isSceneBoundary() ? "is-scene-boundary" : ""}`}>
    <span></span>
    <div>
      <button type="button" title="拆分场景" disabled={!hasFollowingInScene()} onClick={(event) => { event.stopPropagation(); props.splitScene(props.shot.shot_id, props.mark.scene_mark_id); }}><ScissorsIcon />场景</button>
      <button type="button" title="拆分分镜" disabled={!hasFollowingInShot()} onClick={(event) => { event.stopPropagation(); props.splitShot(props.shot.shot_id, props.mark.scene_mark_id); }}><ScissorsIcon />分镜</button>
      <button type="button" title="添加对话" onClick={(event) => { event.stopPropagation(); props.addDialogueAfter(props.shot.shot_id, props.mark.scene_mark_id); }}><PlusIcon />对话</button>
    </div>
  </div>;
}

function SceneProCard(props) {
  return <div class="ocsb-scene-wrap" id={`ocsb-scene-${props.scene.scene_id}`} onClick={() => {
    props.setSelectedShotIndex(props.shotIndex);
    props.setSelectedSceneId(props.scene.marks[0]?.scene_mark_id || "");
  }}>
    <div class="ocsb-scene-line">
      <span>•</span>
      <em>{formatTime(props.scene.duration || 0)}</em>
      <i>Scene {props.scene.scene_index}</i>
    </div>
    <Show when={props.sceneIndex() > 0}>
      <button class="ocsb-merge-scene" type="button" onClick={() => props.mergeSceneUp(props.shot.shot_id, props.scene.marks[0]?.scene_mark_id)}>向上合并</button>
    </Show>
    <For each={props.scene.marks}>{(mark, markIndex) => {
      return <div class="ocsb-dialogue-unit">
        <DialogueCard {...props} mark={mark} />
        <DialogueBreakActions {...props} scene={props.scene} mark={mark} markIndex={markIndex} />
      </div>;
    }}</For>
  </div>;
}

export function StoryboardShotCard(props) {
  return <section class="ocsb-shot-card" id={`ocsb-shot-${props.shot.shot_id}`}>
    <header class="ocsb-shot-sticky">
      <div class="ocsb-shot-title">
        <span></span>
        <input class="ocsb-shot-name-input ocsb-shot-title-input" value={props.shotDisplayName(props.shot)} title={props.shot.shot_id} aria-label={`Shot name ${props.shot.shot_id}`} onFocus={() => {
          props.setSelectedShotIndex(props.shotIndex());
          props.setSelectedSceneId("");
        }} onInput={(event) => props.updateShotField(props.shot.shot_id, "shot_name", event.currentTarget.value)} onBlur={(event) => {
          if (!event.currentTarget.value.trim()) props.updateShotField(props.shot.shot_id, "shot_name", props.shot.shot_id);
        }} />
        <p>({storyboardScenes(props.shot).length || 1} 个场景，{formatTime(shotDuration(props.shot))})</p>
        <Show when={props.shotIndex() > 0}>
          <button type="button" onClick={() => props.mergeShotUp(props.shotIndex())}>向上合并</button>
        </Show>
      </div>
    </header>
    <div class="ocsb-scenes"><For each={storyboardScenes(props.shot)}>{(scene, sceneIndex) => <SceneProCard {...props} shotIndex={props.shotIndex()} scene={scene} sceneIndex={sceneIndex} />}</For></div>
  </section>;
}
