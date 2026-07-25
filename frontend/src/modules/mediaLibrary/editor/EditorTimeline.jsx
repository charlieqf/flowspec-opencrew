import { For, Show, createEffect, createMemo, createSignal, onCleanup, onMount } from "solid-js";
import {
  TIMELINE_MAX_PIXELS_PER_MS,
  clampPixelsPerMs,
  clampTimelineMs,
  fitPixelsPerMs,
  formatTimelineMs,
  timelineRenderWindow,
  timelineFragmentPresentation,
  timelineEdgeScrollDelta,
  timelineMsFromClientX,
  timelineTicks,
  visibleTimelineFragments,
} from "./timelineModel.js";
import { createPointerDrag } from "./createPointerDrag.js";

const TRACKS = Object.freeze([
  ["composite", "综", "综合片段"],
  ["dialogue", "白", "对白片段"],
  ["visual", "画", "画面片段"],
  ["source", "源", "原视频"],
]);

export default function EditorTimeline(props) {
  let viewportNode;
  const [viewportWidth, setViewportWidth] = createSignal(960);
  const [scrollLeft, setScrollLeft] = createSignal(0);
  const [pixelsPerMs, setPixelsPerMs] = createSignal(0.001);

  const fit = createMemo(() => fitPixelsPerMs(props.durationMs, viewportWidth()));
  const canvasWidth = createMemo(() => Math.max(viewportWidth(), Math.ceil(props.durationMs * pixelsPerMs())));
  const renderWindow = createMemo(() => timelineRenderWindow({
    durationMs: props.durationMs,
    scrollLeft: scrollLeft(),
    pixelsPerMs: pixelsPerMs(),
    viewportWidth: viewportWidth(),
  }));
  const ticks = createMemo(() => timelineTicks(
    renderWindow().renderStartMs,
    renderWindow().renderEndMs,
    pixelsPerMs(),
  ));

  onMount(() => {
    const updateWidth = () => setViewportWidth(Math.max(1, viewportNode?.clientWidth || 1));
    updateWidth();
    const observer = new ResizeObserver(updateWidth);
    if (viewportNode) observer.observe(viewportNode);
    onCleanup(() => observer.disconnect());
  });

  createEffect(() => {
    const minimum = fit();
    setPixelsPerMs((current) => clampPixelsPerMs(current, props.durationMs, viewportWidth()));
    if (canvasWidth() <= viewportWidth() && viewportNode) viewportNode.scrollLeft = 0;
    props.onViewportChange?.({
      pixelsPerMs: Math.max(minimum, pixelsPerMs()),
      scrollLeft: scrollLeft(),
      viewportWidth: viewportWidth(),
      renderWindow: renderWindow(),
    });
  });

  const setZoom = (next) => {
    if (!viewportNode) return;
    const oldPpm = pixelsPerMs();
    const centerMs = (viewportNode.scrollLeft + viewportWidth() / 2) / oldPpm;
    const ppm = clampPixelsPerMs(next, props.durationMs, viewportWidth());
    setPixelsPerMs(ppm);
    queueMicrotask(() => {
      const nextLeft = Math.max(0, centerMs * ppm - viewportWidth() / 2);
      viewportNode.scrollLeft = nextLeft;
      setScrollLeft(nextLeft);
    });
  };

  const fitTimeline = () => {
    setPixelsPerMs(fit());
    if (viewportNode) viewportNode.scrollLeft = 0;
    setScrollLeft(0);
  };

  const msFromClientX = (clientX) => {
    const bounds = viewportNode.getBoundingClientRect();
    return timelineMsFromClientX({
      clientX,
      viewportLeft: bounds.left,
      scrollLeft: viewportNode.scrollLeft,
      pixelsPerMs: pixelsPerMs(),
      durationMs: props.durationMs,
    });
  };

  const autoScrollForClientX = (clientX) => {
    if (!viewportNode) return false;
    const bounds = viewportNode.getBoundingClientRect();
    const delta = timelineEdgeScrollDelta(clientX, bounds.left, bounds.width);
    if (!delta) return false;
    const maximum = Math.max(0, canvasWidth() - viewportNode.clientWidth);
    const next = Math.max(0, Math.min(maximum, viewportNode.scrollLeft + delta));
    if (next === viewportNode.scrollLeft) return false;
    viewportNode.scrollLeft = next;
    setScrollLeft(next);
    return true;
  };

  const playheadDrag = createPointerDrag({
    onStart: (clientX) => props.onScrubStart?.(msFromClientX(clientX)),
    onMove: (clientX) => props.onScrubMove?.(msFromClientX(clientX)),
    onEnd: (clientX) => props.onScrubEnd?.(msFromClientX(clientX)),
    onCancel: () => props.onScrubCancel?.(),
    onAutoScroll: autoScrollForClientX,
  });

  const createSelectionDrag = (side) => createPointerDrag({
    onMove: (clientX) => {
      const atMs = msFromClientX(clientX);
      const startMs = side === "start"
        ? Math.min(atMs, props.selection.endMs - 1)
        : props.selection.startMs;
      const endMs = side === "end"
        ? Math.max(atMs, props.selection.startMs + 1)
        : props.selection.endMs;
      props.onSelectionManual?.(startMs, endMs);
    },
    onEnd: (clientX) => {
      const atMs = msFromClientX(clientX);
      const startMs = side === "start"
        ? Math.min(atMs, props.selection.endMs - 1)
        : props.selection.startMs;
      const endMs = side === "end"
        ? Math.max(atMs, props.selection.startMs + 1)
        : props.selection.endMs;
      props.onSelectionManual?.(startMs, endMs);
    },
    onAutoScroll: autoScrollForClientX,
  });
  const startSelectionDrag = createSelectionDrag("start");
  const endSelectionDrag = createSelectionDrag("end");

  const beginTrackScrub = (event) => {
    if (event.target.closest?.(".ml-editor-fragment, .ml-editor-selection-handle")) return;
    playheadDrag.onPointerDown(event);
  };

  const onPlayheadKeyDown = (event) => {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    event.preventDefault();
    event.stopPropagation();
    const direction = event.key === "ArrowRight" ? 1 : -1;
    const stepMs = event.shiftKey ? 1_000 : 100;
    const targetMs = clampTimelineMs(props.playheadMs + direction * stepMs, props.durationMs);
    props.onScrubStart?.(targetMs);
    props.onScrubEnd?.(targetMs);
  };

  const zoomPercent = createMemo(() => {
    const minimum = fit();
    if (TIMELINE_MAX_PIXELS_PER_MS <= minimum) return 0;
    return Math.round(
      (Math.log(pixelsPerMs() / minimum) / Math.log(TIMELINE_MAX_PIXELS_PER_MS / minimum)) * 100,
    );
  });

  const setZoomPercent = (percent) => {
    const minimum = fit();
    const ratio = Math.max(0, Math.min(100, Number(percent))) / 100;
    setZoom(minimum * ((TIMELINE_MAX_PIXELS_PER_MS / minimum) ** ratio));
  };

  return <section class="ml-editor-timeline-panel" aria-label="视频时间轴">
    <header class="ml-editor-timeline-toolbar">
      <div class="ml-editor-track-toggles">
        <For each={TRACKS.filter(([scheme]) => scheme !== "source")}>{([scheme, type, label]) =>
          <label><input
            type="checkbox"
            checked={props.visibleTracks[scheme]}
            onChange={(event) => props.onTrackVisibility?.(scheme, event.currentTarget.checked)}
          />{type} {label}</label>
        }</For>
      </div>
      <div class="ml-editor-zoom">
        <button type="button" onClick={fitTimeline}>适应窗口</button>
        <span>缩放</span>
        <input
          aria-label="时间轴缩放"
          type="range"
          min="0"
          max="100"
          value={zoomPercent()}
          onInput={(event) => setZoomPercent(event.currentTarget.value)}
        />
        <strong>{Math.round(pixelsPerMs() * 1000)} px/s</strong>
      </div>
    </header>
    <div class="ml-editor-timeline-grid">
      <div class="ml-editor-track-heads" aria-hidden="true">
        <div class="ml-editor-ruler-head">时间</div>
        <For each={TRACKS}>{([scheme, type, label]) =>
          <Show when={scheme === "source" || props.visibleTracks[scheme]}>
            <div class={`ml-editor-track-head ${scheme}`}><b>{type}</b><span>{label}</span></div>
          </Show>
        }</For>
      </div>
      <div
        ref={viewportNode}
        class="ml-editor-timeline-viewport"
        onScroll={(event) => setScrollLeft(event.currentTarget.scrollLeft)}
      >
        <div class="ml-editor-timeline-canvas" style={{ width: `${canvasWidth()}px` }}>
          <div
            class="ml-editor-ruler"
            onPointerDown={beginTrackScrub}
            onPointerMove={playheadDrag.onPointerMove}
            onPointerUp={playheadDrag.onPointerUp}
            onPointerCancel={playheadDrag.onPointerCancel}
            onLostPointerCapture={playheadDrag.onLostPointerCapture}
          >
            <For each={ticks().ticks}>{(atMs) =>
              <div class="ml-editor-tick" style={{ left: `${atMs * pixelsPerMs()}px` }}>
                <span>{formatTimelineMs(atMs)}</span>
              </div>
            }</For>
          </div>
          <For each={TRACKS}>{([scheme, type]) =>
            <Show when={scheme === "source" || props.visibleTracks[scheme]}>
              <div
                class={`ml-editor-track ${scheme}`}
                onPointerDown={beginTrackScrub}
                onPointerMove={playheadDrag.onPointerMove}
                onPointerUp={playheadDrag.onPointerUp}
                onPointerCancel={playheadDrag.onPointerCancel}
                onLostPointerCapture={playheadDrag.onLostPointerCapture}
              >
                <Show when={scheme === "source"}>
                  <div class="ml-editor-source-summary">
                    <b>源</b>
                    <span>总时长 {formatTimelineMs(props.durationMs)}</span>
                    <span>当前选区 {formatTimelineMs(props.selection.startMs)}–{formatTimelineMs(props.selection.endMs)}</span>
                  </div>
                  <div
                    class="ml-editor-source-range"
                    title={`当前选区 ${formatTimelineMs(props.selection.startMs)}–${formatTimelineMs(props.selection.endMs)}`}
                    style={{
                      left: `${props.selection.startMs * pixelsPerMs()}px`,
                      width: `${Math.max(1, (props.selection.endMs - props.selection.startMs) * pixelsPerMs())}px`,
                    }}
                  />
                </Show>
                <Show when={scheme !== "source"}>
                  <For each={visibleTimelineFragments(
                    props.fragments[scheme],
                    renderWindow().renderStartMs,
                    renderWindow().renderEndMs,
                  )}>{(fragment) => {
                    const presentation = () => timelineFragmentPresentation(fragment, pixelsPerMs());
                    return <button
                        type="button"
                        aria-label={`${type} ${fragment.label}，${formatTimelineMs(fragment.startMs)} 到 ${formatTimelineMs(fragment.endMs)}${fragment.stale ? "，已过期且只读" : ""}`}
                        classList={{
                          "ml-editor-fragment": true,
                          "is-compact": presentation().compact,
                          stale: fragment.stale,
                          focused: props.focusedFragmentRef === `${fragment.scheme}:${fragment.fragmentId}`,
                        }}
                        style={{
                          left: `${fragment.startMs * pixelsPerMs()}px`,
                          width: `${presentation().widthPx}px`,
                        }}
                        title={`${type} · ${fragment.label} · ${formatTimelineMs(fragment.startMs)}–${formatTimelineMs(fragment.endMs)}${fragment.stale ? " · 已过期且只读" : ""}`}
                        onClick={() => props.onFragmentFocus?.(fragment)}
                        onDblClick={() => props.onFragmentPreview?.(fragment)}
                      >
                        <b class="ml-editor-fragment-type" aria-hidden="true">{type}</b>
                        <Show when={!presentation().compact}><span>{fragment.label}</span></Show>
                        <Show when={fragment.keyframeUrl}><i aria-label="关键帧" /></Show>
                      </button>;
                  }}</For>
                </Show>
              </div>
            </Show>
          }</For>
          <div
            class="ml-editor-selection"
            style={{
              left: `${props.selection.startMs * pixelsPerMs()}px`,
              width: `${Math.max(1, (props.selection.endMs - props.selection.startMs) * pixelsPerMs())}px`,
            }}
          >
            <button
              type="button"
              aria-label="拖动入点"
              class="ml-editor-selection-handle start"
              onPointerDown={startSelectionDrag.onPointerDown}
              onPointerMove={startSelectionDrag.onPointerMove}
              onPointerUp={startSelectionDrag.onPointerUp}
              onPointerCancel={startSelectionDrag.onPointerCancel}
              onLostPointerCapture={startSelectionDrag.onLostPointerCapture}
            />
            <button
              type="button"
              aria-label="拖动出点"
              class="ml-editor-selection-handle end"
              onPointerDown={endSelectionDrag.onPointerDown}
              onPointerMove={endSelectionDrag.onPointerMove}
              onPointerUp={endSelectionDrag.onPointerUp}
              onPointerCancel={endSelectionDrag.onPointerCancel}
              onLostPointerCapture={endSelectionDrag.onLostPointerCapture}
            />
          </div>
          <div class="ml-editor-playhead" style={{ left: `${props.playheadMs * pixelsPerMs()}px` }} />
          <button
            type="button"
            class="ml-editor-playhead-handle"
            style={{ left: `${props.playheadMs * pixelsPerMs()}px` }}
            role="slider"
            aria-label="播放头"
            aria-valuemin="0"
            aria-valuemax={props.durationMs}
            aria-valuenow={props.playheadMs}
            aria-valuetext={formatTimelineMs(props.playheadMs)}
            onKeyDown={onPlayheadKeyDown}
            onPointerDown={playheadDrag.onPointerDown}
            onPointerMove={playheadDrag.onPointerMove}
            onPointerUp={playheadDrag.onPointerUp}
            onPointerCancel={playheadDrag.onPointerCancel}
            onLostPointerCapture={playheadDrag.onLostPointerCapture}
          />
        </div>
      </div>
    </div>
    <footer class="ml-editor-timeline-status">
      <span>可见 {formatTimelineMs(renderWindow().visibleStartMs)}–{formatTimelineMs(renderWindow().visibleEndMs)}</span>
    </footer>
  </section>;
}
