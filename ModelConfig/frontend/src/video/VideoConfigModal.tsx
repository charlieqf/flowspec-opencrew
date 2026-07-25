import { MediaConfigModalBase } from "../shared/MediaConfigModalBase";
import { modelConfigApi } from "../shared/api";

export function VideoConfigModal(props: { open: boolean; onClose: () => void }) {
  return (
    <MediaConfigModalBase
      open={props.open}
      title="Video Model Settings"
      kind="video"
      onClose={props.onClose}
      loadConfig={modelConfigApi.videoConfig}
      saveConfig={modelConfigApi.videoConfigSave}
      testConnection={modelConfigApi.videoConnectionTest}
    />
  );
}

