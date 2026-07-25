import assert from "node:assert/strict";
import {
  MEDIA_LIBRARY_CAPABILITY_KEYS,
  mediaLibraryCapabilityView,
  normalizeMediaLibraryCapabilities,
} from "../src/modules/mediaLibrary/mediaLibraryCapabilities.js";

const enabled = normalizeMediaLibraryCapabilities({
  schema_version: "media_library_capabilities_v1",
  features: Object.fromEntries(
    MEDIA_LIBRARY_CAPABILITY_KEYS.map((key) => [
      key,
      { enabled: true, configuration_valid: true },
    ]),
  ),
});
assert.equal(enabled.valid, true);
assert.deepEqual(mediaLibraryCapabilityView(enabled), {
  analysisActionsEnabled: true,
  searchEntryVisible: true,
  visualSemanticActionEnabled: true,
  compositeActionEnabled: true,
  editorEntryVisible: true,
  editorMutationsEnabled: true,
  clipSearchEnabled: true,
  publishedAnalysisVisible: true,
  successfulClipsVisible: true,
});

const disabled = normalizeMediaLibraryCapabilities({
  schema_version: "media_library_capabilities_v1",
  features: {
    analysis_runs: { enabled: true, configuration_valid: true },
    library_search: { enabled: false, configuration_valid: true },
    visual_semantic: {
      enabled: true,
      configuration_valid: false,
    },
    composite: { enabled: false, configuration_valid: true },
    editor: { enabled: false, configuration_valid: true },
  },
});
const disabledView = mediaLibraryCapabilityView(disabled);
assert.equal(disabledView.searchEntryVisible, false);
assert.equal(disabledView.visualSemanticActionEnabled, false);
assert.equal(disabledView.compositeActionEnabled, false);
assert.equal(disabledView.editorEntryVisible, false);
assert.equal(disabledView.editorMutationsEnabled, false);
assert.equal(disabledView.clipSearchEnabled, false);
assert.equal(disabledView.publishedAnalysisVisible, true);
assert.equal(disabledView.successfulClipsVisible, true);

const invalid = mediaLibraryCapabilityView(
  normalizeMediaLibraryCapabilities({
    schema_version: "media_library_capabilities_v1",
    features: {
      analysis_runs: { enabled: true },
    },
  }),
);
assert.equal(invalid.analysisActionsEnabled, false);
assert.equal(invalid.searchEntryVisible, false);
assert.equal(invalid.editorEntryVisible, false);
assert.equal(invalid.clipSearchEnabled, false);
assert.equal(invalid.publishedAnalysisVisible, true);
assert.equal(invalid.successfulClipsVisible, true);

console.log("media library capabilities contract: ok");
