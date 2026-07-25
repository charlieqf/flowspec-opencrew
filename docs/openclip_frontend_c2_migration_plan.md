# C2 OpenClip Frontend Migration Plan

Status: completed in C2; C3 removed the temporary compatibility wrappers
Last verified: 2026-06-21
Base state: C1 is complete; OpenClip backend lives under `backend/opcrew_backend/koubo/`. OpenClip frontend still lives under `OpenClip/frontend/src/` and is imported by the runtime frontend.

C3 note: this plan intentionally preserves the C2 before/after path details. Current commands should use the Koubo-named cache scripts only; the temporary OpenClip-named wrapper scripts were retired in C3.

## Goal

Move the OpenClip/Koubo frontend source from:

```text
OpenClip/frontend/src/
```

to:

```text
frontend/src/modules/koubo/
```

The runtime entry remains `frontend/`. After C2, Koubo, Analysis V1, and Upload Asset Library frontend code should be edited under `frontend/src/modules/koubo/`, not under `OpenClip/frontend/src/`.

## Non-goals

- Do not migrate `WorkflowAssistant/frontend/src/` in C2. Only repoint Koubo imports that still use the current WorkflowAssistant location.
- Do not migrate `WorkflowAssistant/backend/workflow_assistant/` in C2.
- Do not remove `frontend/vite.config.ts` `server.fs.allow: [path.resolve(__dirname, "..")]` yet. WorkflowAssistant is still outside the Vite root.
- Do not globally remove existing `?v=` static import cache strings. Repoint them and keep their behavior.
- Do not fix unrelated existing red contract assertions. In particular, keep the known text-drift failures in `test_analysis_v1_task_process_indicator_mvp_contract.py` and `test_koubo_storyboard_composer_scope_contract.py` unchanged unless a separate task says otherwise.
- Do not delete the `OpenClip/` root in C2 unless a final audit proves it is empty and unused. Root cleanup belongs to C3.
- Do not treat historical design documents as runtime blockers. Several old `.md` files under `docs/` and `docs/SessionDesign-R2/` mention `OpenClip/frontend/src` as historical implementation notes. C2 should update active architecture and runbook-style guidance, but stale historical design-document path strings are acceptable unless the team explicitly chooses a separate bulk documentation cleanup.

## Verified Inventory

### Runtime imports from App.jsx

`frontend/src/App.jsx` currently has 4 OpenClip import statements on lines 2-5. The line 5 named import was easy to miss and must be included.

| Current import | C2 target |
| --- | --- |
| `../../OpenClip/frontend/src/AnalysisV1/AnalysisV1Module.jsx?v=...` | `./modules/koubo/AnalysisV1/AnalysisV1Module.jsx?v=...` |
| `../../OpenClip/frontend/src/KouboStoryBoardModule.jsx?v=...` | `./modules/koubo/KouboStoryBoardModule.jsx?v=...` |
| `../../OpenClip/frontend/src/UploadAssetLibrary/UploadAssetLibraryPage.jsx?v=...` | `./modules/koubo/UploadAssetLibrary/UploadAssetLibraryPage.jsx?v=...` |
| `../../OpenClip/frontend/src/AnalysisV1/components/AnalysisV1DialogueView.jsx` | `./modules/koubo/AnalysisV1/components/AnalysisV1DialogueView.jsx` |

Review note: some discussion has referred to this as "5 App.jsx imports" because the named import on physical line 5 was previously omitted from counts. During implementation, use this table and the actual `rg` output rather than the count label.

### Reverse relative imports inside OpenClip frontend

Current snapshot has 8 reverse relative import lines total:

- 6 lines import back into the main `frontend/src`.
- 2 lines import into `WorkflowAssistant/frontend/src`.

Do not rewrite these with a single blanket `sed`; the new relative path depends on the importing file depth.

| File after move | Current import | New import |
| --- | --- | --- |
| `frontend/src/modules/koubo/AnalysisV1/AnalysisV1Module.jsx` | `../../../../frontend/src/components/ModelPresetCards.jsx` | `../../../components/ModelPresetCards.jsx` |
| `frontend/src/modules/koubo/OCRebuildModule.jsx` | `../../../frontend/src/debug/debugAdapter.js` | `../../debug/debugAdapter.js` |
| `frontend/src/modules/koubo/OCRebuildModule.jsx` | `../../../frontend/src/components/ModelPresetCards.jsx` | `../../components/ModelPresetCards.jsx` |
| `frontend/src/modules/koubo/OCRebuildSrtBuilder.jsx` | `../../../frontend/src/components/ModelPresetCards.jsx` | `../../components/ModelPresetCards.jsx` |
| `frontend/src/modules/koubo/OpenClipModule.jsx` | `../../../frontend/src/components/ModelPresetCards.jsx` | `../../components/ModelPresetCards.jsx` |
| `frontend/src/modules/koubo/KouboStoryBoard/hostProduct/KouboHostProductBuilder.jsx` | `../../../../../frontend/src/components/ModelPresetCards.jsx` | `../../../../components/ModelPresetCards.jsx` |
| `frontend/src/modules/koubo/OCRebuildModule.jsx` | `../../../WorkflowAssistant/frontend/src/WorkflowAssistantDrawer.jsx` | `../../../../WorkflowAssistant/frontend/src/WorkflowAssistantDrawer.jsx` |
| `frontend/src/modules/koubo/OpenClipModule.jsx` | `../../../WorkflowAssistant/frontend/src/WorkflowAssistantDrawer.jsx` | `../../../../WorkflowAssistant/frontend/src/WorkflowAssistantDrawer.jsx` |

Before editing, regenerate the live list:

```bash
rg -n "\\.\\./.*frontend/src|WorkflowAssistant/frontend/src" OpenClip/frontend/src --glob '!node_modules/**'
```

After moving, the same search should be run against `frontend/src/modules/koubo`.

### Contract tests with frontend source paths

13 contract test files currently hard-code `REPO_ROOT / "OpenClip" / "frontend" / "src"`. C2 must repoint them to `REPO_ROOT / "frontend" / "src" / "modules" / "koubo"` so they do not fail with `FileNotFoundError`.

Files:

```text
backend/tests/contracts/test_analysis_v1_run_to_storyboard_tts_mode_contract.py
backend/tests/contracts/test_analysis_v1_srt_rewrite_free_contract.py
backend/tests/contracts/test_analysis_v1_srt_rewrite_resume_contract.py
backend/tests/contracts/test_analysis_v1_storyboard_quick_contract.py
backend/tests/contracts/test_analysis_v1_task_process_indicator_mvp_contract.py
backend/tests/contracts/test_host_product_preview_image_css_contract.py
backend/tests/contracts/test_koubo_asset_agent_chat_contract.py
backend/tests/contracts/test_koubo_asset_audio_upload_contract.py
backend/tests/contracts/test_koubo_clean_image_contract.py
backend/tests/contracts/test_koubo_storyboard_agents_chat_contract.py
backend/tests/contracts/test_koubo_storyboard_composer_scope_contract.py
backend/tests/contracts/test_koubo_storyboard_tts_template_contract.py
backend/tests/contracts/test_lightweight_role_surface_wiring_contract.py
```

Known discipline: if `composer_scope` or `task_process_indicator` still fails on existing text assertions after path repointing, leave that failure as-is and document it in the verification result.

### Guard, bump, and preflight scripts

C2 must update the guard chain. Otherwise CI or hooks will either miss real Koubo changes or block on old paths.

Files that needed C2 guard updates:

```text
.github/workflows/openclip-bridge-guard.yml
.githooks/pre-commit
frontend/package.json
scripts/opencrew_frontend_preflight.sh
```

Recommended handling:

- Repoint the guard logic from `OpenClip/frontend/src/` to `frontend/src/modules/koubo/`.
- Rename user-facing messages from legacy OpenClip terminology to "Koubo frontend cache" or equivalent.
- Rename scripts to Koubo cache terminology and update callers:
  - add `scripts/check_koubo_frontend_cache_bump.sh`;
  - add `scripts/bump_koubo_frontend_cache_version.sh`;
  - update `.github/workflows/openclip-bridge-guard.yml`, `.githooks/pre-commit`, and `frontend/package.json` to call the new script names.
- C2 temporarily kept wrapper scripts for compatibility; C3 removed those wrappers, so new work should call the Koubo-named scripts directly.
- Update `scripts/opencrew_frontend_preflight.sh` to inspect:
  - `frontend/src/modules/koubo/KouboStoryBoardModule.jsx`;
  - `frontend/src/modules/koubo/KouboStoryBoard`;
  - served imports such as `/src/modules/koubo/KouboStoryBoardModule.jsx?v=...` and any Vite `/@fs/.../frontend/src/modules/koubo/...` form.

## Implementation Order

1. Baseline the repo.

```bash
git status --short --branch
scripts/opencrew_frontend_preflight.sh
cd frontend && npm run build
```

If baseline has known failures, record them before editing. Do not mix unrelated fixes into C2.

2. Move the source tree.

```bash
mkdir -p frontend/src/modules
git mv OpenClip/frontend/src frontend/src/modules/koubo
```

Do not move WorkflowAssistant in this step.

3. Repoint `frontend/src/App.jsx`.

Update the 4 OpenClip import statements listed above, including the named import for `AnalysisV1MediaSidebar`.

4. Repoint reverse imports file by file.

Use the reverse import table above. Recalculate each relative path from the moved file location. Avoid broad string replacement.

5. Repoint the Koubo static `?v=` import chain.

Keep existing cache strings and only change paths where needed. In particular, keep checking:

```text
frontend/src/main.tsx
frontend/src/App.jsx
frontend/src/modules/koubo/KouboStoryBoardModule.jsx
frontend/src/modules/koubo/KouboStoryBoard/**/*.js
frontend/src/modules/koubo/KouboStoryBoard/**/*.jsx
```

6. Repoint contract tests.

Change only source path roots from:

```python
REPO_ROOT / "OpenClip" / "frontend" / "src"
```

to:

```python
REPO_ROOT / "frontend" / "src" / "modules" / "koubo"
```

Do not update expected UI strings unless the task explicitly expands scope.

7. Repoint or rename guard scripts.

Update the check, bump, preflight, CI workflow, git hook, and `frontend/package.json` script references as described above.

8. Update docs.

Update `ARCHITECTURE.md` after the source move so it no longer tells engineers to edit `OpenClip/frontend/src/` for Koubo/Analysis V1/Asset Library frontend work. Keep WorkflowAssistant documented as still external.

Historical `.md` files that describe old implementation work can either be bulk-repointed in this step or explicitly left as historical references. The minimum C2 requirement is to prevent active architecture guidance, scripts, tests, hooks, and CI from pointing engineers at the old source tree.

## Verification Plan

Run static checks first:

```bash
rg -n "OpenClip/frontend/src|REPO_ROOT / \"OpenClip\" / \"frontend\" / \"src\"" frontend backend/tests scripts .github .githooks --glob '!node_modules/**'
rg -n "\\.\\./.*frontend/src" frontend/src/modules/koubo --glob '!node_modules/**'
rg -n "WorkflowAssistant/frontend/src" frontend/src/modules/koubo --glob '!node_modules/**'
```

Expected result:

- No active code, tests, scripts, hooks, or CI references to `OpenClip/frontend/src`.
- No old reverse imports to `../../../frontend/src` style paths.
- The only remaining `WorkflowAssistant/frontend/src` references should be the intentionally repointed imports in `OpenClipModule.jsx` and `OCRebuildModule.jsx`, unless those modules are retired separately.

Run frontend checks:

```bash
cd frontend && npm run build
cd ..
scripts/opencrew_frontend_preflight.sh
```

Run the guard explicitly:

```bash
scripts/check_koubo_frontend_cache_bump.sh --staged
```

Run path-sensitive contracts:

```bash
backend/.venv/bin/python -m pytest -q \
  backend/tests/contracts/test_analysis_v1_run_to_storyboard_tts_mode_contract.py \
  backend/tests/contracts/test_analysis_v1_srt_rewrite_free_contract.py \
  backend/tests/contracts/test_analysis_v1_srt_rewrite_resume_contract.py \
  backend/tests/contracts/test_analysis_v1_storyboard_quick_contract.py \
  backend/tests/contracts/test_analysis_v1_task_process_indicator_mvp_contract.py \
  backend/tests/contracts/test_host_product_preview_image_css_contract.py \
  backend/tests/contracts/test_koubo_asset_agent_chat_contract.py \
  backend/tests/contracts/test_koubo_asset_audio_upload_contract.py \
  backend/tests/contracts/test_koubo_clean_image_contract.py \
  backend/tests/contracts/test_koubo_storyboard_agents_chat_contract.py \
  backend/tests/contracts/test_koubo_storyboard_composer_scope_contract.py \
  backend/tests/contracts/test_koubo_storyboard_tts_template_contract.py \
  backend/tests/contracts/test_lightweight_role_surface_wiring_contract.py
```

Interpretation:

- `FileNotFoundError` or missing-source failures are C2 failures.
- The known text assertion failures in `task_process_indicator` and `composer_scope` are not C2 failures if they match the baseline.

Run runtime smoke after restarting the stack:

```bash
before_frontend_pids="$(lsof -nP -tiTCP:18080 -sTCP:LISTEN 2>/dev/null || true)"
scripts/opencrew_local_stack.sh restart
after_frontend_pids="$(lsof -nP -tiTCP:18080 -sTCP:LISTEN 2>/dev/null || true)"
printf 'frontend pids before: %s\nfrontend pids after:  %s\n' "$before_frontend_pids" "$after_frontend_pids"
curl -fsS http://127.0.0.1:8011/api/health
curl -fsS http://127.0.0.1:18080/api/auth/status
```

Because `frontend/vite.config.ts` has `hmr: false`, C2 verification must confirm that the Vite frontend process was actually restarted and the module graph was rebuilt from `frontend/src/modules/koubo/`. `scripts/opencrew_local_stack.sh restart` should cover this, but the before/after PID check prevents accidentally validating an old process.

Manual browser verification on `http://127.0.0.1:18080`:

- Analysis V1 route loads.
- Koubo Storyboard route loads.
- Upload Asset Library route loads.
- WorkflowAssistant drawer still loads from the legacy WorkflowAssistant location wherever it is reachable.

## Exit Criteria

- `OpenClip/frontend/src/` no longer exists.
- `frontend/src/modules/koubo/` contains the moved OpenClip frontend code.
- `frontend/src/App.jsx` imports Koubo modules from `./modules/koubo/...`.
- Koubo reverse imports into shared frontend code use local `frontend/src` relative paths.
- WorkflowAssistant imports remain valid and still point to the current `WorkflowAssistant/frontend/src/WorkflowAssistantDrawer.jsx`.
- Guard, bump, preflight, workflow, hook, and `frontend/package.json` scripts no longer rely on old `OpenClip/frontend/src` paths.
- `frontend/vite.config.ts` keeps `fs.allow: [path.resolve(__dirname, "..")]`.
- C2 path-sensitive contract tests no longer fail because source files moved.
- Runtime pages load from the new path after stack restart.
- C2 allowed `OpenClip/frontend/` to remain as a shell containing only legacy non-source files such as `__init__.py`; C3 removed that shell and archived the historical OpenClip docs under `docs/openclip-legacy/`.

## Rollback

C2 should land as one focused commit. If runtime import resolution fails or Vite cannot serve the moved modules, revert the C2 commit rather than manually copying files back. Because the move should use `git mv`, rollback should restore the old tree cleanly.
