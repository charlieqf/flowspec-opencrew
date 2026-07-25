import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  MEDIA_UPLOAD_CONCURRENCY,
  mediaUploadChunkBounds,
  mediaUploadWorkerCount,
} from "../src/modules/mediaLibrary/upload/mediaLibraryUploadModel.js";

const here = path.dirname(fileURLToPath(import.meta.url));
const hookSource = fs.readFileSync(path.join(here, "../src/modules/mediaLibrary/upload/useMediaLibraryUpload.js"), "utf8");

assert.equal(MEDIA_UPLOAD_CONCURRENCY, 3);
assert.equal(mediaUploadWorkerCount(0), 0);
assert.equal(mediaUploadWorkerCount(2), 2);
assert.equal(mediaUploadWorkerCount(21), 3);
assert.deepEqual(mediaUploadChunkBounds(335_579_136, 16 * 1024 * 1024, 0), { start: 0, end: 16 * 1024 * 1024 });
assert.deepEqual(mediaUploadChunkBounds(335_579_136, 16 * 1024 * 1024, 20), { start: 335_544_320, end: 335_579_136 });
assert.match(hookSource, /Promise\.all\(Array\.from\(\{ length: mediaUploadWorkerCount\(pending\.length\) \}/);
assert.match(hookSource, /const inFlightBytes = new Map\(\)/);
assert.match(hookSource, /received\.add\(index\)/);

console.log("media library concurrent upload performance contract: ok");
