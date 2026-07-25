function standardPictureInPictureSupported(videoEl = null) {
  const prototype = typeof HTMLVideoElement !== "undefined" ? HTMLVideoElement.prototype : null;
  const request = videoEl?.requestPictureInPicture || prototype?.requestPictureInPicture;
  return Boolean(
    typeof document !== "undefined"
    && document.pictureInPictureEnabled
    && typeof request === "function"
  );
}

function safariPictureInPictureSupported(videoEl = null) {
  const prototype = typeof HTMLVideoElement !== "undefined" ? HTMLVideoElement.prototype : null;
  const setPresentationMode = videoEl?.webkitSetPresentationMode || prototype?.webkitSetPresentationMode;
  return Boolean(typeof setPresentationMode === "function");
}

export function browserSupportsVideoPictureInPicture() {
  return standardPictureInPictureSupported() || safariPictureInPictureSupported();
}

function waitForVideoMetadata(videoEl) {
  if (videoEl.readyState > 0) return Promise.resolve();
  return new Promise((resolve, reject) => {
    let timeoutId = 0;
    const cleanup = () => {
      videoEl.removeEventListener("loadedmetadata", handleLoadedMetadata);
      videoEl.removeEventListener("error", handleError);
      window.clearTimeout(timeoutId);
    };
    const handleLoadedMetadata = () => {
      cleanup();
      resolve();
    };
    const handleError = () => {
      cleanup();
      reject(new Error("Video metadata could not be loaded"));
    };
    videoEl.addEventListener("loadedmetadata", handleLoadedMetadata, { once: true });
    videoEl.addEventListener("error", handleError, { once: true });
    timeoutId = window.setTimeout(() => {
      cleanup();
      reject(new Error("Timed out while loading video metadata"));
    }, 4000);
    videoEl.load();
  });
}

export async function toggleVideoPictureInPicture(videoEl) {
  if (!videoEl) throw new Error("Video preview is not ready");
  videoEl.controls = true;
  await waitForVideoMetadata(videoEl);

  if (safariPictureInPictureSupported(videoEl) && !standardPictureInPictureSupported(videoEl)) {
    const mode = videoEl.webkitPresentationMode === "picture-in-picture" ? "inline" : "picture-in-picture";
    videoEl.webkitSetPresentationMode(mode);
    return;
  }
  if (!standardPictureInPictureSupported(videoEl)) throw new Error("Picture in Picture is not supported");
  if (document.pictureInPictureElement === videoEl) {
    await document.exitPictureInPicture();
    return;
  }
  if (document.pictureInPictureElement) await document.exitPictureInPicture();
  await videoEl.requestPictureInPicture();
}
