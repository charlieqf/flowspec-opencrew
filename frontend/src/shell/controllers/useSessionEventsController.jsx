import { createMemo, createSignal } from "solid-js";
import { api } from "../../lib/api";

export function useSessionEventsController(options) {
    const summary = options.summary;

    const [sessionItems, setSessionItems] = createSignal([]);
    const [selectedSessionId, setSelectedSessionId] = createSignal(null);
    const [sessionTaskSummary, setSessionTaskSummary] = createSignal(null);

    const events = createMemo(() => summary()?.events ?? []);
    const sessionState = createMemo(() => summary()?.summary?.sessions ?? {});
    const selectedSession = createMemo(() => sessionItems().find((item) => item.id === selectedSessionId()) ?? null);

    const loadSessions = async (preferredSessionId) => {
        const res = await api.sessionTasks("demo-group");
        setSessionItems(res.items);
        setSessionTaskSummary(res.summary);
        const current = preferredSessionId ?? selectedSessionId();
        if (current && res.items.some((item) => item.id === current)) {
            return;
        }
        if (preferredSessionId) {
            setSelectedSessionId(preferredSessionId);
            return;
        }
        if (res.items.length > 0) {
            setSelectedSessionId(res.items[0].id);
        }
        else {
            setSelectedSessionId(null);
        }
    };

    return {
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
    };
}
