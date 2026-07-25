# OpenCrew Task Cleanup Plan - 2026-06-09

## Scope

This plan covers deleting all OpenCrew business tasks whose task `updated_at` is earlier than:

- Cutoff: `2026-06-08 00:00:00 Australia/Sydney`
- Cutoff timestamp: `1780840800000` milliseconds

In scope:

- `openclip_tasks.updated_at < 1780840800000`
- `oc_rebuild_tasks.updated_at < 1780840800000`
- Their owning `sessions` rows
- DB rows directly tied to those sessions/tasks
- Session workspace directories and generated artifacts under each candidate session directory

Out of scope by default:

- `task_runs` and `task_logs`: these are a separate runtime log system. `task_runs.session_id` is text and has no reliable FK to `sessions.id` in the current schema, so these should only be pruned with a separate policy.
- `local_usage_log`: this is the billing/metering audit ledger. Its `task_id`, `attempt_id`, and `step_id` are text fields with no FK to workflow tables. It is intentionally retained even if those text references become dangling after task cleanup.
- Provider/model settings, global app settings, auth/session secrets, and any files outside validated candidate session directories.

Execution status: completed on 2026-06-09. The pre-delete DB backup and final manifest are stored under `/Users/macmini-1/.opencrew/backups/task_cleanup_20260609/`.

## Current Candidate Set

Read-only inventory on 2026-06-09 found:

- Candidate business tasks: 29
- Candidate sessions: 29
- OpenClip/Koubo candidates: 21
- Rebuild candidates: 8
- Existing candidate session files: about 14,875 files
- Existing candidate session disk usage: about 6.86 GB

### Candidate Tasks

| kind | task_id | session_id | status | updated |
|---|---:|---:|---|---|
| openclip | 75 | 134 | completed | 2026-06-05 15:41 |
| openclip | 71 | 130 | completed | 2026-06-05 12:52 |
| openclip | 74 | 133 | completed | 2026-06-05 12:09 |
| openclip | 59 | 115 | completed | 2026-06-05 11:12 |
| openclip | 73 | 132 | completed | 2026-06-04 19:37 |
| openclip | 72 | 131 | completed | 2026-06-04 18:41 |
| openclip | 69 | 128 | completed | 2026-06-04 16:31 |
| openclip | 68 | 127 | completed | 2026-06-04 16:21 |
| openclip | 66 | 125 | blocked | 2026-06-04 15:07 |
| openclip | 70 | 129 | draft | 2026-06-04 13:44 |
| openclip | 67 | 126 | completed | 2026-06-04 13:19 |
| openclip | 63 | 119 | completed | 2026-06-03 18:36 |
| openclip | 60 | 116 | completed | 2026-06-03 17:47 |
| openclip | 46 | 102 | failed | 2026-06-03 17:44 |
| openclip | 62 | 118 | completed | 2026-06-03 17:39 |
| openclip | 48 | 104 | completed | 2026-06-03 17:18 |
| openclip | 61 | 117 | blocked | 2026-06-03 16:06 |
| rebuild | 18 | 73 | storyboard_editing | 2026-05-27 19:24 |
| rebuild | 27 | 85 | draft | 2026-05-26 16:35 |
| rebuild | 24 | 82 | draft | 2026-05-25 17:47 |
| rebuild | 21 | 79 | draft | 2026-05-25 17:08 |
| rebuild | 20 | 77 | draft | 2026-05-25 17:01 |
| rebuild | 19 | 76 | draft | 2026-05-25 16:53 |
| openclip | 28 | 75 | draft | 2026-05-25 16:47 |
| openclip | 27 | 74 | draft | 2026-05-25 16:21 |
| rebuild | 5 | 58 | draft | 2026-05-19 09:25 |
| rebuild | 1 | 51 | draft | 2026-05-11 22:15 |
| openclip | 24 | 56 | draft | 2026-05-11 20:50 |
| openclip | 23 | 54 | draft | 2026-05-10 22:27 |

## Associated DB Rows

The deletion should remove these directly associated rows:

| table | candidate rows |
|---|---:|
| `openclip_attempts` | 57 |
| `openclip_prompt_versions` | 0 |
| `openclip_skill_versions` | 0 |
| `oc_rebuild_attempts` | 0 |
| `oc_rebuild_prompt_versions` | 1 |
| `session_events` | 8723 |
| `session_files` | 5600 |
| `session_shares` | 0 |
| `workflow_plans` | 0 |
| `openflow_analysis_runs` | 0 |

The deletion must not remove `local_usage_log` rows by default.

Current read-only checks found possible text references from `local_usage_log` to candidate tasks/sessions:

| match heuristic | rows | unreconciled rows | est_cost_micros | actual_cost_micros |
|---|---:|---:|---:|---:|
| `task_id` matches candidate OpenClip/Rebuild task ID | 854 | 854 | 0 | 100558000 |
| `task_id` matches candidate session ID | 336 | 336 | 0 | 50174000 |
| `attempt_id` matches candidate OpenClip/Rebuild task ID | 0 | 0 | 0 | 0 |
| `attempt_id` matches candidate session ID | 631 | 631 | 0 | 79284000 |

These counts can overlap because the IDs are untyped text. They are audit signals, not deletion counts. Because all observed possible matches are currently unreconciled, deleting them would risk losing billable or auditable usage. If product policy later requires purging usage rows for deleted tasks, that must be a separate billing-approved cleanup with an explicit `billing_reconciled_at IS NOT NULL` guard or equivalent revenue closeout.

The delete order should follow the existing `WorkflowDeletionService.delete_session_db_first()` logic:

1. Delete `oc_rebuild_attempts`, `oc_rebuild_prompt_versions`, `workflow_plans` with `workflow_id LIKE 'oc_rebuild%'`, then `oc_rebuild_tasks`.
2. Delete `openclip_param_versions` for matching OpenClip task IDs if the table exists.
3. Delete `openclip_attempts`, `openclip_skill_versions`, `openclip_prompt_versions`, then `openclip_tasks`.
4. Delete `openflow_analysis_runs`, `workflow_plans`, `session_shares`, `session_files`, `session_events`.
5. Delete `sessions`.

The implementation should reuse this service path rather than maintaining an independent hand-written batch delete order. If a single all-session DB transaction is required, add/refactor a service method that accepts an existing DB connection or deletes a list of sessions inside one transaction; do not duplicate the delete logic in ad hoc SQL.

## Associated Files

The deletion should remove the whole candidate session directory, not only the `workspace` subdirectory, because related artifacts can live beside or under the workspace directory.

Candidate workspace paths from DB:

| session_id | workspace_dir | file state |
|---:|---|---|
| 51 | `/Users/macmini-1/.opencrew/sessions/51/workspace` | session dir exists, but only old backup dirs were observed |
| 54 | `/Users/macmini-1/.opencrew/sessions/54/workspace` | session dir missing |
| 56 | `/Users/macmini-1/.opencrew/sessions/56/workspace` | session dir missing |
| 58 | `/Users/macmini-1/.opencrew/sessions/58/workspace` | session dir missing |
| 73 | `/Users/macmini-1/.opencrew/sessions/73/workspace` | session dir missing |
| 74 | `/Users/macmini-1/.opencrew/sessions/74/workspace` | session dir missing |
| 75 | `/Users/macmini-1/.opencrew/sessions/75/workspace` | session dir missing |
| 76 | `/Users/macmini-1/.opencrew/sessions/76/workspace` | session dir missing |
| 77 | `/Users/macmini-1/.opencrew/sessions/77/workspace` | session dir missing |
| 79 | `/Users/macmini-1/.opencrew/sessions/79/workspace` | session dir missing |
| 82 | `/Users/macmini-1/.opencrew/sessions/82/workspace` | session dir missing |
| 85 | `/Users/macmini-1/.opencrew/sessions/85/workspace` | session dir missing |
| 102 | `/Users/macmini-1/.opencrew/sessions/102/workspace` | session dir exists |
| 104 | `/Users/macmini-1/.opencrew/sessions/104/workspace` | session dir exists |
| 115 | `/Users/macmini-1/.opencrew/sessions/115/workspace` | session dir exists |
| 116 | `/Users/macmini-1/.opencrew/sessions/116/workspace` | session dir exists |
| 117 | `/Users/macmini-1/.opencrew/sessions/117/workspace` | session dir exists |
| 118 | `/Users/macmini-1/.opencrew/sessions/118/workspace` | session dir exists |
| 119 | `/Users/macmini-1/.opencrew/sessions/119/workspace` | session dir exists |
| 125 | `/private/tmp/opencrew-phase0-data/sessions/125/workspace` | outside default data dir; session dir exists but size was 0 KB |
| 126 | `/Users/macmini-1/.opencrew/sessions/126/workspace` | session dir exists |
| 127 | `/Users/macmini-1/.opencrew/sessions/127/workspace` | session dir exists |
| 128 | `/Users/macmini-1/.opencrew/sessions/128/workspace` | session dir exists |
| 129 | `/Users/macmini-1/.opencrew/sessions/129/workspace` | session dir exists but size was 0 KB |
| 130 | `/Users/macmini-1/.opencrew/sessions/130/workspace` | session dir exists |
| 131 | `/Users/macmini-1/.opencrew/sessions/131/workspace` | session dir exists |
| 132 | `/Users/macmini-1/.opencrew/sessions/132/workspace` | session dir exists |
| 133 | `/Users/macmini-1/.opencrew/sessions/133/workspace` | session dir exists |
| 134 | `/Users/macmini-1/.opencrew/sessions/134/workspace` | session dir exists |

File safety checks before removal:

- `workspace_dir` must be absolute.
- `workspace_dir.name` must be `workspace`.
- `session_dir.name` must equal the DB `sessions.id`.
- `session_dir` must not be a symlink.
- Allowed roots:
  - `/Users/macmini-1/.opencrew/sessions`
  - `/private/tmp/opencrew-phase0-data/sessions` only for the observed phase0 candidate session 125
- Missing session dirs are treated as file cleanup no-ops, not failures.

## Recommended Execution Procedure

### 1. Stop or Quiesce New Work

Before deletion, ensure no generation or rebuild job is actively writing into candidate sessions.

Recommended guard:

- Abort if any candidate `sessions.status` is `queued` or `running`.
- Abort if any candidate task has changed and no longer matches `updated_at < 1780840800000`.

Current read-only inventory did not show queued/running candidate sessions, but this must be checked again immediately before deletion.

### 2. Create Backups

Create a DB backup before any delete:

```bash
mkdir -p /Users/macmini-1/.opencrew/backups/task_cleanup_20260609
pg_dump -h 127.0.0.1 -p 5433 -U opencrew -d opencrew -Fc -f /Users/macmini-1/.opencrew/backups/task_cleanup_20260609/opencrew_before.dump
```

For files, prefer a quarantine move before permanent removal:

- Move existing candidate session dirs into `/Users/macmini-1/.opencrew/backups/task_cleanup_20260609/quarantine/`.
- Keep the original parent path encoded in the quarantine manifest.
- Only permanently remove quarantine after DB verification passes.

This gives a practical rollback path without relying on immediate full artifact archive creation. Avoid `/private/tmp` for backups or quarantine because macOS may clear temporary directories across reboot or maintenance.

### 3. Dry Run

Immediately before deletion, regenerate a manifest containing:

- cutoff timestamp
- task kind, task ID, session ID, status, updated_at
- session workspace path
- per-table associated row counts
- `local_usage_log` possible text-reference counts and unreconciled counts; these rows remain preserved by default
- cross-cutoff FK risk: any retained `oc_rebuild_tasks.analysis_task_id` pointing at a candidate `openclip_tasks.id`
- file existence, file count, and disk usage per session
- validation result for each session path

Abort on any validation error except missing session dirs. Also abort if any retained rebuild task references an OpenClip task that would be deleted.

### 4. Quarantine Existing Files

For each validated existing session dir:

1. Create quarantine root.
2. Move the session dir into quarantine.
3. Record source and destination in the manifest.

If any move fails, move already quarantined dirs back and abort before touching the DB.

### 5. Delete DB Rows Through the Existing Service

Use the existing `WorkflowDeletionService.delete_session_db_first()` logic for DB deletion. The implementation should:

1. Recompute candidate sessions from the cutoff immediately before deletion.
2. Run drift guards inside the DB deletion transaction:
   - no candidate `sessions.status` is `queued` or `running`
   - all candidate `openclip_tasks.updated_at` and `oc_rebuild_tasks.updated_at` still satisfy `updated_at < 1780840800000`
   - no retained `oc_rebuild_tasks.analysis_task_id` points at a candidate `openclip_tasks.id`
3. Reuse the service's per-session delete order and table-existence guards.
4. Leave `local_usage_log`, `task_runs`, and `task_logs` untouched.

Current `WorkflowDeletionService.delete_session_db_first()` opens its own transaction per session. If one atomic transaction for all 29 sessions is required, refactor the service before execution, for example by adding a helper that accepts a connection:

```python
delete_session_db_first_in_tx(conn, session_row)
```

Then run one outer transaction and call that helper for each candidate session. This keeps the service as the source of truth and avoids plan/code drift.

Important: `oc_rebuild_tasks.analysis_task_id` can reference `openclip_tasks.id`. Current inventory showed zero retained rebuild tasks referencing candidate OpenClip tasks, but execution must recheck this inside the transaction. If the count is non-zero, abort and either keep the referenced OpenClip task or include/delete the dependent rebuild task according to product policy.

### 6. Verify

Post-delete verification should assert:

```sql
SELECT count(*) FROM openclip_tasks WHERE updated_at < 1780840800000;
SELECT count(*) FROM oc_rebuild_tasks WHERE updated_at < 1780840800000;
```

Expected result for both: `0`.

Also verify:

- candidate session IDs are absent from `sessions`
- direct child tables have no rows for deleted task/session IDs
- existing session directories have been moved to quarantine or removed
- frontend task list no longer shows the deleted tasks

### 7. Permanently Remove Quarantine

After verification and manual approval, delete:

```text
/Users/macmini-1/.opencrew/backups/task_cleanup_20260609/quarantine/
```

If permanent removal is approved in the same maintenance window, this can happen immediately after verification.

## Rollback

Before permanent quarantine deletion:

1. Restore the DB from `/Users/macmini-1/.opencrew/backups/task_cleanup_20260609/opencrew_before.dump`.
2. Move quarantined session dirs back to their original locations using the manifest.
3. Restart the local stack if needed.

After permanent file deletion:

- DB can still be restored from the dump.
- Files can only be restored if an external backup/archive was created before deletion.

## Review Decisions Needed

Before executing, confirm:

1. The cutoff is exactly `2026-06-08 00:00:00 Australia/Sydney`, not end-of-day on June 8.
2. Completed tasks older than the cutoff should also be deleted. This plan includes them.
3. `local_usage_log` should remain untouched as the billing/metering audit ledger, even though text task references may become dangling.
4. `task_runs` and `task_logs` should remain untouched for now.
5. Quarantine-before-delete is acceptable, with permanent removal after verification.
6. Whether to refactor `WorkflowDeletionService` for one all-session transaction before execution, or accept per-session DB transactions plus file quarantine rollback.

## Execution Result

Execution completed with:

- DB backup: `/Users/macmini-1/.opencrew/backups/task_cleanup_20260609/opencrew_before.dump`
- Final manifest: `/Users/macmini-1/.opencrew/backups/task_cleanup_20260609/cleanup_manifest_final.json`
- Quarantine root: `/Users/macmini-1/.opencrew/backups/task_cleanup_20260609/quarantine`
- Deleted business tasks: 29
- Deleted sessions: 29
- Quarantined session dirs: 18
- `local_usage_log`: preserved

Post-delete verification:

| check | result |
|---|---:|
| `openclip_tasks.updated_at < 1780840800000` | 0 |
| `oc_rebuild_tasks.updated_at < 1780840800000` | 0 |
| candidate `sessions` remaining | 0 |
| candidate `openclip_attempts` remaining | 0 |
| candidate `oc_rebuild_prompt_versions` remaining | 0 |
| source session dirs still present | 0 |
