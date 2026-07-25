import { useOpenCrewAppController } from "./shell/useOpenCrewAppController.jsx";
import OpenCrewShellView from "./shell/OpenCrewShellView.jsx";

export default function App() {
    const controller = useOpenCrewAppController();
    return <OpenCrewShellView {...controller} />;
}
