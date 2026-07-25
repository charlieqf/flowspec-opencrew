import { createSignal } from "solid-js";
import { api } from "../../lib/api";
import { parseMeteringHash } from "../appShellUtils.jsx";

export function useMeteringController(options = {}) {
    const onMeteringHashReplaced = options.onMeteringHashReplaced ?? (() => {});

    const initialMeteringRoute = parseMeteringHash(window.location.hash);
    const [meteringReport, setMeteringReport] = createSignal(null);
    const [meteringTab, setMeteringTab] = createSignal(initialMeteringRoute?.tab ?? "task");
    const [meteringTaskId, setMeteringTaskId] = createSignal(initialMeteringRoute?.taskId ? String(initialMeteringRoute.taskId) : "");
    const [meteringAttemptScope, setMeteringAttemptScope] = createSignal("all");
    const [meteringTaskReport, setMeteringTaskReport] = createSignal(null);
    const [meteringDays, setMeteringDays] = createSignal(30);
    const [meteringBusy, setMeteringBusy] = createSignal(false);
    const [meteringTaskBusy, setMeteringTaskBusy] = createSignal(false);
    const [meteringError, setMeteringError] = createSignal("");

    const loadMeteringReport = async (days = meteringDays()) => {
        setMeteringBusy(true);
        setMeteringError("");
        try {
            const res = await api.localMeteringReport(days, 300);
            setMeteringReport(res);
        }
        catch (err) {
            setMeteringError(err instanceof Error ? err.message : "加载全局计费报表失败");
        }
        finally {
            setMeteringBusy(false);
        }
    };
    const loadMeteringTaskReport = async (taskIdValue = meteringTaskId(), attemptValue = meteringAttemptScope()) => {
        const normalizedTaskId = String(taskIdValue || "").trim();
        if (!normalizedTaskId) {
            setMeteringError("请输入任务 ID。");
            return;
        }
        setMeteringTaskBusy(true);
        setMeteringError("");
        try {
            const res = await api.localMeteringTaskReport(normalizedTaskId, attemptValue, 1000, true);
            setMeteringTaskReport(res);
            setMeteringTaskId(normalizedTaskId);
            setMeteringAttemptScope(String(res?.attempt_scope?.requested || attemptValue || "all"));
        }
        catch (err) {
            setMeteringError(err instanceof Error ? err.message : "加载任务计费报表失败");
        }
        finally {
            setMeteringTaskBusy(false);
        }
    };
    const selectMeteringTask = async (taskId) => {
        const normalizedTaskId = String(taskId || "").trim();
        if (!normalizedTaskId)
            return;
        setMeteringTab("task");
        setMeteringTaskId(normalizedTaskId);
        setMeteringAttemptScope("all");
        const nextHash = `#/metering/task/${normalizedTaskId}`;
        if (window.location.hash !== nextHash) {
            window.history.replaceState(null, "", nextHash);
            onMeteringHashReplaced(nextHash);
        }
        await loadMeteringTaskReport(normalizedTaskId, "all");
    };
    const applyMeteringRoute = (route, options = {}) => {
        if (!route)
            return undefined;
        setMeteringTab(route.tab);
        if (!route.taskId)
            return undefined;
        const taskId = String(route.taskId);
        const taskChanged = meteringTaskId() !== taskId;
        setMeteringTaskId(taskId);
        if (taskChanged || options.loadTask)
            setMeteringAttemptScope("all");
        if (options.loadTask)
            return loadMeteringTaskReport(taskId, "all");
        return undefined;
    };

    return {
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
    };
}
