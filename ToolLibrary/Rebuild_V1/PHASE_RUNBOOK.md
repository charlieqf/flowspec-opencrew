# Rebuild V1 Phase Runbook

This file records the current operating boundary for Rebuild V1 phase execution. It applies to both good SRT and bad SRT runs.

## Current Phase Boundary

Phase 1 includes automatic preparation plus one manual boundary pass.

The manual boundary pass must combine:

- deleting unwanted keyframes/images
- adjusting scene boundaries
- confirming first/last frames

Phase 2 starts only after that combined manual pass is complete when running a full shot-plan readiness batch. Plan A and Plan C shot-first execution may instead run the confirmation and prompt prep tools as `Phase 3.0 Shot Preflight` for each shot.

## Phase 1A: Automatic Preparation

Run in this order:

```text
01_Rebuild_SourcePackageLoad
02_Rebuild_ShotPlanBuilder
03_01_ShotPlan_TTSReferenceAudioExtract
03_02_ShotPlan_TTSVoiceRecommend
03_03_ShotPlan_TTSVoiceSelectionWrite
03_04_ShotPlan_PreDeleteReadinessCheck
04_01_ShotPlan_FirstLastFrameMark
```

Purpose:

- load the source package
- build `rebuild_shot_plan.json`
- prepare TTS voice selection
- verify the plan is ready for manual keyframe editing
- auto-mark initial first/last scene boundaries from remaining keyframes

Important rule: `04_01` belongs to the Phase 1 manual-prep boundary even though its tool id begins with `04`. It prepares first/last candidates before the user confirms them.

## Phase 1B: Single Manual Boundary Pass

The user performs these actions in one UI session:

- delete bad or unnecessary keyframes/images
- adjust scene splits when the auto grouping is wrong
- verify the `首` / `尾` badges on each scene boundary
- save the final boundary state
- confirm first/last frames

The saved state should include:

- `reference.scene_marks[].keyframes.first`
- `reference.scene_marks[].keyframes.last`
- `reference.scene_marks[].mark_status.first_last_confirmed = true`
- matching `reference.keyframes[].scene_mark.role` annotations for UI badges

Do not create a second manual stop between image deletion and first/last confirmation.

## Phase 2: Optional ShotPlan Readiness and Prompt Prep

Run after the manual boundary pass when the operator wants to prepare the whole shot plan before generation:

```text
04_02_ShotPlan_FirstLastFrameConfirm
04_03_ShotPlan_FirstLastReadinessCheck
04_04_ShotPlan_SceneAssetCanonicalize
05_01_ShotPlan_ScenePromptRefresh
```

Tool meanings:

- `04_02_ShotPlan_FirstLastFrameConfirm` finalizes or fills confirmation metadata for already confirmed first/last boundaries.
- `04_03_ShotPlan_FirstLastReadinessCheck` is a read-only gate. It must block if any required scene is missing first/last paths or confirmation.
- `04_04_ShotPlan_SceneAssetCanonicalize` normalizes scene ids and asset paths before generation.
- `05_01_ShotPlan_ScenePromptRefresh` refreshes Scene SRT/prompt fields after confirmed boundaries exist.

## Phase 3.0: Shot Preflight

Plan A and Plan C primary execution mode is shot-first. For that mode, each shot starts Phase 3 by running:

```text
04_02_Shot_FirstLastFrameConfirm --shot-id <shot_id>
05_01_Shot_ScenePromptRefresh --shot-id <shot_id>
05_02_Shot_FinalPromptPackageBuild --shot-id <shot_id>
```

This embeds the earlier confirm/prompt-prep work into the per-shot generation chain, then freezes the final executable prompt package for the shot before any media generation starts. It avoids requiring a full-shot-plan Phase 2 batch before the first shot can render.

For StoryBoard-derived shots that do not have original OCR/video visual evidence, replace `05_01_Shot_ScenePromptRefresh` with:

```text
05_01_Shot_StoryboardReferencePromptRefresh --shot-id <shot_id> --reference-task-id <reference_task_id> --reference-session-id <reference_session_id>
```

This StoryBoard-specific 05_01 tool keeps the current StoryBoard dialogue, timing, keyframes, and scene ids, then matches back to the reference task by `source_shot_id` / `source_scene_mark_id` or dialogue similarity to merge reference structure into `scene_description` and prompt seed fields.

After this preflight succeeds, continue the same shot through shared TTS and scene image generation, then branch into either Plan A or Plan C shot video generation before moving to the next shot.

## Phase 3A: Plan A Shot-First Media Chain

For each target shot, run this sequence after Phase 3.0 preflight:

```text
07_01_Shot_PlanA_TTSPromptBuild --shot-id <shot_id>
07_02_Shot_PlanA_TTSGenerateAndLock --shot-id <shot_id>
07_03_Shot_PlanA_TTSTimelineValidate --shot-id <shot_id>
06_01_Shot_PlanA_SceneImageRebuild --shot-id <shot_id>
06_02_Shot_PlanA_SceneImageSelect --shot-id <shot_id>
08_01_Shot_PlanA_ImageSequenceClipPlan --shot-id <shot_id>
08_02_Shot_PlanA_HyperframeSubtitleAlign --shot-id <shot_id>
08_03_Shot_PlanA_ImageSequenceCompose --shot-id <shot_id>
```

The shot is complete for Plan A only after `Assets/<variant_id>/<shot_id>/plan_a.mp4` exists and probes successfully.

## Phase 3C: Plan C Shot-First Media Chain

Plan C reuses the same shared TTS, SRT, scene images, and scene asset manifests created for Plan A. The current V1 implementation is standalone under `OpenCrew/ToolLibrary/Rebuild_V1` and must not call the legacy grouped `OpenCrew/ToolLibrary/Rebuild/plan_a_auto_v0_tools.py`.

For each target shot, run this sequence after Phase 3.0 preflight and after the shared `07_*` / `06_*` assets exist:

```text
11_01_Shot_PlanC_ReadinessCheck --shot-id <shot_id>
11_02_Shot_PlanC_PerSceneR2VGenerate --shot-id <shot_id>
11_03_Shot_PlanC_SRTRetimeCompose --shot-id <shot_id>
11_04_Shot_PlanC_FinalCompose --shot-id <shot_id>
```

Canonical command shape:

```bash
python3 OpenCrew/ToolLibrary/Rebuild_V1/11_01_Shot_PlanC_ReadinessCheck.py --workspace <workspace> --task-id <task_id> --session-id <session_id> --shot-id <shot_id> --print-json
python3 OpenCrew/ToolLibrary/Rebuild_V1/11_02_Shot_PlanC_PerSceneR2VGenerate.py --workspace <workspace> --task-id <task_id> --session-id <session_id> --shot-id <shot_id> --plan-c-frame-seconds 1.0 --print-json
python3 OpenCrew/ToolLibrary/Rebuild_V1/11_03_Shot_PlanC_SRTRetimeCompose.py --workspace <workspace> --task-id <task_id> --session-id <session_id> --shot-id <shot_id> --plan-c-frame-seconds 1.0 --print-json
python3 OpenCrew/ToolLibrary/Rebuild_V1/11_04_Shot_PlanC_FinalCompose.py --workspace <workspace> --task-id <task_id> --session-id <session_id> --shot-id <shot_id> --print-json
```

Plan C writes beside Plan A under the same shot directory:

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

The shot is complete for Plan C only after `Assets/<variant_id>/<shot_id>/plan_c.mp4` exists and probes successfully.

Do not create or depend on a top-level `plan_c/` workspace directory. Do not emit legacy filenames such as `shot_auto_planc.mp4` or `renders/auto_planc.mp4` from V1 tools.

## Phase 3D: Plan D Product/Host Consistency Image Gen

Plan D is a prompt-first workflow that uses the confirmed source first/single keyframe plus host/product consistency references to generate replacement first-frame images. It does not use the Plan A scene image tools.

For each target shot, run:

```text
04_02_Shot_FirstLastFrameConfirm --shot-id <shot_id>
05_01_Shot_ScenePromptRefresh --shot-id <shot_id>
05_02_Shot_FinalPromptPackageBuild --shot-id <shot_id>
12_00_Shot_PlanD_ReplacementImagePromptBuild --shot-id <shot_id>
12_02_Shot_PlanD_CodexImageGenFirstFrameGenerate --shot-id <shot_id> --mode prepare
Codex built-in image_gen for each prepared scene job
12_02_Shot_PlanD_CodexImageGenFirstFrameGenerate --shot-id <shot_id> --scene-mark-id <scene_mark_id> --mode import --generated-image <generated_image_path>
```

If the shot is StoryBoard-derived, use `05_01_Shot_StoryboardReferencePromptRefresh` in place of `05_01_Shot_ScenePromptRefresh` before `05_02`. Do not use the original OCR/VLM refresh tool when the shot has no original video evidence to inspect.

Important rule: do not run `06_01_Shot_PlanA_SceneImageRebuild` or `06_02_Shot_PlanA_SceneImageSelect` as part of Plan D. Those tools belong to Plan A. In Plan D, `12_00` creates `tts_prompt`, `image_prompt`, and `video_prompt`; `12_02` prepares/imports Codex image_gen outputs as `Assets/<variant_id>/<shot_id>/<scene_mark_id>/first.png`.

See `PLAN_D_WORKFLOW.md` for the detailed command contract and guardrails.

## Good SRT / Bad SRT Rule

Good SRT and bad SRT flows share the same Phase 1 boundary:

- Phase 1 does not end at pre-delete readiness.
- Phase 1 does not end immediately after keyframe deletion.
- Phase 1 ends after keyframe deletion and first/last confirmation are both saved.

This reduces manual interventions from two pauses to one pause.

## Gates

Stop before Phase 2 if:

- `04_01` has not produced first/last candidates.
- The UI does not show correct `首` / `尾` badges.
- The user has not confirmed first/last boundaries.

Stop inside Phase 2 or Phase 3.0 if:

- `04_03` returns `blocked`.
- `05_01` reports missing task/session/model/OpenCode context.
- `05_01` cannot read the keyframe images referenced by `rebuild_shot_plan.json`.

Stop inside Plan C Phase 3 if:

- `11_01` returns `completed_with_blockers` or reports missing shared TTS/SRT/timeline/scene assets.
- `11_02` reports unsupported or missing active video provider config.
- `11_02` cannot read every referenced scene image.
- `11_03` cannot read the shot-local Plan C batch plan or generated batch video.
- `11_04` cannot read `plan_c_alignment.json`, `plan_c.srt`, `locked.wav`, or the retimed visual concat.

Do not proceed to media generation for a shot until that shot's Phase 3.0 preflight passes, unless a full-shot-plan Phase 2 batch has already passed for the same `rebuild_shot_plan.json` state.

Do not move to the next Plan C shot until that shot's `plan_c.mp4` exists and probes successfully, unless the operator explicitly accepts a partial run.
