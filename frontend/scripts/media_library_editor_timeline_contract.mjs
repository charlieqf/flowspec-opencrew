import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRoot } from "solid-js";
import { createPointerDrag } from "../src/modules/mediaLibrary/editor/createPointerDrag.js";
import {
  TIMELINE_EDGE_SCROLL_MAX_PX,
  TIMELINE_FRAGMENT_MIN_WIDTH_PX,
  TIMELINE_MAX_PIXELS_PER_MS,
  TIMELINE_TICK_INTERVALS_MS,
  clampPixelsPerMs,
  fitPixelsPerMs,
  formatTimelineMs,
  timelineEdgeScrollDelta,
  timelineFragmentPresentation,
  timelineMsFromClientX,
  timelineRenderWindow,
  timelineTickInterval,
  timelineTicks,
  visibleTimelineFragments,
} from "../src/modules/mediaLibrary/editor/timelineModel.js";

const tenMinutesMs = 600_000;
const viewportWidth = 1_200;
const fit = fitPixelsPerMs(tenMinutesMs, viewportWidth);
assert.equal(fit, 0.002);
assert.equal(clampPixelsPerMs(0.00001, tenMinutesMs, viewportWidth), fit);
assert.equal(clampPixelsPerMs(99, tenMinutesMs, viewportWidth), TIMELINE_MAX_PIXELS_PER_MS);

const tail = timelineRenderWindow({
  durationMs: tenMinutesMs,
  scrollLeft: tenMinutesMs * TIMELINE_MAX_PIXELS_PER_MS - viewportWidth,
  pixelsPerMs: TIMELINE_MAX_PIXELS_PER_MS,
  viewportWidth,
});
assert.deepEqual(tail, {
  visibleStartMs: 594_000,
  visibleEndMs: 600_000,
  bufferMs: 5_000,
  renderStartMs: 589_000,
  renderEndMs: 600_000,
});

const fragments = [
  { fragmentId: "early", startMs: 1_000, endMs: 2_000 },
  { fragmentId: "buffer", startMs: 589_500, endMs: 590_000 },
  { fragmentId: "tail", startMs: 599_000, endMs: 600_000 },
];
assert.deepEqual(
  visibleTimelineFragments(fragments, tail.renderStartMs, tail.renderEndMs).map((item) => item.fragmentId),
  ["buffer", "tail"],
);

for (const ppm of [0.0002, 0.001, 0.002, 0.02, 0.2]) {
  const interval = timelineTickInterval(ppm);
  assert.ok(TIMELINE_TICK_INTERVALS_MS.includes(interval));
  const spacing = interval * ppm;
  if (TIMELINE_TICK_INTERVALS_MS.some((value) => value * ppm >= 80 && value * ppm <= 160)) {
    assert.ok(spacing >= 80 && spacing <= 160);
  }
}

const renderedTicks = timelineTicks(tail.renderStartMs, tail.renderEndMs, TIMELINE_MAX_PIXELS_PER_MS);
assert.ok(renderedTicks.ticks.length < 30, "windowed tail must not render per-frame/per-ms nodes");

assert.equal(formatTimelineMs(151_217), "02:31.217");
assert.equal(formatTimelineMs(3_661_217), "1:01:01.217");
assert.equal(timelineMsFromClientX({ clientX: 150, viewportLeft: 100, scrollLeft: 200, pixelsPerMs: 0.1, durationMs: 10_000 }), 2_500);
assert.equal(timelineMsFromClientX({ clientX: 0, viewportLeft: 100, scrollLeft: 0, pixelsPerMs: 0.1, durationMs: 10_000 }), 0);
assert.equal(timelineMsFromClientX({ clientX: 2_000, viewportLeft: 100, scrollLeft: 0, pixelsPerMs: 0.1, durationMs: 10_000 }), 10_000);
assert.equal(timelineEdgeScrollDelta(100, 100, 400), -TIMELINE_EDGE_SCROLL_MAX_PX);
assert.equal(timelineEdgeScrollDelta(116, 100, 400), -9);
assert.equal(timelineEdgeScrollDelta(300, 100, 400), 0);
assert.equal(timelineEdgeScrollDelta(484, 100, 400), 9);
assert.equal(timelineEdgeScrollDelta(500, 100, 400), TIMELINE_EDGE_SCROLL_MAX_PX);
assert.deepEqual(
  timelineFragmentPresentation({ startMs: 1_000, endMs: 2_000 }, 0.006),
  { naturalWidthPx: 6, widthPx: TIMELINE_FRAGMENT_MIN_WIDTH_PX, compact: true },
);
assert.equal(
  timelineFragmentPresentation({ startMs: 1_000, endMs: 11_000 }, 0.006).compact,
  false,
);

const thirtyMinuteStress = timelineRenderWindow({
  durationMs: 1_800_000,
  scrollLeft: 150_000,
  pixelsPerMs: 0.1,
  viewportWidth: 1_600,
});
assert.equal(
  thirtyMinuteStress.renderEndMs - thirtyMinuteStress.renderStartMs,
  32_000,
  "30-minute source still renders only visible 16s plus half-span buffers on both sides",
);

const timelineSource = readFileSync(new URL("../src/modules/mediaLibrary/editor/EditorTimeline.jsx", import.meta.url), "utf8");
assert.ok(
  timelineSource.indexOf('["composite", "综", "综合片段"]')
    < timelineSource.indexOf('["source", "源", "原视频"]'),
  "timeline tracks must place C/D/V evidence before the source track",
);
assert.match(timelineSource, /ml-editor-source-summary/);
assert.match(timelineSource, /ml-editor-fragment-type/);
assert.doesNotMatch(timelineSource, />窗口化渲染 /, "windowing internals must not be exposed as default UI copy");
assert.doesNotMatch(timelineSource, />主刻度 /, "tick internals must not be exposed as default UI copy");
assert.match(timelineSource, /createPointerDrag/);
assert.doesNotMatch(timelineSource, /window\.addEventListener\("pointer/, "dragging must not leak window pointer listeners");
assert.match(timelineSource, /onPointerCancel=\{playheadDrag\.onPointerCancel\}/);
assert.match(timelineSource, /onPointerCancel=\{startSelectionDrag\.onPointerCancel\}/);
assert.match(timelineSource, /onPointerCancel=\{endSelectionDrag\.onPointerCancel\}/);
assert.match(timelineSource, /role="slider"/);
assert.match(timelineSource, /event\.shiftKey \? 1_000 : 100/);

const pointerCalls = [];
let scheduledFrame = null;
const originalRequestAnimationFrame = globalThis.requestAnimationFrame;
const originalCancelAnimationFrame = globalThis.cancelAnimationFrame;
globalThis.requestAnimationFrame = (callback) => {
  scheduledFrame = callback;
  return 17;
};
globalThis.cancelAnimationFrame = () => { scheduledFrame = null; };
let disposePointerDrag;
let pointerDrag;
createRoot((dispose) => {
  disposePointerDrag = dispose;
  pointerDrag = createPointerDrag({
    onStart: (x) => pointerCalls.push(["start", x]),
    onMove: (x) => pointerCalls.push(["move", x]),
    onEnd: (x) => pointerCalls.push(["end", x]),
    onCancel: () => pointerCalls.push(["cancel"]),
    onAutoScroll: () => false,
  });
});
let captured = null;
const captureNode = {
  setPointerCapture: (pointerId) => { captured = pointerId; },
  hasPointerCapture: (pointerId) => captured === pointerId,
  releasePointerCapture: (pointerId) => { if (captured === pointerId) captured = null; },
};
const pointerEvent = (pointerId, clientX) => ({
  button: 0,
  pointerId,
  clientX,
  currentTarget: captureNode,
  preventDefault() {},
  stopPropagation() {},
});
pointerDrag.onPointerDown(pointerEvent(4, 120));
assert.equal(captured, 4, "pointerdown captures on the actual handle/track");
pointerDrag.onPointerMove(pointerEvent(4, 180));
pointerDrag.onPointerMove(pointerEvent(4, 190));
assert.equal(pointerCalls.filter(([phase]) => phase === "move").length, 0, "pointer moves are rAF throttled");
scheduledFrame();
assert.deepEqual(pointerCalls.at(-1), ["move", 190], "only the latest pointer coordinate is emitted per frame");
pointerDrag.onPointerUp(pointerEvent(4, 205));
assert.deepEqual(pointerCalls.at(-1), ["end", 205], "pointerup emits an exact unthrottled final coordinate");
assert.equal(captured, null);
pointerDrag.onPointerDown(pointerEvent(5, 210));
disposePointerDrag();
assert.deepEqual(pointerCalls.at(-1), ["cancel"], "component cleanup cancels an active drag");
assert.equal(captured, null, "component cleanup releases pointer capture");
globalThis.requestAnimationFrame = originalRequestAnimationFrame;
globalThis.cancelAnimationFrame = originalCancelAnimationFrame;

const pageSource = readFileSync(new URL("../src/modules/mediaLibrary/pages/MediaLibraryEditorPage.jsx", import.meta.url), "utf8");
assert.match(pageSource, /aria-label="入点时间码"/);
assert.match(pageSource, /aria-label="入点 ms"/);
assert.match(pageSource, /<summary>技术详情<\/summary>/);
assert.doesNotMatch(pageSource, /个分析片段 · source /);
assert.match(pageSource, /searchRefSelected\(fragment\) \? "移出检索" : "加入检索"/);
assert.match(pageSource, /流畅预览/);
assert.match(pageSource, /网络波动，正在缓冲/);
assert.match(pageSource, />素材检索<\/button>/);
assert.match(pageSource, /const \[isScrubbing, setIsScrubbing\]/);
assert.match(pageSource, /const \[pendingFinalSeekMs, setPendingFinalSeekMs\]/);
assert.match(pageSource, /if \(!videoNode \|\| isScrubbing\(\)\) return/);
assert.match(pageSource, /onSeeked=\{onVideoSeeked\}/);
assert.match(pageSource, /setRangePreview\(null\);[\s\S]*setIsScrubbing\(true\)/);
assert.match(pageSource, /Math\.abs\(currentMs - targetMs\) > FINAL_SEEK_TOLERANCE_MS/);
assert.match(pageSource, /onScrubCancel=\{cancelScrub\}/);

const editorStyles = readFileSync(new URL("../src/modules/mediaLibrary/editor/mediaLibraryEditor.css", import.meta.url), "utf8");
const lightThemeStart = editorStyles.indexOf("The editor follows the same light product surface");
assert.ok(lightThemeStart > 0, "editor must explicitly share the product light surface");
const lightTheme = editorStyles.slice(lightThemeStart);
assert.match(lightTheme, /\.ml-editor-page\s*\{[\s\S]*background: #f4f7fb/);
assert.match(lightTheme, /\.ml-editor-range-controls\s*\{[\s\S]*display: grid/);
assert.match(lightTheme, /\.ml-editor-video-status/);
assert.match(lightTheme, /\.ml-editor-clip-create/);
assert.match(
  editorStyles,
  /\.ml-editor-page \.ml-editor-time-input > label input\s*\{[\s\S]*max-width: 100%;[\s\S]*min-width: 0;[\s\S]*width: auto;/,
  "time inputs must shrink inside their grid cells instead of overflowing the in/out cards",
);
assert.match(editorStyles, /\.ml-editor-playhead\s*\{[\s\S]*pointer-events: none;[\s\S]*width: 1px;/);
assert.match(editorStyles, /\.ml-editor-playhead-handle\s*\{[\s\S]*height: 30px;[\s\S]*width: 24px;/);

console.log("media library editor timeline contract: ok");
