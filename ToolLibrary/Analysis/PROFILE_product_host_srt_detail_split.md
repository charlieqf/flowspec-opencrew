# Product Host SRT Detail Split Profile

Use this profile for product-founder, expert, or host-style short videos where a person explains product value, usage, trust proof, and call-to-action through spoken content.

## Goal

- Produce detail-only segmentation.
- Use ASR/SRT short sentences as the minimum semantic unit.
- Use OCR subtitle evidence, visual keyframes, and scene boundaries to calibrate or snap boundaries without breaking spoken sentences.
- Output cards that represent complete claims, actions, selling points, proof steps, or natural transitions.

## When To Use

- A single speaker or product host explains a product.
- Spoken content is the primary structure.
- On-screen captions mirror or partially mirror speech.
- The user wants fine-grained cards for review, retake, rebuild, or product-message analysis.
- The user does not need balanced or summary schemes unless explicitly requested.

## Recommended Pipeline

| Order | Tool | Goal | Reason |
|---:|---|---|---|
| 0 | `check_media_dependencies.py` | Check `ffmpeg`/`ffprobe` and media dependencies. | Avoid long workflow failures caused by missing media dependencies. |
| 1 | `01_video_metadata_extractor.py` | Extract video metadata and generate/confirm `source_video.mp4` in the workspace. | Virtual split playback depends on workspace-local `source_video.mp4` in the manifest. |
| 2 | `02_audio_asr_pipeline.py` | Generate ASR, sentence-level timeline, and ASR quality report. | Good ASR audio should be the main segmentation basis. |
| 3 | `04_pyscenedetect_runner.py` | Detect visual cuts and scene ranges. | Use as visual evidence, not as a hard transition segmentation rule. |
| 4 | `05_visual_evidence_extractor.py` | Extract keyframes, visual boundary candidates, and separator candidates. | Provide evidence for OCR, visual boundary support, and later segment descriptions. |
| 5 | `05_1_visual_ocr_timeline_builder.py` | Recognize on-screen text/subtitles and build a visual text timeline. | Use OCR to support semantic understanding and subtitle calibration. |
| 6 | `05_2_subtitle_bidirectional_calibrator.py` | Bidirectionally calibrate ASR and OCR subtitles. | High-quality ASR can correct OCR noise; stable OCR can reveal ASR gaps or timing drift. |
| 7 | `03_semantic_llm_structure_builder.py` | Generate semantic detail candidates from ASR, OCR, and visual evidence. | Core split step; segment by semantic completeness rather than raw transition or silence. |
| 8 | `08_boundary_aligner.py` | Align semantic boundaries to nearby strong visual evidence. | Improve boundary timing without adding new structural boundaries. |
| 9 | `09_visual_boundary_promoter.py` | Promote strong visual boundaries into candidate boundaries. | Supplement obvious visual structure that semantic segmentation may miss. |
| 10 | `10_evidence_collector.py` | Collect ASR, OCR, visual, and boundary evidence. | Build an auditable evidence index for review. |
| 11 | `13_fine_timeline_builder.py` | Generate the final detail timeline. | Consume `03`/`08`/`09`/`10` outputs and produce a no-gap, no-overlap detail split. |
| 12 | `14_segment_descriptor_subtitle_builder.py` | Generate per-segment subtitles and segment JSON descriptions. | Provide each virtual segment's `.srt` and `.json`; use `--description-mode vlm` by default. |
| 13 | `15_scheme_export_validator.py` | Export detail to `schemes/scheme_1` in virtual mode. | Do not cut physical mp4 files; write manifest, SRT, and JSON only. |
| 14 | `16_semantic_first_quality_checker.py` | Run final QA. | Validate timeline coverage, manifest, virtual export, and segment file completeness. |

Run detail only unless the user explicitly asks for balanced or summary outputs.

Default skip decisions for host-style spoken videos:

- Skip `02_0_source_separation.py` when ASR audio quality is good.
- Skip `06_scene_transition_llm_judge.py` unless strict physical location transition judgement is explicitly required.
- Skip `07_silent_visual_segment_detector.py` unless silent visual-only intervals are important.
- Run `09_visual_boundary_promoter.py` by default when visual boundaries are required as candidate boundaries. It should supplement semantic segmentation, not replace ASR/OCR-led semantic splitting.
- Run `10_evidence_collector.py` by default to keep an auditable ASR/OCR/visual evidence index.
- Use `14_segment_descriptor_subtitle_builder.py --description-mode vlm` by default for production-quality retake descriptions.

## Core Commands

Use the actual workspace, task ID, and video path for the run.

```bash
python3 OpenCrew/ToolLibrary/Analysis/check_media_dependencies.py \
  --print-json

python3 OpenCrew/ToolLibrary/Analysis/01_video_metadata_extractor.py \
  --video /path/to/video.mp4 \
  --workspace /path/to/workspace \
  --print-json

python3 OpenCrew/ToolLibrary/Analysis/02_audio_asr_pipeline.py \
  --video /path/to/video.mp4 \
  --workspace /path/to/workspace \
  --print-json

python3 OpenCrew/ToolLibrary/Analysis/04_pyscenedetect_runner.py \
  --video /path/to/video.mp4 \
  --workspace /path/to/workspace \
  --print-json

python3 OpenCrew/ToolLibrary/Analysis/05_visual_evidence_extractor.py \
  --video /path/to/video.mp4 \
  --workspace /path/to/workspace \
  --source pyscenedetect \
  --print-json

python3 OpenCrew/ToolLibrary/Analysis/05_1_visual_ocr_timeline_builder.py \
  --workspace /path/to/workspace \
  --ocr-engine auto \
  --languages ch,en \
  --print-json

python3 OpenCrew/ToolLibrary/Analysis/05_2_subtitle_bidirectional_calibrator.py \
  --workspace /path/to/workspace \
  --print-json

python3 OpenCrew/ToolLibrary/Analysis/03_semantic_llm_structure_builder.py \
  --workspace /path/to/workspace \
  --task-id <task_id> \
  --timeout-seconds 900 \
  --print-json

python3 OpenCrew/ToolLibrary/Analysis/08_boundary_aligner.py \
  --workspace /path/to/workspace \
  --print-json

python3 OpenCrew/ToolLibrary/Analysis/09_visual_boundary_promoter.py \
  --workspace /path/to/workspace \
  --print-json

python3 OpenCrew/ToolLibrary/Analysis/10_evidence_collector.py \
  --workspace /path/to/workspace \
  --print-json

python3 OpenCrew/ToolLibrary/Analysis/13_fine_timeline_builder.py \
  --workspace /path/to/workspace \
  --schemes detail \
  --print-json

python3 OpenCrew/ToolLibrary/Analysis/14_segment_descriptor_subtitle_builder.py \
  --workspace /path/to/workspace \
  --task-id <task_id> \
  --schemes detail \
  --description-mode vlm \
  --resume \
  --timeout-seconds 900 \
  --print-json

python3 OpenCrew/ToolLibrary/Analysis/15_scheme_export_validator.py \
  --workspace /path/to/workspace \
  --schemes detail \
  --clip-mode virtual \
  --print-json

python3 OpenCrew/ToolLibrary/Analysis/16_semantic_first_quality_checker.py \
  --workspace /path/to/workspace \
  --print-json
```

## Segmentation Rules

- Minimum unit: one ASR/SRT short sentence from word-time timestamps.
- Never split a short sentence across two detail cards.
- Usually merge one or two consecutive short sentences per card.
- Use three short sentences only when the claim, proof, or transition is incomplete without them.
- Each detail card should express one complete action, product claim, proof step, emotional turn, comparison, or CTA step.
- Avoid one formula slot becoming one large segment.
- Avoid mechanical one-sentence-per-card output.
- Keep `formula_slot` as a label for product logic, not as a hard boundary.

## Evidence Priority

1. ASR provider word timestamps.
2. ASR sentence timeline in `meta/asr_sentence_timeline.json`.
3. OCR subtitle timeline and `05_2` calibrated alignment.
4. Visual keyframes and PySceneDetect scene boundaries.
5. Formula slots and final prompt structure.

Visual boundaries can snap a semantic boundary to a nearby stronger cut or keyframe boundary, but must not split a spoken sentence. `09_visual_boundary_promoter.py` can add strong visual candidate boundaries, but ASR/OCR semantic completeness remains the primary split rule.

## ASR/OCR Calibration

- `02` must write sentence-level timing derived from provider word timestamps.
- `05_2` must match OCR subtitle rows against ASR sentence rows, not against long ASR segments.
- Use high-confidence ASR text to correct OCR subtitle noise.
- Use stable OCR subtitle evidence to flag ASR gaps or timing drift.
- Keep review outputs for manual inspection when `needs_review` is non-zero.

## Step 03 Prompt Expectations

`03_semantic_llm_structure_builder.py` should ask the model to return detail candidates with:

- `start` and `end` from sentence-level timing.
- `source_sentence_ids`.
- `formula_slot`.
- `merge_reason`.
- a concise title.
- a complete semantic purpose.

The prompt should explicitly forbid:

- splitting one ASR/SRT sentence.
- merging a whole formula slot into one card.
- mechanical one-sentence-per-card segmentation.

## Step 13 And 15 Outputs

- `13` should generate `meta/scheme_detail_segments.json` from semantic detail candidates and aligned boundaries.
- `14` should generate per-segment JSON descriptions and subtitles for detail only, using `--description-mode vlm` by default.
- `15` should default to virtual export for this profile.
- Virtual export writes `schemes/scheme_1/manifest.json`, `segment_###.srt`, and `segment_###.json` only.
- Physical mp4 cutting is optional and should be requested explicitly with `--clip-mode encode` or `--clip-mode copy`.

## UI Requirements

- The card list must be manifest-driven.
- Virtual playback uses `source_video.mp4` plus manifest `start/end`.
- Physical playback uses `segment_###.mp4` when manifest `clip_status` is `exported` or `skipped_existing`.
- SRT and JSON preview should use `srt_path` and `retake_description_path` from manifest.
- The UI should show the real segment count from manifest, not from directory mp4 count.

## Quality Gates

- `meta/asr_quality.json` should be `good` or manually accepted.
- `meta/asr_sentence_timeline.json` should cover all spoken sentences with word-time-derived boundaries.
- `meta/subtitle_alignment_timeline.json` should have low `needs_review` count or manual resolution.
- `meta/semantic_segment_candidates.json` should include source sentence IDs and merge reasons.
- `meta/scheme_detail_segments.json` count should match expected detail granularity.
- `schemes/scheme_1/manifest.json` should have `clip_mode=virtual` by default and item count matching the UI.
- Raw `source_video.mp4` should return `200` and Range should return `206`.

## Common Failure Modes

- UI shows too many cards because stale mp4 files remain and the UI uses directory listing instead of manifest.
- All virtual cards show the same metadata because metadata is keyed by shared `source_video.mp4` instead of per-segment JSON path.
- Virtual video cannot play because `source_video.mp4` is a symlink to outside the workspace and the raw endpoint rejects it.
- Segments are too coarse because formula slots are treated as boundaries instead of labels.
- Segments are too fragmented because each sentence is emitted as one card without semantic merge reasoning.
- OCR subtitle text contains package, watermark, or background text and contaminates the spoken timeline.
