# OpenClip Validation Checklist

## Frontend shell

- Route entry: `#/openclip/tasks`
- Task detail route: `#/openclip/tasks/{id}`
- Debug session route remains available: `#/sessions/task/{session_id}`

## Task lifecycle

- Create Task creates one Task and one OpenCode Session immediately
- Delete Task removes the Task and its workspace
- Prompt generation, Skill generation, Run and Rerun all use the same Session

## Version flow

- Param version can be saved and loaded
- Prompt version can be generated, saved and loaded
- Skill version can be generated, saved and loaded
- Run uses selected Prompt version, Skill version and Run model

## Artifact baseline

- `reports/analysis_summary.json`
- `reports/quality_check.json`
- `reports/openclip_main_result.json`
- `storyboards/formula_slot_mapping.md`
- `storyboards/scheme_filename_manifest.json`
- `transcripts/original_dialogue_segments_scheme_1.json`
- `transcripts/original_dialogue_segments_scheme_2.json`
- `transcripts/original_dialogue_segments_scheme_3.json`
- `transcripts/formula_slot_dialogues.json`
- `clips/scheme_1/*`
- `clips/scheme_2/*`
- `clips/scheme_3/*`

## Formula mapping

- Slot mapping must follow the selected formula
- It must not be hardcoded to Hook / Trust / CTA when another formula is selected
