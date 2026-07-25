# Rebuild_V1

Standalone OC-Rebuild tool package. Each registered tool is intended to be run directly from its own Python file.

Registered script filenames use Tool ID-style PascalCase segments, for example:

```bash
python3 OpenCrew/ToolLibrary/Rebuild_V1/05_01_Scene_ScenePromptRefresh.py \
  --workspace /path/to/workspace \
  --shot-id shot_001 \
  --scene-mark-id shot_001_scene_001 \
  --check-dependencies-only \
  --print-json
```

Short legacy registry ids such as `01`, `02`, `04`, and `04_1` are preserved for registry compatibility, but their scripts still use descriptive filenames such as `02_Rebuild_ShotPlanBuilder.py` and `04_ShotPlan_AssetTaskBuilder.py`.

See `IMPLEMENTATION_PLAN.md` for the implementation contract, dependency model, and test plan.

See `PHASE_RUNBOOK.md` for the current good SRT / bad SRT phase boundary and execution order.

See `PLAN_D_WORKFLOW.md` for the Plan D product/host consistency prompt and Codex image_gen first-frame workflow.

See `RUNBOOK_rebuild_v1_pitfalls.md` for known run pitfalls and regression guards.
