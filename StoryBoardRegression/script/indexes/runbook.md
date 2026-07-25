# StoryBoard Regression Runbook

Run every command from the current repository root.

This bundle contains copied scripts for review, but the regression gate must use live repository paths unless a copied script is explicitly marked runnable here. Do not use the historical `/Users/duheng/Development/OpenCode/CrewAI` checkout path as a gate.

## Current Runnable Gate

Slot matrix comparison:

```bash
python3 scripts/koubo_video_plan_slot_state_check.py --compare
```

Core backend contracts:

```bash
backend/.venv/bin/python -m pytest -q \
  backend/tests/contracts/test_koubo_storyboard_slot_state_contract.py \
  backend/tests/contracts/test_koubo_storyboard_dialogue_asset_key_contract.py \
  backend/tests/contracts/test_koubo_storyboard_manual_asset_status_contract.py \
  backend/tests/contracts/test_koubo_storyboard_stale_edit_contract.py \
  backend/tests/contracts/test_koubo_non_single_scene_plan_state_contract.py \
  backend/tests/contracts/test_analysis_v1_image_plan_tools_contract.py \
  backend/tests/contracts/test_analysis_v1_video_only_plan_tools_contract.py \
  backend/tests/contracts/test_analysis_v1_video_plan_executor_resilience_contract.py \
  backend/tests/contracts/test_analysis_v1_video_plan_composer_contract.py \
  backend/tests/contracts/test_analysis_v1_video_plan_settings_contract.py \
  backend/tests/contracts/test_analysis_v1_video_plan_image_gemini_contract.py \
  backend/tests/contracts/test_analysis_v1_video_plan_image_reference_provider_contract.py
```

Real UI runner:

```bash
npm --prefix frontend run test:e2e:koubo-storyboard
```

This Playwright runner opens the real Koubo StoryBoard UI and uses deterministic API mocks. The default npm entrypoint only orchestrates eight single-purpose UI tests:

- `npm --prefix frontend run test:e2e:koubo-storyboard:identity`: fixed slot rendering, `dialogue_asset_key` priority, and poisoned fallback protection.
- `npm --prefix frontend run test:e2e:koubo-storyboard:upload`: upload entry, multipart request, uploaded asset card, and upload-tab persistence.
- `npm --prefix frontend run test:e2e:koubo-storyboard:binding`: real pointer drag binding for source image, new image, raw video, and final video, then save and reload.
- `npm --prefix frontend run test:e2e:koubo-storyboard:final`: unbound Final file confirm button and Final binding write-back.
- `npm --prefix frontend run test:e2e:koubo-storyboard:talking-head`: talking-head/cutaway context menu and save payload.
- `npm --prefix frontend run test:e2e:koubo-storyboard:merge`: Merge Dialogue save payload keeps the retained key/binding and removes generated Working references for the disappeared dialogue.
- `npm --prefix frontend run test:e2e:koubo-storyboard:plans`: Video Plan, Image Plan, Video Only Plan, Raw/Image/Final/copy-final badges, and a mobile-width modal check.
- `npm --prefix frontend run test:e2e:koubo-storyboard:structure`: split/merge save key stability and new-image clear without Raw/Final cascade.

The shared fixture lives in `frontend/e2e/koubo-storyboard/fixture.mjs`; individual tests should stay thin and single-purpose. The runner writes per-test screenshots and a combined `result.json` under `test-results/koubo-storyboard-regression/<run-id>/`.

The script does not start Vite. It preflights `OPENCREW_E2E_FRONTEND_URL` or `http://127.0.0.1:18080`; if the frontend is unreachable it writes a failed `result.json` and exits with an explicit message to start `npm --prefix frontend run dev -- --host 127.0.0.1 --port 18080`.

UI coverage is intentionally split by responsibility instead of one oversized flow. Each test proves one business invariant and shares only setup/mocks/helpers. The runner uses a poisoned `by_dialogue_id` slot fixture so the page must render Raw state through `dialogue_asset_key`; it asserts upload entry, drag binding, Final confirmation, talking-head/cutaway state, save-reload visibility, modal badge state, Merge Dialogue removal boundaries, split/merge key stability, clear isolation, and plan output paths that do not fall back to `srt_id`, `dialogue_id`, Scene ID, or Shot ID. It does not cover real workspace file existence, history archival, media playback, or long drag/drop edge cases outside the deterministic fixture.

Latest local verification on 2026-06-26:

- Slot matrix comparison: passed with `differences=0`.
- Real UI runner: passed with `run_id=20260626071732`; eight per-test screenshots are under `test-results/koubo-storyboard-regression/20260626071732/`.
- Frontend preflight failure path: verified with `OPENCREW_E2E_FRONTEND_URL=http://127.0.0.1:9`; it writes `ok=false` failure JSON instead of leaving an empty result directory.
- Backend contracts: passed with `485 passed, 1 warning, 92 subtests passed`.

Do not claim real workspace coverage from the deterministic UI runner alone. Real Working files, history archival, media playback, and real upload files still need targeted manual evidence when a change touches those paths.

Real provider chain tests are opt-in and not part of the default gate because they can make paid external calls:

```bash
OPENCREW_REAL_MODEL_TESTS=1 backend/.venv/bin/python -m pytest -q \
  StoryBoardRegression/script/analysis-v1/test_video_plan_executor_real_models.py
```

## Archived, Runnable but Not Default Gate

The following copied tests remain useful as requirements and fixtures. They have been path-adapted enough to run from this checkout, but they are not the default gate unless promoted here with a maintained command:

- `StoryBoardRegression/script/analysis-v1/test_*.py`: path-adapted to load tools from the current repo root; latest targeted run returned `61 passed, 1 skipped`.
- `StoryBoardRegression/script/storyboard-node/*.mjs`: imports now point at `../../../frontend/src/modules/koubo/OCStoryBoard/...`; `fixed_reorganize_task18_regression.mjs` skips with a report if its optional `STORYBOARD_PLAN` fixture is missing.
- `StoryBoardRegression/script/tools/koubo_video_plan_slot_state_check.py`: runnable after path normalization, but the live repo helper remains the canonical gate command.

Current archive-path check:

```bash
rg -n 'StoryBoardRegression/OpenCrew/ToolLibrary|OpenCrew/OpenClip/frontend|/Users/duheng|Development/OpenCode/CrewAI' \
  StoryBoardRegression/script/analysis-v1 StoryBoardRegression/script/storyboard-node
```

The only remaining absolute path match is the optional default `STORYBOARD_PLAN` fixture in `fixed_reorganize_task18_regression.mjs`; set `STORYBOARD_PLAN` to run that archived scenario against a real saved plan.

## Manual UI Regression Minimum

Run these when the change touches frontend rendering, drag/drop, save/reload, asset binding, media playback, or the APIs backing those flows.

- Upload image to source slot.
- Upload image to new image slot.
- Upload video to raw and final video slots.
- Check Video Plan vs Video Only Plan when Raw exists but Audio/Final do not.
- Delete new image and confirm Raw/Final/Prompt are not cascaded.
- Split/Merge Scene and Shot after drag binding.
- Merge Dialogue and verify disappeared generated assets enter history.
- Save, refresh, re-enter task, and verify UI/JSON/Working files match.

Evidence required:

- Browser screenshot of the target OpenCrew page.
- Relevant API JSON before and after the operation.
- Working-file existence checks for every asserted slot.
- `Audio.src`, `dialogue_asset_key`, and `working_assets.audio.path` when audio is involved.

## Important Key Invariant

- `dialogue_asset_key` is the stable identity for asset binding and generated outputs.
- Do not use `srt_id`, `dialogue_id`, Scene ID, or Shot ID as binding fallback keys.
