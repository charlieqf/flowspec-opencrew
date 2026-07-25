import { api } from "../../../lib/api.ts";

export const mediaLibraryUploadApi = {
  create: (file) => api.mediaLibraryUploadCreate({ filename: file.name, size_bytes: file.size, content_type: file.type || "application/octet-stream" }),
  status: (uploadId) => api.mediaLibraryUploadStatus(uploadId),
  uploadChunk: (uploadId, form, options) => api.mediaLibraryUploadChunk(uploadId, form, options),
  complete: (uploadId, sizeBytes) => api.mediaLibraryUploadComplete(uploadId, sizeBytes),
  cancel: (uploadId) => api.mediaLibraryUploadCancel(uploadId),
};
