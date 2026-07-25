# DanceMimic_V1 ToolLibrary Surface

This directory is intentionally thin. DanceMimic_V1 has its own runner target,
surface, and route (`dance_mimic_v1`), but shared capabilities should live in
`ToolLibrary/Analysis_V1` or shared backend services when possible.

## Current Phase

The directory keeps a thin wrapper surface and a small DanceMimic-specific
implementation module for the 00-03 reference-video toolchain:

- `tool_registry.json`
- `00_PrepareSessionVariables.py`
- `01_ReferenceMediaDemux.py`
- `02_ReferenceFaceMaskedVideoBuild.py`
- `03_StoryBoardStandardTaskBuild.py`
- `_tool_impl.py`
- `test_fixtures/`

The wrappers delegate to `_tool_impl.py`. The implementation prepares
`Variables.json`, demuxes the source reference video, builds provider-safe
face-masked segment reference videos with the real `insightface_scrfd` detector
by default (`mediapipe_blazeface` and `opencv_haar` fallbacks), preserves
fixed-bbox CI fixture support, and emits the StoryBoard seed consumed by the
existing 05 video planning/execution path. The default reference privacy mode is
`provider_safe_outline`: 02 masks the detected face, renders a non-identifying
motion outline reference, re-encodes it to H.264, and records file-size QA so
oversized references are blocked before OpenRouter upload.

## Boundaries

- Do not route DanceMimic through the Analysis_V1 seven-step run-to-storyboard
  chain.
- Do not restore `01_VideoProbeMetadata.py` here. DanceMimic step 01 is
  `01_ReferenceMediaDemux`.
- Tool-level variables use `SessionContext/Variables.json.source_video_path`.
  Do not read `Variables.json.reference_video_path`.
- 02/03 reference videos are motion references. They must not be written as
  `Video_Final` or `working_assets.video.path`.
- OpenRouter MaxSR2 `input_references` is the primary downstream video path.
  Seedance/MaxSR2 normalization is only a fallback for legacy or override plans.

## Implementation Order

1. Extend or share `Analysis_V1/00_PrepareSessionVariables.py` for
   `workflow_id=dance_mimic_v1`.
2. Implement `01_ReferenceMediaDemux` with
   `SessionOutput/reference/reference_media_manifest.json` as a required output.
3. Implement `02_ReferenceFaceMaskedVideoBuild` with `insightface_scrfd` as the
   default detector, provider-safe reference rendering, H.264 size control, and
   fake/fixed-bbox fixture support for CI.
4. Implement `03_StoryBoardStandardTaskBuild` from the 03 requirements document,
   including stable `dak_NNNN` keys and empty final-video slots.
5. Wire backend `dance_mimic_v1` runner/route separately from Analysis_V1.
