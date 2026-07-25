# OpenCrew P0 Development Plan

Date: 2026-05-27

Status: Draft for implementation

## Goal

This document turns the first P0 slice from `docs/opencrew_repo_improvement_plan.md` into an executable development plan.

The goal is to reduce immediate correctness and security risk without starting large feature work such as Plan Runner, Prompt Registry, OpenCode auto-healing, or async event bus.

## Business Compatibility Rule

P0 is an infrastructure and safety stabilization slice. It must preserve existing supported business workflows:

- OpenClip analysis task creation, execution, session detail, result browsing, share links, and video playback.
- OC-Rebuild task creation, WorkflowAssistant planning, registry loading, session detail, outputs, and deletion.
- OC-StoryBoard task creation, StoryBoard-aware session/detail routing, outputs, and deletion.
- Existing Session Detail, Debug Console, and Share Page surfaces, with the same core user journeys.

The plan does intentionally change unsafe or incorrect behavior:

- A missing WorkflowAssistant registry path must fail clearly instead of silently falling back to the Analysis registry.
- Anonymous/share surfaces must stop exposing debug/internal events and raw OpenCode properties.
- Raw/zip/share file APIs must stop serving hidden, sensitive, traversal, absolute-path escape, or workspace-outside symlink targets.
- Generic deletion must stop leaving workflow-owned orphan rows or deleting workspace before DB state is safely handled.
- Legacy OC-Analysis playback references outside the workspace must be repaired or rebuilt before symlink enforcement ships.

Each PR must include compatibility checks for its touched workflow. If a supported user journey must change, that change is out of scope for P0 unless this document is updated with an explicit migration path and product decision.

## Branch

Use a dedicated branch:

```bash
git checkout -b p0-workflow-infra
```

## Scope

This first P0 slice includes one preflight gate plus five workstreams:

0. Create the minimum contract-test harness and merge gates.
1. Fix OC-Rebuild Tool Registry routing.
2. Establish migration baseline.
3. Build the minimal event visibility safety loop.
4. Harden File API access.
5. Fix deletion consistency and StoryBoard discriminator.

## Hard Dependencies

These are merge-order constraints, not just preferences:

- Workstream 0 must land before or with Workstream 1 so every P0 change has a stable test home.
- Workstream 2 must land before Workstreams 3, 4, and 5 because they all need schema migrations.
- Workstream 3 must ship in two ordered PRs:
  - `p0-event-visibility-schema`: fields, reader kind-inference, customer-safe whitelist, and read filters.
  - `p0-event-service-redaction`: `SessionEventService`, writer migration, redaction, and OpenCode SSE persistence.
- The event API visibility filter must not go live before reader-side kind-inference or explicit writer marking, otherwise existing customer/share pages can go blank during rollout.
- Workstream 4 must ship legacy OC-Analysis playback detection/repair before enforcing workspace-outside symlink rejection on served files.
- Workstream 5 must add the StoryBoard discriminator before deletion dispatch depends on it.

## Workstream 0: Contract Test Harness

Why preflight: every workstream below requires contract tests, but the repo does not yet define a stable home or harness for them.

Tasks:

- Create a small backend contract-test directory for P0 workflow contracts.
- Add test fixtures for session/task/workspace setup that do not require real model calls.
- Add a documented command for running the P0 contract tests locally.
- Make missing-registry, event visibility, file serving, and delete cascade tests use this harness.

Acceptance:

- Workstream 1 can add a missing-registry contract test without inventing a new test layout.
- Tests can run without external LLM/VLM calls.
- The test command is documented in this file or the repo test docs.

Local command:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/opencrew-pycache \
  backend/.venv/bin/python -m unittest discover -s backend/tests/contracts
```

## Workstream 1: OC-Rebuild Registry Routing

Why first: low risk, no schema change, high correctness impact.

Tasks:

- Change all OC-Rebuild workflow configs from `ToolLibrary/Rebuild` to `ToolLibrary/Rebuild_V1`.
- Scope the fallback removal to the WorkflowAssistant resolver in `WorkflowAssistant/backend/workflow_assistant/routes.py`; OpenClip's own Analysis registry resolver is legitimate and should not be changed as part of this fix.
- Remove silent fallback from a missing configured WorkflowAssistant registry to the Analysis registry.
- Validate all configured workflow registry and agent guide paths at bootstrap or first request.
- Add a registry source assertion to WorkflowAssistant bootstrap / plan validation.
- Confirm the `openclip_analysis` config still resolves to the Analysis registry after the fallback removal.

Acceptance:

- OC-Rebuild loads `ToolLibrary/Rebuild_V1/tool_registry.json`.
- Missing registry path returns a clear backend error.
- OC-Rebuild plan validation cannot accidentally use Analysis tools.
- OpenClip still resolves its Analysis registry after the WorkflowAssistant fallback removal.
- All three OC-Rebuild config variants are updated, not only the default rebuild workflow.

Tests:

- Unit test path resolution for OpenClip, OC-Rebuild, and OC-Rebuild Plan A variants.
- Contract test for missing registry path.

## Workstream 2: Migration Baseline

Why second: subsequent P0 work needs schema changes.

Tasks:

- Add Alembic or equivalent migration tooling.
- Create a baseline migration that recognizes the current schema.
- Keep bootstrap for runtime seed data, but stop using new `ensure_*_columns()` for future schema changes.
- Document how to initialize empty DB and upgrade an existing DB.
- Document rollback behavior for the baseline. If down-migration is intentionally unsupported for the baseline, state that clearly and provide a safe restore path from DB backup.
- Make the baseline re-runnable/idempotent enough that it does not fail or duplicate objects when pointed at an existing initialized DB.

Acceptance:

- Empty DB can initialize with migrations plus bootstrap.
- Existing DB can upgrade without data loss.
- New P0 schema fields are introduced only through migrations.
- Baseline mis-detection of existing objects is covered by tests or an explicit guard.

Tests:

- Empty DB migration smoke.
- Existing schema migration smoke.
- Re-run baseline smoke against an already-initialized schema.

## Workstream 3: Event Visibility Safety Loop

Why third: share/session/debug event leakage is the highest-risk data exposure.

Split this workstream into two PRs. The first PR creates the read-side safety contract. The second PR migrates writers through a single service.

Tasks:

- PR `p0-event-visibility-schema`:
  - Add `session_events` visibility fields through migration:
    - `visibility`
    - `event_scope`
    - `severity`
    - optional workflow/task/attempt/tool metadata fields if included in the same migration
  - Implement reader-side compatibility: if fields are missing or empty, infer safe visibility from event kind.
  - Mark `user.message`, `assistant.final`, and necessary lifecycle events as public/customer-safe.
  - Mark OpenCode raw events and tool/model/provider events as internal/debug by default.
  - Filter Share Page, Session Detail, and Debug Console using backend policy.
- PR `p0-event-service-redaction`:
  - Add a `SessionEventService` facade for new writes.
  - Route OpenCode SSE persistence through `SessionEventService`.
  - Add redaction before event persistence and presentation.
  - Migrate direct `session_events` writers incrementally.

Rollout constraints:

- Do not enable the API visibility filter before kind-inference is available or before writers explicitly mark customer-safe events.
- During rollout, `user.message`, `assistant.final`, `session.created`, `session.completed`, and `session.failed` must remain visible in customer/session surfaces.
- Legacy events without visibility fields default to internal/debug unless kind-inference explicitly promotes them.

Redaction minimum:

- API keys
- Bearer tokens
- `Authorization` headers
- `Cookie` headers
- session/share tokens
- proxy credentials
- phone numbers
- email addresses

Acceptance:

- Share Page cannot retrieve debug/internal events.
- Session Detail shows customer-safe messages and lifecycle events after migration.
- Debug Console still sees debug/internal events.
- OpenCode raw `properties` are not exposed to anonymous share responses.
- Event filtering cannot blank customer/share pages during mixed old/new writer rollout.

Tests:

- Public event visible in Session Detail and share.
- Debug event visible in Debug Console but hidden from share.
- Legacy event without visibility uses kind inference.
- Secret / PII redaction test.
- OpenCode raw event is stored as internal/debug and redacted.

## Workstream 4: File API Hardening

Why fourth: event filtering does not protect files; raw/zip/share must be backend-enforced.

Important coupling: tightening symlink and external-path handling can break legacy OC-Analysis virtual playback if older manifests still point at media outside the workspace. Newer tasks are safer because `ToolLibrary/Analysis/01_video_metadata_extractor.py` stages the source video into `workspace/source_video.mp4`, but the P0 file-security PR must also handle older tasks.

Tasks:

- Introduce `SessionFileService`.
- Use canonical path resolution for all workspace reads.
- Reject path traversal, absolute path escape, directories for raw download, hidden files, and sensitive files.
- Reject symlinks that resolve outside workspace.
- Enforce `session_files.downloadable` and file visibility/sensitivity policy.
- Make zip builder skip hidden, sensitive, non-downloadable, and workspace-escaping entries.
- Keep video Range request behavior intact.
- Add fallback denylist policy while file visibility fields are being rolled out.
- Verify existing OC-Analysis tasks resolve playback to in-workspace `source_video.mp4` rather than an absolute external source path.
- Add a legacy-task detection/repair script for OC-Analysis tasks whose manifests or playback metadata point outside the workspace or whose `source_video.mp4` is missing.
- Ship workspace-outside symlink rejection in the same PR as the legacy playback repair path.
- State the policy boundary clearly: symlink rejection applies to served session workspace files. Runner scratch directories and ffmpeg shim symlinks, such as `.tool_bin` helpers, are out of scope unless they become served workspace entries.

Acceptance:

- `../`, absolute paths, and workspace-outside symlinks are rejected.
- `.env`, token files, secret dumps, and debug raw dumps are not raw/zip/share downloadable.
- Share token can only download public/downloadable files.
- Existing video raw playback still supports `206 Partial Content`.
- Existing and repaired OC-Analysis virtual playback uses the in-workspace `source_video.mp4`.
- Legacy tasks with external playback references can be detected and repaired or rebuilt before symlink enforcement is enabled.

Tests:

- Raw path traversal test.
- Symlink escape test.
- Hidden/sensitive file denial test.
- Zip filtering test.
- Share file permission test.
- Video Range smoke test.
- Legacy OC-Analysis virtual playback regression with a manifest that previously pointed outside the workspace.
- Repair-script dry-run and repair-mode smoke tests.

## Workstream 5: Deletion Consistency and StoryBoard Discriminator

Why fifth: deletion currently risks half-deleted state; StoryBoard must be distinguishable before source-based deletion dispatch is safe.

Tasks:

- Add `WorkflowDeletionService`.
- Route deletion by workflow source plus explicit discriminator.
- Change deletion order to DB-first, workspace cleanup second.
- On workspace cleanup failure, record cleanup event/job rather than rolling back successful DB deletion.
- Add minimal StoryBoard discriminator before full P1 model redesign:
  - use an additive DB `workflow_mode=storyboard` equivalent for P0,
  - do not flip StoryBoard `source` in P0 because that creates mixed old/new source populations and changes existing source-keyed behavior.
- Add a one-time backfill migration or repair script for existing StoryBoard tasks:
  - scan legacy OC-Rebuild workspaces for `storyboard_meta.json`,
  - set `workflow_mode=storyboard` on matching legacy rows,
  - run in dry-run mode first and report candidate task/session IDs,
  - make the write step idempotent so re-running does not mutate unrelated Rebuild rows.
- Keep `storyboard_meta.json` only as a transitional fallback during the compatibility window. Deletion dispatch may consult it only to repair or classify legacy rows that still have `workflow_mode IS NULL`.
- Stop relying on `storyboard_meta.json` as the only durable way to identify StoryBoard tasks.
- Use service-owned explicit DB deletion inside one transaction for P0. Do not rely on implicit `ON DELETE CASCADE` as the primary strategy until FK changes are deliberately designed and migrated.
- Name and cover the current root cause: `openclip_tasks.session_id` references `sessions.id` without `ON DELETE CASCADE`, so generic session deletion can leave workflow data inconsistent or fail late.
- Enumerate cascade coverage for each workflow:
  - task rows
  - prompt versions
  - runtime versions
  - skill versions
  - attempts
  - workflow plans
  - session events
  - session files
  - session shares
  - OpenCode link state
  - workspace cleanup

Acceptance:

- Deleting OpenClip task leaves no orphan session.
- Deleting OC-Rebuild task leaves no orphan task/session/files/events.
- Deleting StoryBoard task uses StoryBoard-aware strategy.
- DB failure does not happen after workspace has already been removed.
- Failed workspace cleanup is visible and retryable.
- Delete cascade tests cover the full workflow-owned data set, not only task/session/files/events.
- `workflow_mode=storyboard` is present for new and backfilled legacy StoryBoard rows before StoryBoard-aware deletion dispatch is enabled.
- Legacy StoryBoard rows with `workflow_mode IS NULL` are detected by the backfill dry-run or handled by the transitional fallback, never silently treated as plain Rebuild rows.

Tests:

- OpenClip delete cascade test.
- OC-Rebuild delete cascade test.
- StoryBoard discriminator and delete routing test.
- StoryBoard workflow_mode backfill dry-run and idempotent write test.
- Legacy StoryBoard deletion test where the row starts as `source=oc-rebuild, workflow_mode=NULL` and the workspace contains `storyboard_meta.json`.
- Simulated workspace cleanup failure test.
- Prompt/runtime/skill version cascade test.
- Attempt/workflow plan/share/OpenCode link cleanup test.

## Non-Goals

Do not include these in the first P0 slice:

- WorkflowAssistant real Plan Runner.
- Prompt Registry hot loading.
- OpenCode auto-healing.
- Async event bus.
- Full StoryBoard standalone workflow redesign.
- Full OC-Analysis Tool Library migration.

## Suggested PR Split

PR names are authoritative; numeric PR order does not match workstream numbers after Workstream 3 splits into two PRs.

| Workstream | PR |
| --- | --- |
| WS0 Contract Test Harness | `p0-contract-test-harness` unless folded into `p0-registry-routing` |
| WS1 OC-Rebuild Registry Routing | `p0-registry-routing` |
| WS2 Migration Baseline | `p0-migration-baseline` |
| WS3 Event Visibility Safety Loop, read side | `p0-event-visibility-schema` |
| WS3 Event Visibility Safety Loop, write side | `p0-event-service-redaction` |
| WS4 File API Hardening | `p0-file-api-security-and-legacy-playback-repair` |
| WS5 Deletion Consistency and StoryBoard Discriminator | `p0-deletion-storyboard-workflow-mode` |

Merge order:

1. `p0-contract-test-harness` unless folded into `p0-registry-routing`
2. `p0-registry-routing`
3. `p0-migration-baseline`
4. `p0-event-visibility-schema`
5. `p0-event-service-redaction`
6. `p0-file-api-security-and-legacy-playback-repair`
7. `p0-deletion-storyboard-workflow-mode`

Each PR should include focused tests and avoid bundling unrelated UI changes.
