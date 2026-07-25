import { createMemo, createSignal } from "solid-js";
import { api } from "../../lib/api";

export function useAsrSettingsController(options) {
    const resetConnectionTest = options.resetConnectionTest;
    const setConnectionTests = options.setConnectionTests;
    const updateConnectionTest = options.updateConnectionTest;

    const [asrDialog, setAsrDialog] = createSignal({ open: false, loading: false, saving: false, error: "", api_key: "" });
    const [asrModels, setAsrModels] = createSignal([]);
    const [asrConfig, setAsrConfig] = createSignal(null);

    const loadAsrConfig = async () => {
        const res = await api.asrConfig();
        setAsrModels(res.models);
        setAsrConfig(res.config);
    };

    const openAsrDialog = async () => {
        setAsrDialog((prev) => ({ ...prev, open: true, loading: true, error: "", api_key: "" }));
        try {
            await loadAsrConfig();
            setAsrDialog((prev) => ({ ...prev, loading: false }));
        }
        catch (err) {
            setAsrDialog((prev) => ({ ...prev, loading: false, error: err instanceof Error ? err.message : "Failed loading ASR config" }));
        }
    };

    const selectedAsrModel = createMemo(() => asrModels().find((item) => item.provider === asrConfig()?.provider && item.model === asrConfig()?.model));
    const asrProviderCards = createMemo(() => {
        const groups = new Map();
        for (const item of asrModels()) {
            if (!groups.has(item.provider))
                groups.set(item.provider, []);
            groups.get(item.provider).push(item);
        }
        return Array.from(groups.entries()).map(([provider, models]) => ({
            provider,
            providerLabel: provider === "aliyun_bailian_fun_asr" ? "Aliyun Bailian" : provider === "local_whisper" ? "Local Whisper" : provider,
            models,
        }));
    });

    const updateAsrModel = (provider, model) => {
        if (asrConfig()?.provider !== provider || asrConfig()?.model !== model)
            resetConnectionTest("asr", provider);
        const option = asrModels().find((item) => item.provider === provider && item.model === model);
        setAsrConfig((prev) => ({
            ...(prev ?? { config_name: "default_asr_provider", provider, model, language: "zh", api_url: option?.api_url ?? "", enabled: true, has_api_key: false, api_key_ref: "", updated_at: null }),
            provider,
            model,
            api_url: option?.api_url ?? prev?.api_url ?? "",
        }));
    };
    const activateAsrProviderForInput = (provider, fallbackModel) => {
        const currentModel = asrConfig()?.provider === provider ? asrConfig()?.model ?? fallbackModel : fallbackModel;
        updateAsrModel(provider, currentModel || "");
    };

    const saveAsrConfig = async () => {
        const current = asrConfig();
        if (!current)
            return;
        setAsrDialog((prev) => ({ ...prev, saving: true, error: "" }));
        try {
            const res = await api.asrConfigSave({
                config_name: "default_asr_provider",
                provider: current.provider,
                model: current.model,
                language: current.language || "zh",
                api_url: current.api_url || selectedAsrModel()?.api_url || "",
                api_key: asrDialog().api_key,
                enabled: current.enabled,
            });
            setAsrModels(res.models);
            setAsrConfig(res.config);
            setConnectionTests({});
            setAsrDialog((prev) => ({ ...prev, saving: false, api_key: "", open: false }));
        }
        catch (err) {
            setAsrDialog((prev) => ({ ...prev, saving: false, error: err instanceof Error ? err.message : "Failed saving ASR config" }));
        }
    };

    const runAsrConnectionTest = async (provider, model) => {
        updateConnectionTest("asr", provider, { status: "testing", message: "Testing...", detail: "", expanded: false });
        try {
            const res = await api.asrConnectionTest({ provider, model });
            updateConnectionTest("asr", provider, { status: res.ok ? "success" : "failed", message: res.message, detail: res.detail || "", expanded: !res.ok });
        }
        catch (err) {
            updateConnectionTest("asr", provider, { status: "failed", message: "Connection failed", detail: err instanceof Error ? err.message : "Unknown error", expanded: true });
        }
    };

    return {
        asrDialog,
        setAsrDialog,
        asrModels,
        setAsrModels,
        asrConfig,
        setAsrConfig,
        loadAsrConfig,
        openAsrDialog,
        selectedAsrModel,
        asrProviderCards,
        updateAsrModel,
        activateAsrProviderForInput,
        saveAsrConfig,
        runAsrConnectionTest,
    };
}
