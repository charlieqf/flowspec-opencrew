# OpenCrew ToolLibrary Agent Guide

This guide is written for an Agent that plans and recommends an OpenClip semantic-first workflow before running tools. Do not blindly run every tool. Build a plan from the user goal, the video properties, and the outputs produced by earlier tools. Present the plan to the user for confirmation before high-cost or VLM-heavy steps.

## Operating Principles

- Start with low-cost evidence gathering, then decide whether expensive tools are justified.
- Prefer `--print-json` so tool outputs can be parsed by the Agent.
- Prefer `--resume` for long-running VLM tools.
- Treat `01`, `02`, `04`, and `05` as the usual foundation for video work.
- Treat `02_0` as an optional audio preprocessing tool when strong background music makes ASR unreliable; it separates `vocals.wav` from `no_vocals.wav` for later VAD/ASR use.
- Treat `05_1` as an optional visual-text foundation when keyframes contain slides, title cards, screenshots, charts, subtitles, or other on-screen text that may affect semantic segmentation. It separates subtitle candidates from other visual text.
- Treat `05_2` as the ASR/OCR subtitle bidirectional calibration step when SRT accuracy matters. It lets high-quality ASR correct OCR subtitles and stable OCR subtitles correct ASR gaps or drift.
- Treat `03` and `13` as core for semantic-first timeline generation.
- Treat `06`, `07`, `09`, `11`, and `12` as conditional refinement tools.
- Treat `14`, `15`, and `16` as delivery/validation tools when the user needs retake descriptions or final scheme packages.
- Before running VLM-heavy tools (`06`, `14 --description-mode vlm`), explain why they are needed, estimate cost, and ask for confirmation if the user has not already approved.

## Recommended Planning Flow

1. Run foundation tools when missing:
    - `01 VideoMetadataExtractor`
    - `02_0 SourceSeparation` when background music is strong or mixed-audio ASR is unreliable
    - `02 AudioASRPipeline`
    - `04 PySceneDetectRunner`
    - `05 VisualEvidenceExtractor`
    - `05_1 VisualOCRTimelineBuilder` when on-screen text may carry semantic meaning
    - `05_2 SubtitleBidirectionalCalibrator` when subtitles/SRT should be aligned between ASR and OCR
2. Inspect outputs:
   - `meta/video_metadata.json`
   - `meta/asr_quality.json`
   - `meta/asr_segments.json`
   - `meta/pyscenedetect_cuts.json`
    - `meta/visual_boundary_candidates.json`
    - `meta/visual_keyframes.json`
    - `meta/visual_ocr_timeline.json`, `meta/visual_subtitle_timeline.json`, `meta/visual_text_timeline.json`, and `meta/scene_visual_ocr_alignment.json` when `05_1` was run
    - `meta/subtitle_alignment_timeline.json` and `meta/visual_subtitle_timeline_calibrated.json` when `05_2` was run
3. Decide whether conditional visual tools are needed:
   - Run `06` only when physical scene transition accuracy matters and cut count/visual complexity justify VLM cost.
   - Run `07` only when long ASR-silent intervals may still contain meaningful visual structure.
   - Run `09` only when strong visual boundary candidates should affect detail segmentation.
4. Run semantic structure and low-cost refinements:
   - `03 SemanticLLMStructureBuilder`
   - `08 BoundaryAligner`
   - `10 EvidenceCollector`
   - `11 OverCoarseSegmentRefiner` when semantic segments are too long.
   - `12 OverFragmentedSegmentMerger` when semantic segments are too short or fragmented.
5. Build final timelines:
    - `13_01 SceneSRTCalibrator` when detail timeline should use Scene-level SRT calibrated by ASR/OCR and SceneDetect.
    - `13 FineTimelineBuilder`
6. If retake descriptions are required:
   - `14 SegmentDescriptorSubtitleBuilder`
   - Use `--description-mode vlm` for production retake descriptions.
   - Use `--description-mode rule` only for quick local/debug output.
7. If deliverables are required:
   - `15 SchemeExportValidator`
8. Always run final QA when producing deliverables:
   - `16 SemanticFirstQualityChecker`

## Dynamic Decision Rules

### Skip Or Defer `06 SceneTransitionLLMJudge`

Skip or defer `06` when:

- The video appears to use one stable shooting location.
- The user does not need strict physical scene transition detection.
- Runtime/cost budget is low.
- PySceneDetect cut count is low and 05 visual evidence does not suggest complex scene changes.

Run `06` when:

- The video includes multiple physical locations and boundary accuracy matters.
- PySceneDetect cut count is high and many cuts may be camera-angle changes rather than true location changes.
- The final timeline depends on knowing whether cuts open a new shooting space.
- The user explicitly requests high-accuracy scene/location segmentation.

Cost estimate:

- Very high.
- VLM calls are approximately `ceil(candidate_cut_count / batch_size)`.
- Supports `--resume`, independent batch cache, and progress logs.

### Skip Or Defer `07 SilentVisualSegmentDetector`

Skip or defer `07` when:

- ASR coverage is strong.
- There are no meaningful long silent ranges.
- The video is primarily spoken content and silent intervals are not important.

Run `07` when:

- ASR shows long no-speech intervals.
- Silent intervals may still contain meaningful visual actions, slides, B-roll, or demonstrations.

### Skip Or Defer `09 VisualBoundaryPromoter`

Skip or defer `09` when:

- Visual changes are weak or rare.
- Existing semantic boundaries are sufficient.
- You are using a fast/low-cost profile.

Run `09` when:

- 05 detects strong visual boundary candidates.
- Detail segmentation should respect visual transitions in addition to semantic boundaries.
- You want more granular detail segments, while preserving minimum duration constraints.

### Run `11` And `12` Only When Needed

Run `11 OverCoarseSegmentRefiner` when:

- 03 produces long semantic segments.
- A segment has dense ASR, many internal cuts, or internal evidence suggesting multiple subtopics.

Run `12 OverFragmentedSegmentMerger` when:

- 03 produces very short or fragmented semantic segments.
- Adjacent segments share topic/formula slot and are too small for practical editing.

### Choose `14` Mode

Use `14 --description-mode vlm` when:

- User needs production-quality retake descriptions.
- Visual details such as props, people, scene, actions, and camera guidance matter.

Use `14 --description-mode rule` only when:

- User needs a quick structural placeholder.
- VLM is unavailable or cost must be avoided.

Cost estimate:

- `rule`: low.
- `vlm`: very high, one VLM call per detail segment.
- Supports `--resume`, independent segment cache, image compression, and progress logs.

## Cost Levels

- `very_low`: seconds, local JSON processing.
- `low`: local processing, usually quick.
- `medium`: video/audio processing or moderate file generation.
- `high`: local heavy compute or many exported clips.
- `very_high`: LLM/VLM calls or long-running model work.

## Common Profiles

Use `pipeline_profiles.json` for machine-readable profiles. Human-readable summary:

- `fast_structure`: quick semantic timeline, no VLM-heavy optional tools.
- `standard_semantic`: semantic-first timeline with low-cost evidence and refinement.
- `visual_strict`: uses VLM scene transition judgement and visual promotion.
- `retake_delivery_vlm`: produces VLM retake descriptions and complete scheme packages.
- `full_quality`: runs all major conditional refinements and final deliverables.

## Product Host / SRT-Driven Detail Flow

For product-founder/product-host explanation videos where spoken SRT and frame subtitle alignment are critical, prefer this detail-only flow:

1. `01 VideoMetadataExtractor`
2. `02 AudioASRPipeline`
3. `04 PySceneDetectRunner`
4. `05 VisualEvidenceExtractor`
5. `05_1 VisualOCRTimelineBuilder`
6. `05_2 SubtitleBidirectionalCalibrator`
7. `03 SemanticLLMStructureBuilder`
8. `08 BoundaryAligner` when boundary snapping is useful
9. `13_01 SceneSRTCalibrator`
10. `13 FineTimelineBuilder` with default detail-only output

In this profile, do not run balanced/summary generation unless explicitly requested. Treat ASR/OCR subtitle alignment as bidirectional: high-quality ASR can correct OCR subtitle text and time; stable OCR subtitles can fill ASR gaps or correct drift.

## Agent Recommendation Format

Before executing a non-trivial workflow, present this structure to the user:

```text
Recommended profile: <profile_name>

Goal interpretation:
- <what the user wants>

Already available outputs:
- <detected existing files>

Recommended tools:
- <tool ids and reasons>

Conditional tools to decide after intermediate outputs:
- <tool ids and decision criteria>

Skipped for now:
- <tool ids and reasons>

Estimated runtime:
- <rough estimate by cost category>

VLM/LLM calls:
- <which tools use model calls and expected call counts>

Resume/caching:
- <which long tools support resume>

Commands to run:
- <ordered commands>

Waiting for confirmation:
- <especially VLM-heavy tools>
```

## Quality Gate

When the user expects final deliverables, finish with `16 SemanticFirstQualityChecker`. A successful final package should have:

- `reports/quality_check.json` with `status=passed`.
- Three scheme folders:
  - `schemes/scheme_1` = detail.
  - `schemes/scheme_2` = balanced.
  - `schemes/scheme_3` = summary.
- Every segment has exactly three deliverables:
  - `segment_XXX.mp4`
  - `segment_XXX.srt`
  - `segment_XXX.json`
