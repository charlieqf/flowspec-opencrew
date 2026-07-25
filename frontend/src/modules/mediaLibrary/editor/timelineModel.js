export const TIMELINE_MAX_PIXELS_PER_MS = 0.2;
export const TIMELINE_FRAGMENT_MIN_WIDTH_PX = 18;
export const TIMELINE_FRAGMENT_TEXT_MIN_WIDTH_PX = 52;
export const TIMELINE_EDGE_SCROLL_ZONE_PX = 32;
export const TIMELINE_EDGE_SCROLL_MAX_PX = 18;
export const TIMELINE_TICK_INTERVALS_MS = Object.freeze([
  100,
  200,
  500,
  1_000,
  2_000,
  5_000,
  10_000,
  30_000,
  60_000,
  300_000,
]);

export function fitPixelsPerMs(durationMs, viewportWidth) {
  const duration = Math.max(1, Number(durationMs) || 1);
  const width = Math.max(1, Number(viewportWidth) || 1);
  return Math.min(TIMELINE_MAX_PIXELS_PER_MS, width / duration);
}

export function clampPixelsPerMs(value, durationMs, viewportWidth) {
  const fit = fitPixelsPerMs(durationMs, viewportWidth);
  return Math.min(TIMELINE_MAX_PIXELS_PER_MS, Math.max(fit, Number(value) || fit));
}

export function timelineRenderWindow({
  durationMs,
  scrollLeft,
  pixelsPerMs,
  viewportWidth,
}) {
  const duration = Math.max(0, Math.round(Number(durationMs) || 0));
  const ppm = Math.max(Number.EPSILON, Number(pixelsPerMs) || Number.EPSILON);
  const left = Math.max(0, Number(scrollLeft) || 0);
  const width = Math.max(0, Number(viewportWidth) || 0);
  const visibleStartMs = Math.max(0, Math.floor(left / ppm));
  const visibleEndMs = Math.min(duration, Math.ceil((left + width) / ppm));
  const bufferMs = Math.max(5_000, Math.ceil((visibleEndMs - visibleStartMs) * 0.5));
  return {
    visibleStartMs,
    visibleEndMs,
    bufferMs,
    renderStartMs: Math.max(0, visibleStartMs - bufferMs),
    renderEndMs: Math.min(duration, visibleEndMs + bufferMs),
  };
}

export function timelineTickInterval(pixelsPerMs) {
  const ppm = Math.max(Number.EPSILON, Number(pixelsPerMs) || Number.EPSILON);
  const inPreferredRange = TIMELINE_TICK_INTERVALS_MS.find((interval) => {
    const spacing = interval * ppm;
    return spacing >= 80 && spacing <= 160;
  });
  if (inPreferredRange) return inPreferredRange;
  return TIMELINE_TICK_INTERVALS_MS.find((interval) => interval * ppm >= 80)
    || TIMELINE_TICK_INTERVALS_MS.at(-1);
}

export function timelineTicks(renderStartMs, renderEndMs, pixelsPerMs) {
  const intervalMs = timelineTickInterval(pixelsPerMs);
  const start = Math.max(0, Math.floor(Number(renderStartMs) / intervalMs) * intervalMs);
  const end = Math.max(start, Number(renderEndMs) || 0);
  const ticks = [];
  for (let atMs = start; atMs <= end; atMs += intervalMs) ticks.push(atMs);
  return { intervalMs, ticks };
}

export function visibleTimelineFragments(fragments, renderStartMs, renderEndMs) {
  if (!Array.isArray(fragments)) return [];
  return fragments.filter((fragment) => (
    Number(fragment?.endMs) > renderStartMs
    && Number(fragment?.startMs) < renderEndMs
  ));
}

export function timelineFragmentPresentation(fragment, pixelsPerMs) {
  const startMs = Math.max(0, Number(fragment?.startMs) || 0);
  const endMs = Math.max(startMs, Number(fragment?.endMs) || startMs);
  const naturalWidthPx = Math.max(0, (endMs - startMs) * Math.max(0, Number(pixelsPerMs) || 0));
  return {
    naturalWidthPx,
    widthPx: Math.max(TIMELINE_FRAGMENT_MIN_WIDTH_PX, naturalWidthPx),
    compact: naturalWidthPx < TIMELINE_FRAGMENT_TEXT_MIN_WIDTH_PX,
  };
}

export function clampTimelineMs(value, durationMs) {
  const parsed = Math.round(Number(value) || 0);
  return Math.max(0, Math.min(Math.max(0, Math.round(Number(durationMs) || 0)), parsed));
}

export function timelineMsFromClientX({
  clientX,
  viewportLeft,
  scrollLeft,
  pixelsPerMs,
  durationMs,
}) {
  const ppm = Math.max(Number.EPSILON, Number(pixelsPerMs) || Number.EPSILON);
  return clampTimelineMs(
    (Number(clientX) - Number(viewportLeft || 0) + Math.max(0, Number(scrollLeft) || 0)) / ppm,
    durationMs,
  );
}

export function timelineEdgeScrollDelta(
  clientX,
  viewportLeft,
  viewportWidth,
  edgeZonePx = TIMELINE_EDGE_SCROLL_ZONE_PX,
  maxDeltaPx = TIMELINE_EDGE_SCROLL_MAX_PX,
) {
  const width = Math.max(0, Number(viewportWidth) || 0);
  const left = Number(viewportLeft) || 0;
  const zone = Math.max(1, Number(edgeZonePx) || TIMELINE_EDGE_SCROLL_ZONE_PX);
  const maxDelta = Math.max(1, Number(maxDeltaPx) || TIMELINE_EDGE_SCROLL_MAX_PX);
  const x = Number(clientX) || 0;
  if (x < left + zone) {
    return -Math.ceil(maxDelta * Math.min(1, (left + zone - x) / zone));
  }
  if (x > left + width - zone) {
    return Math.ceil(maxDelta * Math.min(1, (x - (left + width - zone)) / zone));
  }
  return 0;
}

export function formatTimelineMs(value) {
  const milliseconds = Math.max(0, Math.round(Number(value) || 0));
  const hours = Math.floor(milliseconds / 3_600_000);
  const minutes = Math.floor((milliseconds % 3_600_000) / 60_000);
  const seconds = Math.floor((milliseconds % 60_000) / 1_000);
  const millis = milliseconds % 1_000;
  return `${hours ? `${hours}:` : ""}${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}.${String(millis).padStart(3, "0")}`;
}
