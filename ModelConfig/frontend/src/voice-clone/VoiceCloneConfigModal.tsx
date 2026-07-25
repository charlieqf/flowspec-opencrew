import { TTSConfigModal } from "../tts/TTSConfigModal";

export function VoiceCloneConfigModal(props: { open: boolean; onClose: () => void }) {
  return (
    <TTSConfigModal
      open={props.open}
      onClose={props.onClose}
      kind="voice-clone"
      title="Voice Clone Settings"
    />
  );
}
