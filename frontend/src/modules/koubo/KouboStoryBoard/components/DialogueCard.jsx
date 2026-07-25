import { Show, createEffect, createSignal, onCleanup } from "solid-js";
import { kbApi } from "../kouboStoryboardApi.js";
import { allDialogues, dialogueText, fmtSeconds, spokenCharCount } from "../kouboStoryboardModel.js";
import { assetKind } from "../kouboStoryboardAssets.js";
import { AudioLinesIcon, CutawayIcon, FilmIcon, ImageIcon, PauseIcon, PictureInPictureIcon, PlayIcon, PlusIcon, SaveIcon, ScissorIcon, XIcon } from "../kouboStoryboardIcons.jsx";
import { toggleVideoPictureInPicture } from "../../UploadAssetLibrary/videoPictureInPicture.js";

export default function DialogueCard(props) {
  const dialogueWorkingAssets = () => {
    const assets = props.dialogue.working_assets && typeof props.dialogue.working_assets === "object" ? props.dialogue.working_assets : {};
    const images = Array.isArray(assets.images) ? assets.images : [];
    return {
      audio: assets.audio || {},
      images,
      video: assets.video || {},
    };
  };
  const originalImagePath = () => props.dialogue.source_image_paths?.[0] || props.dialogue.image_path || "";
  const newImagePath = () => {
    const bound = dialogueWorkingAssets().images?.[0]?.path || props.dialogue.bound_image_path || "";
    return bound && bound !== originalImagePath() ? bound : "";
  };
  const versionedRawFileUrl = (path) => {
    if (!path) return "";
    const version = String(props.mediaVersion?.() || "").trim();
    const url = kbApi.rawFileUrl(props.sessionId(), path);
    return version ? `${url}?v=${encodeURIComponent(version)}` : url;
  };
  const originalImageUrl = () => versionedRawFileUrl(originalImagePath());
  const newImageUrl = () => versionedRawFileUrl(newImagePath());
  const audioPath = () => dialogueWorkingAssets().audio?.path || "";
  const videoSlotState = () => props.videoSlotState?.(props.dialogue.dialogue_id, props.dialogue.dialogue_asset_key) || {};
  const rawVideoPath = () => videoSlotState().raw_video_path || "";
  const finalVideoPath = () => dialogueWorkingAssets().video?.path || videoSlotState().final_video_path || "";
  const finalVideoNeedsBind = () => Boolean(videoSlotState().final_video_exists && !videoSlotState().final_video_bound && finalVideoPath());
  const [audioLoadFailedPath, setAudioLoadFailedPath] = createSignal("");
  const [activeVideoSlots, setActiveVideoSlots] = createSignal({});
  const [mediaOrientations, setMediaOrientations] = createSignal({});
  const rememberMediaOrientation = (slot, width, height) => {
    const next = Number(width) > Number(height) ? "landscape" : Number(height) > 0 ? "portrait" : "";
    if (!next || mediaOrientations()[slot] === next) return;
    setMediaOrientations((value) => ({ ...value, [slot]: next }));
  };
  const mediaOrientationClass = (slot, fallbackSlot = "") => {
    const orientation = mediaOrientations()[slot] || (fallbackSlot ? mediaOrientations()[fallbackSlot] : "");
    return orientation === "landscape" ? "is-landscape" : orientation === "portrait" ? "is-portrait" : "";
  };
  const currentTtsAudioReady = () => {
    const state = (props.sceneAudioState?.() || {})[props.dialogue.dialogue_id] || {};
    return state.phase === "ready" && state.output === audioPath();
  };
  const audioFileMissing = () => Boolean(audioPath() && (audioLoadFailedPath() === audioPath() || (videoSlotState().audio_exists === false && !currentTtsAudioReady())));
  const audioUrl = () => audioPath() && !audioFileMissing() ? versionedRawFileUrl(audioPath()) : "";
  const audioSlotTitle = () => audioFileMissing() ? `Audio 文件缺失：${audioPath()}` : audioPath();
  const rawVideoUrl = () => versionedRawFileUrl(rawVideoPath());
  const finalVideoUrl = () => versionedRawFileUrl(finalVideoPath());
  const videoSlotKey = (slot, url) => `${slot}:${url}`;
  const videoSlotActive = (slot, url) => Boolean(url && activeVideoSlots()[videoSlotKey(slot, url)]);
  const isCutaway = () => props.dialogue.video_plan?.is_talking_head === false;
  const [audioPlaying, setAudioPlaying] = createSignal(false);
  const [audioTimeSeconds, setAudioTimeSeconds] = createSignal(0);
  const [talkingHeadMenu, setTalkingHeadMenu] = createSignal(null);
  let audioEl;
  let rawVideoEl;
  let finalVideoEl;
  let closeTalkingHeadMenuListeners = null;
  const canMerge = () => props.dialogueIndex() > 0;
  const canDelete = () => allDialogues(props.shot).length > 1;
  const hasFollowingInScene = () => props.scene.dialogues.some((_, index) => index > props.dialogueIndex());
  const shotDialogues = () => allDialogues(props.shot).map((item) => item.dialogue);
  const hasFollowingInShot = () => shotDialogues().findIndex((dialogue) => dialogue.dialogue_id === props.dialogue.dialogue_id) < shotDialogues().length - 1;
  const isSceneBoundary = () => !hasFollowingInScene() && hasFollowingInShot();
  const isEditing = () => props.editingDialogueId?.() === props.dialogue.dialogue_id;
  const restoreTextFocus = (target, selectionStart, selectionEnd) => {
    const restore = () => {
      const nextTarget = target?.isConnected
        ? target
        : document.querySelector(`textarea[data-kbsp-dialogue-textarea="${props.dialogue.dialogue_id}"]`);
      const active = document.activeElement;
      const focusFellThrough = !active || active === document.body || active === document.documentElement;
      if (!nextTarget || (active !== nextTarget && !focusFellThrough)) return;
      if (active !== nextTarget) nextTarget.focus({ preventScroll: true });
      try {
        nextTarget.setSelectionRange(selectionStart, selectionEnd);
      } catch {
        // Some IME/browser states do not expose a stable text selection during composition.
      }
    };
    queueMicrotask(restore);
    requestAnimationFrame(restore);
    setTimeout(restore, 0);
  };
  const handleTextInput = (event) => {
    const target = event.currentTarget;
    const selectionStart = target.selectionStart ?? target.value.length;
    const selectionEnd = target.selectionEnd ?? selectionStart;
    props.updateDialogue(props.dialogue.dialogue_id, "text", target.value);
    restoreTextFocus(target, selectionStart, selectionEnd);
  };
  const blockTextDrop = (event) => {
    event.preventDefault();
    event.stopPropagation();
    if (event.dataTransfer) event.dataTransfer.dropEffect = "none";
  };
  const bindSelectedAsset = (event, targetKind) => {
    event.stopPropagation();
    props.selectDialogue(props.shotIndex, props.dialogue.dialogue_id);
    const asset = props.selectedAsset();
    if (!asset) return false;
    const requiredKind = targetKind === "source" ? "image" : targetKind === "raw_video" || targetKind === "final_video" ? "video" : targetKind;
    if (requiredKind && assetKind(asset) !== requiredKind) return false;
    event.preventDefault();
    props.assignAsset(props.dialogue.dialogue_id, asset, targetKind);
    return true;
  };
  const mediaDrop = (event, targetKind) => props.dropAsset(event, props.dialogue.dialogue_id, targetKind);
  const closeTalkingHeadMenu = () => {
    setTalkingHeadMenu(null);
    closeTalkingHeadMenuListeners?.();
    closeTalkingHeadMenuListeners = null;
  };
  const openTalkingHeadMenu = (event) => {
    event.preventDefault();
    event.stopPropagation();
    props.selectDialogue(props.shotIndex, props.dialogue.dialogue_id);
    setTalkingHeadMenu({ x: event.clientX, y: event.clientY });
    closeTalkingHeadMenuListeners?.();
    const onPointerDown = () => closeTalkingHeadMenu();
    const onKeyDown = (keyboardEvent) => {
      if (keyboardEvent.key === "Escape") closeTalkingHeadMenu();
    };
    setTimeout(() => {
      window.addEventListener("pointerdown", onPointerDown);
      window.addEventListener("keydown", onKeyDown);
    }, 0);
    closeTalkingHeadMenuListeners = () => {
      window.removeEventListener("pointerdown", onPointerDown);
      window.removeEventListener("keydown", onKeyDown);
    };
  };
  const markTalkingHead = async (event, value) => {
    event.preventDefault();
    event.stopPropagation();
    closeTalkingHeadMenu();
    await props.setDialogueTalkingHead?.(props.dialogue.dialogue_id, value);
  };
  const audioTimeLabel = () => {
    const seconds = Math.max(0, Math.floor(audioTimeSeconds()));
    return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
  };
  const toggleCardAudio = async (event) => {
    event.stopPropagation();
    if (!audioEl || !audioUrl()) return;
    if (!audioEl.paused) {
      audioEl.pause();
      return;
    }
    await audioEl.play();
  };
  const activateVideoSlot = (event, slot, url) => {
    event.stopPropagation();
    if (!url) return;
    setActiveVideoSlots((value) => ({ ...value, [videoSlotKey(slot, url)]: true }));
  };
  const activateVideoSlotByKeyboard = (event, slot, url) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    activateVideoSlot(event, slot, url);
  };
  const alertPictureInPictureFailure = (message) => {
    if (typeof window !== "undefined" && typeof window.alert === "function") window.alert(message);
  };
  const handleVideoPictureInPicture = async (event, slot) => {
    event.preventDefault();
    event.stopPropagation();
    const videoEl = slot === "raw" ? rawVideoEl : finalVideoEl;
    if (!videoEl) return;
    try {
      await toggleVideoPictureInPicture(videoEl);
    } catch (error) {
      console.warn("Unable to open storyboard video picture-in-picture", error);
      const unsupported = /not supported/i.test(String(error?.message || ""));
      alertPictureInPictureFailure(unsupported
        ? "当前浏览器或页面不支持画中画。请用 Chrome/Safari 打开，或检查浏览器画中画权限。"
        : "画中画未打开。请确认视频可以播放后再试。"
      );
    }
  };
  const generatedAudioDurationIsSuspicious = (duration) => {
    const seconds = Number(duration || 0);
    const chars = spokenCharCount(dialogueText(props.dialogue));
    if (!Number.isFinite(seconds) || seconds <= 0 || chars <= 0) return false;
    return seconds > Math.max(4, chars * 0.8) && seconds > chars;
  };
  const handleAudioMetadata = () => {
    const sourceType = String(dialogueWorkingAssets().audio?.source_type || "");
    if (sourceType === "generated" && generatedAudioDurationIsSuspicious(audioEl?.duration)) {
      audioEl?.pause();
    }
  };
  createEffect(() => {
    const path = audioPath();
    audioUrl();
    if (audioLoadFailedPath() && audioLoadFailedPath() !== path) setAudioLoadFailedPath("");
    if (audioEl) {
      audioEl.pause();
      setAudioPlaying(false);
    }
  });
  onCleanup(() => {
    audioEl?.pause();
    closeTalkingHeadMenuListeners?.();
  });
  const selectAndMaybeBindAsset = (event) => {
    if (event.target?.closest?.(".kbsp-dialogue-duration-title, .kbsp-dialogue-delete, .kbsp-dialogue-merge, .kbsp-media-slot button, audio, video, textarea")) return;
    props.selectDialogue(props.shotIndex, props.dialogue.dialogue_id);
    event.stopPropagation();
	    if (!props.selectedAsset()) return;
	    event.preventDefault();
	    props.assignAsset(props.dialogue.dialogue_id, props.selectedAsset());
  };
  return <div class={`kbsp-dialogue-unit ${props.selectedDialogueId() === props.dialogue.dialogue_id ? "is-active" : ""}`}>
    <div class={`kbsp-dialogue-card ${props.selectedDialogueId() === props.dialogue.dialogue_id ? "is-active" : ""} ${props.selectedAsset() ? "can-bind" : ""}`} id={`kbsp-dialogue-${props.dialogue.dialogue_id}`} data-kbsp-drop="true" data-kbsp-dialogue-id={props.dialogue.dialogue_id} data-kbsp-dialogue-asset-key={props.dialogue.dialogue_asset_key || ""} onClick={selectAndMaybeBindAsset}>
      <div class="kbsp-grip">⋮⋮</div>
      <div class="kbsp-dialogue-layout">
        <div class="kbsp-dialogue-text-panel" onDragEnter={blockTextDrop} onDragOver={blockTextDrop} onDrop={blockTextDrop}>
          <div class="kbsp-dialogue-duration-title" title={`Duration ${Number(props.dialogue.duration || 0).toFixed(2)}s`}>
            <span>DURATION</span>
            <strong>{fmtSeconds(props.dialogue.duration)}</strong>
          </div>
          <textarea rows="2" value={props.dialogue.text || ""} data-kbsp-dialogue-textarea={props.dialogue.dialogue_id} placeholder="Action lines or dialogue..." onFocus={() => {
            props.selectDialogue(props.shotIndex, props.dialogue.dialogue_id);
            props.setEditingDialogueId?.(props.dialogue.dialogue_id);
          }} onBlur={() => {
            if (props.editingDialogueId?.() === props.dialogue.dialogue_id) props.setEditingDialogueId?.("");
          }} onInput={handleTextInput} onDragEnter={blockTextDrop} onDragOver={blockTextDrop} onDrop={blockTextDrop} />
        </div>
        <div class="kbsp-dialogue-media-panel">
          <div class="kbsp-media-frames">
            <div class={`kbsp-audio-chip ${audioPath() ? "has-media" : ""} ${audioFileMissing() ? "is-missing" : ""} ${props.selectedAsset() && assetKind(props.selectedAsset()) === "audio" ? "can-bind" : ""}`} title={audioSlotTitle()} data-kbsp-drop="true" data-kbsp-drop-kind="audio" data-kbsp-dialogue-id={props.dialogue.dialogue_id} onClick={(event) => bindSelectedAsset(event, "audio")} onDragEnter={(event) => props.allowAssetDrop(event, "audio")} onDragOver={(event) => props.allowAssetDrop(event, "audio")} onDrop={(event) => mediaDrop(event, "audio")}>
              <div class="kbsp-media-label"><AudioLinesIcon /><span>Audio</span></div>
              <Show when={audioFileMissing()} fallback={<Show when={audioUrl()} fallback={<div class="kbsp-audio-empty"><AudioLinesIcon /></div>}>
                <div class="kbsp-audio-bar">
                  <button class="kbsp-audio-play-button" type="button" title={audioPlaying() ? "Pause Audio" : "Play Audio"} aria-label={audioPlaying() ? "Pause Audio" : "Play Audio"} onClick={toggleCardAudio}>{audioPlaying() ? <PauseIcon /> : <PlayIcon />}</button>
                  <span>{audioTimeLabel()}</span>
                  <div class="kbsp-audio-mini-track"><i style={{ width: `${Math.min(100, (audioEl?.duration ? (audioTimeSeconds() / audioEl.duration) * 100 : 0))}%` }} /></div>
                </div>
                <audio ref={audioEl} src={audioUrl()} preload="none" onClick={(event) => event.stopPropagation()} onLoadedMetadata={handleAudioMetadata} onError={() => setAudioLoadFailedPath(audioPath())} onPlay={() => setAudioPlaying(true)} onPause={() => setAudioPlaying(false)} onEnded={() => setAudioPlaying(false)} onTimeUpdate={() => setAudioTimeSeconds(audioEl?.currentTime || 0)} />
              </Show>}>
                <div class="kbsp-audio-missing"><AudioLinesIcon /><span>文件缺失</span></div>
              </Show>
              <Show when={audioPath()}><button class="kbsp-audio-remove" type="button" title="Remove Audio" onClick={(event) => { event.stopPropagation(); void props.assignAsset(props.dialogue.dialogue_id, "", "audio"); }}><XIcon /></button></Show>
            </div>
            <div class={`kbsp-media-slot kbsp-media-frame kbsp-media-original ${mediaOrientationClass("original")} ${originalImageUrl() ? "has-media" : ""} ${isCutaway() ? "is-cutaway" : ""} ${props.selectedAsset() && assetKind(props.selectedAsset()) === "image" ? "can-bind" : ""}`} data-kbsp-drop="true" data-kbsp-drop-kind="source" data-kbsp-dialogue-id={props.dialogue.dialogue_id} onContextMenu={openTalkingHeadMenu} onClick={(event) => {
              if (bindSelectedAsset(event, "source")) return;
              event.stopPropagation();
              if (originalImageUrl()) props.openImage({ path: originalImagePath(), label: "Original" });
            }} onDragEnter={(event) => props.allowAssetDrop(event, "image")} onDragOver={(event) => props.allowAssetDrop(event, "image")} onDrop={(event) => mediaDrop(event, "source")}>
              <div class="kbsp-media-label">{isCutaway() ? <CutawayIcon /> : <ImageIcon />}<span>原图</span></div>
              <Show when={isCutaway()}><span class="kbsp-cutaway-badge">空镜</span></Show>
              <Show when={originalImageUrl()} fallback={<div class="kbsp-drop-empty">{isCutaway() ? <CutawayIcon /> : <ImageIcon />}</div>}>
                <img src={originalImageUrl()} loading="lazy" draggable="false" onLoad={(event) => rememberMediaOrientation("original", event.currentTarget.naturalWidth, event.currentTarget.naturalHeight)} />
                <button type="button" title="Remove Original Image" onClick={(event) => {
                  event.stopPropagation();
                  void props.assignAsset(props.dialogue.dialogue_id, "", "source");
                }}><XIcon /></button>
              </Show>
              <Show when={talkingHeadMenu()}><div class="kbsp-talking-head-menu" style={{ left: `${talkingHeadMenu().x}px`, top: `${talkingHeadMenu().y}px` }} onPointerDown={(event) => event.stopPropagation()} onClick={(event) => event.stopPropagation()}>
                <button type="button" disabled={props.dialogueVideoPlanBusy?.()} onClick={(event) => markTalkingHead(event, false)}><CutawayIcon />设置空镜</button>
                <button type="button" disabled={props.dialogueVideoPlanBusy?.()} onClick={(event) => markTalkingHead(event, true)}><ImageIcon />标记口播</button>
              </div></Show>
            </div>
            <div class={`kbsp-media-slot kbsp-media-frame kbsp-media-new ${mediaOrientationClass("new")} ${newImageUrl() ? "has-media" : ""} ${props.selectedAsset() && assetKind(props.selectedAsset()) === "image" ? "can-bind" : ""}`} data-kbsp-drop="true" data-kbsp-drop-kind="image" data-kbsp-dialogue-id={props.dialogue.dialogue_id} onClick={(event) => {
              if (bindSelectedAsset(event, "image")) return;
              if (newImageUrl()) props.openImage({ path: newImagePath(), label: props.dialogue.text || props.dialogue.dialogue_id });
            }} onDragEnter={(event) => props.allowAssetDrop(event, "image")} onDragOver={(event) => props.allowAssetDrop(event, "image")} onDrop={(event) => mediaDrop(event, "image")}>
              <div class="kbsp-media-label"><ImageIcon /><span>新图</span></div>
              <Show when={newImageUrl()} fallback={<div class="kbsp-drop-empty"><ImageIcon /></div>}>
                <img src={newImageUrl()} loading="lazy" draggable="false" onLoad={(event) => rememberMediaOrientation("new", event.currentTarget.naturalWidth, event.currentTarget.naturalHeight)} />
                <button type="button" title="Remove Image" onClick={(event) => { event.stopPropagation(); void props.assignAsset(props.dialogue.dialogue_id, "", "image"); }}><XIcon /></button>
              </Show>
            </div>
            <div class={`kbsp-media-slot kbsp-media-frame kbsp-media-video kbsp-media-raw-video ${mediaOrientationClass("raw", "new")} ${rawVideoUrl() ? "has-media" : ""} ${props.selectedAsset() && assetKind(props.selectedAsset()) === "video" ? "can-bind" : ""}`} data-kbsp-drop="true" data-kbsp-drop-kind="raw_video" data-kbsp-dialogue-id={props.dialogue.dialogue_id} onClick={(event) => bindSelectedAsset(event, "raw_video")} onDragEnter={(event) => props.allowAssetDrop(event, "video")} onDragOver={(event) => props.allowAssetDrop(event, "video")} onDrop={(event) => mediaDrop(event, "raw_video")}>
              <div class="kbsp-media-label"><FilmIcon /><span>新视频</span></div>
              <Show when={rawVideoUrl()} fallback={<div class="kbsp-drop-empty"><FilmIcon /></div>}>
                <Show when={videoSlotActive("raw", rawVideoUrl())} fallback={<div class="kbsp-video-thumb-placeholder" role="button" tabIndex="0" title="点击预览视频" onClick={(event) => activateVideoSlot(event, "raw", rawVideoUrl())} onKeyDown={(event) => activateVideoSlotByKeyboard(event, "raw", rawVideoUrl())}><FilmIcon /><span>预览</span></div>}>
                  <video ref={(el) => { rawVideoEl = el; }} src={rawVideoUrl()} preload="metadata" controls playsInline onLoadedMetadata={(event) => rememberMediaOrientation("raw", event.currentTarget.videoWidth, event.currentTarget.videoHeight)} onClick={(event) => event.stopPropagation()} />
                  <button class="kbsp-slot-pip" type="button" title="画中画" aria-label="画中画预览新视频" onClick={(event) => void handleVideoPictureInPicture(event, "raw")}><PictureInPictureIcon /></button>
                </Show>
                <button type="button" title="Remove Raw Video" onClick={(event) => { event.stopPropagation(); void props.assignAsset(props.dialogue.dialogue_id, "", "raw_video"); }}><XIcon /></button>
              </Show>
            </div>
            <div class={`kbsp-media-slot kbsp-media-frame kbsp-media-video kbsp-media-final-video ${mediaOrientationClass("final", "new")} ${finalVideoUrl() ? "has-media" : ""} ${props.selectedAsset() && assetKind(props.selectedAsset()) === "video" ? "can-bind" : ""}`} data-kbsp-drop="true" data-kbsp-drop-kind="final_video" data-kbsp-dialogue-id={props.dialogue.dialogue_id} onClick={(event) => bindSelectedAsset(event, "final_video")} onDragEnter={(event) => props.allowAssetDrop(event, "video")} onDragOver={(event) => props.allowAssetDrop(event, "video")} onDrop={(event) => mediaDrop(event, "final_video")}>
              <div class="kbsp-media-label"><FilmIcon /><span>终视频</span></div>
              <Show when={finalVideoUrl()} fallback={<div class="kbsp-drop-empty"><FilmIcon /></div>}>
                <Show when={videoSlotActive("final", finalVideoUrl())} fallback={<div class="kbsp-video-thumb-placeholder" role="button" tabIndex="0" title="点击预览视频" onClick={(event) => activateVideoSlot(event, "final", finalVideoUrl())} onKeyDown={(event) => activateVideoSlotByKeyboard(event, "final", finalVideoUrl())}><FilmIcon /><span>预览</span></div>}>
                  <video ref={(el) => { finalVideoEl = el; }} src={finalVideoUrl()} preload="metadata" controls playsInline onLoadedMetadata={(event) => rememberMediaOrientation("final", event.currentTarget.videoWidth, event.currentTarget.videoHeight)} onClick={(event) => event.stopPropagation()} />
                  <button class="kbsp-slot-pip" type="button" title="画中画" aria-label="画中画预览终视频" onClick={(event) => void handleVideoPictureInPicture(event, "final")}><PictureInPictureIcon /></button>
                </Show>
                <Show when={finalVideoNeedsBind()}>
                  <button class="kbsp-slot-confirm" type="button" title="Confirm Final Binding" onClick={(event) => { event.stopPropagation(); void props.assignAsset(props.dialogue.dialogue_id, finalVideoPath(), "final_video"); }}><SaveIcon /></button>
                </Show>
                <button type="button" title="Remove Final Video" onClick={(event) => { event.stopPropagation(); void props.assignAsset(props.dialogue.dialogue_id, "", "final_video"); }}><XIcon /></button>
              </Show>
            </div>
          </div>
        </div>
      </div>
      <Show when={canMerge()}><button class="kbsp-dialogue-merge" data-kbsp-action="merge-dialogue-up" type="button" title="向上合并对话" onClick={(event) => { event.stopPropagation(); props.mergeDialogueUp(props.shot.shot_id, props.scene.scene_id, props.dialogue.dialogue_id); }}>向上合并</button></Show>
      <button class="kbsp-dialogue-delete" data-kbsp-action="delete-dialogue" type="button" title="Delete Dialogue" disabled={!canDelete()} onClick={(event) => { event.stopPropagation(); props.deleteDialogue(props.shot.shot_id, props.scene.scene_id, props.dialogue.dialogue_id); }}><XIcon /></button>
    </div>
    <div class={`kbsp-dialogue-break ${isEditing() ? "is-editing" : ""} ${isSceneBoundary() ? "is-scene-boundary" : ""}`} data-kbsp-boundary={isSceneBoundary() ? "scene" : "dialogue"}>
      <span></span>
      <div>
        <button data-kbsp-action="split-scene" type="button" title="拆分场景" disabled={!hasFollowingInScene()} onClick={(event) => { event.stopPropagation(); props.splitScene(props.shot.shot_id, props.scene.scene_id, props.dialogue.dialogue_id); }}><ScissorIcon />场景</button>
        <button data-kbsp-action="split-shot" type="button" title="拆分分镜" disabled={!hasFollowingInShot()} onClick={(event) => { event.stopPropagation(); props.splitShot(props.shot.shot_id, props.scene.scene_id, props.dialogue.dialogue_id); }}><ScissorIcon />分镜</button>
        <button data-kbsp-action="add-dialogue" type="button" title="添加对话" onClick={(event) => { event.stopPropagation(); props.addDialogueAfter(props.shot.shot_id, props.scene.scene_id, props.dialogue.dialogue_id); }}><PlusIcon />对话</button>
      </div>
    </div>
  </div>;
}
