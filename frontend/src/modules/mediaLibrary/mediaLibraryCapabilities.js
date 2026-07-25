export const MEDIA_LIBRARY_CAPABILITY_KEYS = Object.freeze([
  "analysis_runs",
  "library_search",
  "visual_semantic",
  "composite",
  "editor",
  "visual_search_v1",
  "clip_search_v1",
]);

function disabledFeature() {
  return { enabled: false, configurationValid: false };
}

export function normalizeMediaLibraryCapabilities(raw) {
  const validSchema = (
    raw?.schema_version === "media_library_capabilities_v1"
  );
  const features = {};
  for (const key of MEDIA_LIBRARY_CAPABILITY_KEYS) {
    const feature = validSchema ? raw?.features?.[key] : null;
    features[key] = feature
      && typeof feature.enabled === "boolean"
      && typeof feature.configuration_valid === "boolean"
      ? {
        enabled: feature.enabled,
        configurationValid: feature.configuration_valid,
      }
      : disabledFeature();
  }
  return {
    valid: validSchema && MEDIA_LIBRARY_CAPABILITY_KEYS.every(
      (key) => (
        typeof raw?.features?.[key]?.enabled === "boolean"
        && typeof raw?.features?.[key]?.configuration_valid
          === "boolean"
      ),
    ),
    schema: validSchema ? raw.schema_version : "",
    features,
  };
}

export function mediaLibraryFeatureEnabled(capabilities, key) {
  const feature = capabilities?.features?.[key];
  return Boolean(
    capabilities?.valid
    && feature?.enabled
    && feature?.configurationValid,
  );
}

export function mediaLibraryCapabilityView(capabilities) {
  const analysisRuns = mediaLibraryFeatureEnabled(
    capabilities,
    "analysis_runs",
  );
  return {
    analysisActionsEnabled: analysisRuns,
    searchEntryVisible: mediaLibraryFeatureEnabled(
      capabilities,
      "library_search",
    ),
    visualSemanticActionEnabled: analysisRuns
      && mediaLibraryFeatureEnabled(
        capabilities,
        "visual_semantic",
      ),
    compositeActionEnabled: analysisRuns
      && mediaLibraryFeatureEnabled(capabilities, "composite"),
    editorEntryVisible: mediaLibraryFeatureEnabled(
      capabilities,
      "editor",
    ),
    editorMutationsEnabled: mediaLibraryFeatureEnabled(
      capabilities,
      "editor",
    ),
    clipSearchEnabled: mediaLibraryFeatureEnabled(
      capabilities,
      "clip_search_v1",
    ),
    publishedAnalysisVisible: true,
    successfulClipsVisible: true,
  };
}
