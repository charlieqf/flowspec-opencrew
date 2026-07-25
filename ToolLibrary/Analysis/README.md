# OpenCrew Tool Library

Reusable Python tools for OpenCrew/OpenClip workflows.

## Project Media Binaries

OpenCrew tools resolve media binaries in this order: `OPENCREW_FFMPEG_PATH`/`OPENCREW_FFPROBE_PATH`, project-local `OpenCrew/.bin/`, system `PATH`, then `imageio_ffmpeg` for `ffmpeg` only. `ffprobe` is required for tools that need reliable media probing or Demucs audio loading.

Install project-local `ffmpeg` and `ffprobe` without system-level installation:

```bash
python3 OpenCrew/ToolLibrary/Analysis/install_media_binaries.py --overwrite --print-json
```

Check availability:

```bash
python3 OpenCrew/ToolLibrary/Analysis/check_media_dependencies.py --print-json
```

For Agent-driven workflow planning, read these files first:

- `AGENT_TOOL_GUIDE.md`: human-readable planning strategy, skip/run rules, and recommendation format.
- `tool_registry.json`: machine-readable tool metadata, dependencies, costs, run/skip conditions, and outputs.
- `pipeline_profiles.json`: reusable workflow profiles for fast, standard, visual-strict, retake-delivery, and full-quality runs.
- `PROMPT_TEMPLATES.md`: OpenClip Simple Prompt and Simple-to-Final business prompt templates.

Tools must not be hard-bound to a specific task folder layout. Prefer passing `--output-dir` from the Skill/session layer. The conventional OpenClip task layout is:

```text
audio/
history/
inbox/
input/
keyframes/
meta/
outbox/
reports/
schemes/
storyboards/
transcripts/
```

## VideoMetadataExtractor

Extracts basic video metadata and writes outputs into a caller-defined folder.

Example:

```bash
python OpenCrew/ToolLibrary/Analysis/01_video_metadata_extractor.py \
  --video /path/to/video.mp4 \
  --output-dir /path/to/Task#Demo/meta \
  --print-json
```

If `--output-dir` is omitted and `--workspace` is provided, the conventional default output path is:

```text
<workspace>/meta/
```

Outputs:

- `video_metadata.json`
- `video_metadata.md`
- `run_result.json`

## SourceSeparation

Separates vocals from background music before ASR/VAD. This is intended for short dramas, music-heavy edits, or any video where `ffmpeg silencedetect` sees continuous audio activity but speech recognition is unreliable.

Example:

```bash
python3 OpenCrew/ToolLibrary/Analysis/02_0_source_separation.py \
  --video /path/to/video.mp4 \
  --workspace /path/to/Task#Demo \
  --overwrite \
  --print-json
```

The tool uses Demucs by default (`htdemucs`, `--two-stems vocals`) and writes separated tracks without modifying ASR outputs.

Outputs:

- `audio/source_separation_input.wav`
- `audio/separated/vocals.wav`
- `audio/separated/no_vocals.wav`
- `meta/source_separation.json`

## AudioASRPipeline

Extracts ASR-ready audio, runs local Whisper ASR, evaluates ASR quality, and writes normalized ASR segments.

The tool reads ASR provider configuration from OpenCrew PostgreSQL using `OPENCREW_DATABASE_URL` and table `tool_asr_provider_configs`. If PostgreSQL is unavailable, it falls back to `local_whisper` with model `small` and language `zh`.

Example:

```bash
python3 OpenCrew/ToolLibrary/Analysis/02_audio_asr_pipeline.py \
  --video /path/to/video.mp4 \
  --workspace /path/to/Task#Demo \
  --config-name local_whisper_default \
  --print-json
```

Outputs:

- `audio/reference_audio.wav`
- `transcripts/transcript.json`
- `transcripts/original_asr_full.txt`
- `meta/asr_segments.json`
- `meta/asr_quality.json`
- `meta/asr_normalized_segments.json`
- `meta/02_audio_asr_pipeline_result.json`

## PySceneDetectRunner

Runs PySceneDetect as an independent visual cut/scene detector. It does not depend on ASR, semantic units, Final Prompt, or an OpenCode session, so it can also be used for videos without audio or ASR.

Example:

```bash
python3 OpenCrew/ToolLibrary/Analysis/04_pyscenedetect_runner.py \
  --video /path/to/video.mp4 \
  --workspace /path/to/Task#Demo \
  --print-json
```

The first version runs one pass only, but keeps `--profile`, `--pass-name`, and `pyscenedetect_passes.json` for future multi-pass recall/rerun optimization.

Outputs:

- `meta/pyscenedetect_cuts.json`
- `meta/pyscenedetect_scenes.json`
- `meta/pyscenedetect_passes.json`
- `meta/pyscenedetect_summary.md`
- `meta/04_pyscenedetect_runner_result.json`

## VisualEvidenceExtractor

Scans frame-level visual changes, detects visual separator candidates, and extracts keyframe evidence. It is independent from ASR and LLM steps. If PySceneDetect outputs are present, it can use them as the keyframe source; otherwise it can fall back to uniform sampling.

Example:

```bash
python3 OpenCrew/ToolLibrary/Analysis/05_visual_evidence_extractor.py \
  --video /path/to/video.mp4 \
  --workspace /path/to/Task#Demo \
  --source pyscenedetect \
  --print-json
```

Outputs:

- `meta/frame_change_scores.json`
- `meta/visual_boundary_candidates.json`
- `meta/separator_candidates.json`
- `meta/visual_keyframes.json`
- `meta/segment_keyframes.json`
- `meta/05_visual_evidence_extractor_result.json`
- `keyframes/visual_candidates/*.jpg`
- `keyframes/pyscenedetect_scenes/*.jpg`
- `keyframes/separators/*.jpg`

## VisualOCRTimelineBuilder

Runs OCR over keyframes produced by `05 VisualEvidenceExtractor` and builds a compact visual text timeline for downstream semantic segmentation. It is independent from ASR and LLM steps. `03 SemanticLLMStructureBuilder` can treat `meta/visual_ocr_timeline.json` as a weak dependency when on-screen text carries semantic meaning.

Example:

```bash
python3 OpenCrew/ToolLibrary/Analysis/05_1_visual_ocr_timeline_builder.py \
  --workspace /path/to/Task#Demo \
  --ocr-engine auto \
  --languages ch,en \
  --print-json
```

Outputs:

- `meta/visual_ocr_text.json`
- `meta/visual_ocr_timeline.json`
- `meta/visual_ocr_timeline_summary.md`
- `meta/05_1_visual_ocr_timeline_builder_result.json`

## SceneTransitionLLMJudge

Uses an OpenCode vision-capable run model to judge whether PySceneDetect cuts represent true shooting-location transitions. It combines 04 scene cuts, 05 keyframe evidence, and only the selected Task's Final Prompt. The prompt is intentionally industry/location agnostic and only asks whether the physical shooting space changed, not whether topic, task, camera angle, or subject changed.

Use `--resume` to reuse completed `meta/scene_transition_batches/batch_XXX.json` cache files after interruption. Runtime progress is printed to stdout and written to `vlm_progress.json`/`vlm_progress.jsonl` under the batch cache directory.

Example:

```bash
python3 OpenCrew/ToolLibrary/Analysis/06_scene_transition_llm_judge.py \
  --workspace /path/to/Task#Demo \
  --task-id 8 \
  --print-json
```

Outputs:

- `meta/shooting_location_transition_candidates.json`
- `meta/scene_transition_llm_analysis.json`
- `meta/06_scene_transition_llm_judge_result.json`
- `meta/scene_transition_batches/batch_*.json`
- `meta/scene_transition_batches/vlm_progress.json`
- `meta/scene_transition_batches/vlm_progress.jsonl`
- `keyframes/scene_transition_contact_sheets/*.jpg`

## SilentVisualSegmentDetector

Detects ASR-free intervals that still contain meaningful visual structure. It uses `asr_segments.json` to find silent ranges, then checks `visual_boundary_candidates.json`, `separator_candidates.json`, `pyscenedetect_cuts.json`, and `visual_keyframes.json` for supporting evidence. It is conservative by default: silent ranges without visual/cut/separator evidence are rejected rather than promoted as visual segments.

Example:

```bash
python3 OpenCrew/ToolLibrary/Analysis/07_silent_visual_segment_detector.py \
  --workspace /path/to/Task#Demo \
  --print-json
```

Outputs:

- `meta/silent_visual_segments.json`
- `meta/silent_visual_segments_summary.md`
- `meta/07_silent_visual_segment_detector_result.json`

## BoundaryAligner

Aligns existing semantic boundary candidates to nearby visual evidence without adding new structural boundaries. It reads semantic boundaries plus PySceneDetect cuts, visual boundary candidates, and separator candidates. By default it snaps within `0.5s`, weak-snaps high-confidence evidence within `1.5s`, records evidence within `3.0s`, and avoids reusing the same visual evidence for multiple semantic boundaries.

Example:

```bash
python3 OpenCrew/ToolLibrary/Analysis/08_boundary_aligner.py \
  --workspace /path/to/Task#Demo \
  --print-json
```

Outputs:

- `meta/boundary_alignment.json`
- `meta/boundary_alignment_summary.md`
- `meta/08_boundary_aligner_result.json`

## VisualBoundaryPromoter

Promotes only strong visual structure signals into additional boundary candidates without modifying the final timeline. It requires the 03 semantic segment output so it can avoid duplicating semantic structure and reject promoted boundaries that would split any 03 segment into sub-segments shorter than `--min-resulting-segment-duration` (default `3.0` seconds). If `boundary_alignment.json` from 08 exists, the tool uses it to avoid re-promoting visual evidence already consumed by semantic boundary alignment. If 08 is missing or `--ignore-boundary-alignment` is passed, it falls back to 03 segment start/end boundaries for de-duplication.

Example:

```bash
python3 OpenCrew/ToolLibrary/Analysis/09_visual_boundary_promoter.py \
  --workspace /path/to/Task#Demo \
  --print-json
```

Outputs:

- `meta/promoted_visual_boundaries.json`
- `meta/rejected_visual_boundaries.json`
- `meta/visual_boundary_promotion_summary.md`
- `meta/09_visual_boundary_promoter_result.json`

## EvidenceCollector

Collects unused and supporting visual/ASR evidence into a single audit index without modifying segmentation. It requires the 03 semantic segment output so every evidence item can be scoped to a semantic segment, semantic gap, or outside range. Outputs from 08/09/04/05/02 are optional; missing optional inputs are reported in `dependency_status` rather than treated as failures.

Example:

```bash
python3 OpenCrew/ToolLibrary/Analysis/10_evidence_collector.py \
  --workspace /path/to/Task#Demo \
  --print-json
```

Outputs:

- `meta/evidence_index.json`
- `meta/evidence_index_summary.md`
- `meta/10_evidence_collector_result.json`

## OverCoarseSegmentRefiner

Suggests split boundaries for semantic segments that look too long, too dense, or contain strong internal evidence. This tool requires the 03 semantic segment output and treats 08 boundary alignment plus 09 promoted visual boundaries as optional evidence. The first version is suggestions-only: it writes split recommendations but does not modify the timeline. By default, multiple internal candidates only trigger refinement for segments at least `15s` long, and every suggested sub-segment must remain at least `8s` long.

Example:

```bash
python3 OpenCrew/ToolLibrary/Analysis/11_overcoarse_segment_refiner.py \
  --workspace /path/to/Task#Demo \
  --print-json
```

Outputs:

- `meta/overcoarse_refinement.json`
- `meta/overcoarse_refinement_summary.md`
- `meta/11_overcoarse_segment_refiner_result.json`

## OverFragmentedSegmentMerger

Suggests merges for semantic segments that are too short or have too little dialogue. This tool requires the 03 semantic segment output and treats 08 boundary alignment plus 09 promoted visual boundaries as optional boundary-protection evidence. The first version is suggestions-only: it writes merge recommendations but does not modify the timeline.

Example:

```bash
python3 OpenCrew/ToolLibrary/Analysis/12_overfragmented_segment_merger.py \
  --workspace /path/to/Task#Demo \
  --print-json
```

Outputs:

- `meta/overfragmented_merge_decision.json`
- `meta/overfragmented_merge_summary.md`
- `meta/12_overfragmented_segment_merger_result.json`

## FineTimelineBuilder

Builds complete no-gap/no-overlap timelines and writes three schemes: `detail`, `balanced`, and `summary`. It requires the 03 semantic segment output and can use 08/09/10/11/12 outputs when present. `fine_logical_segments.json` defaults to the balanced scheme. All schemes are validated from `0` to video duration.

Example:

```bash
python3 OpenCrew/ToolLibrary/Analysis/13_fine_timeline_builder.py \
  --workspace /path/to/Task#Demo \
  --default-scheme balanced \
  --print-json
```

Outputs:

- `meta/fine_logical_segments.json`
- `meta/scheme_detail_segments.json`
- `meta/scheme_balanced_segments.json`
- `meta/scheme_summary_segments.json`
- `meta/timeline_coverage_check.json`
- `meta/13_fine_timeline_builder_result.json`
- `storyboards/scheme_detail_storyboard.md`
- `storyboards/scheme_balanced_storyboard.md`
- `storyboards/scheme_summary_storyboard.md`

## SegmentDescriptorSubtitleBuilder

Builds one complete JSON retake-description file per segment and cuts subtitles for `detail`, `balanced`, and `summary` schemes. It requires 13 scheme outputs, ASR segments, and 05 visual keyframes. In `--description-mode vlm`, each detail segment calls the Task-bound OpenCode Run Model once with compressed keyframes; balanced and summary descriptions aggregate detail JSON so visual analysis is performed only once at the finest level. `--description-mode rule` remains available for local/debug generation.

Example:

```bash
python3 OpenCrew/ToolLibrary/Analysis/14_segment_descriptor_subtitle_builder.py \
  --workspace /path/to/Task#Demo \
  --print-json
```

VLM example:

```bash
python3 OpenCrew/ToolLibrary/Analysis/14_segment_descriptor_subtitle_builder.py \
  --workspace /path/to/Task#Demo1 \
  --task-id 8 \
  --description-mode vlm \
  --resume \
  --timeout-seconds 900 \
  --image-max-side 1024 \
  --image-quality 75 \
  --print-json
```

Outputs:

- `meta/segment_descriptions/scheme_detail/segment_*.json`
- `meta/segment_descriptions/scheme_balanced/segment_*.json`
- `meta/segment_descriptions/scheme_summary/segment_*.json`
- `meta/scheme_detail_segment_descriptions.json`
- `meta/scheme_balanced_segment_descriptions.json`
- `meta/scheme_summary_segment_descriptions.json`
- `transcripts/scheme_detail_subtitles/segment_*.srt`
- `transcripts/scheme_balanced_subtitles/segment_*.srt`
- `transcripts/scheme_summary_subtitles/segment_*.srt`
- `meta/segment_descriptions/scheme_detail/raw/segment_*_vlm_request.json` in VLM mode
- `meta/segment_descriptions/scheme_detail/raw/segment_*_vlm_response.json` in VLM mode
- `meta/segment_descriptions/scheme_detail/raw/vlm_progress.json` in VLM mode
- `meta/segment_descriptions/scheme_detail/raw/vlm_progress.jsonl` in VLM mode
- `keyframes/segment_descriptor_compressed/*.jpg` in VLM mode
- `meta/14_segment_descriptor_subtitle_builder_result.json`

## SchemeExportValidator

Runs the export-stage bundle for one or more final timeline schemes: cuts SRT subtitles per segment, exports video clips per segment, copies the per-segment retake description JSON from 14, and validates that the selected timeline fully covers the source video without gaps or overlaps. By default it exports all three schemes: detail to `schemes/scheme_1/`, balanced to `schemes/scheme_2/`, and summary to `schemes/scheme_3/`.

Example:

```bash
python3 OpenCrew/ToolLibrary/Analysis/15_scheme_export_validator.py \
  --workspace /path/to/Task#Demo1 \
  --overwrite \
  --print-json
```

Outputs:

- `schemes/scheme_1/segment_*.srt` for detail
- `schemes/scheme_1/segment_*.mp4` for detail
- `schemes/scheme_1/segment_*.json` for detail retake descriptions
- `schemes/scheme_1/manifest.json` for detail
- `schemes/scheme_2/segment_*.srt` for balanced
- `schemes/scheme_2/segment_*.mp4` for balanced
- `schemes/scheme_2/segment_*.json` for balanced retake descriptions
- `schemes/scheme_2/manifest.json` for balanced
- `schemes/scheme_3/segment_*.srt` for summary
- `schemes/scheme_3/segment_*.mp4` for summary
- `schemes/scheme_3/segment_*.json` for summary retake descriptions
- `schemes/scheme_3/manifest.json` for summary
- `reports/timeline_coverage_check.json`
- `meta/15_scheme_export_validator_result.json`

If explicit schemes are requested, they are written in the requested order as `scheme_1`, `scheme_2`, and `scheme_3`:

```bash
python3 OpenCrew/ToolLibrary/Analysis/15_scheme_export_validator.py \
  --workspace /path/to/Task#Demo1 \
  --schemes detail,balanced,summary \
  --print-json
```

Partial export is supported. For example, when the user wants to review only the detailed cut first, export only `detail`; it will be written as `schemes/scheme_1/` and `15_scheme_export_validator_result.json` records `selected_schemes`, `partial_export`, and the output-to-source `scheme_mapping`:

```bash
python3 OpenCrew/ToolLibrary/Analysis/15_scheme_export_validator.py \
  --workspace /path/to/Task#Demo1 \
  --schemes detail \
  --print-json
```

## SemanticFirstQualityChecker

Runs final quality checks across the semantic-first pipeline. It validates upstream result status, selected-scheme timeline coverage, complete exported scheme packages, per-segment retake fields, VLM completion, and final export counts. When 15 exported only one or two schemes, 16 reads the 15 result/coverage metadata and validates only those exported schemes instead of requiring all three scheme folders.

Example:

```bash
python3 OpenCrew/ToolLibrary/Analysis/16_semantic_first_quality_checker.py \
  --workspace /path/to/Task#Demo1 \
  --print-json
```

Outputs:

- `reports/quality_check.json`
- `meta/16_semantic_first_quality_checker_result.json`

## DetailSchemeRecomposer

Recomposes an existing `detail` scheme into a new `balanced` or `summary` scheme from text only. Use this when the detail segmentation is acceptable, but the user wants a different higher-level grouping without rerunning ASR, VLM keyframe analysis, semantic segmentation, or detail segment descriptions. The tool reads existing detail segments and detail retake JSON, asks the Task-bound Run Model for a grouping plan, rewrites the target scheme JSON, target SRT files, and target retake JSON files, then exports only the target scheme package. `balanced` writes `schemes/scheme_2`; `summary` writes `schemes/scheme_3`.

Example:

```bash
python3 OpenCrew/ToolLibrary/Analysis/17_detail_scheme_recomposer.py \
  --workspace /path/to/Task#Demo \
  --task-id 19 \
  --target balanced \
  --instruction "按用户指定的五段业务逻辑合并 detail 分镜" \
  --overwrite \
  --fresh-session \
  --print-json
```

Outputs:

- `meta/scheme_balanced_segments.json` or `meta/scheme_summary_segments.json`
- `meta/scheme_balanced_segment_descriptions.json` or `meta/scheme_summary_segment_descriptions.json`
- `meta/segment_descriptions/scheme_balanced/segment_*.json` or `meta/segment_descriptions/scheme_summary/segment_*.json`
- `transcripts/scheme_balanced_subtitles/segment_*.srt` or `transcripts/scheme_summary_subtitles/segment_*.srt`
- `schemes/scheme_2/segment_*.mp4|srt|json` for `--target balanced`
- `schemes/scheme_3/segment_*.mp4|srt|json` for `--target summary`
- `storyboards/scheme_balanced_storyboard.md` or `storyboards/scheme_summary_storyboard.md`
- `meta/17_detail_scheme_recomposer_result.json`
