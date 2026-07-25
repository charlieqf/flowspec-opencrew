import { createMemo, createSignal, onCleanup } from "solid-js";
import { api } from "../../lib/api";
import {
    FALLBACK_USD_CNY_RATE,
    LIPSYNC_PRICE_COMPARISON,
    MEDIA_PRICE_POINTS,
    formatCnyAmount,
    formatCurrencyAmount,
    unitLabel,
} from "../../lib/meteringFormat.js";

export function useMediaSettingsController(options) {
    const resetConnectionTest = options.resetConnectionTest;
    const setConnectionTests = options.setConnectionTests;
    const updateConnectionTest = options.updateConnectionTest;

    const [mediaDialog, setMediaDialog] = createSignal({ open: false, kind: "image", loading: false, saving: false, error: "" });
    const [mediaConfig, setMediaConfig] = createSignal(null);
    const [mediaApiKeys, setMediaApiKeys] = createSignal({});
    const [mediaPriceListOpen, setMediaPriceListOpen] = createSignal(false);
    const [mediaUnitPriceOpen, setMediaUnitPriceOpen] = createSignal(false);
    const [usdCnyRate, setUsdCnyRate] = createSignal({ rate: FALLBACK_USD_CNY_RATE, date: "fallback", source: "fallback", loading: false, error: "" });
    const [mediaAgentDrag, setMediaAgentDrag] = createSignal(null);
    const mediaDialogTitle = createMemo(() => {
        const kind = mediaDialog().kind;
        if (kind === "image")
            return "Image Model Settings";
        if (kind === "video")
            return "Video Model Settings";
        if (kind === "lipsync")
            return "Lip Sync Settings";
        if (kind === "digital-human")
            return "Digital Human Settings";
        if (kind === "voice-clone")
            return "Voice Clone Settings";
        if (kind === "tts")
            return "TTS Settings";
        return "Media Model Settings";
    });
    const mediaDialogKindLabel = () => {
        const kind = mediaDialog().kind;
        if (kind === "image")
            return "Image";
        if (kind === "video")
            return "Video";
        if (kind === "lipsync")
            return "Lip Sync";
        if (kind === "digital-human")
            return "Digital Human";
        if (kind === "voice-clone")
            return "Voice Clone";
        if (kind === "tts")
            return "TTS";
        return "Media";
    };

    const loadUsdCnyRate = async () => {
        const today = new Date().toISOString().slice(0, 10);
        const current = usdCnyRate();
        if (current.date === today && !current.error)
            return;
        setUsdCnyRate((prev) => ({ ...prev, loading: true }));
        try {
            const res = await fetch("https://open.er-api.com/v6/latest/USD");
            if (!res.ok)
                throw new Error(`HTTP ${res.status}`);
            const payload = await res.json();
            const rate = Number(payload.rates?.CNY);
            if (!Number.isFinite(rate) || rate <= 0)
                throw new Error("USD/CNY rate missing");
            setUsdCnyRate({ rate, date: today, source: "open.er-api.com", loading: false, error: "" });
        }
        catch (err) {
            setUsdCnyRate({ rate: FALLBACK_USD_CNY_RATE, date: today, source: "fallback", loading: false, error: err instanceof Error ? err.message : "Failed loading exchange rate" });
        }
    };

    const mediaPriceRanking = createMemo(() => {
        const availableModels = new Set((mediaConfig()?.providers ?? []).flatMap((provider) => provider.models.map((model) => model.model)));
        const rate = usdCnyRate().rate;
        return MEDIA_PRICE_POINTS
            .filter((item) => item.kind === mediaDialog().kind && availableModels.has(item.model))
            .map((item) => ({
                ...item,
                cnyAmount: item.currency === "USD" ? item.amount * rate : item.amount,
                originalText: `${formatCurrencyAmount(item.amount, item.currency)}/${item.unit}`,
            }))
            .sort((a, b) => a.cnyAmount - b.cnyAmount || a.model.localeCompare(b.model) || a.variant.localeCompare(b.variant));
    });
    const lipsyncPriceComparisonRows = createMemo(() => {
        const rate = usdCnyRate().rate;
        return LIPSYNC_PRICE_COMPARISON.map((row) => {
            const cnyAmount = row.currency === "USD" ? row.amount * rate : row.amount;
            const sourcePrice = row.currency === "USD" ? `${formatCurrencyAmount(row.amount, "USD")}/${unitLabel(row.unit)}` : "";
            return {
                ...row,
                cnyAmount,
                cnyPrice: `${formatCnyAmount(cnyAmount)}/${unitLabel(row.unit)}`,
                conversionNote: sourcePrice ? "按当日汇率换算。" : "",
            };
        });
    });
    const mediaUnitPriceRows = createMemo(() => {
        if (mediaDialog().kind === "lipsync") {
            return lipsyncPriceComparisonRows()
                .map((row) => ({
                    providerLabel: row.provider,
                    model: row.model,
                    variant: row.note,
                    cnyAmount: row.cnyAmount,
                    cnyPrice: row.cnyPrice,
                    originalText: row.currency === "USD" ? `${formatCurrencyAmount(row.amount, row.currency)}/${unitLabel(row.unit)}` : row.cnyPrice,
                }))
                .sort((a, b) => a.cnyAmount - b.cnyAmount || a.providerLabel.localeCompare(b.providerLabel) || a.model.localeCompare(b.model));
        }
        return mediaPriceRanking().map((row) => ({
            providerLabel: row.providerLabel,
            model: row.model,
            variant: row.variant,
            cnyAmount: row.cnyAmount,
            cnyPrice: `${formatCnyAmount(row.cnyAmount)}/${unitLabel(row.unit)}`,
            originalText: row.originalText,
        }));
    });

    let mediaAgentPoolEl;
    const setMediaAgentPoolElement = (element) => { mediaAgentPoolEl = element; };
    let mediaAgentDragCandidate = null;
    const mediaAgentAliases = createMemo(() => mediaConfig()?.agent_model_aliases ?? []);
    const mediaSupportsAgentAliases = (kind = mediaDialog().kind) => kind === "image" || kind === "video";
    const mediaAgentKindLabel = () => mediaDialog().kind === "video" ? "Video" : "Image";
    const mediaProviderLabel = (providerId) => mediaConfig()?.providers.find((item) => item.provider === providerId)?.provider_label || providerId;
    const mediaModelLabel = (providerId, modelId) => {
        const provider = mediaConfig()?.providers.find((item) => item.provider === providerId);
        return provider?.models.find((item) => item.model === modelId)?.label || modelId;
    };
    const mediaProviderConfig = (providerId) => mediaConfig()?.providers.find((item) => item.provider === providerId) || null;
    const mediaCredentialFields = (providerOrId) => {
        const provider = typeof providerOrId === "string" ? mediaProviderConfig(providerOrId) : providerOrId;
        return provider?.credential_fields?.length
            ? provider.credential_fields
            : [{ key: "api_key", label: "API Key", type: "password", required_group: "" }];
    };
    const mediaCredentialKey = (provider, field) => mediaCredentialFields(provider).length === 1 && field.key === "api_key"
        ? provider.provider
        : `${provider.provider}:${field.key}`;
    const mediaCredentialValues = (provider) => mediaCredentialFields(provider).map((field) => ({
        field,
        value: String(mediaApiKeys()[mediaCredentialKey(provider, field)] ?? "").trim(),
    }));
    const mediaCredentialGroupIncomplete = (provider) => {
        const groups = new Map();
        for (const { field, value } of mediaCredentialValues(provider)) {
            if (!field.required_group)
                continue;
            groups.set(field.required_group, [...(groups.get(field.required_group) ?? []), value]);
        }
        return [...groups.values()].some((values) => values.some(Boolean) && values.some((value) => !value));
    };
    const hasMediaKeyInput = (providerId) => {
        const provider = mediaProviderConfig(providerId);
        return Boolean(provider && mediaCredentialValues(provider).length && mediaCredentialValues(provider).every((item) => item.value));
    };
    const mediaProviderApiKeyPayload = (providerId) => {
        const provider = mediaProviderConfig(providerId);
        if (!provider)
            return "";
        const values = mediaCredentialValues(provider);
        if (values.length === 1 && values[0].field.key === "api_key")
            return values[0].value;
        if (!values.some((item) => item.value))
            return "";
        return JSON.stringify(Object.fromEntries(values.map((item) => [item.field.key, item.value])));
    };
    const selectedMediaModel = (provider) => provider.models.find((item) => item.model === provider.model) || provider.models[0] || null;
    const selectedMediaModelPriceText = (provider) => {
        const model = selectedMediaModel(provider);
        if (!model)
            return "";
        if (mediaDialog().kind === "lipsync") {
            const row = lipsyncPriceComparisonRows().find((item) => item.providerId === provider.provider && item.modelId === model.model);
            if (row)
                return row.cnyPrice;
        }
        return model.price_summary || "";
    };
    const defaultAgentAlias = () => {
        const normalized = `${mediaAgentKindLabel()} Model`;
        const used = new Set(mediaAgentAliases().map((item) => String(item.alias || "").toLowerCase()));
        if (!used.has(normalized.toLowerCase()))
            return normalized;
        let index = 2;
        while (used.has(`${normalized} ${index}`.toLowerCase()))
            index += 1;
        return `${normalized} ${index}`;
    };
    const setMediaAgentAliases = (aliases) => {
        setMediaConfig((prev) => prev ? { ...prev, agent_model_aliases: aliases } : prev);
    };
    const addMediaAgentAlias = (provider, model) => {
        if (!mediaSupportsAgentAliases())
            return;
        if (mediaAgentAliases().some((item) => item.provider === provider && item.model === model))
            return;
        const now = Date.now();
        setMediaAgentAliases([
            ...mediaAgentAliases(),
            { alias: defaultAgentAlias(), provider, model, created_at: now, updated_at: now },
        ]);
    };
    const updateMediaAgentAlias = (index, alias) => {
        setMediaAgentAliases(mediaAgentAliases().map((item, itemIndex) => itemIndex === index ? { ...item, alias, updated_at: Date.now() } : item));
    };
    const removeMediaAgentAlias = (index) => {
        setMediaAgentAliases(mediaAgentAliases().filter((_, itemIndex) => itemIndex !== index));
    };
    const removeMediaAgentDragListeners = () => {
        window.removeEventListener("pointermove", handleMediaAgentPointerMove);
        window.removeEventListener("pointerup", handleMediaAgentPointerUp);
        window.removeEventListener("pointercancel", handleMediaAgentPointerUp);
    };
    const finishMediaAgentDrag = (x, y) => {
        const active = mediaAgentDrag();
        const rect = mediaAgentPoolEl?.getBoundingClientRect();
        if (active && rect && x >= rect.left && x <= rect.right && y >= rect.top && y <= rect.bottom) {
            addMediaAgentAlias(active.provider, active.model);
        }
        mediaAgentDragCandidate = null;
        setMediaAgentDrag(null);
        removeMediaAgentDragListeners();
    };
    function handleMediaAgentPointerMove(event) {
        const candidate = mediaAgentDragCandidate;
        if (!candidate)
            return;
        const dx = Math.abs(event.clientX - candidate.startX);
        const dy = Math.abs(event.clientY - candidate.startY);
        if (!candidate.moved && dx + dy < 6)
            return;
        candidate.moved = true;
        event.preventDefault();
        setMediaAgentDrag({ provider: candidate.provider, model: candidate.model, label: candidate.label, x: event.clientX, y: event.clientY });
    }
    function handleMediaAgentPointerUp(event) {
        const candidate = mediaAgentDragCandidate;
        if (!candidate)
            return;
        if (candidate.moved) {
            event.preventDefault();
            finishMediaAgentDrag(event.clientX, event.clientY);
            return;
        }
        mediaAgentDragCandidate = null;
        setMediaAgentDrag(null);
        removeMediaAgentDragListeners();
    }
    const startMediaAgentDrag = (event, provider, model, label) => {
        if (!mediaSupportsAgentAliases() || event.button !== 0)
            return;
        mediaAgentDragCandidate = { provider, model, label, startX: event.clientX, startY: event.clientY, moved: false };
        window.addEventListener("pointermove", handleMediaAgentPointerMove, { passive: false });
        window.addEventListener("pointerup", handleMediaAgentPointerUp, { passive: false });
        window.addEventListener("pointercancel", handleMediaAgentPointerUp, { passive: false });
    };
    onCleanup(removeMediaAgentDragListeners);
    const handleMediaAgentDrop = (event) => {
        if (!mediaSupportsAgentAliases())
            return;
        event.preventDefault();
        const raw = event.dataTransfer?.getData("application/json") || "";
        if (!raw)
            return;
        try {
            const payload = JSON.parse(raw);
            const provider = String(payload.provider || "").trim();
            const model = String(payload.model || "").trim();
            if (provider && model)
                addMediaAgentAlias(provider, model);
        }
        catch {
            // Ignore non-model drags.
        }
    };

    const openMediaDialog = async (kind) => {
        setMediaDialog({ open: true, kind, loading: true, saving: false, error: "" });
        setMediaApiKeys({});
        setMediaPriceListOpen(false);
        setMediaUnitPriceOpen(false);
        void loadUsdCnyRate();
        try {
            const res = await api.mediaModelConfig(kind);
            setMediaConfig(res);
            setMediaDialog((prev) => ({ ...prev, loading: false }));
        }
        catch (err) {
            setMediaDialog((prev) => ({ ...prev, loading: false, error: err instanceof Error ? err.message : `Failed loading ${kind} model config` }));
        }
    };
    const setActiveMediaProvider = (provider) => {
        setMediaConfig((prev) => prev ? {
            ...prev,
            active_provider: provider,
            providers: prev.providers.map((item) => ({ ...item, active: item.provider === provider })),
        } : prev);
    };
    const updateMediaProviderModel = (provider, model) => {
        const current = mediaConfig()?.providers.find((item) => item.provider === provider);
        if (current?.model !== model)
            resetConnectionTest(`media:${mediaDialog().kind}`, provider);
        setMediaConfig((prev) => prev ? {
            ...prev,
            providers: prev.providers.map((item) => item.provider === provider ? { ...item, model } : item),
        } : prev);
    };
    const saveMediaConfig = async () => {
        const current = mediaConfig();
        if (!current)
            return;
        const incompleteProvider = current.providers.find(mediaCredentialGroupIncomplete);
        if (incompleteProvider) {
            setMediaDialog((prev) => ({ ...prev, error: `${incompleteProvider.provider_label || "Provider"} credentials must be filled together, or all left blank to keep the saved credentials.` }));
            return;
        }
        setMediaDialog((prev) => ({ ...prev, saving: true, error: "" }));
        try {
            const res = await api.mediaModelConfigSave(current.kind, {
                active_provider: current.active_provider,
                providers: current.providers.map((item) => ({
                    provider: item.provider,
                    model: item.model,
                    api_key: mediaProviderApiKeyPayload(item.provider),
                    enabled: item.enabled,
                })),
                agent_model_aliases: mediaSupportsAgentAliases(current.kind) ? mediaAgentAliases().map((item) => ({
                    alias: String(item.alias || "").trim(),
                    provider: item.provider,
                    model: item.model,
                    created_at: item.created_at ?? null,
                    updated_at: item.updated_at ?? null,
                })) : undefined,
            });
            setMediaConfig(res);
            setMediaApiKeys({});
            setConnectionTests({});
            setMediaDialog((prev) => ({ ...prev, saving: false, open: false }));
        }
        catch (err) {
            setMediaDialog((prev) => ({ ...prev, saving: false, error: err instanceof Error ? err.message : `Failed saving ${current.kind} model config` }));
        }
    };

    const runMediaConnectionTest = async (provider, model) => {
        const scope = `media:${mediaDialog().kind}`;
        updateConnectionTest(scope, provider, { status: "testing", message: "Testing...", detail: "", expanded: false });
        try {
            const res = await api.mediaModelConnectionTest(mediaDialog().kind, { provider, model });
            updateConnectionTest(scope, provider, { status: res.ok ? "success" : "failed", message: res.message, detail: res.detail || "", expanded: !res.ok });
        }
        catch (err) {
            updateConnectionTest(scope, provider, { status: "failed", message: "Connection failed", detail: err instanceof Error ? err.message : "Unknown error", expanded: true });
        }
    };

    return {
        mediaDialog,
        setMediaDialog,
        mediaConfig,
        setMediaConfig,
        mediaApiKeys,
        setMediaApiKeys,
        mediaPriceListOpen,
        setMediaPriceListOpen,
        mediaUnitPriceOpen,
        setMediaUnitPriceOpen,
        usdCnyRate,
        setUsdCnyRate,
        mediaAgentDrag,
        setMediaAgentDrag,
        mediaDialogTitle,
        mediaDialogKindLabel,
        loadUsdCnyRate,
        mediaPriceRanking,
        lipsyncPriceComparisonRows,
        mediaUnitPriceRows,
        setMediaAgentPoolElement,
        mediaAgentAliases,
        mediaSupportsAgentAliases,
        mediaAgentKindLabel,
        mediaProviderLabel,
        mediaModelLabel,
        mediaCredentialFields,
        mediaCredentialKey,
        hasMediaKeyInput,
        mediaProviderApiKeyPayload,
        selectedMediaModel,
        selectedMediaModelPriceText,
        defaultAgentAlias,
        setMediaAgentAliases,
        addMediaAgentAlias,
        updateMediaAgentAlias,
        removeMediaAgentAlias,
        removeMediaAgentDragListeners,
        finishMediaAgentDrag,
        startMediaAgentDrag,
        handleMediaAgentDrop,
        openMediaDialog,
        setActiveMediaProvider,
        updateMediaProviderModel,
        saveMediaConfig,
        runMediaConnectionTest,
        handleMediaAgentPointerMove,
        handleMediaAgentPointerUp,
    };
}
