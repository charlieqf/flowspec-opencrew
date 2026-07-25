import { For, Show } from "solid-js";
import DialogueCard from "./DialogueCard.jsx";
import { fmtTime, shotDuration, sceneDuration } from "../kouboStoryboardModel.js";

export default function ShotCard(props) {
  const sceneHasBoundAudio = (scene) => {
    const dialogues = Array.isArray(scene?.dialogues) ? scene.dialogues : [];
    return Boolean(dialogues.length && dialogues.every((dialogue) => String(dialogue?.working_assets?.audio?.path || "").trim()));
  };
  const sceneTtsPhase = (scene) => {
    const playbackPhase = props.playbackPhase?.() || "";
    if (props.playbackCurrentSceneId?.() === scene.scene_id && ["generating", "playing", "paused"].includes(playbackPhase)) return playbackPhase;
    return (props.sceneAudioState?.() || {})[scene.scene_id]?.phase || (sceneHasBoundAudio(scene) ? "ready" : "idle");
  };
  const sceneTtsLabel = (scene) => {
    const phase = sceneTtsPhase(scene);
    if (phase === "generating") return "生成中";
    if (phase === "playing") return "播放中";
    if (phase === "paused") return "继续播放";
    if (phase === "ready") return "播放TTS";
    if (phase === "error") return "重试TTS";
    return "生成TTS";
  };
  return <section class="kbsp-shot-card" id={`kbsp-shot-${props.shot.shot_id}`}>
    <header class="kbsp-shot-sticky">
      <Show when={props.shotIndex() > 0}><button class="kbsp-merge-shot" data-kbsp-action="merge-shot-up" type="button" onClick={() => props.mergeShotUp(props.shotIndex())}>向上合并</button></Show>
      <div class="kbsp-shot-title">
        <span></span>
        <input class="kbsp-shot-name-input kbsp-shot-title-input" value={props.shot.shot_name || props.shot.shot_id} aria-label={`Shot name ${props.shot.shot_id}`} title={props.shot.shot_id} onFocus={() => props.selectDialogue(props.shotIndex(), "")} onInput={(event) => props.updateShotName(props.shot.shot_id, event.currentTarget.value)} onBlur={(event) => {
          props.updateShotName(props.shot.shot_id, event.currentTarget.value, { commit: true });
        }} />
        <p>({(props.shot.scenes || []).length || 1} 个场景，{fmtTime(shotDuration(props.shot))})</p>
      </div>
    </header>
    <div class="kbsp-scenes">
      <For each={props.shot.scenes || []}>{(scene, sceneIndex) => <div class="kbsp-scene-wrap" id={`kbsp-scene-${scene.scene_id}`} onClick={() => props.selectDialogue(props.shotIndex(), scene.dialogues?.[0]?.dialogue_id || "")}>
        <div class="kbsp-scene-line"><span>•</span><em>{fmtTime(scene.duration || 0)}</em><i>Scene {scene.scene_index}</i><button class={`kbsp-scene-tts-button is-${sceneTtsPhase(scene)}`} type="button" title="按当前 Scene 文本生成并播放 TTS" disabled={sceneTtsPhase(scene) === "generating"} onClick={(event) => { event.stopPropagation(); void props.playSceneTTS?.(props.shotIndex(), props.shot, scene); }}>{sceneTtsLabel(scene)}</button></div>
        <Show when={sceneIndex() > 0}><button class="kbsp-merge-scene" data-kbsp-action="merge-scene-up" type="button" onClick={(event) => { event.stopPropagation(); props.mergeSceneUp(props.shot.shot_id, scene.scene_id); }}>向上合并</button></Show>
        <For each={scene.dialogues || []}>{(dialogue, dialogueIndex) => <DialogueCard {...props} shotIndex={props.shotIndex()} shot={props.shot} scene={scene} dialogue={dialogue} dialogueIndex={dialogueIndex} />}</For>
      </div>}</For>
    </div>
  </section>;
}
