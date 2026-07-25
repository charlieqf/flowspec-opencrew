import { createContext, createSignal, useContext, type JSX } from "solid-js";
import { ASRConfigModal } from "./asr/ASRConfigModal";
import { DigitalHumanConfigModal } from "./digital-human/DigitalHumanConfigModal";
import { ImageConfigModal } from "./image/ImageConfigModal";
import { TTSConfigModal } from "./tts/TTSConfigModal";
import { AudioWaveIcon, DigitalHumanIcon, ImageModelIcon, LipSyncModelIcon, TTSModelIcon, VideoModelIcon, VoiceCloneIcon } from "./shared/icons";
import { SyncConfigModal } from "./sync/SyncConfigModal";
import { VideoConfigModal } from "./video/VideoConfigModal";
import { VoiceCloneConfigModal } from "./voice-clone/VoiceCloneConfigModal";

type ModalKind = "asr" | "image" | "video" | "tts" | "voice-clone" | "lipsync" | "digital-human" | null;

type ModelConfigContextValue = {
  activeModal: () => ModalKind;
  openASRConfig: () => void;
  openImageConfig: () => void;
  openVideoConfig: () => void;
  openTTSConfig: () => void;
  openVoiceCloneConfig: () => void;
  openLipSyncConfig: () => void;
  openDigitalHumanConfig: () => void;
  closeModelConfig: () => void;
};

const ModelConfigContext = createContext<ModelConfigContextValue>();

export function ModelConfigProvider(props: { children: JSX.Element }) {
  const [activeModal, setActiveModal] = createSignal<ModalKind>(null);
  const value: ModelConfigContextValue = {
    activeModal,
    openASRConfig: () => setActiveModal("asr"),
    openImageConfig: () => setActiveModal("image"),
    openVideoConfig: () => setActiveModal("video"),
    openTTSConfig: () => setActiveModal("tts"),
    openVoiceCloneConfig: () => setActiveModal("voice-clone"),
    openLipSyncConfig: () => setActiveModal("lipsync"),
    openDigitalHumanConfig: () => setActiveModal("digital-human"),
    closeModelConfig: () => setActiveModal(null),
  };
  return (
    <ModelConfigContext.Provider value={value}>
      {props.children}
    </ModelConfigContext.Provider>
  );
}

function useModelConfig() {
  const ctx = useContext(ModelConfigContext);
  if (!ctx) throw new Error("ModelConfig components must be used inside ModelConfigProvider");
  return ctx;
}

export function ModelConfigActions() {
  const modelConfig = useModelConfig();
  return (
    <>
      <button class="icon-action header-icon-action" type="button" title="Voice Clone Settings" aria-label="Voice Clone Settings" onClick={modelConfig.openVoiceCloneConfig}>
        <VoiceCloneIcon />
      </button>
      <button class="icon-action header-icon-action" type="button" title="TTS Model Settings" aria-label="TTS Model Settings" onClick={modelConfig.openTTSConfig}>
        <TTSModelIcon />
      </button>
      <button class="icon-action header-icon-action" type="button" title="Image Model Settings" aria-label="Image Model Settings" onClick={modelConfig.openImageConfig}>
        <ImageModelIcon />
      </button>
      <button class="icon-action header-icon-action" type="button" title="Video Model Settings" aria-label="Video Model Settings" onClick={modelConfig.openVideoConfig}>
        <VideoModelIcon />
      </button>
      <button class="icon-action header-icon-action" type="button" title="数字人设置" aria-label="数字人设置" onClick={modelConfig.openDigitalHumanConfig}>
        <DigitalHumanIcon />
      </button>
      <button class="icon-action header-icon-action" type="button" title="Sync.so Lip Sync Key Settings" aria-label="Sync.so Lip Sync Key Settings" onClick={modelConfig.openLipSyncConfig}>
        <LipSyncModelIcon />
      </button>
      <button class="icon-action header-icon-action" type="button" title="ASR Model Settings" aria-label="ASR Model Settings" onClick={modelConfig.openASRConfig}>
        <AudioWaveIcon />
      </button>
    </>
  );
}

export function ModelConfigModals() {
  const modelConfig = useModelConfig();
  return (
    <>
      <ASRConfigModal open={modelConfig.activeModal() === "asr"} onClose={modelConfig.closeModelConfig} />
      <ImageConfigModal open={modelConfig.activeModal() === "image"} onClose={modelConfig.closeModelConfig} />
      <VideoConfigModal open={modelConfig.activeModal() === "video"} onClose={modelConfig.closeModelConfig} />
      <DigitalHumanConfigModal open={modelConfig.activeModal() === "digital-human"} onClose={modelConfig.closeModelConfig} />
      <SyncConfigModal open={modelConfig.activeModal() === "lipsync"} onClose={modelConfig.closeModelConfig} />
      <VoiceCloneConfigModal open={modelConfig.activeModal() === "voice-clone"} onClose={modelConfig.closeModelConfig} />
      <TTSConfigModal open={modelConfig.activeModal() === "tts"} onClose={modelConfig.closeModelConfig} />
    </>
  );
}
