import { For, Show, createSignal, onCleanup } from "solid-js";
import { dialogueText } from "../kouboStoryboardModel.js";
import { AudioLinesIcon, CutawayIcon, FilmIcon, ImageIcon, MoonIcon, SunIcon } from "../kouboStoryboardIcons.jsx";
import StoryboardIcon from "../../shared/StoryboardIcon.jsx";

function dialogueWorkingAssets(dialogue) {
  const assets = dialogue?.working_assets && typeof dialogue.working_assets === "object" ? dialogue.working_assets : {};
  return {
    audio: assets.audio || {},
    images: Array.isArray(assets.images) ? assets.images : [],
    video: assets.video || {},
  };
}

function dialogueBindingStatus(dialogue) {
  const assets = dialogueWorkingAssets(dialogue);
  const originalImage = dialogue?.source_image_paths?.[0] || dialogue?.image_path || "";
  const explicitNewImage = assets.images?.[1]?.path || "";
  const boundImage = assets.images?.[0]?.path || dialogue?.bound_image_path || "";
  const newImage = explicitNewImage || (boundImage && boundImage !== originalImage ? boundImage : "");
  return {
    audio: Boolean(assets.audio?.path),
    original: Boolean(originalImage),
    image: Boolean(newImage),
    video: Boolean(assets.video?.path),
  };
}

function DialogueBindingIcons(props) {
  const status = () => dialogueBindingStatus(props.dialogue);
  const isCutaway = () => props.dialogue?.video_plan?.is_talking_head === false;
  const [menu, setMenu] = createSignal(null);
  let cleanupMenu = null;
  const closeMenu = () => {
    setMenu(null);
    cleanupMenu?.();
    cleanupMenu = null;
  };
  const openMenu = (event) => {
    event.preventDefault();
    event.stopPropagation();
    setMenu({ x: event.clientX, y: event.clientY });
    cleanupMenu?.();
    const onPointerDown = () => closeMenu();
    const onKeyDown = (keyboardEvent) => {
      if (keyboardEvent.key === "Escape") closeMenu();
    };
    setTimeout(() => {
      window.addEventListener("pointerdown", onPointerDown);
      window.addEventListener("keydown", onKeyDown);
    }, 0);
    cleanupMenu = () => {
      window.removeEventListener("pointerdown", onPointerDown);
      window.removeEventListener("keydown", onKeyDown);
    };
  };
  const markTalkingHead = async (event, value) => {
    event.preventDefault();
    event.stopPropagation();
    if (props.dialogueVideoPlanBusy?.()) return;
    closeMenu();
    await props.setDialogueTalkingHead?.(props.dialogue?.dialogue_id, value);
  };
  onCleanup(() => cleanupMenu?.());
  const items = () => [
    { key: "audio", label: "声音", active: status().audio, icon: <AudioLinesIcon /> },
    { key: "original", label: isCutaway() ? "原图 · 空镜" : "原图", active: status().original, cutaway: isCutaway(), icon: isCutaway() ? <CutawayIcon /> : <ImageIcon /> },
    { key: "image", label: "新图", active: status().image, icon: <ImageIcon /> },
    { key: "video", label: "终视频", active: status().video, icon: <FilmIcon /> },
  ];
  return <span class="kbsp-tree-bindings" aria-label="绑定状态">
    <For each={items()}>{(item) => <span class={`kbsp-tree-binding is-${item.key} ${item.cutaway ? "is-cutaway" : ""} ${item.active ? "has-binding" : "is-empty"}`} title={`${item.label}: ${item.active ? "已绑定" : "未绑定"}`} aria-label={`${item.label}: ${item.active ? "已绑定" : "未绑定"}`} onContextMenu={item.key === "original" ? openMenu : undefined}>
      {item.icon}
    </span>}</For>
    <Show when={menu()}><span class="kbsp-talking-head-menu" style={{ left: `${menu().x}px`, top: `${menu().y}px` }} onPointerDown={(event) => event.stopPropagation()} onClick={(event) => event.stopPropagation()}>
      <span class={`kbsp-talking-head-menu-item ${props.dialogueVideoPlanBusy?.() ? "is-disabled" : ""}`} onClick={(event) => markTalkingHead(event, false)}><CutawayIcon />设置空镜</span>
      <span class={`kbsp-talking-head-menu-item ${props.dialogueVideoPlanBusy?.() ? "is-disabled" : ""}`} onClick={(event) => markTalkingHead(event, true)}><ImageIcon />标记口播</span>
    </span></Show>
  </span>;
}

export default function KouboSidebar(props) {
  return <aside class="kbsp-left" style={{ width: `${props.leftWidth()}px` }}>
    <div class="kbsp-brand">
      <div><StoryboardIcon /><span>Storyboard Pro</span></div>
      <button class="kbsp-theme-toggle" type="button" title={props.theme() === "dark" ? "Light Mode" : "Dark Mode"} aria-label={props.theme() === "dark" ? "Light Mode" : "Dark Mode"} onClick={() => props.setTheme(props.theme() === "dark" ? "light" : "dark")}>{props.theme() === "dark" ? <SunIcon /> : <MoonIcon />}</button>
    </div>
    <div class="kbsp-tree">
      <For each={props.shots()}>{(shot, shotIndex) => <section>
        <div class={`kbsp-tree-shot ${props.selectedShotIndex() === shotIndex() ? "is-active" : ""}`}>
          <button class="kbsp-tree-shot-select" type="button" title={shot.shot_id} onClick={() => {
            props.setSelectedShotIndex(shotIndex());
            props.setSelectedDialogueId("");
            props.setScope?.("shot");
            props.scrollToNode(`kbsp-shot-${shot.shot_id}`);
          }}><FilmIcon /></button>
          <input class="kbsp-shot-name-input kbsp-tree-shot-name" value={shot.shot_name || shot.shot_id} aria-label={`Shot name ${shot.shot_id}`} title={shot.shot_id} onFocus={() => {
            props.setSelectedShotIndex(shotIndex());
            props.setSelectedDialogueId("");
            props.setScope?.("shot");
          }} onInput={(event) => props.updateShotName(shot.shot_id, event.currentTarget.value)} onBlur={(event) => {
            props.updateShotName(shot.shot_id, event.currentTarget.value, { commit: true });
          }} />
        </div>
        <div class="kbsp-tree-scenes">
          <For each={shot.scenes || []}>{(scene) => <div class="kbsp-tree-scene">
            <button class={(scene.dialogues || []).some((dialogue) => props.selectedDialogueId() === dialogue.dialogue_id) ? "is-active" : ""} type="button" onClick={() => {
              props.setSelectedShotIndex(shotIndex());
              props.setSelectedDialogueId(scene.dialogues?.[0]?.dialogue_id || "");
              props.setScope?.("scene");
              props.scrollToNode(`kbsp-scene-${scene.scene_id}`);
            }}><span class="kbsp-tree-name">{scene.scene_id}</span></button>
            <For each={scene.dialogues || []}>{(dialogue) => <button class={`kbsp-tree-dialogue ${props.selectedDialogueId() === dialogue.dialogue_id ? "is-active" : ""}`} type="button" title={dialogueText(dialogue) || "Empty line"} onClick={() => {
              props.setSelectedShotIndex(shotIndex());
              props.setSelectedDialogueId(dialogue.dialogue_id);
              props.setScope?.("scene");
              props.scrollToNode(`kbsp-dialogue-${dialogue.dialogue_id}`);
            }}>
              <DialogueBindingIcons dialogue={dialogue} setDialogueTalkingHead={props.setDialogueTalkingHead} dialogueVideoPlanBusy={props.dialogueVideoPlanBusy} />
              <span class="kbsp-tree-dialogue-text">{dialogueText(dialogue) || "Empty line..."}</span>
            </button>}</For>
          </div>}</For>
        </div>
      </section>}</For>
    </div>
    <button class="kbsp-left-resize-grip" type="button" title="Resize left navigation" aria-label="Resize left navigation" onMouseDown={props.startResize} />
  </aside>;
}
