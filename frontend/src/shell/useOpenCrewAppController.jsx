import { createEffect, createMemo, createSignal, onMount } from "solid-js";
import { api } from "../lib/api";
import { emitDebugError } from "../debug/debugAdapter.js";
import { useShellLayoutController } from "./controllers/useShellLayoutController.jsx";
import { useSessionEventsController } from "./controllers/useSessionEventsController.jsx";
import { useConnectionTestsController } from "./controllers/useConnectionTestsController.jsx";
import { useAsrSettingsController } from "./controllers/useAsrSettingsController.jsx";
import { useMediaSettingsController } from "./controllers/useMediaSettingsController.jsx";
import { useMihomoController } from "./controllers/useMihomoController.jsx";
import { useMeteringController } from "./controllers/useMeteringController.jsx";
import { useShellRoutingController } from "./controllers/useShellRoutingController.jsx";
import {
    DEFAULT_NPC_SERVER_ADDR,
    DEFAULT_NPC_MULTI_ACCOUNT_PATH,
    statusLabel,
    parseJson,
    parsePublishChecks,
    toPublishConfigPayload,
    summarizeNpcResult,
    parseSessionHash,
    parseMeteringTaskHash,
} from "./appShellUtils.jsx";

export function useOpenCrewAppController() {
    const [summary, setSummary] = createSignal(null);
    const [busyStep, setBusyStep] = createSignal(null);
    const [error, setError] = createSignal(null);
    const [authState, setAuthState] = createSignal({ loading: true, enabled: true, configured: false, authenticated: false, role: "", capabilities: {}, debug_console_enabled: false });
    const [authPassword, setAuthPassword] = createSignal("");
    const [authError, setAuthError] = createSignal("");
    const [authBusy, setAuthBusy] = createSignal(false);
    const [opencodeBaseUrl, setOpencodeBaseUrl] = createSignal("");
    const [opencodeUsername, setOpencodeUsername] = createSignal("");
    const [opencodePassword, setOpencodePassword] = createSignal("");
    const [opencodeDirty, setOpencodeDirty] = createSignal(false);
    const [opencodeCandidates, setOpencodeCandidates] = createSignal([]);
    const [discoverAttempted, setDiscoverAttempted] = createSignal(false);
    const [publishUrl, setPublishUrl] = createSignal("");
    const [publishDirty, setPublishDirty] = createSignal(false);
    const [publishPreview, setPublishPreview] = createSignal(null);
    const [publishChecks, setPublishChecks] = createSignal([]);
    const [skills, setSkills] = createSignal({ install: null, run: null, uninstall: null, publish_validate: null });
    const [tasks, setTasks] = createSignal({ install: null, run: null, uninstall: null, publish_validate: null });
    const [taskLogs, setTaskLogs] = createSignal({ install: [], run: [], uninstall: [], publish_validate: [] });
    const [activeTaskIds, setActiveTaskIds] = createSignal({ install: null, run: null, uninstall: null, publish_validate: null });
    const [editorKind, setEditorKind] = createSignal(null);
    const [editorContent, setEditorContent] = createSignal("");
    const [envDialog, setEnvDialog] = createSignal({ open: false, platform: "", arch: "", environment: "" });
    const [runDialog, setRunDialog] = createSignal({ open: false, server_addr: DEFAULT_NPC_SERVER_ADDR, public_base_url: "", vkey: "", conn_type: "tcp", auto_reconnection: true, max_conn: 1000, flow_limit: 1000, rate_limit: 1000, basic_username: "", basic_password: "", crypt: true, compress: true, disconnect_timeout: 60, mode: "tcp", target_addr: "127.0.0.1:18080", server_port: 10000, multi_account_line: "npc=npc.pwd", conf_text: "", multi_account_text: "npc=npc.pwd\n", multi_account_path: DEFAULT_NPC_MULTI_ACCOUNT_PATH, loading: false, result: null, error: "" });
    const [publishGuideOpen, setPublishGuideOpen] = createSignal(false);
    const authReady = createMemo(() => authState().loading === false && (!authState().enabled || authState().authenticated));
    const {
        rightSidebarWidth,
        setRightSidebarWidth,
        rightResizeState,
        setRightResizeState,
        analysisV1MediaItem,
        setAnalysisV1MediaItem,
        danceMimicMediaItem,
        setDanceMimicMediaItem,
        talkingHeadMediaItem,
        setTalkingHeadMediaItem,
        kouboStoryBoardSidebar,
        setKouboStoryBoardSidebar,
        navCollapsed,
        setNavCollapsed,
        startRightResize,
    } = useShellLayoutController();
    const state = createMemo(() => summary()?.summary);
    const npcState = createMemo(() => state()?.npc ?? {});
    const publishState = createMemo(() => state()?.publish ?? {});
    const {
        sessionItems,
        setSessionItems,
        selectedSessionId,
        setSelectedSessionId,
        sessionTaskSummary,
        setSessionTaskSummary,
        events,
        sessionState,
        selectedSession,
        loadSessions,
    } = useSessionEventsController({ summary });
    const {
        connectionTests,
        setConnectionTests,
        testStateKey,
        resetConnectionTest,
        updateConnectionTest,
        connectionTestState,
        renderConnectionTestControl,
    } = useConnectionTestsController();
    const {
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
    } = useAsrSettingsController({ resetConnectionTest, setConnectionTests, updateConnectionTest });
    const {
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
    } = useMediaSettingsController({ resetConnectionTest, setConnectionTests, updateConnectionTest });
    const {
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
    } = useMihomoController();
    let syncRouteHashFromExternalReplace = () => {};
    const {
        initialMeteringRoute,
        meteringReport,
        setMeteringReport,
        meteringTab,
        setMeteringTab,
        meteringTaskId,
        setMeteringTaskId,
        meteringAttemptScope,
        setMeteringAttemptScope,
        meteringTaskReport,
        setMeteringTaskReport,
        meteringDays,
        setMeteringDays,
        meteringBusy,
        setMeteringBusy,
        meteringTaskBusy,
        setMeteringTaskBusy,
        meteringError,
        setMeteringError,
        loadMeteringReport,
        loadMeteringTaskReport,
        selectMeteringTask,
        applyMeteringRoute,
    } = useMeteringController({ onMeteringHashReplaced: (nextHash) => syncRouteHashFromExternalReplace(nextHash) });
    const {
        activeNav,
        setActiveNav,
        routeHash,
        setRouteHash,
        canManageConnection,
        canViewMetering,
        roleAccess,
        navAllowed,
        statusCanManageConnection,
        statusCanViewMetering,
        isRetiredNavHash,
        goToBusinessHome,
        applyRoleRoute,
        syncActiveNavFromHash,
        syncRouteHashFromExternalReplace: routingSyncRouteHashFromExternalReplace,
    } = useShellRoutingController({
        authState,
        authReady,
        onMeteringRoute: applyMeteringRoute,
        onClearSessionSelection: () => setSelectedSessionId(null),
    });
    syncRouteHashFromExternalReplace = routingSyncRouteHashFromExternalReplace;
    const npcVerified = createMemo(() => String(npcState().verify_status ?? "idle") === "available");
    const publishStepStatus = createMemo(() => String((publishPreview()?.status ?? publishState().status) ?? "idle"));
    const publishMessage = createMemo(() => publishPreview()?.message || String(publishState().message ?? "-"));
    const publishLastError = createMemo(() => publishPreview()?.last_error || String(publishState().last_error ?? "-"));
    const publishHasIssue = createMemo(() => {
        const status = String(publishStepStatus() ?? "idle");
        if (["failed", "error"].includes(status))
            return true;
        const lastError = publishLastError();
        return Boolean(lastError && lastError !== "-");
    });
    const publishCheckGroups = createMemo(() => {
        const groups = new Map();
        publishChecks().forEach((check) => {
            const key = check.category || "general";
            const list = groups.get(key) ?? [];
            list.push(check);
            groups.set(key, list);
        });
        return Array.from(groups.entries());
    });
    const npcStepStatus = createMemo(() => {
        const verifyStatus = String(npcState().verify_status ?? "idle");
        const installStatus = String(npcState().install_status ?? "idle");
        if (verifyStatus === "available")
            return "connected";
        if (verifyStatus === "failed" || installStatus === "failed")
            return "failed";
        if (installStatus === "installed")
            return "installed";
        if (installStatus === "installing" || verifyStatus === "verifying" || String(npcState().environment_status ?? "") === "detecting")
            return "running";
        return installStatus !== "idle" ? installStatus : verifyStatus;
    });
    const npcResultMessage = createMemo(() => npcState().last_error ? String(npcState().last_error) : summarizeNpcResult(parseJson(String(npcState().last_result ?? ""))));
    const npcMessage = createMemo(() => statusLabel(npcStepStatus()));
    const npcResultVariant = createMemo(() => {
        const status = String(npcStepStatus() ?? "idle");
        if (["connected", "installed", "available", "success", "ready"].includes(status))
            return "success-variant";
        if (["failed", "error"].includes(status))
            return "error-variant";
        return "";
    });
    const refresh = async () => {
        try {
            const res = await api.summary();
            setSummary(res);
            const discoveredBaseUrl = String(res.summary.opencode?.base_url ?? "").trim();
            if (discoveredBaseUrl && (!opencodeDirty() || !opencodeBaseUrl().trim())) {
                setOpencodeBaseUrl(discoveredBaseUrl);
                setOpencodeUsername(String(res.summary.opencode?.auth_username ?? ""));
                setOpencodePassword(String(res.summary.opencode?.auth_password ?? ""));
                setOpencodeDirty(false);
            }
            if (res.summary.publish) {
                const publish = toPublishConfigPayload(res.summary.publish);
                setPublishPreview(publish);
                setPublishChecks(parsePublishChecks(publish.test_detail));
                if (publish.input_url && (!publishDirty() || !publishUrl().trim())) {
                    setPublishUrl(publish.input_url);
                    setPublishDirty(false);
                }
            }
        }
        catch (err) {
            setError(err instanceof Error ? err.message : "Failed loading summary");
        }
    };
    const loadInitialData = async (hashTaskId = null, isAdmin = canManageConnection()) => {
        if (isAdmin) {
            await refresh();
            await loadSkills();
            await loadNpcConfig();
            await loadAsrConfig();
            await loadMihomoConfig();
            await loadPublishConfig();
        }
        if (isAdmin)
            await loadSessions(hashTaskId);
        if (canViewMetering() && activeNav() === "metering") {
            const taskIdFromHash = parseMeteringTaskHash(window.location.hash);
            if (taskIdFromHash)
                await loadMeteringTaskReport(String(taskIdFromHash), "all");
            await loadMeteringReport();
        }
    };
    const submitAuth = async () => {
        const password = authPassword().trim();
        if (password.length < 8) {
            setAuthError("Password must be at least 8 characters.");
            return;
        }
        setAuthBusy(true);
        setAuthError("");
        try {
            if (authState().configured) {
                await api.authLogin(password);
            }
            else {
                await api.authSetup(password);
            }
            const status = await api.authStatus();
            setAuthState({ ...status, loading: false });
            setAuthPassword("");
            applyRoleRoute(status);
            await loadInitialData(parseSessionHash(window.location.hash || ""), statusCanManageConnection(status));
        }
        catch (err) {
            setAuthError(err instanceof Error ? err.message : "Authentication failed");
        }
        finally {
            setAuthBusy(false);
        }
    };
    const saveOpenCodeConfig = async (baseUrl = opencodeBaseUrl(), username = opencodeUsername(), password = opencodePassword()) => {
        const nextBaseUrl = baseUrl.trim();
        const nextUsername = username.trim();
        setOpencodeBaseUrl(nextBaseUrl);
        setOpencodeUsername(nextUsername);
        setOpencodePassword(password);
        await api.opencodeSave(nextBaseUrl, nextUsername, password);
        setOpencodeDirty(false);
    };
    const loadSkill = async (kind) => {
        const skill = kind === "publish_validate" ? await api.publishSkill() : await api.npcSkill(kind);
        setSkills((prev) => ({ ...prev, [kind]: skill }));
        return skill;
    };
    const loadSkills = async () => {
        const [install, run, uninstall, publishValidate] = await Promise.all([api.npcSkill("install"), api.npcSkill("run"), api.npcSkill("uninstall"), api.publishSkill()]);
        setSkills({ install, run, uninstall, publish_validate: publishValidate });
    };
    const loadNpcConfig = async () => {
        const config = await api.npcConfig();
        setRunDialog((prev) => ({
            ...prev,
            ...config,
            server_addr: DEFAULT_NPC_SERVER_ADDR,
            multi_account_path: config.multi_account_path || DEFAULT_NPC_MULTI_ACCOUNT_PATH,
        }));
    };
    const loadPublishConfig = async () => {
        const config = await api.publishConfig();
        setPublishPreview(config);
        setPublishChecks(parsePublishChecks(config.test_detail));
        if (config.input_url && (!publishDirty() || !publishUrl().trim())) {
            setPublishUrl(config.input_url);
            setPublishDirty(false);
        }
    };
    const currentPublishUrl = () => publishUrl().trim();
    const savePublishConfig = async () => {
        const config = await api.publishConfigSave(currentPublishUrl());
        setPublishPreview(config);
        setPublishChecks(parsePublishChecks(config.test_detail));
        setPublishUrl(config.input_url);
        setPublishDirty(false);
    };
    const refreshPublishTask = async (taskId) => {
        const [task, logs] = await Promise.all([api.publishTask(taskId), api.publishTaskLogs(taskId)]);
        setTasks((prev) => ({ ...prev, publish_validate: task }));
        setTaskLogs((prev) => ({ ...prev, publish_validate: logs.items }));
        if (task.status === "succeeded" || task.status === "failed") {
            setActiveTaskIds((prev) => ({ ...prev, publish_validate: null }));
            const parsed = parseJson(task.summary);
            if (parsed) {
                setPublishPreview(toPublishConfigPayload(parsed));
                setPublishChecks(parsePublishChecks(String(parsed.test_detail ?? "")));
            }
            else {
                await loadPublishConfig();
            }
            setPublishGuideOpen(true);
            await refresh();
        }
    };
    const startPublishValidationTask = async () => {
        setError(null);
        setPublishGuideOpen(true);
        setPublishChecks([]);
        const task = await api.publishValidate(currentPublishUrl());
        setActiveTaskIds((prev) => ({ ...prev, publish_validate: task.task_id }));
        await refreshPublishTask(task.task_id);
    };
    const currentRunConfig = () => {
        const input = runDialog();
        return {
            server_addr: input.server_addr.trim(),
            public_base_url: input.public_base_url.trim(),
            vkey: input.vkey.trim(),
            conn_type: input.conn_type,
            auto_reconnection: input.auto_reconnection,
            max_conn: Number(input.max_conn),
            flow_limit: Number(input.flow_limit),
            rate_limit: Number(input.rate_limit),
            basic_username: input.basic_username,
            basic_password: input.basic_password,
            crypt: input.crypt,
            compress: input.compress,
            disconnect_timeout: Number(input.disconnect_timeout),
            mode: input.mode,
            target_addr: input.target_addr.trim(),
            server_port: Number(input.server_port),
            multi_account_line: input.multi_account_line,
            conf_text: input.conf_text,
            multi_account_text: input.multi_account_text,
        };
    };
    const saveNpcConfig = async () => {
        await api.npcConfigSave(currentRunConfig());
        await loadNpcConfig();
    };
    const refreshTask = async (kind, taskId) => {
        const [task, logs] = await Promise.all([api.npcTask(taskId), api.npcTaskLogs(taskId)]);
        setTasks((prev) => ({ ...prev, [kind]: task }));
        setTaskLogs((prev) => ({ ...prev, [kind]: logs.items }));
        if (task.status === "succeeded" || task.status === "failed") {
            setActiveTaskIds((prev) => ({ ...prev, [kind]: null }));
            if (kind === "run") {
                setRunDialog((prev) => ({
                    ...prev,
                    open: true,
                    loading: false,
                    result: parseJson(task.summary),
                    error: task.error ?? "",
                }));
            }
            await refresh();
        }
    };
    const startNpcTask = async (kind, fn) => {
        setError(null);
        if (kind === "run") {
            setRunDialog((prev) => ({ ...prev, loading: true, result: null, error: "" }));
        }
        const task = await fn();
        setActiveTaskIds((prev) => ({ ...prev, [kind]: task.task_id }));
        await refreshTask(kind, task.task_id);
    };
    const refreshAnyTask = async (kind, taskId) => {
        if (kind === "publish_validate") {
            await refreshPublishTask(taskId);
            return;
        }
        await refreshTask(kind, taskId);
    };
    const runAction = async (step, fn) => {
        setError(null);
        setBusyStep(step);
        try {
            await fn();
            await refresh();
        }
        catch (err) {
            setError(err instanceof Error ? err.message : `Failed: ${step}`);
        }
        finally {
            setBusyStep(null);
        }
    };
    const runDiscover = async () => {
        setDiscoverAttempted(true);
        const result = await api.opencodeDiscover();
        setOpencodeCandidates(result.candidates);
        if (!opencodeBaseUrl().trim() && result.selected?.base_url) {
            setOpencodeBaseUrl(result.selected.base_url);
            setOpencodeUsername(result.selected.username ?? "");
            setOpencodePassword(result.selected.password ?? "");
            return;
        }
        if (!result.selected?.base_url && !opencodeBaseUrl().trim()) {
            setOpencodeBaseUrl("");
            setOpencodeUsername("");
            setOpencodePassword("");
        }
    };
    const runNpcDetect = async () => {
        setError(null);
        setBusyStep("npcDetect");
        setEnvDialog({ open: false, platform: "", arch: "", environment: "" });
        try {
            const result = await api.npcDetect();
            await refresh();
            const runtime = result.environment ?? {};
            setEnvDialog({
                open: true,
                platform: String(runtime.platform ?? "-"),
                arch: String(runtime.arch ?? "-"),
                environment: String(runtime.available ? "Available" : "Unavailable"),
            });
        }
        catch (err) {
            setError(err instanceof Error ? err.message : "Failed: npcDetect");
        }
        finally {
            setBusyStep(null);
        }
    };
    const openRunDialog = async () => {
        await loadNpcConfig();
        setRunDialog((prev) => ({
            ...prev,
            open: true,
            result: null,
            error: "",
            server_addr: DEFAULT_NPC_SERVER_ADDR,
            multi_account_path: prev.multi_account_path || DEFAULT_NPC_MULTI_ACCOUNT_PATH,
        }));
    };
    const reconnectNpcService = async () => {
        const config = currentRunConfig();
        await runAction("npcReconnect", () => startNpcTask("run", () => api.npcReconnect(config)));
    };
    const openEditor = async (kind) => {
        const skill = skills()[kind] ?? (await loadSkill(kind));
        setEditorKind(kind);
        setEditorContent(skill.content);
    };
    const saveEditor = async () => {
        const kind = editorKind();
        if (!kind)
            return;
        if (kind === "publish_validate") {
            await api.publishSkillSave(editorContent());
        }
        else {
            await api.npcSkillSave(kind, editorContent());
        }
        await loadSkill(kind);
        setEditorKind(null);
    };
    const restoreSkill = async (kind) => {
        if (kind === "publish_validate") {
            await api.publishSkillRestore();
        }
        else {
            await api.npcSkillRestore(kind);
        }
        const skill = await loadSkill(kind);
        if (editorKind() === kind)
            setEditorContent(skill.content);
    };
    const loginToOpenCode = (baseUrlInput, usernameInput, passwordInput) => {
        const baseUrl = baseUrlInput.trim();
        const username = usernameInput.trim();
        const password = passwordInput.trim();
        if (!baseUrl || !username || !password) {
            setError("OpenCode URL, username, and password are required for one-click login.");
            return;
        }
        try {
            const url = new URL(baseUrl.startsWith("http") ? baseUrl : `http://${baseUrl}`);
            url.username = username;
            url.password = password;
            window.open(url.toString(), "_blank", "noopener,noreferrer");
        }
        catch {
            setError("Invalid OpenCode URL.");
        }
    };
    onMount(async () => {
        const initialHash = window.location.hash || "";
        const initialHashRetired = isRetiredNavHash(initialHash);
        const hashTaskId = initialHashRetired ? null : parseSessionHash(initialHash);
        try {
            const status = await api.authStatus();
            setAuthState({ ...status, loading: false });
            applyRoleRoute(status, initialHash);
            if (!status.enabled || status.authenticated) {
                await loadInitialData(hashTaskId, statusCanManageConnection(status));
            }
        }
        catch (err) {
            setAuthState((prev) => ({ ...prev, loading: false }));
            setAuthError(err instanceof Error ? err.message : "Failed checking authentication");
        }
    });
    createEffect(() => {
        if (authState().loading || (authState().enabled && !authState().authenticated) || activeNav() !== "metering")
            return;
        if (!canViewMetering())
            return;
        void loadMeteringReport(meteringDays());
    });
    createEffect(() => {
        if (!error())
            return;
        emitDebugError(error(), { family: "frontend" });
    });
    const logout = async () => {
        await api.authLogout();
        setAuthState((prev) => ({ ...prev, authenticated: false, role: "", capabilities: {} }));
        setAuthPassword("");
        setAuthError("");
    };
    return {
        summary,
        setSummary,
        activeNav,
        setActiveNav,
        busyStep,
        setBusyStep,
        error,
        setError,
        authState,
        setAuthState,
        authPassword,
        setAuthPassword,
        authError,
        setAuthError,
        authBusy,
        setAuthBusy,
        opencodeBaseUrl,
        setOpencodeBaseUrl,
        opencodeUsername,
        setOpencodeUsername,
        opencodePassword,
        setOpencodePassword,
        opencodeDirty,
        setOpencodeDirty,
        opencodeCandidates,
        setOpencodeCandidates,
        discoverAttempted,
        setDiscoverAttempted,
        publishUrl,
        setPublishUrl,
        publishDirty,
        setPublishDirty,
        publishPreview,
        setPublishPreview,
        publishChecks,
        setPublishChecks,
        skills,
        setSkills,
        tasks,
        setTasks,
        taskLogs,
        setTaskLogs,
        activeTaskIds,
        setActiveTaskIds,
        editorKind,
        setEditorKind,
        editorContent,
        setEditorContent,
        rightSidebarWidth,
        setRightSidebarWidth,
        rightResizeState,
        setRightResizeState,
        envDialog,
        setEnvDialog,
        runDialog,
        setRunDialog,
        asrDialog,
        setAsrDialog,
        asrModels,
        setAsrModels,
        asrConfig,
        setAsrConfig,
        mediaDialog,
        setMediaDialog,
        mediaConfig,
        setMediaConfig,
        mediaApiKeys,
        setMediaApiKeys,
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
        mediaPriceListOpen,
        setMediaPriceListOpen,
        mediaUnitPriceOpen,
        setMediaUnitPriceOpen,
        usdCnyRate,
        setUsdCnyRate,
        meteringReport,
        setMeteringReport,
        meteringTab,
        setMeteringTab,
        meteringTaskId,
        setMeteringTaskId,
        meteringAttemptScope,
        setMeteringAttemptScope,
        meteringTaskReport,
        setMeteringTaskReport,
        meteringDays,
        setMeteringDays,
        meteringBusy,
        setMeteringBusy,
        meteringTaskBusy,
        setMeteringTaskBusy,
        meteringError,
        setMeteringError,
        connectionTests,
        setConnectionTests,
        sessionItems,
        setSessionItems,
        selectedSessionId,
        setSelectedSessionId,
        sessionTaskSummary,
        setSessionTaskSummary,
        routeHash,
        setRouteHash,
        publishGuideOpen,
        setPublishGuideOpen,
        analysisV1MediaItem,
        setAnalysisV1MediaItem,
        danceMimicMediaItem,
        setDanceMimicMediaItem,
        talkingHeadMediaItem,
        setTalkingHeadMediaItem,
        kouboStoryBoardSidebar,
        setKouboStoryBoardSidebar,
        navCollapsed,
        setNavCollapsed,
        mediaAgentDrag,
        setMediaAgentDrag,
        initialMeteringRoute,
        events,
        state,
        npcState,
        publishState,
        sessionState,
        npcVerified,
        publishStepStatus,
        publishMessage,
        publishLastError,
        publishHasIssue,
        canManageConnection,
        canViewMetering,
        roleAccess,
        navAllowed,
        statusCanManageConnection,
        statusCanViewMetering,
        isRetiredNavHash,
        goToBusinessHome,
        applyRoleRoute,
        syncActiveNavFromHash,
        publishCheckGroups,
        selectedSession,
        npcStepStatus,
        npcResultMessage,
        npcMessage,
        npcResultVariant,
        refresh,
        loadMihomoConfig,
        saveMihomoConfig,
        testMihomoConfig,
        loadMeteringReport,
        loadMeteringTaskReport,
        selectMeteringTask,
        loadInitialData,
        submitAuth,
        loadAsrConfig,
        openAsrDialog,
        selectedAsrModel,
        asrProviderCards,
        testStateKey,
        resetConnectionTest,
        updateConnectionTest,
        connectionTestState,
        updateAsrModel,
        activateAsrProviderForInput,
        saveAsrConfig,
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
        runAsrConnectionTest,
        renderConnectionTestControl,
        saveOpenCodeConfig,
        loadSessions,
        loadSkill,
        loadSkills,
        loadNpcConfig,
        loadPublishConfig,
        currentPublishUrl,
        savePublishConfig,
        refreshPublishTask,
        startPublishValidationTask,
        currentRunConfig,
        saveNpcConfig,
        refreshTask,
        startNpcTask,
        refreshAnyTask,
        runAction,
        runDiscover,
        runNpcDetect,
        openRunDialog,
        reconnectNpcService,
        openEditor,
        saveEditor,
        restoreSkill,
        loginToOpenCode,
        startRightResize,
        authReady,
        logout,
        handleMediaAgentPointerMove,
        handleMediaAgentPointerUp
    };
}
