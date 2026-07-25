import { onCleanup } from "solid-js";

const requestFrame = (callback) => (
  typeof globalThis.requestAnimationFrame === "function"
    ? globalThis.requestAnimationFrame(callback)
    : globalThis.setTimeout(callback, 16)
);

const cancelFrame = (frame) => {
  if (!frame) return;
  if (typeof globalThis.cancelAnimationFrame === "function") {
    globalThis.cancelAnimationFrame(frame);
  } else {
    globalThis.clearTimeout(frame);
  }
};

export function createPointerDrag(options) {
  let activePointerId = null;
  let captureNode = null;
  let latestClientX = 0;
  let frame = 0;

  const releaseCapture = (node, pointerId) => {
    if (
      node
      && pointerId !== null
      && node.hasPointerCapture?.(pointerId)
    ) {
      try {
        node.releasePointerCapture(pointerId);
      } catch {
        // The browser may already have released capture during unmount.
      }
    }
  };

  const clear = () => {
    cancelFrame(frame);
    frame = 0;
    const node = captureNode;
    const pointerId = activePointerId;
    activePointerId = null;
    captureNode = null;
    releaseCapture(node, pointerId);
  };

  const runFrame = () => {
    frame = 0;
    if (activePointerId === null) return;
    const keepAutoScrolling = Boolean(options.onAutoScroll?.(latestClientX));
    options.onMove?.(latestClientX);
    if (keepAutoScrolling) frame = requestFrame(runFrame);
  };

  const scheduleFrame = () => {
    if (!frame) frame = requestFrame(runFrame);
  };

  const onPointerDown = (event) => {
    if (activePointerId !== null || (event.button ?? 0) !== 0) return;
    event.preventDefault();
    event.stopPropagation();
    activePointerId = event.pointerId;
    captureNode = event.currentTarget;
    latestClientX = event.clientX;
    try {
      captureNode.setPointerCapture?.(activePointerId);
    } catch {
      // Continue on browsers that expose PointerEvent without capture.
    }
    options.onStart?.(latestClientX);
  };

  const onPointerMove = (event) => {
    if (event.pointerId !== activePointerId) return;
    latestClientX = event.clientX;
    scheduleFrame();
  };

  const onPointerUp = (event) => {
    if (event.pointerId !== activePointerId) return;
    latestClientX = event.clientX;
    cancelFrame(frame);
    frame = 0;
    options.onEnd?.(latestClientX);
    clear();
  };

  const onPointerCancel = (event) => {
    if (event.pointerId !== activePointerId) return;
    options.onCancel?.();
    clear();
  };

  const onLostPointerCapture = (event) => {
    if (event.pointerId !== activePointerId) return;
    options.onCancel?.();
    clear();
  };

  onCleanup(() => {
    if (activePointerId !== null) options.onCancel?.();
    clear();
  });

  return {
    onPointerDown,
    onPointerMove,
    onPointerUp,
    onPointerCancel,
    onLostPointerCapture,
  };
}
