# Rebuild V1 Pitfalls

## DB Task Context Must Own Workspace Resolution

Symptom: OC-Rebuild Task #1 showed `0 Shots` in the UI even after Phase 1 tools completed.

Root cause: tools wrote outputs under a manually supplied subdirectory such as `workspace/rebuild/`, while the UI and later Rebuild APIs read `rebuild_shot_plan.json` from the OC-Rebuild session workspace root. The run also relied on hand-passed Analysis `meta/` and `schemes/` paths instead of resolving them from the task's bound Analysis Task.

Correct rule: `--task-id` is the source of truth. A Rebuild V1 tool must resolve:

- OC-Rebuild Task -> Rebuild Session -> Rebuild workspace.
- OC-Rebuild Task -> Analysis Task -> Analysis Session -> Analysis workspace.
- `source_package_path` from `oc_rebuild_tasks.source_package_path`.

Do not infer Analysis inputs from the Rebuild workspace. A Rebuild workspace may not contain `meta/` or `schemes/`; those belong to the bound Analysis workspace.

## UI Should Not Raw-Read Shot Plan by Guessing Paths

Symptom: generated shot plan existed on disk but UI still rendered an empty Shot Plan panel.

Root cause: frontend raw-read `rebuild_shot_plan.json` directly from the session workspace and bypassed task validation.

Correct rule: the frontend should request a task-aware backend endpoint such as `/api/ocrebuild/tasks/{task_id}/shot-plan`. The backend must load the file from the task's DB-bound Rebuild workspace and validate `plan.task.task_id` / `plan.task.session_id` against the DB row.

## Phase 1 Output Placement

For a task with `source_package_path = rebuild/source_package.json`:

- `01_Rebuild_SourcePackageLoad` writes `source_package.json`, `rebuild_input_check.json`, and `rebuild_intent.json` under `workspace/rebuild/`.
- `02_Rebuild_ShotPlanBuilder` reads `workspace/rebuild/source_package.json` and `workspace/rebuild/rebuild_intent.json`.
- `02_Rebuild_ShotPlanBuilder` writes `workspace/rebuild_shot_plan.json`, because current Rebuild editing APIs operate on the root shot plan.

If this contract changes, update both backend resolver helpers and frontend loading logic in the same change.

## Incident: Task #1 Empty, Task #3 Has Data

Date: 2026-05-12.

Observed behavior:

- `/ocrebuild/tasks/1` displayed `Shot Plan 0 Shots` even though Phase 1 tools reported success.
- `/ocrebuild/tasks/3` displayed existing shots because its `rebuild_shot_plan.json` was already in the session workspace root.
- Task #1 DB state was valid: Rebuild Task #1 -> Session #51, source package path `rebuild/source_package.json`, Analysis Task #24 -> Session #56.

What went wrong:

- The tools were run with manually supplied filesystem paths.
- `01_Rebuild_SourcePackageLoad` initially looked for Analysis `meta/` and `schemes/` under Session #51, which is a Rebuild workspace and can be empty.
- After manually passing Analysis Session #56 paths, outputs were written under `Session #51/workspace/rebuild/`.
- The frontend was still raw-reading `Session #51/workspace/rebuild_shot_plan.json`; therefore it showed zero shots.

Fix applied:

- `01_Rebuild_SourcePackageLoad.py` now treats `--task-id` as the source of truth and resolves both the Rebuild workspace and bound Analysis workspace from the database.
- `02_Rebuild_ShotPlanBuilder.py` now reads `source_package_path` from the DB-bound Rebuild workspace and writes the root `rebuild_shot_plan.json` used by current editing APIs.
- Backend added task-aware `GET /api/ocrebuild/tasks/{task_id}/shot-plan`, with `task_id` and `session_id` validation.
- Frontend `loadShotPlan()` now calls the task-aware endpoint instead of raw-reading a guessed session file.

Validation commands used:

```bash
python3 OpenCrew/ToolLibrary/Rebuild_V1/01_Rebuild_SourcePackageLoad.py --task-id 1 --print-json
python3 OpenCrew/ToolLibrary/Rebuild_V1/02_Rebuild_ShotPlanBuilder.py --task-id 1 --print-json
python3 OpenCrew/ToolLibrary/Rebuild_V1/03_04_ShotPlan_PreDeleteReadinessCheck.py --workspace /Users/duheng/.opencrew/sessions/51/workspace --task-id 1 --session-id 51 --print-json
```

Expected API validation:

```text
GET /api/ocrebuild/tasks/1/shot-plan
-> task.task_id = 1
-> task.session_id = 51
-> task.analysis_task_id = 24
-> task.analysis_session_id = 56
-> source.source_package_path = rebuild/source_package.json
-> shots.length = 24
```

Regression guard:

- Never run Rebuild V1 Phase 1 by guessing `--workspace`, `--meta-dir`, `--schemes-dir`, or `--rebuild-dir` when a Rebuild Task exists.
- Prefer `--task-id`; only override paths for isolated debugging.
- If a UI task shows zero shots after a successful tool run, first compare `plan.task.task_id/session_id` with the DB row and verify whether the UI/API is reading the same path the tool wrote.

## First/Last Marker Must Update Both Scene Marks and Keyframes

Date: 2026-05-12.

Symptom: `04_01_ShotPlan_FirstLastFrameMark` reported success and wrote `scene_marks[].keyframes.first/last`, but the UI thumbnails did not show the `首` / `尾` badges.

Root cause: the UI thumbnail strip renders badges from `reference.keyframes[].scene_mark.role`, while the first V1 marker only updated `reference.scene_marks[]`. The shot plan therefore had valid scene mark data, but the keyframe badge annotations were missing.

Correct rule: any tool that creates, replaces, canonicalizes, or edits scene boundaries must keep these two representations in sync:

- `reference.scene_marks[]` is the canonical scene boundary data.
- `reference.keyframes[].scene_mark` is the UI badge/click annotation derived from the canonical scene marks.

Fix applied:

- `04_01_ShotPlan_FirstLastFrameMark.py` now syncs keyframe annotations after marking.
- `04_01_Shot_FirstLastFrameMark.py` now syncs keyframe annotations after marking.

Regression guard:

- After `04_01`, inspect a target shot and verify each boundary keyframe has `scene_mark.role = first`, `last`, or `single`.
- If the UI shows no badges but `scene_marks[].keyframes` exists, check `reference.keyframes[].scene_mark` first.

## First/Last Grouping Must Preserve PySceneDetect Boundary Pairs

Date: 2026-05-12.

Symptom: `shot_001` showed only one first/last pair even though the remaining keyframes represented two visual scene groups. The first marker was placed on the first kept frame and the last marker on the final kept frame, leaving the middle boundary unmarked.

Root cause: the V1 split initially simplified the original Rebuild logic. It used the first remaining keyframe and the last remaining keyframe as one fallback scene when a shot already had one auto-created scene mark. It omitted the original PySceneDetect grouping rules.

Correct rule: `04_01` must infer multiple scene marks from PySceneDetect filename patterns before falling back to one scene:

- `_middle_N` followed by `_end_near_N+1` is one scene group.
- `_start_N` followed by `_end_near_N+2` is one scene group.
- `_start_N` followed by `_middle_N+1` is one scene group when no matching end frame exists.

Example from `shot_001`:

```text
middle_0002 -> end_near_0003 = shot_001_scene_001
start_0004  -> end_near_0006 = shot_001_scene_002
```

Fix applied:

- Restored local PySceneDetect grouping in `04_01_ShotPlan_FirstLastFrameMark.py`.
- Restored local PySceneDetect grouping in `04_01_Shot_FirstLastFrameMark.py`.
- Preserved V1 neutral state fields under `mark_status`; do not reintroduce `plan_a` fields in Phase 1 / Phase 2 tools.
- Do not replace already confirmed scene marks with `mark_status.first_last_confirmed = true`.

Regression guard:

- `04_01` should split unconfirmed auto fallback scenes when inferred groups are more specific.
- `04_01` must not collapse all remaining keyframes into one first/last scene unless no PySceneDetect grouping can be inferred.
- `04_01` must not overwrite user-confirmed first/last boundaries.

## TTS Reference Audio Belongs to the Bound Analysis Session

Date: 2026-05-12.

Observed during Task #5 / Session #58 Phase 1.

Symptom: `03_01_ShotPlan_TTSReferenceAudioExtract` completed with warning `no source media path found in source_package.json`, then `03_02_ShotPlan_TTSVoiceRecommend` wrote default `Cherry` recommendations for every shot.

Root cause: the reference WAV is produced by Analysis and lives under the bound Analysis Session workspace, not under the Rebuild Session workspace. Task #5 was bound to Analysis Task #23 / Session #54, where the valid reference audio was:

```text
/Users/duheng/.opencrew/sessions/54/workspace/audio/reference_audio.wav
```

The V1 `03_01` tool only scanned a few top-level media fields in `source_package.json` and did not resolve `source.analysis_workspace`. `03_02` also treated the presence of a manifest as enough and fell back to default voice selection instead of matching against the reference audio.

Correct rule:

- `01_Rebuild_SourcePackageLoad` must preserve the bound Analysis workspace in `source.analysis_workspace`.
- `03_01_ShotPlan_TTSReferenceAudioExtract` must look for reference audio under the bound Analysis workspace, preferring `audio/reference_audio.wav`, then `outbox/reference_audio.wav`, `audio/asr_enhanced_audio.wav`, and `audio/original_audio.wav`.
- `03_02_ShotPlan_TTSVoiceRecommend` must use the reference audio manifest when present and perform voice matching against available TTS preview WAVs instead of blindly using `Cherry`.

Fix applied:

- `03_01_ShotPlan_TTSReferenceAudioExtract.py` now writes `tts/tts_reference_audio_manifest.json` with `reference_audio.path`, `source`, `format`, and `size_bytes` from the bound Analysis workspace.
- `03_02_ShotPlan_TTSVoiceRecommend.py` now uses the shared voice matching feature extraction logic and local `OpenCrew/ModelConfig/tts_voice_previews` preview WAVs to rank Qwen voices.
- For Task #5, the corrected top recommendations were `Elias`, `Katerina`, and `Serena`; `03_03` then wrote `Elias` into all 19 shots.

Validation commands used:

```bash
python3 OpenCrew/ToolLibrary/Rebuild_V1/03_01_ShotPlan_TTSReferenceAudioExtract.py --workspace /Users/duheng/.opencrew/sessions/58/workspace --task-id 5 --session-id 58 --print-json
python3 OpenCrew/ToolLibrary/Rebuild_V1/03_02_ShotPlan_TTSVoiceRecommend.py --workspace /Users/duheng/.opencrew/sessions/58/workspace --task-id 5 --session-id 58 --print-json
python3 OpenCrew/ToolLibrary/Rebuild_V1/03_03_ShotPlan_TTSVoiceSelectionWrite.py --workspace /Users/duheng/.opencrew/sessions/58/workspace --task-id 5 --session-id 58 --print-json
```

Regression guard:

- If `03_01` says no source/reference audio, inspect `source_package.json.source.analysis_workspace` before assuming the source data is missing.
- Check for `audio/reference_audio.wav` in the bound Analysis Session, not only in the Rebuild Session.
- If `03_02` outputs only default `Cherry`, inspect `tts/tts_voice_recommendations.json.match_result`; it should contain `reference_audio`, `reference_profile`, and ranked `top` candidates.
- After rerunning `03_02`, rerun `03_03` so `rebuild_shot_plan.json` receives the corrected `tts_selection`.

## Phase Boundary: Combine Keyframe Deletion and First/Last Confirmation

Date: 2026-05-12.

Updated operating rule: for both good SRT and bad SRT runs, Phase 1 should end only after the user has completed the single manual boundary pass. That pass includes deleting unwanted keyframes and confirming first/last frames together.

Previous issue: the workflow stopped once for keyframe deletion, then stopped again after `04_01` for first/last confirmation. This created an unnecessary second manual intervention.

Correct rule:

- Phase 1 automatic preparation runs through `04_01_ShotPlan_FirstLastFrameMark`.
- The user then performs one manual UI pass: delete images, adjust scene boundaries, and confirm first/last frames.
- Phase 2 begins after that confirmation and should be automatic unless a readiness gate blocks.

Regression guard:

- Do not treat `04_01` as an automatic Phase 2 stop point.
- Do not enter `05_01` until confirmed first/last state exists.
- `04_02` should be interpreted as confirmation-state finalization, not a separate manual review step.

## Plan A Shot-First Phase 3 Includes Shot Preflight

Date: 2026-05-12.

Updated operating rule: Plan A Phase 3 is shot-first and includes shot-local confirmation/prompt prep at the start of each shot.

Correct per-shot order:

```text
04_02_Shot_FirstLastFrameConfirm
05_01_Shot_ScenePromptRefresh
07_01_Shot_PlanA_TTSPromptBuild
07_02_Shot_PlanA_TTSGenerateAndLock
07_03_Shot_PlanA_TTSTimelineValidate
06_01_Shot_PlanA_SceneImageRebuild
06_02_Shot_PlanA_SceneImageSelect
08_01_Shot_PlanA_ImageSequenceClipPlan
08_02_Shot_PlanA_HyperframeSubtitleAlign
08_03_Shot_PlanA_ImageSequenceCompose
```

This means a full-shot-plan Phase 2 batch is optional for Plan A. If it has not already run, the Agent must run `04_02_Shot_*` and `05_01_Shot_*` for the current shot before any media generation.

Regression guard:

- Do not start `07_01` if the shot's scene SRT is empty.
- Do not start `06_01` if the shot's first/last frames are not confirmed.
- Do not move to the next shot until `Assets/<variant>/<shot>/plan_a.mp4` exists and probes successfully.

## Source Package Workspace May Not Contain Readable Keyframes

Date: 2026-05-12.

Observed during Task #1 `shot_001` Plan A run.

Symptom: `05_01_Shot_ScenePromptRefresh` passed dependency checks after supplying `--source-package rebuild/source_package.json`, then failed with:

```text
No readable scene mark images for shot_001: ['keyframes/...jpg', ...]
```

Root cause: `rebuild/source_package.json` had `workspace = /Users/duheng/.opencrew/sessions/51/workspace`, but the keyframes referenced by `rebuild_shot_plan.json` existed in the bound Analysis workspace `/Users/duheng/.opencrew/sessions/56/workspace`. The prompt refresh tool resolved image paths against the Rebuild workspace and could not read them.

Fix used for the reference run:

- Copy the required keyframes from Analysis workspace into the Rebuild workspace under the same relative paths before running `05_01`.

Better long-term fix:

- `01_Rebuild_SourcePackageLoad` or the shot plan builder should record the bound Analysis workspace explicitly in a field consumed by `source_workspace_from_package`.
- Prompt/image tools should resolve keyframes against both Rebuild workspace and bound Analysis workspace.

Regression guard:

- Before running `05_01`, verify each `scene_marks[].keyframes.first/last` path is readable from the Rebuild workspace or source workspace resolver.
- If the file is missing in Rebuild workspace, resolve it from the Analysis workspace and either copy it or fix the source package workspace fields.

## Refresh TTS Timeline After Scene Images Exist

Date: 2026-05-12.

Observed during Task #1 `shot_001` Plan A run.

Symptom: `08_02_Shot_PlanA_HyperframeSubtitleAlign` returned `empty_hyperframe_alignment` after TTS and scene images had both completed.

Root cause: `07_02_Shot_PlanA_TTSGenerateAndLock` builds `shot_tts_timeline.locked.json` from currently available scene image items. In the first run, TTS was generated before `06_01` / `06_02`, so the timeline existed but had no `image_pages`. `08_02` therefore had no pages to align.

Fix used for the reference run:

```text
07_02_Shot_PlanA_TTSGenerateAndLock --shot-id shot_001
07_03_Shot_PlanA_TTSTimelineValidate --shot-id shot_001
08_02_Shot_PlanA_HyperframeSubtitleAlign --shot-id shot_001
08_03_Shot_PlanA_ImageSequenceCompose --shot-id shot_001
```

When `locked.wav` already exists, rerunning `07_02` without `--force-tts-refresh` reuses the locked audio and refreshes the timeline with the now-existing scene images.

Regression guard:

- Before `08_02`, inspect `tts/shot_tts_timeline.locked.json` and ensure `image_pages` is non-empty.
- If `image_pages` is empty but scene images exist, rerun `07_02` and `07_03` before alignment.

## Use Bundled ffprobe When PATH Lacks ffprobe

Date: 2026-05-12.

Observed during Task #1 `shot_001` verification.

Symptom: shell verification with `ffprobe` failed because `ffprobe` was not in `PATH`, even though video composition succeeded.

Correct verification command:

```bash
OpenCrew/ToolLibrary/vendor/static_ffmpeg/darwin_arm64/ffprobe \
  -v error \
  -show_entries format=duration,size \
  -of json \
  /path/to/Assets/variant_001/shot_001/plan_a.mp4
```

Regression guard:

- Prefer the bundled static ffmpeg/ffprobe path for verification scripts.
- Do not assume `ffmpeg` or `ffprobe` exists in the user's shell `PATH`.

## Plan C Must Run Through Rebuild_V1 Standalone Tools

Date: 2026-05-12.

Observed during Task #1 `shot_001` Plan C run.

Symptom: Plan C media generated successfully, but the first run wrote legacy outputs under a top-level `plan_c/` directory and used `shot_auto_planc.mp4` as the final shot filename.

Root cause: the run invoked the legacy grouped tool `OpenCrew/ToolLibrary/Rebuild/plan_a_auto_v0_tools.py`, whose Plan C implementation still used legacy helpers such as `workspace/plan_c/shots/<shot_id>/...`, `shot_auto_planc.mp4`, and `renders/auto_planc.mp4`.

Correct rule: when working in Rebuild V1, Plan C must use the V1 standalone tools only:

```text
11_01_Shot_PlanC_ReadinessCheck
11_02_Shot_PlanC_PerSceneR2VGenerate
11_03_Shot_PlanC_SRTRetimeCompose
11_04_Shot_PlanC_FinalCompose
```

Canonical V1 output layout:

```text
Assets/<variant_id>/<shot_id>/plan_c.mp4
Assets/<variant_id>/<shot_id>/plan_c.srt
Assets/<variant_id>/<shot_id>/plan_c_alignment.json
Assets/<variant_id>/<shot_id>/<scene_mark_id>/plan_c_r2v.mp4
Assets/<variant_id>/<shot_id>/<scene_mark_id>/plan_c_retimed.mp4
Assets/<variant_id>/<shot_id>/<scene_mark_id>/plan_c_video_plan.json
Assets/<variant_id>/<shot_id>/<scene_mark_id>/plan_c_retime.json
Assets/<variant_id>/<shot_id>/reports/plan_c_11_01_readiness_check.json
Assets/<variant_id>/<shot_id>/reports/plan_c_11_02_per_scene_r2v_generate.json
Assets/<variant_id>/<shot_id>/reports/plan_c_11_03_srt_retime_compose.json
Assets/<variant_id>/<shot_id>/reports/plan_c_11_04_final_compose.json
```

Regression guard:

- Do not run `OpenCrew/ToolLibrary/Rebuild/plan_a_auto_v0_tools.py` for Rebuild V1 Plan C.
- V1 Plan C code and registry must not contain `plan_c/shots`, `workspace / "plan_c"`, `shot_auto_planc`, `auto_planc`, or `missing_shot_auto_planc`.
- After a V1 Plan C run, `workspace/plan_c/**` should not exist for the run's Plan C outputs.
- The final shot video must be `Assets/<variant_id>/<shot_id>/plan_c.mp4` and must probe successfully.

Validated V1 reference run:

```text
Task #1 shot_001 plan_c.mp4: 3.500000s, 360140 bytes
Task #1 shot_002 plan_c.mp4: 3.166667s, 582555 bytes
```

Validation commands used:

```bash
python3 OpenCrew/ToolLibrary/Rebuild_V1/11_01_Shot_PlanC_ReadinessCheck.py --workspace /Users/duheng/.opencrew/sessions/51/workspace --task-id 1 --session-id 51 --shot-id shot_001 --print-json
python3 OpenCrew/ToolLibrary/Rebuild_V1/11_02_Shot_PlanC_PerSceneR2VGenerate.py --workspace /Users/duheng/.opencrew/sessions/51/workspace --task-id 1 --session-id 51 --shot-id shot_001 --plan-c-frame-seconds 1.0 --force --print-json
python3 OpenCrew/ToolLibrary/Rebuild_V1/11_03_Shot_PlanC_SRTRetimeCompose.py --workspace /Users/duheng/.opencrew/sessions/51/workspace --task-id 1 --session-id 51 --shot-id shot_001 --plan-c-frame-seconds 1.0 --print-json
python3 OpenCrew/ToolLibrary/Rebuild_V1/11_04_Shot_PlanC_FinalCompose.py --workspace /Users/duheng/.opencrew/sessions/51/workspace --task-id 1 --session-id 51 --shot-id shot_001 --print-json
```

## Plan C Requires Shared TTS and Scene Image Assets

Date: 2026-05-12.

Symptom: Plan C readiness can block before R2V generation even when `rebuild_shot_plan.json` exists.

Root cause: Plan C does not create shared TTS or scene image assets itself. It consumes the same neutral assets used by Plan A: locked TTS, shot SRT, shot TTS timeline, scene `first.png`, and `asset_manifest.json`.

Correct rule: before running `11_02`, `11_01` must pass with no blockers for the target shot.

Required shared assets:

```text
Assets/<variant_id>/<shot_id>/tts/locked.wav
Assets/<variant_id>/<shot_id>/tts/shot.srt
Assets/<variant_id>/<shot_id>/tts/shot_tts_timeline.locked.json
Assets/<variant_id>/<shot_id>/<scene_mark_id>/first.png
Assets/<variant_id>/<shot_id>/<scene_mark_id>/asset_manifest.json
```

Regression guard:

- If `11_01` reports missing TTS or timeline, run `07_01`, `07_02`, and `07_03` for the shot first.
- If `11_01` reports missing scene images or manifests, run `06_01` and `06_02` for the shot first.
- If `shot_tts_timeline.locked.json` has empty image pages, rerun `07_02` without forcing TTS regeneration after scene images exist, then rerun `07_03`.

## Plan C Retime Warnings Are Not Always Blocking

Date: 2026-05-12.

Observed during Task #1 `shot_001` and `shot_002` V1 Plan C validation.

Symptom: `11_03_Shot_PlanC_SRTRetimeCompose` completed with warnings such as `retime_ratio_outside_safe_range`.

Root cause: Plan C generated fixed 1.0s R2V scene slices, then retimed them to locked TTS scene durations. For Task #1, the source scene slices were shorter than the locked TTS scene durations, producing speed ratios below the conservative range.

Correct rule: retime warnings should be recorded in `plan_c_alignment.json` and scene `plan_c_retime.json`, but they are not blockers when `blocking_errors` is empty and `plan_c.mp4` probes successfully.

Regression guard:

- Treat `blocking_errors` as the hard gate.
- Treat `retime_ratio_outside_safe_range` as a quality warning requiring visual review, not an automatic failure.
- If the visual quality is poor, rerun `11_02` with a different `--plan-c-frame-seconds` or provider/model settings, then rerun `11_03` and `11_04`.

## UI Must Show Both Plan A and Plan C Final Shot Videos

Date: 2026-05-12.

Symptom: the Shot R2V dialog showed only one final video card even when both `plan_a.mp4` and `plan_c.mp4` existed.

Root cause: frontend helper `shotPlanCFinalOutput()` pointed at `plan_a.mp4` and the dialog rendered a single final video card.

Correct rule: the UI should probe both shot-local final videos and render whichever are available:

```text
Assets/<variant_id>/<shot_id>/plan_a.mp4
Assets/<variant_id>/<shot_id>/plan_c.mp4
```

Fix applied:

- `OCRebuildModule.jsx` now builds final video candidates with `shotPlanFinalVideoOutputs()`.
- The Shot R2V dialog renders one final video card per available Plan A / Plan C output.
- Missing or not-yet-generated videos are hidden after the video element reports a load error.

Regression guard:

- When both files exist for a shot, the Shot R2V dialog must show both final video cards.
- Do not hard-code Plan C final output to `plan_a.mp4`.
- Do not assume only one final shot video exists.

## Task #1 shot_001 Reference Outcome

Date: 2026-05-12.

Successful output:

```text
/Users/duheng/.opencrew/sessions/51/workspace/Assets/variant_001/shot_001/plan_a.mp4
```

Validated durations:

```text
plan_a.mp4: 3.520000s
tts/locked.wav: 3.520000s
```

Generated core assets:

```text
Assets/variant_001/shot_001/tts/locked.wav
Assets/variant_001/shot_001/tts/shot.srt
Assets/variant_001/shot_001/tts/shot_tts_timeline.locked.json
Assets/variant_001/shot_001/shot_001_scene_001/first.png
Assets/variant_001/shot_001/shot_001_scene_001/asset_manifest.json
Assets/variant_001/shot_001/shot_001_scene_001/plan_a_clip_plan.json
Assets/variant_001/shot_001/shot_001_scene_002/first.png
Assets/variant_001/shot_001/shot_001_scene_002/asset_manifest.json
Assets/variant_001/shot_001/shot_001_scene_002/plan_a_clip_plan.json
Assets/variant_001/shot_001/plan_a_alignment.json
Assets/variant_001/shot_001/plan_a.srt
Assets/variant_001/shot_001/plan_a.mp4
```
