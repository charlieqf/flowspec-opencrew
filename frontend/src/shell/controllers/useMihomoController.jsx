import { createSignal } from "solid-js";
import { api } from "../../lib/api";

export function useMihomoController() {
    const [mihomoConfig, setMihomoConfig] = createSignal(null);
    const [mihomoSubscriptionUrl, setMihomoSubscriptionUrl] = createSignal("");
    const [mihomoBusy, setMihomoBusy] = createSignal(false);
    const [mihomoError, setMihomoError] = createSignal("");
    const [mihomoTestResult, setMihomoTestResult] = createSignal(null);

    const loadMihomoConfig = async () => {
        const res = await api.mihomoConfig();
        setMihomoConfig(res);
        setMihomoSubscriptionUrl("");
    };
    const saveMihomoConfig = async (enabled = mihomoConfig()?.enabled ?? false) => {
        setMihomoBusy(true);
        setMihomoError("");
        try {
            const res = await api.mihomoConfigSave({ enabled, subscription_url: mihomoSubscriptionUrl() });
            setMihomoConfig(res);
            setMihomoSubscriptionUrl("");
        }
        catch (err) {
            setMihomoError(err instanceof Error ? err.message : "Failed saving mihomo config");
        }
        finally {
            setMihomoBusy(false);
        }
    };
    const testMihomoConfig = async () => {
        setMihomoBusy(true);
        setMihomoError("");
        try {
            const res = await api.mihomoTest();
            setMihomoTestResult(res);
        }
        catch (err) {
            setMihomoError(err instanceof Error ? err.message : "Failed testing mihomo");
        }
        finally {
            setMihomoBusy(false);
        }
    };

    return {
        mihomoConfig,
        setMihomoConfig,
        mihomoSubscriptionUrl,
        setMihomoSubscriptionUrl,
        mihomoBusy,
        setMihomoBusy,
        mihomoError,
        setMihomoError,
        mihomoTestResult,
        setMihomoTestResult,
        loadMihomoConfig,
        saveMihomoConfig,
        testMihomoConfig,
    };
}
