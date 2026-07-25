# OC-Analysis Run Pitfalls

This runbook records issues found during Task #22 / Session #53 and should be checked before similar OC-Analysis runs.

## Environment And Database

- OC-Analysis runs depend on OpenCrew database state: task row, session row, attempt row, `opencode_session_id`, app settings, and model/provider settings.
- If `03_semantic_llm_structure_builder.py` fails with incomplete OpenCode connection or missing auth, inspect app settings and the selected session before rerunning semantic steps.
- Use the Task ID and Session ID together. In this run, Task #22 mapped to Session #53 and workspace `/Users/duheng/.opencrew/sessions/53/workspace`.
- Frontend may show latest attempt metadata while workspace files have been manually regenerated. When UI data looks stale, compare API responses, attempt IDs, and actual workspace files.

## Node And NPM

- Do not run frontend build from the repository root unless a root `package.json` exists.
- For this workspace, the frontend package is `OpenCrew/frontend`.
- Use a login shell so local Node/npm paths are available:

```bash
zsh -lc 'npm run build'
```

- Known local Node path in this run: `/Users/duheng/.local/node-v22.14.0-darwin-arm64/bin/node`.

## ffmpeg And ffprobe

- Tools resolve media binaries via `media_binaries.py`: env vars, project `.bin`, system `PATH`, then `imageio_ffmpeg` for `ffmpeg` only.
- `ffmpeg` availability does not imply `ffprobe` availability.
- `01_video_metadata_extractor.py` should not hard-fail only because `ffprobe` is missing. It should record `source_backends.ffprobe=false` and continue with OpenCV plus ffmpeg fallback when possible.
- Check dependencies before long runs:

```bash
python3 OpenCrew/ToolLibrary/Analysis/check_media_dependencies.py --print-json
```

- Install project-local media binaries when needed:

```bash
python3 OpenCrew/ToolLibrary/Analysis/install_media_binaries.py --overwrite --print-json
```

## ASR And OCR

- Product-host videos should be driven by spoken SRT/ASR semantics, not by raw scene cuts alone.
- Do not use long ASR segments to overwrite individual OCR subtitle rows.
- Build sentence-level ASR timeline from provider word timestamps: `words[].begin_time` and `words[].end_time`.
- OCR subtitle and ASR sentence timeline must be calibrated bidirectionally in `05_2`.
- OCR often includes packaging or background noise. Clean non-dialogue text such as brand fragments, wrapper copy, watermark fragments, and decorative text before semantic segmentation.
- In this run, `paddleocr` was unavailable, so OCR used `rapidocr`.

## Semantic Detail Splitting

- `formula_slot` is a label, not a hard coarse segment boundary.
- Detail segmentation must not become one long segment per formula slot.
- Detail segmentation must not become mechanical one-sentence-per-card output.
- The smallest unit is an ASR/SRT short sentence. Do not split one sentence into multiple detail cards.
- One detail card should normally contain one or two consecutive short sentences; use three only when needed for a complete selling point or reasoning step.
- `03` should output semantic detail candidates with source sentence IDs, slot labels, and merge reasons.
- `13` should build the detail timeline from `03` semantic segments plus ASR sentence timeline. Do not revert to raw scene-SRT rows as the main driver.

## Step 15 Export

- Old behavior cut one mp4 per segment. After reruns, stale `segment_018.mp4` through `segment_037.mp4` remained when segment count changed from 37 to 17.
- UI used directory mp4 listing, so stale clips caused the UI to still show 37 cards despite a 17-item manifest.
- Current default behavior should be virtual export:

```bash
python3 OpenCrew/ToolLibrary/Analysis/15_scheme_export_validator.py \
  --workspace /path/to/workspace \
  --schemes detail \
  --print-json
```

- Virtual export writes SRT, JSON, and manifest only. It does not cut mp4 files.
- If physical mp4 clips are required later, use `--clip-mode encode` or `--clip-mode copy`.
- The UI must treat `schemes/<scheme>/manifest.json` as the source of truth. Directory listing is only a fallback for legacy outputs.

## Task #24 / Session #56 Virtual Export QA

- `15_scheme_export_validator.py --clip-mode virtual` should write `manifest.json`, SRT, and JSON only. It should not require physical `segment_*.mp4` files.
- `16_semantic_first_quality_checker.py` must read `schemes/<scheme>/manifest.json` and branch validation by `clip_mode`.
- For `clip_mode=virtual`, QA should validate:
  - `manifest.items` count matches the expected segment count.
  - Each item has `clip_status=virtual`.
  - Each item uses `clip_path=source_video.mp4` and `source_video_path=source_video.mp4`.
  - `source_video.mp4` exists inside the workspace.
  - Per-segment SRT and JSON exist.
  - `segment_*.mp4` count may be `0` and must not fail QA.
- For `clip_mode=copy` or `clip_mode=encode`, QA should still require physical `segment_*.mp4` files.
- Do not run physical export only to satisfy QA when the user selected Virtual. That changes `manifest.clip_mode` and can cause UI playback/card behavior to diverge from the selected export mode.
- UI must trust `manifest.json` over directory listing. Stale `segment_*.mp4` files must not override `clip_mode=virtual`.

## Video Playback

- Two playback modes must be supported:
- Physical clip mode: each card plays `segment_###.mp4`.
- Virtual mode: each card plays `source_video.mp4` and seeks to manifest `start/end`.
- `01_video_metadata_extractor.py` must stage the task reference video into the session workspace as `source_video.mp4` when `--workspace` is provided. This is the stable source used by later virtual exports.
- Virtual cards sharing the same `source_video.mp4` must key metadata by each segment JSON path, not by video path.
- The raw video endpoint must support Range requests. Verify `206 Partial Content`:

```bash
curl -s -D - -o /dev/null -H 'Range: bytes=0-1023' \
  http://127.0.0.1:18080/api/session-tasks/<session_id>/raw/source_video.mp4
```

- If `source_video.mp4` is a symlink pointing outside the workspace, the raw endpoint may reject it as escaping workspace. Copy the video into the workspace or ensure the read-only raw endpoint explicitly supports safe workspace-contained symlink paths.
- If `15_scheme_export_validator.py --clip-mode virtual` runs before `source_video.mp4` exists, it falls back to the original absolute reference video path in `schemes/<scheme>/manifest.json`. The frontend can still display cards from the manifest, but video playback fails because the session raw endpoint cannot serve files outside the workspace. Fix by staging `source_video.mp4` first and rerunning step 15.

## Review Pages

- Temporary review pages are useful for manual validation of ASR, OCR, alignment, and semantic detail outputs.
- For local media review, use a Range-capable static server. Plain `python3 -m http.server` may not be enough for reliable video seeking.
- Review pages should support play-by-start/end, text inspection, JSON inspection, and correct/incorrect marking.

## Quick Debug Checklist

- Confirm workspace path and Task/Session IDs.
- Check `meta/asr_sentence_timeline.json` exists and has word-time-derived sentence rows.
- Check `meta/subtitle_alignment_timeline.json` exists after `05_2`.
- Check `meta/semantic_segment_candidates.json` count and merge reasons.
- Check `meta/scheme_detail_segments.json` count.
- Check `schemes/scheme_1/manifest.json` count, `clip_mode`, and `clip_status`.
- Check stale `schemes/scheme_1/segment_*.mp4` files if UI card count is wrong.
- Check raw source video endpoint returns `200` and Range returns `206`.
- Check `schemes/scheme_1/manifest.json` uses `source_video.mp4` for virtual `clip_path` / `source_video_path`, not an absolute path outside the workspace.
- Rebuild frontend from `OpenCrew/frontend`, not repo root.
