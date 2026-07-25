import { createMemo, createSignal } from "solid-js";
import { mediaLibraryUploadApi } from "./mediaLibraryUploadApi.js";
import {
  mediaUploadChunkBounds,
  mediaUploadErrorMessage,
  mediaUploadWorkerCount,
  validateMediaUploadFile,
} from "./mediaLibraryUploadModel.js";

const RETRY_DELAYS = [0, 500, 1200];

const wait = (delay) => new Promise((resolve) => window.setTimeout(resolve, delay));

async function completeAndWait(uploadId, sizeBytes) {
  let completed = await mediaLibraryUploadApi.complete(uploadId, sizeBytes);
  let polls = 0;
  while (completed?.upload?.status === "finalizing") {
    await wait(500);
    polls += 1;
    const status = await mediaLibraryUploadApi.status(uploadId);
    if (status.status === "failed") throw new Error(status.error || "视频保存失败");
    if (status.status === "ready" || polls % 20 === 0) completed = await mediaLibraryUploadApi.complete(uploadId, sizeBytes);
  }
  return completed;
}

export function useMediaLibraryUpload(options = {}) {
  const [file, setFile] = createSignal(null);
  const [stage, setStage] = createSignal("idle");
  const [progressBytes, setProgressBytes] = createSignal(0);
  const [error, setError] = createSignal("");
  const [uploadId, setUploadId] = createSignal("");
  let controller = null;

  const active = createMemo(() => ["preparing", "uploading", "saving"].includes(stage()));
  const percent = createMemo(() => {
    const total = Number(file()?.size || 0);
    if (!total) return 0;
    return Math.min(100, Math.max(0, Math.round((progressBytes() / total) * 100)));
  });

  function selectFile(nextFile) {
    if (active()) return;
    const message = validateMediaUploadFile(nextFile);
    if (message) {
      setError(message);
      return false;
    }
    setFile(nextFile);
    setStage("selected");
    setProgressBytes(0);
    setError("");
    return true;
  }

  function reset() {
    if (active()) return false;
    setFile(null);
    setStage("idle");
    setProgressBytes(0);
    setError("");
    setUploadId("");
    controller = null;
    return true;
  }

  async function uploadChunkWithRetry(transaction, chunkIndex, blob, onProgress) {
    let lastError = null;
    for (let attempt = 0; attempt < RETRY_DELAYS.length; attempt += 1) {
      if (controller?.signal.aborted) throw new DOMException("Upload cancelled", "AbortError");
      if (RETRY_DELAYS[attempt]) await wait(RETRY_DELAYS[attempt]);
      const form = new FormData();
      form.append("chunk_index", String(chunkIndex));
      form.append("total_chunks", String(transaction.total_chunks));
      form.append("file", blob, `${file().name}.part-${chunkIndex}`);
      try {
        return await mediaLibraryUploadApi.uploadChunk(transaction.upload_id, form, {
          signal: controller.signal,
          onProgress: (loaded) => onProgress(Math.min(loaded, blob.size)),
        });
      } catch (uploadError) {
        if (uploadError?.name === "AbortError") throw uploadError;
        lastError = uploadError;
      }
    }
    throw lastError || new Error("上传分片失败");
  }

  async function start() {
    const selected = file();
    const validation = validateMediaUploadFile(selected);
    if (validation) {
      setError(validation);
      return;
    }
    if (active()) return;
    controller = new AbortController();
    setError("");
    setStage("preparing");
    try {
      let transaction;
      if (uploadId()) transaction = await mediaLibraryUploadApi.status(uploadId());
      else {
        transaction = await mediaLibraryUploadApi.create(selected);
        setUploadId(transaction.upload_id);
      }
      const received = new Set((transaction.received_chunks || []).map(Number));
      const inFlightBytes = new Map();
      const chunkBounds = (index) => mediaUploadChunkBounds(selected.size, transaction.chunk_size, index);
      const updateProgress = () => {
        const confirmed = [...received].reduce((total, index) => {
          const bounds = chunkBounds(index);
          return total + Math.max(0, bounds.end - bounds.start);
        }, 0);
        const active = [...inFlightBytes.values()].reduce((total, loaded) => total + loaded, 0);
        setProgressBytes(Math.min(selected.size, confirmed + active));
      };
      updateProgress();
      setStage("uploading");
      const pending = Array.from({ length: transaction.total_chunks }, (_, index) => index).filter((index) => !received.has(index));
      let cursor = 0;
      const worker = async () => {
        while (cursor < pending.length) {
          const index = pending[cursor];
          cursor += 1;
          const bounds = chunkBounds(index);
          const blob = selected.slice(bounds.start, bounds.end);
          inFlightBytes.set(index, 0);
          await uploadChunkWithRetry(transaction, index, blob, (loaded) => {
            inFlightBytes.set(index, loaded);
            updateProgress();
          });
          inFlightBytes.delete(index);
          received.add(index);
          updateProgress();
        }
      };
      await Promise.all(Array.from({ length: mediaUploadWorkerCount(pending.length) }, () => worker()));
      setProgressBytes(selected.size);
      setStage("saving");
      const completed = await completeAndWait(transaction.upload_id, selected.size);
      setStage("completed");
      await options.onComplete?.(completed.item, completed.upload);
      return completed;
    } catch (uploadError) {
      if (uploadError?.name === "AbortError") {
        setStage("cancelled");
        return;
      }
      setError(mediaUploadErrorMessage(uploadError));
      setStage("failed");
    } finally {
      controller = null;
    }
  }

  async function cancel() {
    controller?.abort();
    const currentUploadId = uploadId();
    if (currentUploadId) {
      try {
        await mediaLibraryUploadApi.cancel(currentUploadId);
      } catch (cancelError) {
        if (stage() !== "completed") setError(mediaUploadErrorMessage(cancelError));
      }
    }
    setStage("cancelled");
    setUploadId("");
  }

  return { file, stage, progressBytes, error, active, percent, uploadId, selectFile, reset, start, cancel };
}
