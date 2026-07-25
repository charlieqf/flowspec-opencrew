import { MediaConfigModalBase } from "../shared/MediaConfigModalBase";
import { modelConfigApi } from "../shared/api";

export function SyncConfigModal(props: { open: boolean; onClose: () => void }) {
  return (
    <MediaConfigModalBase
      open={props.open}
      title="Lip Sync Settings"
      kind="lipsync"
      onClose={props.onClose}
      loadConfig={modelConfigApi.lipsyncConfig}
      saveConfig={modelConfigApi.lipsyncConfigSave}
      testConnection={modelConfigApi.lipsyncConnectionTest}
    />
  );
}
