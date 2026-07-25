import { MediaConfigModalBase } from "../shared/MediaConfigModalBase";
import { modelConfigApi } from "../shared/api";

export function DigitalHumanConfigModal(props: { open: boolean; onClose: () => void }) {
  return (
    <MediaConfigModalBase
      open={props.open}
      title="数字人设置"
      kind="digital-human"
      onClose={props.onClose}
      loadConfig={modelConfigApi.digitalHumanConfig}
      saveConfig={modelConfigApi.digitalHumanConfigSave}
      testConnection={modelConfigApi.digitalHumanConnectionTest}
    />
  );
}
