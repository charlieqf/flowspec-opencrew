import { createEffect, createSignal, onCleanup } from "solid-js";

export function useShellLayoutController() {
    const [rightSidebarWidth, setRightSidebarWidth] = createSignal(320);
    const [rightResizeState, setRightResizeState] = createSignal(null);
    const [analysisV1MediaItem, setAnalysisV1MediaItem] = createSignal(null);
    const [danceMimicMediaItem, setDanceMimicMediaItem] = createSignal(null);
    const [talkingHeadMediaItem, setTalkingHeadMediaItem] = createSignal(null);
    const [kouboStoryBoardSidebar, setKouboStoryBoardSidebar] = createSignal(null);
    const [navCollapsed, setNavCollapsed] = createSignal(false);

    const startRightResize = (event) => {
        event.preventDefault();
        setRightResizeState({ startX: event.clientX, startWidth: rightSidebarWidth() });
    };

    createEffect(() => {
        const state = rightResizeState();
        if (!state)
            return;
        const onMove = (event) => {
            const next = Math.min(window.innerWidth - 520, Math.max(280, state.startWidth + (state.startX - event.clientX)));
            setRightSidebarWidth(next);
        };
        const onUp = () => setRightResizeState(null);
        window.addEventListener("mousemove", onMove);
        window.addEventListener("mouseup", onUp);
        onCleanup(() => {
            window.removeEventListener("mousemove", onMove);
            window.removeEventListener("mouseup", onUp);
        });
    });

    return {
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
    };
}
