# Rebuild_V1 Implementation Plan

## Goal

`Rebuild_V1` is a new OpenCrew Tool Library package beside the existing `Rebuild` and `Analysis` packages. It provides a clean, Agent-friendly implementation of OC-Rebuild where every registered tool is a standalone Python file.

The current `OpenCrew/ToolLibrary/Rebuild` package remains unchanged and available as the legacy implementation. Agents can choose either registry:

- Legacy grouped implementation: `OpenCrew/ToolLibrary/Rebuild/tool_registry.json`
- Standalone tool implementation: `OpenCrew/ToolLibrary/Rebuild_V1/tool_registry.json`

## Non-Goals

- Do not remove or rewrite `OpenCrew/ToolLibrary/Rebuild`.
- Do not require `Rebuild_V1` tools to import or call `Rebuild/plan_a_tools.py`.
- Do not create workflow entrypoints in the first implementation pass.
- Do not add `PlanA_PhaseBatch` or `PlanA_ShotFirst` to the first `Rebuild_V1` registry.
- Do not create a shared `_common.py` dependency for V1 tools.

Workflow orchestration will be designed later after all standalone tools exist and their dependency behavior is validated.

## Directory Layout

Target directory:

```text
OpenCrew/ToolLibrary/Rebuild_V1/
  IMPLEMENTATION_PLAN.md
  README.md
  __init__.py
  tool_registry.json
  01_Rebuild_SourcePackageLoad.py
  02_Rebuild_ShotPlanBuilder.py
  03_1_SceneMarkBuilder.py
  04_ShotPlan_AssetTaskBuilder.py
  04_01_Shot_AssetTaskBuilder.py
  03_01_ShotPlan_TTSReferenceAudioExtract.py
  03_02_ShotPlan_TTSVoiceRecommend.py
  03_03_ShotPlan_TTSVoiceSelectionWrite.py
  03_04_ShotPlan_PreDeleteReadinessCheck.py
  ...
```

Registered script filenames use Tool ID-style PascalCase segments. Short legacy registry ids `01`, `02`, `04`, and `04_1` are preserved for compatibility, but their script names are descriptive: `01_Rebuild_SourcePackageLoad.py`, `02_Rebuild_ShotPlanBuilder.py`, `04_ShotPlan_AssetTaskBuilder.py`, and `04_01_Shot_AssetTaskBuilder.py`.

`README.md` should describe how to select `Rebuild_V1`, how to run a tool directly, and how dependency checks work.

## Tool Independence Rules

Every registered `Rebuild_V1` tool must be directly executable as its own Python program.

Required properties:

- Has its own `argparse` parser.
- Has its own `main()` function.
- Has a file-local `TOOL_ID` constant.
- Has file-local dependency metadata.
- Can print structured JSON with `--print-json`.
- Can run dependency checks without doing work via `--check-dependencies-only`.
- Can be repeated safely against the same workspace.
- Does not import `OpenCrew.ToolLibrary.Rebuild.plan_a_tools`.
- Does not import `OpenCrew/ToolLibrary/Rebuild/plan_a_tools.py` by file path.
- Does not depend on a V1 shared helper such as `_common.py`.
- Does not require `--tool-id`; the tool identity is the file itself.

Allowed implementation choice:

- Duplicate small helper functions inside each file when needed.
- Copy existing standalone Rebuild tools into `Rebuild_V1` and adjust paths, CLI metadata, and registry references.

This intentionally prioritizes Agent searchability and local tool readability over code reuse.

## Registry Rules

Create an independent registry:

```text
OpenCrew/ToolLibrary/Rebuild_V1/tool_registry.json
```

Registry requirements:

- Must be valid JSON.
- Must use `schema_version: "1.0"` unless a schema migration is explicitly introduced.
- Must describe the V1 package as standalone-tool based.
- Must not include workflow entries in the first pass.
- Must not include `PlanA_PhaseBatch`.
- Must not include `PlanA_ShotFirst`.
- Every `script` must point to `OpenCrew/ToolLibrary/Rebuild_V1/<tool_file>.py`.
- For full descriptive tool ids, the script basename must be `<tool id>.py`.
- Short legacy ids `01`, `02`, `04`, and `04_1` are the only registry exceptions to the basename rule.
- No `script` may point to `OpenCrew/ToolLibrary/Rebuild/plan_a_tools.py`.
- No `script` may point to any legacy `OpenCrew/ToolLibrary/Rebuild/...` file.

Recommended registry-level convention:

```json
{
  "schema_version": "1.0",
  "description": "Machine-readable registry for Agent-driven OpenClip Rebuild V1 standalone tools.",
  "conventions": {
    "implementation": "standalone_python_files",
    "workflow_policy": "agents_orchestrate_tools_explicitly",
    "agent_instruction": "Use this registry when the user wants Rebuild_V1 standalone tools. Each tool should be checked with --check-dependencies-only before execution when inputs are uncertain."
  },
  "tools": []
}
```

## CLI Contract

Every V1 tool must support these common arguments:

```text
--workspace
--task-id
--session-id
--input
--output
--source-package
--database-url
--database-url-env
--print-json
--check-dependencies-only
--force
```

Default argument behavior:

- `--input` defaults to `rebuild_shot_plan.json` where the tool uses a shot plan.
- `--output` defaults to `rebuild_shot_plan.json` where the tool mutates the shot plan.
- `--source-package` defaults to `source_package.json` where the tool needs analysis/source context.
- `--database-url-env` defaults to `OPENCREW_DATABASE_URL` for tools that need database context.
- `--force` allows execution despite dependency warnings, but the warnings must still appear in the output JSON.

Tools may also support these scoped or provider arguments when relevant:

```text
--shot-id
--scene-mark-id
--tts-provider
--tts-model
--tts-voice
--force-inferred-scene-marks
--force-tts-refresh
```

V1 tools must not support these legacy grouped-entrypoint arguments:

```text
--tool-id
--run-all
--workflow
```

## Standard JSON Output

Every tool must return one structured JSON payload when `--print-json` is used.

Successful shape:

```json
{
  "tool": "06_01_Scene_PlanA_SceneImageRebuild",
  "tool_version": "1.0.0",
  "status": "completed",
  "workspace": "/path/to/workspace",
  "dependencies": {
    "status": "satisfied",
    "satisfied": [],
    "missing": [],
    "warnings": []
  },
  "result": {}
}
```

Blocked shape:

```json
{
  "tool": "06_01_Scene_PlanA_SceneImageRebuild",
  "tool_version": "1.0.0",
  "status": "blocked",
  "workspace": "/path/to/workspace",
  "dependencies": {
    "status": "blocked",
    "satisfied": [
      "rebuild_shot_plan.json",
      "source_package.json"
    ],
    "missing": [
      {
        "dependency": "confirmed_scene_srt",
        "reason": "scene mark has no calibrated srt_text",
        "suggested_tools": [
          "05_01_Scene_ScenePromptRefresh"
        ]
      }
    ],
    "warnings": []
  },
  "suggested_previous_tools": [
    "04_02_Scene_FirstLastFrameConfirm",
    "05_01_Scene_ScenePromptRefresh"
  ],
  "suggested_next_tools": [
    "06_02_Scene_PlanA_SceneImageSelect"
  ],
  "result": null
}
```

Failed shape:

```json
{
  "tool": "06_01_Scene_PlanA_SceneImageRebuild",
  "tool_version": "1.0.0",
  "status": "failed",
  "workspace": "/path/to/workspace",
  "message": "human-readable error",
  "dependencies": {
    "status": "unknown",
    "satisfied": [],
    "missing": [],
    "warnings": []
  }
}
```

Exit code rules:

- `completed`: exit code `0`.
- `completed_with_warnings`: exit code `0`.
- `blocked`: exit code `2`.
- `failed`: exit code `1`.

## Dependency Model

Every tool must declare its own dependency metadata in the file.

Recommended constants:

```python
TOOL_ID = "06_01_Scene_PlanA_SceneImageRebuild"
TOOL_NAME = "Scene Plan A Scene Image Rebuild"
TOOL_VERSION = "1.0.0"

REQUIRES = [
    "rebuild_shot_plan.json",
    "source_package.json",
    "confirmed_scene_srt",
    "confirmed_first_last",
    "active_image_provider_config",
]

PRODUCES = [
    "assets/variant_001/<shot_id>/<scene_mark_id>/scene_image.png",
    "reports/plan_a/06_01_Scene_PlanA_SceneImageRebuild.json",
]

SUGGESTED_PREVIOUS_TOOLS = [
    "04_02_Scene_FirstLastFrameConfirm",
    "05_01_Scene_ScenePromptRefresh",
]

SUGGESTED_NEXT_TOOLS = [
    "06_02_Scene_PlanA_SceneImageSelect",
]
```

Each tool must implement a local dependency check function:

```python
def check_dependencies(workspace: Path, args: argparse.Namespace) -> dict[str, Any]:
    ...
```

The function must return:

```json
{
  "status": "satisfied | blocked | warning",
  "satisfied": [],
  "missing": [],
  "warnings": []
}
```

Dependency checks must be actionable. A missing dependency must include:

- `dependency`: stable dependency name.
- `reason`: plain-language explanation.
- `suggested_tools`: tool IDs that can satisfy or inspect the dependency.
- `scope`: optional scope such as `shot_id` or `scene_mark_id`.

Example:

```json
{
  "dependency": "locked_tts",
  "reason": "locked.wav does not exist for shot_003 variant_001",
  "suggested_tools": [
    "07_02_Shot_PlanA_TTSGenerateAndLock"
  ],
  "scope": {
    "shot_id": "shot_003"
  }
}
```

## Dependency Runtime Behavior

Every tool must run dependency checks before doing work.

Required behavior:

- If `--check-dependencies-only` is present, only dependency checks run.
- If dependencies are satisfied, the tool runs normally.
- If dependencies are blocked and `--force` is not present, the tool returns `status: blocked` and does not mutate workspace files.
- If dependencies are blocked and `--force` is present, the tool may run only when the tool can safely handle partial inputs.
- If `--force` is used, missing dependencies must still be included in the output under `dependencies.missing`.
- If the tool cannot safely run even with `--force`, it must return `status: blocked` or `status: failed` with a clear reason.

The dependency output is part of the tool contract. Agents should use it to decide which previous tool to execute next.

## Repeatability Rules

Every V1 tool must support repeated execution.

Repeatability requirements:

- Re-running a tool with the same inputs should not corrupt workspace state.
- Existing output files may be overwritten if they are deterministic status, plan, or manifest files.
- Generated media assets should either be overwritten intentionally or versioned under a stable variant directory.
- Tools must report when they reused existing outputs.
- Tools must report when outputs were refreshed.
- Tools must not delete unrelated outputs produced by other tools unless explicitly requested by a documented argument.

## Tool List

The first `Rebuild_V1` implementation should include these standalone files.

### Existing Standalone Tools To Copy Into V1

```text
01_Rebuild_SourcePackageLoad.py
02_Rebuild_ShotPlanBuilder.py
03_1_SceneMarkBuilder.py
04_ShotPlan_AssetTaskBuilder.py
04_01_Shot_AssetTaskBuilder.py
```

These should be copied from `Rebuild`, adjusted to the V1 registry path, and updated to follow the V1 CLI and dependency-output contract.

### Phase 1 Tools

```text
03_01_ShotPlan_TTSReferenceAudioExtract.py
03_02_ShotPlan_TTSVoiceRecommend.py
03_03_ShotPlan_TTSVoiceSelectionWrite.py
03_04_ShotPlan_PreDeleteReadinessCheck.py
```

### Phase 2 Tools

```text
04_01_Shot_FirstLastFrameMark.py
04_01_ShotPlan_FirstLastFrameMark.py
04_02_Scene_FirstLastFrameConfirm.py
04_02_Shot_FirstLastFrameConfirm.py
04_02_ShotPlan_FirstLastFrameConfirm.py
04_03_ShotPlan_FirstLastReadinessCheck.py
04_04_ShotPlan_SceneAssetCanonicalize.py
05_01_Scene_ScenePromptRefresh.py
05_01_Shot_ScenePromptRefresh.py
05_01_ShotPlan_ScenePromptRefresh.py
```

### Phase 3 Tools

```text
06_01_Scene_PlanA_SceneImageRebuild.py
06_01_Shot_PlanA_SceneImageRebuild.py
06_01_ShotPlan_PlanA_SceneImageRebuild.py
06_02_Scene_PlanA_SceneImageSelect.py
06_02_Shot_PlanA_SceneImageSelect.py
06_02_ShotPlan_PlanA_SceneImageSelect.py
07_01_Shot_PlanA_TTSPromptBuild.py
07_01_ShotPlan_PlanA_TTSPromptBuild.py
07_02_Shot_PlanA_TTSGenerateAndLock.py
07_02_ShotPlan_PlanA_TTSGenerateAndLock.py
07_03_Shot_PlanA_TTSTimelineValidate.py
07_03_ShotPlan_PlanA_TTSTimelineValidate.py
08_01_Scene_PlanA_ImageSequenceClipPlan.py
08_01_Shot_PlanA_ImageSequenceClipPlan.py
08_01_ShotPlan_PlanA_ImageSequenceClipPlan.py
08_02_Shot_PlanA_HyperframeSubtitleAlign.py
08_02_ShotPlan_PlanA_HyperframeSubtitleAlign.py
08_03_Shot_PlanA_ImageSequenceCompose.py
08_03_ShotPlan_PlanA_ImageSequenceCompose.py
09_01_ShotPlan_PlanA_AssemblyBuild.py
09_02_ShotPlan_PlanA_Compose.py
09_03_ShotPlan_PlanA_QualityCheck.py
```

## Tool Dependency Expectations

### 01 Rebuild Source Package Loader

Expected dependencies:

- Analysis output directory or database-bound analysis task.
- Rebuild task Final Prompt.
- Workspace path.

Expected next tools:

- `02_Rebuild_ShotPlanBuilder`
- `03_1_SceneMarkBuilder.py` when scene mark initialization is needed.

### 02 Rebuild Shot Plan Builder

Expected dependencies:

- `source_package.json`
- `rebuild_intent.json`
- Task run model.
- Task Final Prompt.

Expected next tools:

- `03_01_ShotPlan_TTSReferenceAudioExtract`
- `03_04_ShotPlan_PreDeleteReadinessCheck`

### 03_01 ShotPlan TTS Reference Audio Extract

Expected dependencies:

- `source_package.json`
- Source media or extracted reference audio source.

Expected next tools:

- `03_02_ShotPlan_TTSVoiceRecommend`

### 03_02 ShotPlan TTS Voice Recommend

Expected dependencies:

- `rebuild_shot_plan.json`
- `tts/tts_reference_audio_manifest.json`
- Task model context when LLM recommendation is used.

Expected next tools:

- `03_03_ShotPlan_TTSVoiceSelectionWrite`

### 03_03 ShotPlan TTS Voice Selection Write

Expected dependencies:

- `rebuild_shot_plan.json`
- `tts/tts_voice_recommendations.json` or explicit CLI voice arguments.

Expected next tools:

- `03_04_ShotPlan_PreDeleteReadinessCheck`

### 03_04 ShotPlan Pre Delete Readiness Check

Expected dependencies:

- `rebuild_shot_plan.json`
- Keyframes or explicit blockers.
- TTS selection or explicit blockers.

Expected next tools:

- Human keyframe deletion/editing.
- `04_01_ShotPlan_FirstLastFrameMark`

### 04_01 First Last Frame Mark Tools

Expected dependencies:

- `rebuild_shot_plan.json`
- Saved keyframes for target shot or shot plan.

Expected next tools:

- `04_02_Scene_FirstLastFrameConfirm`
- `04_02_Shot_FirstLastFrameConfirm`
- `04_02_ShotPlan_FirstLastFrameConfirm`

### 04_02 First Last Frame Confirm Tools

Expected dependencies:

- `rebuild_shot_plan.json`
- Marked `first` and `last` keyframes for required first-last scenes.

Expected next tools:

- `04_03_ShotPlan_FirstLastReadinessCheck`
- `05_01_Scene_ScenePromptRefresh`

### 04_03 ShotPlan First Last Readiness Check

Expected dependencies:

- `rebuild_shot_plan.json`
- Confirmed first-last scene marks where required.

Expected next tools:

- `05_01_ShotPlan_ScenePromptRefresh`

### 05_01 Scene Prompt Refresh Tools

Expected dependencies:

- `rebuild_shot_plan.json`
- `source_package.json`
- Target shot and scene mark for scene-level execution.
- Rebuild task OpenCode session context.
- Run model when LLM/VLM refresh is required.

Expected next tools:

- `07_01_Shot_PlanA_TTSPromptBuild`
- `06_01_Scene_PlanA_SceneImageRebuild`

### 06_01 Scene Image Rebuild Tools

Expected dependencies:

- `rebuild_shot_plan.json`
- `source_package.json`
- Confirmed scene first/last frames where required.
- Calibrated scene SRT.
- Active image provider config.

Expected next tools:

- `06_02_Scene_PlanA_SceneImageSelect`

### 06_02 Scene Image Select Tools

Expected dependencies:

- Generated scene image candidates or existing scene image manifest.

Expected next tools:

- `08_01_Scene_PlanA_ImageSequenceClipPlan`
- `08_01_Shot_PlanA_ImageSequenceClipPlan`

### 07_01 TTS Prompt Build Tools

Expected dependencies:

- `rebuild_shot_plan.json`
- Scene SRT text for each target shot.
- TTS selection for each target shot.

Expected next tools:

- `07_02_Shot_PlanA_TTSGenerateAndLock`

### 07_02 TTS Generate And Lock Tools

Expected dependencies:

- TTS prompt package.
- TTS provider/model/voice config.
- Scene SRT text.

Expected next tools:

- `07_03_Shot_PlanA_TTSTimelineValidate`

### 07_03 TTS Timeline Validate Tools

Expected dependencies:

- Locked TTS audio.
- Shot SRT.
- Shot-level TTS timeline file or enough inputs to build it.

Expected next tools:

- `08_01_Shot_PlanA_ImageSequenceClipPlan`

### 08_01 Image Sequence Clip Plan Tools

Expected dependencies:

- Selected scene assets.
- Locked TTS audio or validated shot timeline.
- Scene SRT/shot SRT.

Expected next tools:

- `08_02_Shot_PlanA_HyperframeSubtitleAlign`

### 08_02 Hyperframe Subtitle Align Tools

Expected dependencies:

- Image sequence clip plan.
- Locked TTS audio.
- Shot SRT.

Expected next tools:

- `08_03_Shot_PlanA_ImageSequenceCompose`

### 08_03 Image Sequence Compose Tools

Expected dependencies:

- HyperFrames alignment plan.
- Selected scene assets.
- Locked TTS audio.

Expected next tools:

- `09_01_ShotPlan_PlanA_AssemblyBuild`

### 09_01 Assembly Build

Expected dependencies:

- Composed shot videos.

Expected next tools:

- `09_02_ShotPlan_PlanA_Compose`

### 09_02 Auto V0 Compose

Expected dependencies:

- Assembly plan.
- Composed shot videos.

Expected next tools:

- `09_03_ShotPlan_PlanA_QualityCheck`

### 09_03 Auto V0 Quality Check

Expected dependencies:

- Final composed video or known compose blockers.
- Shot videos or known shot blockers.

Expected next tools:

- None. This is a terminal report tool.

## Migration Phases

### Phase 0: Package Skeleton

Create:

```text
OpenCrew/ToolLibrary/Rebuild_V1/__init__.py
OpenCrew/ToolLibrary/Rebuild_V1/README.md
OpenCrew/ToolLibrary/Rebuild_V1/tool_registry.json
```

Acceptance:

- Registry exists and is valid JSON.
- Registry contains no workflow entries.
- Registry contains no legacy `Rebuild/` script paths.

### Phase 1: Existing Standalone Tool Migration

Copy and adapt existing standalone tools:

```text
01_Rebuild_SourcePackageLoad.py
02_Rebuild_ShotPlanBuilder.py
03_1_SceneMarkBuilder.py
04_ShotPlan_AssetTaskBuilder.py
04_01_Shot_AssetTaskBuilder.py
```

Acceptance:

- Each tool supports `--help`.
- Each tool supports `--check-dependencies-only`.
- Each registry entry points to the V1 path.

### Phase 2: Phase 1 Plan A Tool Split

Implement:

```text
03_01_ShotPlan_TTSReferenceAudioExtract.py
03_02_ShotPlan_TTSVoiceRecommend.py
03_03_ShotPlan_TTSVoiceSelectionWrite.py
03_04_ShotPlan_PreDeleteReadinessCheck.py
```

Acceptance:

- Tools do not import the legacy grouped file.
- Missing dependencies return `status: blocked`.
- Repeated runs preserve workspace validity.

### Phase 3: Phase 2 Tool Split

Implement first/last and scene prompt tools.

Acceptance:

- Scene-level tools require exactly one `--shot-id` and one `--scene-mark-id` where applicable.
- Shot-level tools require exactly one `--shot-id` where applicable.
- ShotPlan-level tools can loop all target shots internally.
- Dependency output names the more specific previous tool when possible.

### Phase 4: Phase 3 Media Tool Split

Implement TTS, image, clip plan, align, compose, assembly, and quality tools.

Acceptance:

- Provider config checks are explicit and actionable.
- Missing media outputs are reported with suggested previous tools.
- Tools can be run by Agent one by one without workflow entrypoints.

### Phase 5: Test Hardening

Add tests under:

```text
tests/rebuild_v1/
```

Acceptance:

- Registry and standalone execution tests pass.
- Dependency checks behave consistently.
- No test imports `Rebuild/plan_a_tools.py` for V1 validation.

## Testing Strategy

### Registry Tests

Create:

```text
tests/rebuild_v1/test_tool_registry_paths.py
```

Test cases:

- `tool_registry.json` is valid JSON.
- Every `script` path exists.
- No `script` points to `OpenCrew/ToolLibrary/Rebuild/`.
- No `script` contains `plan_a_tools.py`.
- No workflow IDs exist in V1 registry.
- Every script path starts with `OpenCrew/ToolLibrary/Rebuild_V1/`.

### Standalone CLI Tests

Create:

```text
tests/rebuild_v1/test_tools_are_standalone.py
```

Test cases:

- Every registry tool supports `python3 <script> --help`.
- Every registry tool supports `--check-dependencies-only`.
- No tool requires `--tool-id`.
- No tool exposes `--run-all`.
- No tool exposes `--workflow`.

### Dependency Check Tests

Create:

```text
tests/rebuild_v1/test_dependency_checks.py
```

Test cases:

- Missing workspace inputs return `status: blocked`.
- Missing dependency entries include `dependency`, `reason`, and `suggested_tools`.
- `--check-dependencies-only` does not mutate files.
- `--force` records dependency warnings.
- Tools provide `suggested_previous_tools` and `suggested_next_tools` where applicable.

### Tool-Level Behavior Tests

Create:

```text
tests/rebuild_v1/test_plan_a_v1_tool_levels.py
```

Test cases:

- Scene-level tools enforce scene scope.
- Shot-level tools enforce shot scope.
- ShotPlan-level tools can operate across all shots.
- Repeated execution keeps `rebuild_shot_plan.json` readable.
- Status files are written to stable paths.

### Source Independence Tests

Create:

```text
tests/rebuild_v1/test_no_legacy_imports.py
```

Test cases:

- No V1 Python file imports `plan_a_tools`.
- No V1 Python file imports from `OpenCrew.ToolLibrary.Rebuild`.
- No V1 Python file references `OpenCrew/ToolLibrary/Rebuild/plan_a_tools.py`.
- No V1 Python file imports `_common`.

## Manual Verification Commands

Run after creating the package skeleton:

```bash
python3 -m json.tool OpenCrew/ToolLibrary/Rebuild_V1/tool_registry.json
```

Run after adding each tool:

```bash
python3 OpenCrew/ToolLibrary/Rebuild_V1/<tool_file>.py --help
```

Run dependency-only checks:

```bash
python3 OpenCrew/ToolLibrary/Rebuild_V1/03_04_ShotPlan_PreDeleteReadinessCheck.py \
  --workspace /path/to/workspace \
  --check-dependencies-only \
  --print-json
```

Example scoped scene check:

```bash
python3 OpenCrew/ToolLibrary/Rebuild_V1/06_01_Scene_PlanA_SceneImageRebuild.py \
  --workspace /path/to/workspace \
  --shot-id shot_001 \
  --scene-mark-id shot_001_scene_001 \
  --check-dependencies-only \
  --print-json
```

## Acceptance Criteria

`Rebuild_V1` is acceptable when all of the following are true:

- `OpenCrew/ToolLibrary/Rebuild_V1/tool_registry.json` exists.
- `OpenCrew/ToolLibrary/Rebuild_V1/tool_registry.json` is valid JSON.
- `Rebuild_V1` registry contains no workflow entries.
- Every registry entry points to a V1 Python file.
- Every registry Python file exists.
- Every full descriptive registry tool script basename matches `<tool id>.py`, except short legacy ids `01`, `02`, `04`, and `04_1`.
- Every registry Python file supports `--help`.
- Every registry Python file supports `--check-dependencies-only`.
- Every registry Python file prints structured JSON with `--print-json`.
- No V1 tool imports the legacy grouped implementation.
- No V1 tool depends on a shared `_common.py` file.
- Every tool declares `REQUIRES`, `PRODUCES`, `SUGGESTED_PREVIOUS_TOOLS`, and `SUGGESTED_NEXT_TOOLS`.
- Every tool checks dependencies before mutation.
- Missing dependencies return `status: blocked` with actionable suggested previous tools.
- Re-running a tool is safe and does not corrupt workspace state.
- Existing `OpenCrew/ToolLibrary/Rebuild` behavior remains unchanged.

## Future Workflow Design

Workflow orchestration is intentionally deferred.

After all standalone tools are implemented and validated, Agent-managed workflow design should decide:

- Whether workflows live only in prompts/Agent logic.
- Whether workflow definitions should be data-only JSON.
- Whether optional workflow runner scripts are useful.
- How Agents should choose between legacy `Rebuild` and standalone `Rebuild_V1`.
- How Agents should use dependency output to dynamically repair or resume a workflow.
