# Rebuild V1 Plan D Workflow

Plan D is the product/host consistency workflow for generating replacement first-frame images and then using those images for TTS-driven video generation.

This workflow is intentionally separate from Plan A image generation. Do not run `06_01_Shot_PlanA_SceneImageRebuild` or `06_02_Shot_PlanA_SceneImageSelect` for Plan D image prompt or first-frame generation.

## Required Inputs

- `rebuild_shot_plan.json`
- confirmed `reference.scene_marks[]`
- confirmed `reference.scene_marks[].keyframes.first` or `single`
- refreshed Scene SRT and visual prompt fields from `05_01`
- host consistency reference: `consistency_references/host/HOST.png`
- product consistency reference: `consistency_references/product/PRODUCT.png`

The source first/single keyframe remains the composition and pose target. The host/product references are identity anchors only.

## Prompt And Image Flow

Run per shot:

```text
04_02_Shot_FirstLastFrameConfirm
05_01_Shot_ScenePromptRefresh
05_02_Shot_FinalPromptPackageBuild
12_00_Shot_PlanD_ReplacementImagePromptBuild
12_02_Shot_PlanD_CodexImageGenFirstFrameGenerate --mode prepare
Codex built-in image_gen for each prepared scene job
12_02_Shot_PlanD_CodexImageGenFirstFrameGenerate --mode import
```

Tool meanings:

- `04_02` confirms existing first/last scene boundary metadata. It does not create replacement images.
- `05_01` refreshes Scene SRT, narration, and visual prompt fields from confirmed source frames.
- `05_02` writes the initial executable `final_prompt_package.json`.
- `12_00` rewrites `final_prompt_package.json` into Plan D `final_v1`, adding:
  - `tts_prompt`
  - `tts_speed_notes`
  - `references.host_image`
  - `references.product_image`
  - `scenes[].reference_image`
  - `scenes[].image_prompt`
  - `scenes[].video_prompt`
- `12_00` builds `scenes[].image_prompt` with the Session run model by default. It sends the source first/single frame, `HOST.png`, `PRODUCT.png`, `host_reference_manifest.json`, `product_reference_manifest.json`, and `prompt_references/提示词撰写指南_口播_人物产品一致性模型_GPT.MD` to the bound OpenCode run model, then stores the returned segmented image prompt.
- `12_00` still builds `tts_prompt` and `scenes[].video_prompt` locally from templates. The run-model authoring path is only for Plan D image prompts.
- `12_02 --mode prepare` creates per-scene Codex image_gen jobs and `codex_imagegen_prompt.txt` files.
- Codex built-in `image_gen` generates replacement first-frame bitmaps from the prepared prompt plus the three reference images:
  - source first/single keyframe
  - host consistency image
  - product consistency image
- `12_02 --mode import` copies the generated bitmap to `Assets/<variant_id>/<shot_id>/<scene_mark_id>/first.png`, writes `asset_manifest.json`, and syncs `rebuild_shot_plan.json`.

## Canonical Commands

```bash
python3 OpenCrew/ToolLibrary/Rebuild_V1/04_02_Shot_FirstLastFrameConfirm.py \
  --workspace <workspace> --task-id <task_id> --session-id <session_id> \
  --shot-id <shot_id> --source-package source_package.json --print-json

python3 OpenCrew/ToolLibrary/Rebuild_V1/05_01_Shot_ScenePromptRefresh.py \
  --workspace <workspace> --task-id <task_id> --session-id <session_id> \
  --shot-id <shot_id> --source-package source_package.json --print-json

python3 OpenCrew/ToolLibrary/Rebuild_V1/05_02_Shot_FinalPromptPackageBuild.py \
  --workspace <workspace> --task-id <task_id> --session-id <session_id> \
  --shot-id <shot_id> --variant-id <variant_id> --print-json

python3 OpenCrew/ToolLibrary/Rebuild_V1/12_00_Shot_PlanD_ReplacementImagePromptBuild.py \
  --workspace <workspace> --task-id <task_id> --session-id <session_id> \
  --shot-id <shot_id> --variant-id <variant_id> --print-json

python3 OpenCrew/ToolLibrary/Rebuild_V1/12_02_Shot_PlanD_CodexImageGenFirstFrameGenerate.py \
  --workspace <workspace> --task-id <task_id> --session-id <session_id> \
  --shot-id <shot_id> --variant-id <variant_id> --mode prepare --force --print-json

python3 OpenCrew/ToolLibrary/Rebuild_V1/12_02_Shot_PlanD_CodexImageGenFirstFrameGenerate.py \
  --workspace <workspace> --task-id <task_id> --session-id <session_id> \
  --shot-id <shot_id> --scene-mark-id <scene_mark_id> --variant-id <variant_id> \
  --mode import --generated-image <generated_image_path> --print-json
```

## Outputs

- `Assets/<variant_id>/<shot_id>/final_prompt_package.json`
- `Assets/<variant_id>/<shot_id>/codex_imagegen_jobs.json`
- `Assets/<variant_id>/<shot_id>/<scene_mark_id>/codex_imagegen_prompt.txt`
- `Assets/<variant_id>/<shot_id>/<scene_mark_id>/first.png`
- `Assets/<variant_id>/<shot_id>/<scene_mark_id>/asset_manifest.json`
- `Assets/<variant_id>/<shot_id>/reports/plan_d_12_00_replacement_image_prompt_build.json`
- `Assets/<variant_id>/<shot_id>/reports/plan_d_12_02_codex_imagegen_first_frame_jobs.json`

## Guardrails

- Do not run `06_01` or `06_02` for Plan D. Those are Plan A scene image tools.
- Do not treat generated `first.png` as a prerequisite for `12_00`; `12_00` uses the source first/single keyframe and consistency references to write prompts.
- Do not reuse stale `codex_imagegen_prompt.txt` after `HOST.png`, `PRODUCT.png`, or either consistency manifest changes. The file path may still point to `PRODUCT.png`, but the old prompt text may retain the previous product name, host clothing, or weak replacement priorities. Re-run `12_00 --force`, then `12_02 --mode prepare --force`.
- `final_prompt_package.json` stores both semantic `scenes[].image_prompt` and resolved `scenes[].reference_image_paths`. If the resolved paths look right but the prompt text still contains an old product/person, the stale layer is the run-model prompt text and `12_00 --force` must be rerun.
- Plan D image prompts must clearly state reference priority: `HOST_REFERENCE` wins for face, identity, hair, clothing, microphone, skin/hand style, and accessories; `PRODUCT_REFERENCE` wins for package identity; `TARGET_FRAME` only wins for framing, background, pose category, hand/product placement, lighting, perspective, and phone-video texture. Without this priority, image generation tends to keep the original source-frame host or clothing and only replace the product.
- Do not overwrite manually confirmed scene boundaries with `03_1 --mark-mode rebuild` unless the operator explicitly wants scene regrouping.
- If a shot has no `scene_marks`, create or repair scene marks first, then confirm them with `04_02`.
- If `05_01` cannot read keyframes, resolve them from the bound Analysis workspace and copy them into the Rebuild workspace under the same relative paths.

## Reference Run: Task 5 / Session 58 / Shot 003-004

Validated on 2026-05-21 for:

```text
workspace = /Users/duheng/.opencrew/sessions/58/workspace
task_id = 5
session_id = 58
variant_id = variant_001
shots = shot_003, shot_004
```

The executed Plan D chain was:

```text
04_02_Shot_FirstLastFrameConfirm
05_01_Shot_ScenePromptRefresh --force
05_02_Shot_FinalPromptPackageBuild --force
12_00_Shot_PlanD_ReplacementImagePromptBuild --force
12_02_Shot_PlanD_CodexImageGenFirstFrameGenerate --mode prepare --force
Codex built-in image_gen
12_02_Shot_PlanD_CodexImageGenFirstFrameGenerate --mode import
```

Generated outputs:

```text
Assets/variant_001/shot_003/final_prompt_package.json
Assets/variant_001/shot_003/codex_imagegen_jobs.json
Assets/variant_001/shot_003/shot_003_scene_001/codex_imagegen_prompt.txt
Assets/variant_001/shot_003/shot_003_scene_001/first.png
Assets/variant_001/shot_003/shot_003_scene_001/asset_manifest.json

Assets/variant_001/shot_004/final_prompt_package.json
Assets/variant_001/shot_004/codex_imagegen_jobs.json
Assets/variant_001/shot_004/shot_004_scene_001/codex_imagegen_prompt.txt
Assets/variant_001/shot_004/shot_004_scene_001/first.png
Assets/variant_001/shot_004/shot_004_scene_001/asset_manifest.json
```

Validation:

- both final prompt packages are `prompt_package_version = final_v1`
- both final prompt packages contain `tts_prompt`, `scenes[].image_prompt`, and `scenes[].video_prompt`
- both scene manifests use `source = codex_builtin_image_gen`
- both scene marks sync `plan_d.replacement_first_frame.selected_image`
- no Plan A `06_01` / `06_02` tools are part of this Plan D reference run
