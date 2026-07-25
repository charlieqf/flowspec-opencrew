import assert from "node:assert/strict";
import {
  editorNavigationFromHash,
  mediaLibraryAssetCanOpenEditor,
  mediaLibraryRouteFromHash,
  normalizeMediaAsset,
} from "../src/modules/mediaLibrary/mediaLibraryModel.js";

assert.deepEqual(
  mediaLibraryRouteFromHash("#/media-library/mla_001/editor"),
  { view: "editor", assetId: "mla_001", navigation: {}, error: "" },
);
assert.equal(
  mediaLibraryRouteFromHash("#/media-library/mla_001/editor?start_ms=1000").view,
  "editor",
);
assert.equal(mediaLibraryRouteFromHash("#/media-library/mla_001").view, "detail");
assert.equal(mediaLibraryRouteFromHash("#/media-library/mla_001?tab=dialogue").view, "detail");
assert.equal(mediaLibraryRouteFromHash("#/media-library/mla_001/unknown").view, "invalid");
assert.equal(mediaLibraryRouteFromHash("#/media-library/mla_001/editor/unknown").view, "invalid");
assert.equal(mediaLibraryRouteFromHash("#/media-library/%2Fetc%2Fpasswd").view, "invalid");
assert.equal(mediaLibraryRouteFromHash("#/media-library").view, "list");

const editorReadyAsset = normalizeMediaAsset({
  asset_id: "mla_001",
  source_version: "a".repeat(64),
  upload_status: "ready",
  archived: false,
});
assert.equal(editorReadyAsset.sourceVersion, "a".repeat(64));
assert.equal(mediaLibraryAssetCanOpenEditor(editorReadyAsset), true);
assert.equal(mediaLibraryAssetCanOpenEditor({ ...editorReadyAsset, uploadStatus: "uploading" }), false);
assert.equal(mediaLibraryAssetCanOpenEditor({ ...editorReadyAsset, archived: true }), false);
assert.equal(mediaLibraryAssetCanOpenEditor({ ...editorReadyAsset, sourceVersion: "" }), false);

assert.deepEqual(
  editorNavigationFromHash(
    "#/media-library/mla_001/editor?start_ms=1000&end_ms=2000&target_task_id=27"
      + "&dialogue_asset_key=dialogue_0005&search_id=mls_1&matched_fragment_id=srt_1"
      + "&return_to=storyboard_dialogue&provider=forbidden&source_url=https%3A%2F%2Fevil.invalid",
  ),
  {
    start_ms: 1000,
    end_ms: 2000,
    target_task_id: 27,
    dialogue_asset_key: "dialogue_0005",
    search_id: "mls_1",
    matched_fragment_id: "srt_1",
    return_to: "storyboard_dialogue",
  },
);
assert.deepEqual(
  editorNavigationFromHash(
    "#/media-library/mla_001/editor?start_ms=-1&end_ms=1&target_task_id=0"
      + "&dialogue_asset_key=bad%2Fpath&return_to=https%3A%2F%2Fevil.invalid",
  ),
  {},
);

console.log("media library editor route contract: ok");
