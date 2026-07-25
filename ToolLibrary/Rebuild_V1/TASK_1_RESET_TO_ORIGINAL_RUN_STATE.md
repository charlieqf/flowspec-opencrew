# Rebuild Task #1 Reset To Original Run State

Date: 2026-05-12

## Scope

This records the cleanup performed for OC-Rebuild Task #1 / Session #51.

Goal: clear generated Session outputs and runtime database traces while preserving the task's configured inputs so the task can be run again from a clean starting point.

## Preserved

- Rebuild Task row: `oc_rebuild_tasks.id = 1`.
- Bound Rebuild Session: `session_id = 51`.
- Bound Analysis Task: `analysis_task_id = 24`.
- Source package pointer: `source_package_path = rebuild/source_package.json`.
- User configuration fields on `oc_rebuild_tasks`, including target topic, platform, aspect ratio, audience, product info, rebuild goal, preserve/replace strategies, style fields, `simple_prompt`, `final_prompt`, prompt model, and run model.
- Saved intent/prompt version: `oc_rebuild_prompt_versions.task_id = 1`.
- Current version pointer: `oc_rebuild_tasks.current_version_id = 6`.
- Historical backup workspace: `~/.opencrew/sessions/51/workspace.before_reset_20260511_192646/`.
- Task creation and config save events: `ocrebuild.task.created`, `ocrebuild.config.saved`.

## Cleared

- Current workspace contents under `~/.opencrew/sessions/51/workspace/`.
- Runtime attempts from `oc_rebuild_attempts` for Task #1 / Session #51.
- Workflow plans from `workflow_plans` for Task #1 / Session #51.
- Runtime Rebuild events from `session_events` for Session #51, excluding task creation and config save events.

## Filesystem Operation

The current workspace directory itself was kept, but its current generated contents were removed:

```text
~/.opencrew/sessions/51/workspace/
```

Removed current workspace entries included:

```text
.DS_Store
plan_a/
rebuild/
reports/
rebuild_shot_plan.json
scene_marks.json
```

The historical backup directory was intentionally not deleted:

```text
~/.opencrew/sessions/51/workspace.before_reset_20260511_192646/
```

## Database Operation

Runtime-only records were removed with this boundary:

```sql
DELETE FROM oc_rebuild_attempts
WHERE task_id = 1 OR session_id = 51;

DELETE FROM workflow_plans
WHERE task_id = 1 OR session_id = 51;

DELETE FROM session_events
WHERE session_id = 51
  AND kind LIKE 'ocrebuild.%'
  AND kind NOT IN ('ocrebuild.task.created', 'ocrebuild.config.saved');
```

The following were intentionally not deleted or nulled:

```sql
-- Keep the configured task.
oc_rebuild_tasks WHERE id = 1

-- Keep saved prompt/intent versions.
oc_rebuild_prompt_versions WHERE task_id = 1

-- Keep the pointer to the saved version.
oc_rebuild_tasks.current_version_id

-- Keep user-authored/generated prompt fields used as pre-run configuration.
oc_rebuild_tasks.simple_prompt
oc_rebuild_tasks.final_prompt
```

## Result

The task is reset to a clean runnable state:

- Task configuration remains available in the UI/API.
- The current Session workspace no longer contains generated shot plans, scene marks, reports, or phase output files.
- Historical backup outputs remain available for inspection.
- Runtime event history no longer shows previous generation/provider/heartbeat activity.
- The saved intent version remains attached to the task so the task is not reduced to an unconfigured shell.
