# TalkingHead_V1 ToolLibrary Surface

`TalkingHead_V1` is the workflow-specific surface for person talking-head videos.
It owns every workflow-specific stage and keeps only the generic 05_01 video
plan generator from Analysis V1.

## Current Scope

- `00_PrepareSessionVariables.py`: prepares task variables, portrait paths,
  HeyGen voice timing, and the Wan R2V default video config.
- `04_01_SRTRewrite.py`: prepares talking-head SRT. Existing script/SRT wins;
  otherwise it falls back to complex prompt, then simple prompt.
- `01_StoryBoardGenerate.py`: generates the TalkingHead StoryBoard draft and
  replaces `Analysis_V1/04_03_StoryBoardQuick.py` for this workflow.
- `02_StoryBoardStructure.py`: forces one Shot and one Scene, then uses the
  selected clone voice plus Tempo to generate a calibration audio file,
  calculate seconds per spoken unit, and merge consecutive SRT lines into
  Dialogue/Segment units up to the configured single video length.
- `03_StoryBoardConfig.py`: places portrait `Image_New` slots by reuse policy,
  generates/binds HeyGen clone voice `Audio_Final` per merged Dialogue, and
  rewrites Dialogue timing from the final audio duration.
- `05_02_VideoPlanExecutor.py`: executes segment video generation using only
  Flush X, Max 1.5 X, Max 2.7 W, or Max SD 2 and the TalkingHead-owned prompt assets.
  Flush X dispatches to `video_grok_10.py` + `Video_Grok_10.md`; Max 1.5 X
  dispatches to `video_grok_15.py` + `Video_Grok_15.md`, so both prompts can be
  tuned independently.
- Max SD 2 dispatches to the TalkingHead-local `video_openrouter.py` and
  `Video_SDR2V_TalkingHead.md`. Its fixed fallback reference is
  `Reference/05_02/Video_SDR2V_TalkingHead.mp4`; the filename is stable and the
  bundled clip must not exceed 15 seconds. Its local privacy-grid tools process the
  reference video, uploaded portrait, generated first frames, and continuation
  tail frames. The reference video supplies the reference person's facial
  expression and expression changes, plus motion, pose, rhythm, gestures, and
  camera movement; identity remains anchored to the uploaded portrait.
- `06_01_VideoPlanComposer.py`: composes TalkingHead segment videos into the
  final movie.

## Downstream Reuse

TalkingHead_V1 must not execute `Analysis_V1/04_03_StoryBoardQuick.py`.
After `03_StoryBoardConfig.py`, one-click movie execution reuses only:

- `Analysis_V1/05_01_VideoPlanGenerator.py`

It then runs:

- `TalkingHead_V1/05_02_VideoPlanExecutor.py`
- `TalkingHead_V1/06_01_VideoPlanComposer.py`

## Boundaries

- SRT lines are source sentences. A Dialogue is the video-generation Segment
  after merging consecutive SRT lines by the configured single video length.
- Do not split a sentence/SRT line; if a single line exceeds the configured
  length, keep it whole and warn.
- A task has exactly one Shot and one Scene by default.
- The uploaded portrait image is the first-frame seed. Reuse it every
  `portrait_segments_per_image` segments.
- Voice is optional at task creation. If a HeyGen clone voice is selected, `02`
  must generate a real calibration audio sample to estimate grouping, and `03`
  must generate final Dialogue audio and use the final audio duration as the
  authoritative Dialogue duration.
- The selected HeyGen voice and Tempo are the workflow authority for StoryBoard
  TTS. They must be written into Session Variables and `storyboard_tts_selection`;
  a stale generic Qwen/Cherry selection in `koubo_storyboard_edit.json` must not
  override the TalkingHead voice. Saving only Tempo must preserve the same voice,
  provider, model, candidate, and non-empty candidate list.
- TTS generation must be persisted as StoryBoard state, not only rendered as a
  wav file. Each generated or cache-hit Dialogue audio must update the locked TTS
  manifest, `working_assets.audio.path`, and the Dialogue/Scene/Shot duration
  using the final audio duration.
- The Max 2.7 W default reference MP4 belongs to TalkingHead_V1/05_02, not to
  task creation, StoryBoard generation, or run-start validation.
