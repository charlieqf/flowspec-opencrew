import { For, Index, Show, createSignal, onCleanup } from "solid-js";
import FlowIcon from "../components/FlowIcon.jsx";
import { googleTtsScenarioById } from "../../../../shared/tts/googleTtsScenarioGuide";
import "./ttsAgent.css";

function joinWords(words) {
  return (words || []).filter(Boolean).join("、");
}

function rolePromptPrefix(role) {
  return role?.prompt_prefix || joinWords([...(role?.style || []), ...(role?.pace || [])]);
}

function Waveform() {
  const heights = [15, 22, 12, 30, 18, 34, 14, 25, 19, 36, 24, 16, 31, 20, 27, 13, 35, 18, 23, 16, 29, 17, 26, 14];
  return <div class="ual-tts-waveform" aria-hidden="true">
    <For each={heights}>{(height) => <span style={{ height: `${height}px` }} />}</For>
  </div>;
}

function VoicePicker(props) {
  const [open, setOpen] = createSignal(false);
  const options = () => props.options?.() || [];
  const selected = () => options().find((item) => item.voice_id === props.value) || {
    voice_id: props.value || "",
    name: props.value || "Voice",
    gender: "未标注",
    description: "",
  };
  const choose = (voiceId) => {
    props.onChange?.(voiceId);
    setOpen(false);
  };
  return <div class={`ual-tts-voice-picker ${open() ? "is-open" : ""} ${props.placement === "up" ? "is-up" : ""}`} onFocusOut={(event) => {
    if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false);
  }}>
    <button type="button" class="ual-tts-voice-trigger" aria-haspopup="listbox" aria-expanded={open()} title={selected().label || selected().description || selected().voice_id} onClick={() => setOpen(!open())}>
      <span>{selected().name || selected().voice_id}</span>
      <small>{selected().gender || "未标注"}</small>
    </button>
    <Show when={open()}>
      <div class="ual-tts-voice-menu" role="listbox">
        <For each={options()}>{(voice) => {
          const active = () => voice.voice_id === props.value;
          const previewing = () => props.previewing?.() === voice.voice_id;
          return <article class={`ual-tts-voice-option ${active() ? "is-active" : ""}`}>
            <button type="button" class="ual-tts-voice-option-main" role="option" aria-selected={active()} onClick={() => choose(voice.voice_id)}>
              <strong>{voice.name || voice.voice_id}</strong>
              <span>{voice.gender || "未标注"}</span>
              <small>{voice.description || voice.label || voice.voice_id}</small>
            </button>
            <button type="button" class="ual-tts-voice-preview" title={previewing() ? "试听中" : "试听"} aria-label={`试听 ${voice.name || voice.voice_id}`} disabled={previewing()} onClick={(event) => {
              event.stopPropagation();
              void props.onPreview?.(voice.voice_id);
            }}>
              <FlowIcon name={previewing() ? "radioButtonUnchecked" : "audio"} />
            </button>
          </article>;
        }}</For>
      </div>
    </Show>
  </div>;
}

function audioColumns(columns) {
  const value = Number(columns?.() || columns || 6);
  if (value >= 8) return 8;
  if (value >= 6) return 6;
  return 4;
}

function formatDuration(value) {
  const seconds = Math.max(0, Math.round(Number(value || 0)));
  const minutes = Math.floor(seconds / 60);
  return `${minutes}:${String(seconds % 60).padStart(2, "0")}`;
}

function sessionAudioAsset(session) {
  const audio = session?.audio && typeof session.audio === "object" ? session.audio : null;
  const audioPath = audio?.path || audio?.audio_path || "";
  const jsonPath = session?.json_path || session?.agent_session_path || "";
  if (audioPath) {
    return {
      ...audio,
      id: audioPath,
      path: audioPath,
      filename: audio.filename || audioPath.split("/").pop(),
      label: audio.filename || audioPath.split("/").pop(),
      agent_session_path: jsonPath,
      json_path: jsonPath,
      tts_agent_session: session,
      source: "agent",
    };
  }
  if (!session?.roles?.length && !session?.dialogues?.length) return null;
  const filename = `${session.id}.wav`;
  const path = `SessionOutput/storyboard/assets/audios/${filename}`;
  return {
    id: path,
    path,
    filename,
    label: session.title || filename,
    audio_exists: false,
    missing_audio: true,
    agent_session_path: jsonPath,
    json_path: jsonPath,
    tts_agent_session: session,
    source: "agent",
  };
}

export default function TtsAgentWorkspace(props) {
  const controller = props.controller;
  const [openAudioMenuPath, setOpenAudioMenuPath] = createSignal("");
  const [guideOffset, setGuideOffset] = createSignal({ x: 0, y: 0 });
  let stopGuideDrag = null;
  const audios = () => props.audios?.() || [];
  const columns = () => audioColumns(props.imageColumns);
  const assetLabel = (asset) => props.assetLabel?.(asset) || asset?.label || asset?.filename || String(asset?.path || "Audio").split("/").pop();
  const assetPath = (asset) => asset?.path || asset?.history_path || asset?.audio_path || asset?.id || "";
  const assetTrashPath = (asset) => asset?.path || asset?.audio_path || asset?.history_path || asset?.agent_session_path || asset?.json_path || asset?.id || "";
  const audioItems = () => {
    const items = [...audios()];
    const seen = new Set(items.map(assetPath).filter(Boolean));
    for (const session of controller.agentSessions()) {
      const audio = sessionAudioAsset(session);
      const path = assetPath(audio);
      if (!path || seen.has(path)) continue;
      seen.add(path);
      items.push(audio);
    }
    return items;
  };
  const guide = () => controller.guide();
  const guideScenario = () => googleTtsScenarioById(guide()?.scenario_id);
  const guideRole = () => controller.roles().find((role) => role.speaker_id === guide()?.speaker_id) || controller.roles()[0];
  const audioFileStatus = () => {
    if (controller.generationError()) return controller.generationError();
    if (controller.audioState() === "generating") return "生成中";
    if (controller.audioState() === "ready") return "已生成";
    return "等待生成";
  };
  const audioFileStatusTone = () => {
    if (controller.generationError()) return "error";
    if (controller.audioState() === "ready") return "ready";
    if (controller.audioState() === "generating") return "generating";
    return "empty";
  };
  const beginGuideDrag = (event) => {
    if (event.button !== 0 || event.target.closest("button, input, select, textarea")) return;
    event.preventDefault();
    stopGuideDrag?.();
    const startX = event.clientX;
    const startY = event.clientY;
    const startOffset = guideOffset();
    const move = (moveEvent) => {
      setGuideOffset({
        x: startOffset.x + moveEvent.clientX - startX,
        y: startOffset.y + moveEvent.clientY - startY,
      });
    };
    const stop = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", stop);
      window.removeEventListener("pointercancel", stop);
      stopGuideDrag = null;
    };
    stopGuideDrag = stop;
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop);
    window.addEventListener("pointercancel", stop);
  };
  onCleanup(() => stopGuideDrag?.());

  return <section class="ual-tts-workspace">
    <Show when={controller.workspaceMode() === "library"}>
      <section class="ual-tts-card ual-tts-audio-library">
        <header class="ual-tts-card-head">
          <div>
            <h3>Audio 文件</h3>
          </div>
          <button type="button" class="ual-tts-primary ual-tts-icon-only" title="新增 Session" aria-label="新增 Session" onClick={controller.createAgentSession}>
            <FlowIcon name="add" />
          </button>
        </header>
        <div
          class="ual-tts-audio-list"
          style={{
            "--ual-tts-audio-columns": String(columns()),
          }}
        >
          <Show when={audioItems().length} fallback={<div class="ual-tts-empty">暂无 Audio 文件，可以新增 Session 生成一个。</div>}>
            <For each={audioItems()}>{(asset) => {
              const kind = () => controller.audioKind(asset);
              const session = () => controller.sessionForAudio(asset);
              const path = () => assetPath(asset);
              const src = () => controller.audioAssetUrl(asset);
              const canMoveToTrash = () => Boolean(props.onMoveAudioToHistory && assetTrashPath(asset));
              const hasMenu = () => Boolean(session() || canMoveToTrash());
              const menuOpen = () => openAudioMenuPath() === path();
              const playing = () => controller.playingAssetPath() === path();
              return <article class={`ual-tts-audio-item is-${kind()} ${hasMenu() ? "has-menu" : ""} ${menuOpen() ? "is-menu-open" : ""}`} title={assetLabel(asset)}>
                <div class={`ual-tts-compact-audio ${src() ? "" : "is-disabled"}`} aria-label={src() ? "播放音频" : "等待生成，暂不可播放"}>
                  <button type="button" class={`ual-tts-compact-play ${playing() ? "is-playing" : ""}`} disabled={!src()} aria-label={playing() ? "停止播放" : src() ? "播放音频" : "等待生成，暂不可播放"} onClick={(event) => {
                    event.stopPropagation();
                    if (src()) void controller.playAssetAudio(asset);
                  }}></button>
                  <Show when={columns() <= 4}>
                    <span class="ual-tts-compact-time">0:00 / {src() ? formatDuration(asset?.duration_seconds || asset?.duration) : "0:00"}</span>
                  </Show>
                  <span class="ual-tts-compact-progress"><i></i></span>
                  <span class="ual-tts-compact-volume" aria-hidden="true"></span>
                </div>
                <Show when={hasMenu()}>
                  <div class="ual-tts-audio-menu-wrap">
                    <button type="button" class="ual-tts-audio-menu-button" title="更多" aria-label="更多" aria-expanded={menuOpen()} onClick={(event) => {
                      event.stopPropagation();
                      setOpenAudioMenuPath(menuOpen() ? "" : path());
                    }}>
                      <FlowIcon name="moreVert" />
                    </button>
                    <Show when={menuOpen()}>
                      <div class="ual-tts-audio-menu" role="menu" onClick={(event) => event.stopPropagation()}>
                        <Show when={session()}>
                          <button type="button" role="menuitem" onClick={() => {
                            setOpenAudioMenuPath("");
                            controller.loadAgentSession(session());
                          }}>查看 Session 详情</button>
                        </Show>
                        <Show when={session() && canMoveToTrash()}>
                          <hr />
                        </Show>
                        <Show when={canMoveToTrash()}>
                          <button type="button" role="menuitem" class="is-danger" onClick={async () => {
                            setOpenAudioMenuPath("");
                            await props.onMoveAudioToHistory?.(asset);
                            const currentSession = session();
                            if (currentSession?.id) controller.removeAgentSession?.(currentSession.id);
                          }}><FlowIcon name="delete" />Move to Trash</button>
                        </Show>
                      </div>
                    </Show>
                  </div>
                </Show>
              </article>;
            }}</For>
          </Show>
        </div>
      </section>
    </Show>

    <Show when={controller.workspaceMode() === "upload"}>
      <section class="ual-tts-card">
        <header class="ual-tts-card-head">
          <div>
            <h3>{assetLabel(controller.selectedUploadAsset())}</h3>
            <p>Upload 音频只支持播放，不进入角色表和 TTS 表。</p>
          </div>
          <button type="button" onClick={controller.backToAudioLibrary}><FlowIcon name="arrowBack" /> 返回 Audio 文件</button>
        </header>
        <div class="ual-tts-audio-grid">
          <div class={`ual-tts-audio-result ${controller.audioPlaying() ? "is-playing" : ""}`}>
            <button type="button" class="ual-tts-play-disc" title={controller.audioPlaying() ? "停止播放" : "播放声音"} aria-label={controller.audioPlaying() ? "停止播放" : "播放声音"} onClick={() => void controller.playAudio()}>
              <FlowIcon name={controller.audioPlaying() ? "radioButtonUnchecked" : "arrowForward"} />
            </button>
            <div class="ual-tts-audio-control-only">
              <Waveform />
            </div>
          </div>
        </div>
      </section>
    </Show>

    <Show when={controller.workspaceMode() === "session"}>
      <section class="ual-tts-card">
        <header class="ual-tts-card-head">
          <div>
            <h3>{controller.activeSession()?.title || "Agent Session"}</h3>
          </div>
          <button type="button" onClick={controller.backToAudioLibrary}><FlowIcon name="arrowBack" /> 返回 Audio 文件</button>
        </header>
      </section>

      <Show when={controller.started()} fallback={<section class="ual-tts-card"><div class="ual-tts-empty">等待右侧 Agent 生成角色表与 TTS 表</div></section>}>
      <>
        <section class="ual-tts-card ual-tts-table-card">
          <div class="ual-tts-table-title">
            <h3>角色表</h3>
            <button type="button" title="添加角色" onClick={controller.addRole}><FlowIcon name="add" /> 添加角色</button>
          </div>
        <div class="ual-tts-role-table" role="table" aria-label="TTS roles">
          <div class="ual-tts-role-row is-head" role="row">
            <span>Speaker</span>
            <span>Voice</span>
            <span>Prompt Prefix</span>
            <span></span>
          </div>
          <Index each={controller.roles()}>{(role) => (
            <div class="ual-tts-role-row" role="row">
              <input value={role().speaker} onInput={(event) => controller.updateRole(role().speaker_id, { speaker: event.currentTarget.value })} />
              <VoicePicker
                value={role().voice}
                options={controller.voiceOptions}
                previewing={controller.previewingVoiceId}
                onChange={(voice) => controller.updateRole(role().speaker_id, { voice })}
                onPreview={(voice) => controller.previewVoice(voice, { speakerId: role().speaker_id })}
              />
              <input value={rolePromptPrefix(role())} onInput={(event) => controller.updateRole(role().speaker_id, { prompt_prefix: event.currentTarget.value })} />
              <div>
                <button type="button" class="ual-tts-icon-button" title="角色配置" aria-label="角色配置" onClick={() => controller.openRoleGuide(role().speaker_id)}><FlowIcon name="tune" /></button>
                <button type="button" class="ual-tts-icon-button" title="删除角色" aria-label="删除角色" onClick={() => controller.removeRole(role().speaker_id)}><FlowIcon name="delete" /></button>
              </div>
            </div>
          )}</Index>
        </div>
        </section>

        <section class="ual-tts-card ual-tts-table-card">
        <div class="ual-tts-prompt-list" aria-label="TTS prompts">
          <div class="ual-tts-table-title">
            <h3>TTS 表</h3>
            <button type="button" title="添加对白" onClick={controller.addDialogue}><FlowIcon name="add" /> 添加对白</button>
          </div>
          <div class="ual-tts-prompt-row is-head">
            <span>Speaker</span>
            <span>Voice</span>
            <span>Tempo</span>
            <span>Prompt</span>
            <span></span>
          </div>
          <Index each={controller.promptItems()}>{(item) => (
            <article class={`ual-tts-prompt ual-tts-prompt-row ${controller.playingDialogueId() === item().line_id ? "is-playing" : ""}`}>
              <select value={item().speaker_id} onInput={(event) => controller.updateDialogueSpeaker(item().line_id, event.currentTarget.value)}>
                <For each={controller.roles()}>{(role) => <option value={role.speaker_id}>{role.speaker}</option>}</For>
              </select>
              <VoicePicker
                value={item().voice}
                options={controller.voiceOptions}
                previewing={controller.previewingVoiceId}
                placement="up"
                onChange={(voice) => controller.updateDialogue(item().line_id, { voice_id: voice })}
                onPreview={(voice) => controller.previewVoice(voice, { lineId: item().line_id })}
              />
              <input class="ual-tts-tempo-input" type="number" min="0.5" max="2" step="0.05" value={item().tempo} title="Tempo: 1 为原速，越大越快，越小越慢" onInput={(event) => controller.updateDialogue(item().line_id, { tempo: event.currentTarget.value })} />
              <textarea value={item().rendered_prompt} rows="3" onInput={(event) => controller.updateDialogue(item().line_id, { final_prompt: event.currentTarget.value })} />
              <div>
                  <button type="button" class="ual-tts-icon-button" disabled={Boolean(controller.promptPlayDisabledReason())} title={controller.promptPlayDisabledReason() || "播放单句"} aria-label={`播放 ${item().speaker}`} onClick={() => void controller.playSinglePrompt(item().line_id)}>
                    <FlowIcon name="arrowForward" />
                  </button>
                  <button type="button" class="ual-tts-icon-button" title="Prompt 配置" aria-label="Prompt 配置" onClick={() => controller.openPromptGuide(item().line_id)}><FlowIcon name="tune" /></button>
                  <button type="button" class="ual-tts-icon-button" title="删除对白" aria-label="删除对白" onClick={() => controller.removeDialogue(item().line_id)}><FlowIcon name="delete" /></button>
              </div>
            </article>
          )}</Index>
        </div>
        </section>
      </>
      </Show>

    <section class="ual-tts-card">
      <header class="ual-tts-card-head">
        <div>
          <h3 class="ual-tts-title-with-status">声音文件 <span class={`is-${audioFileStatusTone()}`}>{audioFileStatus()}</span></h3>
        </div>
        <button type="button" class="ual-tts-primary" disabled={Boolean(controller.generateDisabledReason())} title={controller.generateDisabledReason() || "生成声音文件"} onClick={() => void controller.generateAudio()}>
          <FlowIcon name="audio" />
          {controller.audioState() === "generating" ? "真实生成中..." : controller.audioState() === "ready" ? "重新生成声音文件" : "生成声音文件"}
        </button>
      </header>
      <div class="ual-tts-audio-grid">
        <div class={`ual-tts-audio-result ${controller.audioState() === "ready" ? "is-ready" : ""} ${controller.audioState() === "generating" ? "is-generating" : ""} ${controller.audioPlaying() ? "is-playing" : ""}`}>
          <button type="button" class="ual-tts-play-disc" disabled={Boolean(controller.playAudioDisabledReason())} title={controller.audioPlaying() ? "停止播放" : controller.playAudioDisabledReason() || "播放声音"} aria-label={controller.audioPlaying() ? "停止播放" : "播放声音"} onClick={() => void controller.playAudio()}>
            <FlowIcon name={controller.audioPlaying() ? "radioButtonUnchecked" : "arrowForward"} />
          </button>
          <div class="ual-tts-audio-control-only">
            <Waveform />
          </div>
        </div>
      </div>
    </section>
    </Show>

    <Show when={guide()}>
      <div class="ual-tts-modal-backdrop" onClick={(event) => {
        if (event.target === event.currentTarget) controller.closeGuide();
      }}>
        <section
          class="ual-tts-guide-modal"
          role="dialog"
          aria-modal="true"
          aria-labelledby="ual-tts-guide-title"
          style={{
            "--ual-guide-drag-x": `${guideOffset().x}px`,
            "--ual-guide-drag-y": `${guideOffset().y}px`,
          }}
        >
          <header class="ual-tts-guide-drag-handle" onPointerDown={beginGuideDrag}>
            <div>
              <h3 id="ual-tts-guide-title">{guide()?.title} <span>{guideRole()?.speaker}</span></h3>
            </div>
            <div class="ual-tts-guide-actions">
              <button type="button" class="ual-tts-primary" title={guide()?.kind === "role" ? "保存并套用到当前角色" : "保存并套用到当前单句"} aria-label={guide()?.kind === "role" ? "保存并套用到当前角色" : "保存并套用到当前单句"} onClick={controller.applyGuide}>
                <FlowIcon name="check" />
              </button>
              <button type="button" title="Close" aria-label="Close" onClick={controller.closeGuide}><FlowIcon name="close" /></button>
            </div>
          </header>
          <div class="ual-tts-guide-grid">
            <div class="ual-tts-guide-list">
              <div class="ual-tts-guide-list-head">
                <h4>情景列表</h4>
                <label class="ual-tts-guide-voice-field">
                  <span>Voice</span>
                  <VoicePicker
                    value={guide()?.voice || ""}
                    options={controller.voiceOptions}
                    previewing={controller.previewingVoiceId}
                    onChange={(voice) => controller.updateGuide({ voice })}
                    onPreview={(voice) => controller.previewVoice(voice, { lineId: guide()?.line_id, speakerId: guide()?.speaker_id })}
                  />
                </label>
              </div>
              <For each={controller.scenarioGuides()}>{(scenario) => (
                <article class={`ual-tts-guide-scenario ${guide()?.scenario_id === scenario.id ? "is-active" : ""}`}>
                  <button type="button" onClick={() => controller.selectGuideScenario(scenario.id)}>
                    <strong>{scenario.label}</strong>
                    <span>{scenario.category}</span>
                  </button>
                  <Show when={controller.guideSelectedWords(scenario.id).length}>
                    <div class="ual-tts-guide-selected">
                      <div>
                        <For each={controller.guideSelectedWords(scenario.id)}>{(word) => (
                          <button type="button" title="删除" onClick={(event) => {
                            event.stopPropagation();
                            controller.removeGuideKeyword(word, scenario.id);
                          }}>{word}<FlowIcon name="close" /></button>
                        )}</For>
                      </div>
                      <button type="button" class="ual-tts-guide-clear" onClick={(event) => {
                        event.stopPropagation();
                        controller.clearGuideKeywords(scenario.id);
                      }}>清空</button>
                    </div>
                  </Show>
                </article>
              )}</For>
            </div>
            <div class="ual-tts-guide-detail">
              <div class="ual-tts-guide-info">
                <strong>{guideScenario()?.infoTitle || guideScenario()?.label}</strong>
                <p>{guideScenario()?.infoBodyZh}</p>
                <div>
                  <For each={guideScenario()?.verifies || []}>{(tag) => (
                    <button
                      type="button"
                      class={`ual-tts-guide-tag ${controller.guideKeywordSelected("验证标签", tag) ? "is-selected" : ""}`}
                      onClick={() => controller.toggleGuideKeyword("验证标签", tag)}
                    >{tag}</button>
                  )}</For>
                </div>
              </div>
              <section class="ual-tts-guide-simple">
                <strong>Simple Prompt</strong>
                <p>{guideScenario()?.simplePrompt}</p>
              </section>
              <h4>{guideScenario()?.label} 关键词字典</h4>
              <For each={guideScenario()?.groups || []}>{(group) => (
                <section>
                  <strong>{group.title}</strong>
                  <p><For each={group.words}>{(word) => (
                    <button
                      type="button"
                      class={`ual-tts-guide-keyword ${controller.guideKeywordSelected(group.title, word) ? "is-selected" : ""}`}
                      onClick={() => controller.toggleGuideKeyword(group.title, word)}
                    >{word}</button>
                  )}</For></p>
                </section>
              )}</For>
            </div>
          </div>
        </section>
      </div>
    </Show>

    <Show when={controller.toast()}>
      <div class="ual-tts-toast"><span></span>{controller.toast()}</div>
    </Show>
  </section>;
}
