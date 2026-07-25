import { createSignal, onCleanup, onMount } from "solid-js";
import { Portal } from "solid-js/web";

const VIEWPORT_MARGIN = 8;
const MENU_GAP = 6;
const MENU_WIDTH = 188;

function clamp(value, min, max) {
  return Math.min(Math.max(value, min), Math.max(min, max));
}

function viewportSize() {
  return {
    width: window.innerWidth || document.documentElement.clientWidth || 0,
    height: window.innerHeight || document.documentElement.clientHeight || 0,
  };
}

function horizontalBounds(anchor, menuWidth, viewportWidth) {
  const mainRect = anchor.closest?.(".ual-main")?.getBoundingClientRect();
  const minLeft = Math.max(VIEWPORT_MARGIN, (mainRect?.left ?? 0) + VIEWPORT_MARGIN);
  const maxRight = Math.min(viewportWidth - VIEWPORT_MARGIN, (mainRect?.right ?? viewportWidth) - VIEWPORT_MARGIN);
  return {
    minLeft,
    maxLeft: Math.max(minLeft, maxRight - menuWidth),
  };
}

export default function FloatingAssetMenu(props) {
  let menuEl;
  let frame = 0;
  let schedulePlaceMenu;
  let handlePointerDown;
  let handleKeyDown;
  const [placement, setPlacement] = createSignal({ left: VIEWPORT_MARGIN, top: VIEWPORT_MARGIN, ready: false });

  const placeMenu = () => {
    const anchor = props.anchor?.();
    if (!anchor || !menuEl) return;

    const anchorRect = anchor.getBoundingClientRect();
    const menuRect = menuEl.getBoundingClientRect();
    const viewport = viewportSize();
    const menuWidth = Math.min(menuRect.width || MENU_WIDTH, viewport.width - (VIEWPORT_MARGIN * 2));
    const menuHeight = Math.min(menuRect.height || 1, viewport.height - (VIEWPORT_MARGIN * 2));
    const bounds = horizontalBounds(anchor, menuWidth, viewport.width);

    const left = clamp(anchorRect.right - menuWidth, bounds.minLeft, bounds.maxLeft);
    const belowTop = anchorRect.bottom + MENU_GAP;
    const aboveTop = anchorRect.top - menuHeight - MENU_GAP;
    const top = belowTop + menuHeight <= viewport.height - VIEWPORT_MARGIN || aboveTop < VIEWPORT_MARGIN
      ? clamp(belowTop, VIEWPORT_MARGIN, viewport.height - menuHeight - VIEWPORT_MARGIN)
      : aboveTop;

    setPlacement({ left, top, ready: true });
  };

  onMount(() => {
    setPlacement({ left: VIEWPORT_MARGIN, top: VIEWPORT_MARGIN, ready: false });
    frame = window.requestAnimationFrame(placeMenu);
    schedulePlaceMenu = () => {
      window.cancelAnimationFrame(frame);
      frame = window.requestAnimationFrame(placeMenu);
    };
    handlePointerDown = (event) => {
      const target = event.target;
      const anchor = props.anchor?.();
      if (menuEl?.contains(target) || anchor?.contains(target)) return;
      props.onClose?.();
    };
    handleKeyDown = (event) => {
      if (event.key !== "Escape") return;
      event.stopPropagation();
      props.onClose?.();
    };

    window.addEventListener("resize", schedulePlaceMenu, true);
    window.addEventListener("scroll", schedulePlaceMenu, true);
    document.addEventListener("pointerdown", handlePointerDown, true);
    document.addEventListener("keydown", handleKeyDown, true);
  });

  onCleanup(() => {
    window.cancelAnimationFrame(frame);
    if (schedulePlaceMenu) {
      window.removeEventListener("resize", schedulePlaceMenu, true);
      window.removeEventListener("scroll", schedulePlaceMenu, true);
    }
    if (handlePointerDown) document.removeEventListener("pointerdown", handlePointerDown, true);
    if (handleKeyDown) document.removeEventListener("keydown", handleKeyDown, true);
  });

  const menuClass = () => {
    const anchor = props.anchor?.();
    return [
      "ual-card-menu",
      "ual-floating-card-menu",
      props.class,
      anchor?.closest?.(".ual-theme-dark") ? "is-theme-dark" : "",
    ].filter(Boolean).join(" ");
  };
  const menuStyle = () => ({
    "box-sizing": "border-box",
    bottom: "auto",
    left: `${Math.round(placement().left)}px`,
    "max-height": `calc(100dvh - ${VIEWPORT_MARGIN * 2}px)`,
    "max-width": `calc(100vw - ${VIEWPORT_MARGIN * 2}px)`,
    "min-width": "0",
    overflow: "auto",
    "pointer-events": "auto",
    position: "fixed",
    right: "auto",
    top: `${Math.round(placement().top)}px`,
    visibility: placement().ready ? "visible" : "hidden",
    width: `${MENU_WIDTH}px`,
    "z-index": "1301",
  });

  return <Portal>
    <div
      ref={(el) => { menuEl = el; }}
      class={menuClass()}
      role={props.role || "menu"}
      aria-label={props["aria-label"]}
      style={menuStyle()}
      onClick={props.onClick}
    >
      {props.children}
    </div>
  </Portal>;
}
