# Analysis_V1 Tool Run Guide

This guide is the authoritative run guide for `OpenCrew/ToolLibrary/Analysis_V1`.

Do not use the legacy guide under `OpenCrew/ToolLibrary/Analysis/` when planning or running Analysis_V1 tools. Analysis_V1 has a different session contract, workspace layout, sandbox policy, and rerun behavior.

## Current Scope

Analysis_V1 currently supports this main chain:

```text
S1_00_PrepareSessionVariables  <- 00_PrepareSessionVariables.py
S2_01_VideoProbeMetadata       <- 01_VideoProbeMetadata.py
S3_02_01_AudioASR              <- 02_01_AudioASR.py
S4_02_02_VideoSRTFrame         <- 02_02_VideoSRTFrame.py
S5_03_01_TTSBuilderG           <- 03_01_TTSBuilderG.py
S6_04_01_SRTRewrite            <- 04_01_SRTRewrite.py
S7_04_02_StoryBoard            <- 04_02_StoryBoard.py
S8_05_01_VideoPlanGenerator    <- 05_01_VideoPlanGenerator.py
workflow_id = openclip_analysis
```

`Backup/` tools are legacy/reference files only. Keep the files in `Backup/`, but do not include them in the current run chain, tool guide, or normal execution scenario.

## Required Permissions

Before running the chain, the Codex session must have:

```text
file_system read/write: /Users/duheng/.opencrew
network enabled
```

Network is only for reading the existing OpenCrew PostgreSQL database in step 00, when explicitly authorized for configured cloud ASR in `02_01_AudioASR.py`, for Gemini TTS audio calls in `03_01_TTSBuilderG.py`, and for OpenCode run-model calls in `03_01_TTSBuilderG.py` Scene Profile, `04_01_SRTRewrite.py`, and `04_02_StoryBoard.py`. If the database cannot be reached, stop. Do not start or restart any service.

## Database Rules

The default database URL is defined in:

```text
OpenCrew/ToolLibrary/Analysis_V1/__init__.py
```

Resolution order:

```text
--database-url
OPENCREW_DATABASE_URL
DEFAULT_OPENCREW_DATABASE_URL from Analysis_V1/__init__.py
```

Never write the database URL, password, API key, cookie, auth header, access token, or refresh token into `Variables.json` or `Result.json`.

Step 00 may write public provider metadata into `Variables.json`, including `default_asr_config`, `default_tts_config`, `default_image_config`, `default_video_config`, `default_lipsync_config`, `gemini_builder_g_config`, `opencode_session_id`, `run_model_provider`, `run_model_id`, `rewrite_prompt`, and `storyboard_prompt`. These fields may include provider/model names, `api_key_ref`, and `has_api_key`, but never the API key itself. `03_01_TTSBuilderG.py` must choose its Gemini TTS model from these Variables and read the actual Gemini TTS API key from the database only into process memory. `05_02_VideoPlanExecutor.py` must choose image/video/lipsync models from these Variables and read the actual API keys from the database only into process memory. Its Scene Profile call must use the OpenCode run model from `run_model_provider/run_model_id`.

Forbidden actions when DB access fails:

```text
do not run opencrew_local_stack.sh
do not start PostgreSQL
do not restart backend
do not scan ports
do not auto-discover another DATABASE_URL
do not create a database process
do not create a database
```

Return `status=blocked` instead.

## Standard Commands

Step 00 prepares the session:

```bash
python3 OpenCrew/ToolLibrary/Analysis_V1/00_PrepareSessionVariables.py \
  --task-id <task_id> \
  --session-id <session_id> \
  --attempt-mode latest \
  --clip-mode virtual \
  --allow-cloud-asr-data-transfer \
  --print-json
```

Then run:

```bash
python3 OpenCrew/ToolLibrary/Analysis_V1/01_VideoProbeMetadata.py \
  --workspace <workspace> \
  --print-json

python3 OpenCrew/ToolLibrary/Analysis_V1/02_01_AudioASR.py \
  --workspace <workspace> \
  --asr-mode local \
  --print-json

python3 OpenCrew/ToolLibrary/Analysis_V1/02_02_VideoSRTFrame.py \
  --workspace <workspace> \
  --print-json

python3 OpenCrew/ToolLibrary/Analysis_V1/03_01_TTSBuilderG.py \
  --workspace <workspace> \
  --scene-profile-mode auto \
  --print-json

python3 OpenCrew/ToolLibrary/Analysis_V1/04_01_SRTRewrite.py \
  --workspace <workspace> \
  --print-json

python3 OpenCrew/ToolLibrary/Analysis_V1/04_02_StoryBoard.py \
  --workspace <workspace> \
  --print-json

python3 OpenCrew/ToolLibrary/Analysis_V1/05_01_VideoPlanGenerator.py \
  --workspace <workspace> \
  --target-type task \
  --max-video-seconds 4 \
  --min-video-seconds 4 \
  --print-json
```

For a clean rerun of an individual step, pass `--force` to that step. Each tool cleans only its own step directory and the session-level outputs it owns.

When running `02_01_AudioASR.py` in default/cloud mode, do not request cloud ASR transfer authorization at step 02_01. The authorization must already be recorded by step 00 in `Variables.json` as `cloud_asr_data_transfer_allowed=true`; otherwise 02_01 must block before extracting or uploading audio.

## Workspace Layout

At the start of step 00, remove legacy OpenClip workspace directories:

```text
inbox/
meta/
outbox/
```

Then ensure the Analysis_V1 session layout:

```text
<workspace>/
  SessionContext/
  SessionReport/
  SessionOutput/
  S1_00_PrepareSessionVariables/
    Output/
    Report/
  S2_01_VideoProbeMetadata/
    Working/
    Output/
    Report/
  S3_02_01_AudioASR/
    Working/
    Output/
    Report/
  S4_02_02_VideoSRTFrame/
    Working/
    Output/
    Report/
  S5_03_01_TTSBuilderG/
    Working/
    Output/
    Prompt/
    Report/
  S6_04_01_SRTRewrite/
    Working/
    Output/
    Prompt/
    Report/
  S7_04_02_StoryBoard/
    Working/
    Output/
    Prompt/
    Report/
  S8_05_01_VideoPlanGenerator/
    Working/
    Output/
    Report/
```

Successful step 00 produces only:

```text
SessionContext/Variables.json
SessionContext/Video_Source.mp4
S1_00_PrepareSessionVariables/Output/Variables.json
S1_00_PrepareSessionVariables/Report/Result.json
```

`02_02_VideoSRTFrame.py` is the final SRT-frame binding tool for this scope. Its final business output is:

```text
SessionOutput/subtitle/final_srt_frame_items.json
SessionOutput/visual/srt_frames/
```

It does not generate an HTML report. Any HTML comparison page is a separate review artifact outside the tool contract.

`03_01_TTSBuilderG.py` builds three flat Builder-G/Gemini voice candidates. Its final business output is:

```text
SessionOutput/tts/tts_builder_candidates.json
SessionOutput/tts/tts_builder_candidate_001.wav
SessionOutput/tts/tts_builder_candidate_002.wav
SessionOutput/tts/tts_builder_candidate_003.wav
```

All model prompts used by `03_01_TTSBuilderG.py` must be written under `S5_03_01_TTSBuilderG/Prompt/` before the model call, and the model call must read the prompt from that file without hidden code-side prompt concatenation.

The Scene Profile model output should include `voice_prompt_guidance`. Voice prompts must prioritize that field, then fall back to generic Scene Profile fields. Do not add video-specific prompt logic in code; local wording may only provide generic natural-speech constraints and voice test labels.

`03_01_TTSBuilderG.py` defaults to the Gemini Builder-G TTS model stored in `SessionContext/Variables.json`:

```text
default_tts_config.model
gemini_builder_g_config.selected_tts_model
run_model_provider
run_model_id
```

The built-in Gemini TTS model name is only a fallback. Passing `--tts-model` explicitly overrides the TTS Variables selection for that run. Scene Profile does not use a Gemini scene-profile override; it uses OpenCode `opencode_session_id` plus `run_model_provider/run_model_id`.

`04_01_SRTRewrite.py` rewrites final SRT-frame items sentence by sentence. Its final business output is:

```text
SessionOutput/subtitle/rewritten_srt_items.json
SessionOutput/subtitle/rewritten_dialogue.srt
```

It must preserve item count, `srt_id` order, `start`, `end`, `duration`, and `image_path` from `SessionOutput/subtitle/final_srt_frame_items.json`. Its model prompt must be written under `S6_04_01_SRTRewrite/Prompt/`, and the rewrite call must use OpenCode `opencode_session_id` plus `run_model_provider/run_model_id`.

`04_02_StoryBoard.py` groups rewritten SRT items into a Shot / Scene storyboard. Its final business output is:

```text
SessionOutput/storyboard/srt_storyboard.json
SessionOutput/storyboard/assets/images/
SessionOutput/storyboard/assets/videos/
SessionOutput/storyboard/Working/
```

Its only main input is `SessionOutput/subtitle/rewritten_srt_items.json`. It must not rewrite SRT dialogue, change `srt_id`, change timing, change duration, or reselect frames. Grouping goals must come from `SessionContext/Variables.json -> storyboard_prompt.final_prompt` / `storyboard_final_prompt`; do not hard-code Shot or Scene duration targets in the tool. Its model prompt must be written under `S7_04_02_StoryBoard/Prompt/`, and the StoryBoard call must use OpenCode `opencode_session_id` plus `run_model_provider/run_model_id`. The model does not see images; it only receives the final prompt and rewritten SRT item metadata. The tool validates full `srt_id` coverage and contiguous Shot / Scene grouping, then backfills time spans, scene-level `dialogue_items`, and key frame paths from the input. A Scene may contain multiple SRT ids, but every Dialogue/SRT item must remain a separate record. In each Scene, only the first dialogue item keeps `image_path`; later dialogue items use an empty `image_path`. StoryBoard assets are intentionally simple: `assets/images/` and `assets/videos/` are backup upload pools, while final Scene media lives flat under `Working/` and is indexed only by `srt_storyboard.json` through `asset_key` and `working_assets`.

`05_01_VideoPlanGenerator.py` converts StoryBoard Scene / Shot / Task scope into a video generation plan. Its final business output is:

```text
SessionOutput/storyboard/video_generation_plan.json
```

The tool also writes the same plan to:

```text
S8_05_01_VideoPlanGenerator/Output/video_generation_plan.json
S8_05_01_VideoPlanGenerator/Report/Result.json
```

It reads only `SessionContext/Variables.json`, `SessionOutput/storyboard/srt_storyboard.json`, and optional final consistency reference images under `SessionContext/Consistency/`, does not query the database, does not call models, does not create a `Prompt/` directory, and does not write back to `srt_storyboard.json`. It supports `--target-type scene`, `--target-type shot`, and `--target-type task`; `task` means the current Session StoryBoard file, not a database lookup. The default `--max-video-seconds` and `--min-video-seconds` are both `4`. Missing host/person or product final consistency reference images are recorded in `video_generation_plan.json -> consistency_references.missing[]` and do not block plan generation.

Planning rules:

1. Video files are planned at the first Dialogue key of each video segment, e.g. `SessionOutput/storyboard/Working/srt_0001_Video_Final.mp4`.
2. Old/original images trigger image prompt + image generation; they are not direct video first frames.
3. Only images already placed in the Dialogue new-image slot can be direct video first frames. If the placed image came from original or uploaded assets, the plan must record the copy source and Working destination in `materialize_first_frame`.
4. If a Scene has one image but runs longer than `--max-video-seconds`, split on Dialogue boundaries and use each previous segment tail frame as the next first frame.
5. If every Dialogue has an image, each Dialogue remains its own video segment even when it is short. The timeline `duration` stays tied to the Dialogue, but `planned_video_duration` must be at least `--min-video-seconds`; later compose/align tools can trim or handle the extra generated duration.
6. A single Dialogue longer than `--max-video-seconds` must not be split; mark `duration_exceeds_limit_unavoidable=true`.
7. The first Scene with no visual source is `skipped`, not generated.
8. Later Scenes without their own visual source may use the previous segment or previous Scene tail frame. If that dependency is unavailable, the Scene is `blocked`; later Scenes with their own visual source continue planning.
9. Tail-frame dependencies must be explicit in each segment through `depends_on_segment_id`, `depends_on_video_path`, and `depends_on_tail_frame_path`.
10. Each video segment must plan `planned_outputs.segment_audio_path` and sync controls: `need_lipsync`, `need_audio_video_sync`, `need_sync`, `sync_mode`, `lipsync_disabled_by_ui`, and `lipsync_reason`. UI-explicit lipsync off writes `need_lipsync=false` and still requires `sync_mode=audio_replace_retime`.
11. A Dialogue-bound video is also a segment anchor. Its segment is marked ready for video generation (`need_video_prompt=false`, `need_video=false`) and records `existing_video.materialize_video`; `05_02` copies it into the tool Working area, replaces the original audio with Segment TTS audio, retimes video duration to that audio, and publishes the standard `{first_dialogue_asset_key}_Video_Final.mp4` Working path instead of calling the video model.
12. Cutaway segments (`dialogue.video_plan.is_talking_head=false`) cannot provide tail frames for following empty Dialogue or empty Scene continuation. Missing `is_talking_head` remains backward-compatible and is treated as talking-head continuation.

`05_02_VideoPlanExecutor.py` should consume the single session plan at `SessionOutput/storyboard/video_generation_plan.json`. Image, video, and lipsync prompt files must be generated deterministically inside the selected provider module from the module templates under `OpenCrew/ToolLibrary/Analysis_V1/Reference/05_02/`, plus StoryBoard, plan, and Scene Profile fields. There is no outer all-in-one 05_02 prompt template after the module split. Do not call another text model to freely generate image or video prompts. The generated `ImagePrompt.json` and `VideoPrompt.json` should record `template_blocks[]` so the prompt is auditable and can be reproduced. Default image, video, and lipsync provider/model values come from `SessionContext/Variables.json` as `default_image_config`, `default_video_config`, and `default_lipsync_config`; step 00 must populate those configs. API keys are read from the database into memory only during model calls and must never be written to tool files. Raw videos, bound-video working copies, audio-retime temporary videos, and lipsync temporary videos stay in `S9_05_02_VideoPlanExecutor/Working/`; final images, segment final audio, final videos, final tail frames, and business prompt JSON are copied to `Output/` and then `SessionOutput/storyboard/Working/`. If `need_lipsync=false`, the raw or bound video is first retimed to Segment TTS audio duration and has its original audio replaced; only that synced video is promoted to final video. If a segment is completed by a Dialogue-bound video, `05_02` does not call the video model, but it still performs audio replacement/retime and extracts the tail frame from the synced final video. `05_02` does not consume frontend-edited Prompt JSON by default; frontend prompt edits use a separate API. `--force` only cleans the tool step directories, and successful overwrites of StoryBoard Working files must first back up existing files to `SessionOutput/storyboard/assets/history/`. Provider timeouts are `failed_timeout`. `05_02` does not compose Scene / Shot / Task videos.

## Force Rerun Behavior

`00 --force` resets only step 00 owned state:

```text
SessionContext/
0_SessionContext/  # legacy name, removed only during migration/force rerun
S1_00_PrepareSessionVariables/
```

Then it recreates step 00 outputs from the database and source video.

`00 --force` must not delete:

```text
SessionReport/
SessionOutput/
S2_*
S3_*
S4_*
S5_*
other downstream tool directories
```

If the whole tool chain must rerun, the Plan Runner or caller must decide whether downstream directories should be cleaned.

## Output Contract

Later Analysis_V1 tools must read global context from:

```text
SessionContext/Variables.json
```

Later tools must read global source video from:

```text
SessionContext/Video_Source.mp4
```

Later tools must not query the database by default and must not read the original external media path as their runtime input.

## Blocked Conditions

Return `status=blocked` when:

```text
workflow_id is not openclip_analysis
task does not exist
session mismatch
workspace is missing or not writable
/Users/duheng/.opencrew is not writable
opencode_session_id is missing
final_prompt is missing
source video is missing
source video is not .mp4
database access fails
```

When sandbox access blocks the run, tell the user to authorize:

```text
file_system read/write: /Users/duheng/.opencrew
network enabled
```
