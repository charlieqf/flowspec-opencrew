import { For, Show, createEffect, createSignal } from "solid-js";
import FlowIcon from "../components/FlowIcon.jsx";
import { dataItems, normalizeAudioAsset, normalizeVoice, text } from "./digitalHumanModel.js";

function selectedAudioPath(asset) {
  return asset?.path || asset?.asset_path || "";
}

function SelectedAudioCard(props) {
  const asset = () => props.asset?.();
  const src = () => props.assetUrl?.(asset()) || asset()?.url || "";
  return <Show when={asset()} fallback={<div class="dh-selected-audio-empty">请在中间 Audios 区域点击一个声音文件。</div>}>
    <div class="dh-selected-audio-card">
      <audio src={src()} controls preload="none" />
    </div>
  </Show>;
}

export default function VoicePickerModal(props) {
  let cloneFileInput;
  let audioFileInput;
  const defaultLanguage = "Chinese";
  const [tab, setTab] = createSignal("select");
  const [voiceType, setVoiceType] = createSignal("private");
  const [language, setLanguage] = createSignal(defaultLanguage);
  const [gender, setGender] = createSignal("");
  const [items, setItems] = createSignal([]);
  const [loading, setLoading] = createSignal(false);
  const [error, setError] = createSignal("");
  const [voiceName, setVoiceName] = createSignal("");
  const [cloneFile, setCloneFile] = createSignal(null);
  const [creating, setCreating] = createSignal(false);
  const [audioFile, setAudioFile] = createSignal(null);
  const [registeringAudio, setRegisteringAudio] = createSignal(false);
  const selectedAudio = () => props.selectedAudio?.() || null;
  const selectedAssetPath = () => selectedAudioPath(selectedAudio());

  async function loadVoices() {
    if (!props.open?.() || !props.taskId?.()) return;
    setLoading(true);
    setError("");
    try {
      const payload = await props.api.assetLibraryDigitalHumanVoices(props.taskId(), {
        type: voiceType() || "private",
        language: language(),
        gender: gender(),
        limit: 50,
      });
      setItems(dataItems(payload).map(normalizeVoice).filter((item) => item.id));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }

  createEffect(() => {
    if (props.open?.() && tab() === "select") void loadVoices();
  });

  async function cloneVoice() {
    const name = text(voiceName(), cloneFile()?.name || "Cloned Voice");
    if (!cloneFile() && !selectedAssetPath()) {
      setError("请先在中间 Audios 选择音频，或上传音频。");
      return;
    }
    setCreating(true);
    setError("");
    try {
      const result = await props.api.cloneAssetLibraryDigitalHumanVoice(props.taskId(), {
        voiceName: name,
        file: cloneFile(),
        assetPath: selectedAssetPath(),
        language: language(),
        removeBackgroundNoise: true,
      });
      const voice = normalizeVoice({
        voice_id: result?.result?.data?.voice_clone_id || result?.result?.voice_clone_id,
        name,
        language: language(),
        status: "complete",
      });
      props.onSelectVoice?.(voice);
      props.onClose?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setCreating(false);
    }
  }

  async function registerAudio() {
    if (!audioFile() && !selectedAssetPath()) {
      setError("请先在中间 Audios 选择声音文件，或上传声音文件。");
      return;
    }
    if (!audioFile() && selectedAudio()) {
      props.onSelectAudio?.(normalizeAudioAsset(selectedAudio()));
      props.onClose?.();
      return;
    }
    setRegisteringAudio(true);
    setError("");
    try {
      const result = await props.api.registerAssetLibraryDigitalHumanAudio(props.taskId(), {
        file: audioFile(),
        assetPath: selectedAssetPath(),
      });
      props.onSelectAudio?.(normalizeAudioAsset(result?.asset || {}));
      props.onClose?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setRegisteringAudio(false);
    }
  }

  return <Show when={props.open?.()}>
    <div class="dh-modal-backdrop" role="presentation" onClick={() => props.onClose?.()}>
      <section class="dh-modal" role="dialog" aria-label="Voice" onClick={(event) => event.stopPropagation()}>
        <header class="dh-modal-head">
          <strong>Voice</strong>
          <button type="button" onClick={() => props.onClose?.()} aria-label="Close"><FlowIcon name="close" /></button>
        </header>
        <div class="dh-tabs dh-tabs-three">
          <button class={tab() === "select" ? "is-active" : ""} type="button" onClick={() => setTab("select")}>选择声音</button>
          <button class={tab() === "clone" ? "is-active" : ""} type="button" onClick={() => setTab("clone")}>克隆声音</button>
          <button class={tab() === "audio" ? "is-active" : ""} type="button" onClick={() => setTab("audio")}>声音文件生成</button>
        </div>
        <Show when={error()}><div class="dh-error">{error()}</div></Show>

        <Show when={tab() === "select"}>
          <div class="dh-filter-row">
            <select value={voiceType()} onChange={(event) => setVoiceType(event.currentTarget.value)}>
              <option value="private">Private</option>
              <option value="public">Public</option>
            </select>
            <input value={language()} onInput={(event) => setLanguage(event.currentTarget.value)} placeholder="Language" />
            <select value={gender()} onChange={(event) => setGender(event.currentTarget.value)}>
              <option value="">All</option>
              <option value="female">Female</option>
              <option value="male">Male</option>
            </select>
            <button type="button" onClick={() => void loadVoices()} disabled={loading()}>{loading() ? "Loading" : "Refresh"}</button>
          </div>
          <Show when={!loading()} fallback={<div class="dh-empty">Loading voices...</div>}>
            <Show when={items().length} fallback={<div class="dh-empty">没有可用声音</div>}>
              <div class="dh-list">
                <For each={items()}>{(item) => (
                  <article class="dh-list-item">
                    <div>
                      <strong>{item.name}</strong>
                      <span>{[item.language, item.gender].filter(Boolean).join(" · ") || "云端声音"}</span>
                    </div>
                    <Show when={item.preview_audio_url}><audio src={item.preview_audio_url} controls preload="none" /></Show>
                    <button type="button" onClick={() => { props.onSelectVoice?.(item); props.onClose?.(); }}>选择</button>
                  </article>
                )}</For>
              </div>
            </Show>
          </Show>
        </Show>

        <Show when={tab() === "clone"}>
          <div class="dh-create-form">
            <label>声音名称<input value={voiceName()} onInput={(event) => setVoiceName(event.currentTarget.value)} placeholder="Voice name" /></label>
            <label>语言<input value={language()} onInput={(event) => setLanguage(event.currentTarget.value)} placeholder="zh / en / auto" /></label>
            <div class="dh-field-group">
              <strong>Asset Library Audio</strong>
              <SelectedAudioCard asset={selectedAudio} assetUrl={props.assetUrl} />
            </div>
            <input ref={cloneFileInput} type="file" accept="audio/*,.wav,.mp3,.m4a,.aac,.ogg,.flac,.opus,.aiff,.caf" hidden onChange={(event) => setCloneFile(event.currentTarget.files?.[0] || null)} />
            <button type="button" class="dh-secondary" onClick={() => cloneFileInput?.click()}><FlowIcon name="audio" />{cloneFile() ? "已选择上传音频" : "上传音频"}</button>
            <button type="button" class="dh-primary" disabled={creating()} onClick={() => void cloneVoice()}>{creating() ? "克隆中" : "克隆并选择声音"}</button>
          </div>
        </Show>

        <Show when={tab() === "audio"}>
          <div class="dh-create-form">
            <p class="dh-help">该模式会用声音文件本身驱动 Avatar 口型，不使用 voice_id，也不发送 script。</p>
            <div class="dh-field-group">
              <strong>Asset Library Audio</strong>
              <SelectedAudioCard asset={selectedAudio} assetUrl={props.assetUrl} />
            </div>
            <input ref={audioFileInput} type="file" accept="audio/*,.wav,.mp3,.m4a,.aac,.ogg,.flac,.opus,.aiff,.caf" hidden onChange={(event) => setAudioFile(event.currentTarget.files?.[0] || null)} />
            <button type="button" class="dh-secondary" onClick={() => audioFileInput?.click()}><FlowIcon name="audio" />{audioFile() ? "已选择上传声音文件" : "上传声音文件"}</button>
            <button type="button" class="dh-primary" disabled={registeringAudio()} onClick={() => void registerAudio()}>{registeringAudio() ? "处理中" : "使用该声音文件"}</button>
          </div>
        </Show>
      </section>
    </div>
  </Show>;
}
