import { MediaConfigModalBase } from "../shared/MediaConfigModalBase";
import { modelConfigApi } from "../shared/api";

export function ImageConfigModal(props: { open: boolean; onClose: () => void }) {
  return (
    <MediaConfigModalBase
      open={props.open}
      title="Image Model Settings"
      kind="image"
      onClose={props.onClose}
      loadConfig={modelConfigApi.imageConfig}
      saveConfig={modelConfigApi.imageConfigSave}
      testConnection={modelConfigApi.imageConnectionTest}
    />
  );
}

