# OpenCut V1

OpenCut V1 is an independent Tool Library toolset for reusable media analysis. It currently contains independent dialogue and Scene Detect visual schemes.

Dialogue analysis:

```text
00_PrepareSessionVariables
  -> 01_VideoProbeMetadata
  -> 02_01_AudioASR
  -> 02_02_VideoSRTFrame
```

The final unit is one calibrated dialogue sentence/page, one source-video time range, and one keyframe. It does not rewrite SRT text and does not produce separate clip files.

Scene Detect visual analysis:

```text
00_PrepareSessionVariables
  -> 01_VideoProbeMetadata
  -> 03_01_VideoSceneDetect
  -> 03_02_SceneKeyframeIndex
```

The visual baseline creates one detected scene, one source-video time range, and one midpoint keyframe. It does not perform visual semantic understanding and does not depend on dialogue results.

## Independence boundary

- Runtime code under this directory does not import `ToolLibrary/Analysis_V1` or `ToolLibrary/Analysis`.
- Shared backend Tool Use Session infrastructure and provider configuration storage remain platform dependencies.
- Media/OCR/ASR Python packages are runtime dependencies, not dependencies on another business toolset.

## Main output

```text
SessionOutput/subtitle/final_srt_frame_items.json
SessionOutput/subtitle/calibrated_srt_items.json
SessionOutput/visual/srt_frames/

SessionOutput/visual/scene_detect_cuts.json
SessionOutput/visual/scene_detect_scenes.json
SessionOutput/visual/final_scene_frame_items.json
SessionOutput/visual/scene_frames/
```

The UI treats `start`/`end` as a virtual clip in the original video. No per-sentence MP4 is generated.
