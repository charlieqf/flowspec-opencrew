# Reset Task To Initial Workspace State

This runbook records how to reset an OpenClip Analysis task so it can be rerun from a clean Session workspace while keeping task configuration.

## Scope

Use this when the goal is to remove generated outputs and database runtime residue, but keep the OpenClip task configuration.

Keep configuration:

- `openclip_tasks` business fields, `reference_video_path`, prompt model, and run model.
- `openclip_prompt_versions` for the task.
- `openclip_skill_versions` for the task, if any.
- global provider/config tables such as `tool_asr_provider_configs`, `tool_media_provider_configs`, and `app_settings`.
- external source media referenced by `openclip_tasks.reference_video_path`.

Remove runtime state:

- generated files under the Session workspace.
- `session_events` for the Session.
- `session_files` for the Session.
- `workflow_plans` for the Task or Session.
- `openclip_attempts` for the Task or Session.
- task/session status fields that indicate a previous run.

## Identify Task And Session

Do not assume `Task #N` equals `Session #N`. Query the task first:

```sql
SELECT id, session_id, status, reference_video_path, current_prompt_version_id,
       current_skill_version_id, latest_attempt_id, run_model_provider, run_model_id
FROM openclip_tasks
WHERE id = :task_id;
```

Then query the Session workspace:

```sql
SELECT id, source, status, workspace_dir, opencode_session_id
FROM sessions
WHERE id = :session_id;
```

## Restore Workspace To Initial State

The initial OpenCrew Session workspace contains only these directories:

```text
workspace/
  inbox/
  outbox/
  meta/
```

For an OpenClip Analysis Session, remove generated Analysis directories and files:

```bash
rm -rf \
  "$WORKSPACE/audio" \
  "$WORKSPACE/history" \
  "$WORKSPACE/input" \
  "$WORKSPACE/keyframes" \
  "$WORKSPACE/meta" \
  "$WORKSPACE/reports" \
  "$WORKSPACE/schemes" \
  "$WORKSPACE/storyboards" \
  "$WORKSPACE/transcripts" \
  "$WORKSPACE/rebuild" \
  "$WORKSPACE/clips" \
  "$WORKSPACE/source_video.mp4" \
  "$WORKSPACE/.DS_Store"

mkdir -p \
  "$WORKSPACE/inbox" \
  "$WORKSPACE/outbox" \
  "$WORKSPACE/meta"
```

Do not recreate Analysis-specific folders such as `audio`, `input`, `keyframes`, `reports`, `schemes`, `storyboards`, or `transcripts` if the target is true initial Session state. Analysis tools will recreate them when rerun.

Important: delete and recreate `meta/`; do not merely keep the existing directory. Tools such as `03 SemanticLLMStructureBuilder` and `14 SegmentDescriptorSubtitleBuilder` write raw LLM/VLM cache under `meta/semantic_llm/raw/` and `meta/segment_descriptions/scheme_detail/raw/`. Keeping existing `meta/` contents can leave stale raw files behind.

## Clear Database Runtime State

Use one transaction. Replace `:task_id`, `:session_id`, and `:now_ms`.

```sql
BEGIN;

DELETE FROM session_files
WHERE session_id = :session_id;

DELETE FROM session_events
WHERE session_id = :session_id;

DELETE FROM workflow_plans
WHERE task_id = :task_id OR session_id = :session_id;

DELETE FROM openclip_attempts
WHERE task_id = :task_id OR session_id = :session_id;

UPDATE openclip_tasks
SET
  status = 'draft',
  latest_attempt_id = NULL,
  updated_at = :now_ms
WHERE id = :task_id;

UPDATE sessions
SET
  status = 'draft',
  last_summary = NULL,
  started_at = NULL,
  finished_at = NULL,
  updated_at = :now_ms
WHERE id = :session_id;

COMMIT;
```

## Verification

Workspace should contain only:

```text
inbox/
meta/
outbox/
```

Database counts should be zero for runtime tables, while prompt/skill versions remain:

```sql
SELECT
  (SELECT count(*) FROM session_events WHERE session_id = :session_id) AS events,
  (SELECT count(*) FROM session_files WHERE session_id = :session_id) AS files,
  (SELECT count(*) FROM openclip_attempts WHERE task_id = :task_id OR session_id = :session_id) AS attempts,
  (SELECT count(*) FROM workflow_plans WHERE task_id = :task_id OR session_id = :session_id) AS plans,
  (SELECT count(*) FROM openclip_prompt_versions WHERE task_id = :task_id) AS prompt_versions,
  (SELECT count(*) FROM openclip_skill_versions WHERE task_id = :task_id) AS skill_versions;
```

The API file listing for generated schemes should be empty:

```bash
curl -s "http://127.0.0.1:8011/api/session-tasks/$SESSION_ID/files?path=schemes/scheme_1"
```

Expected after true initial workspace reset: `schemes/scheme_1` may not exist, so the API can return `404 Directory not found`. That is acceptable because `schemes/` is an Analysis output directory, not an initial Session directory.

## Example: Task 21

In the local environment checked on 2026-05-12:

- OpenClip Task: `21`
- Session: `50`
- Workspace: `/Users/duheng/.opencrew/sessions/50/workspace`
- External source video remained configured at `/Users/duheng/Development/OpenCode/CrewAI/Media/de8e59546729465bf6bb3a3eefb7ae52.mp4`

Important distinction:

- `Session #21` was an older `openflow-analysis` Session.
- `OpenClip Task #21` used `Session #50`.

For this example, the workspace was restored to only `inbox/`, `outbox/`, and `meta/`, while `openclip_tasks.reference_video_path` and prompt version `39` were kept.
