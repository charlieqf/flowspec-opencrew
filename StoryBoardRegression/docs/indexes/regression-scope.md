# Regression Scope

Included:

- StoryBoard slot matrix and Segment Truth gold standard.
- StoryBoard / Video Plan / Image Plan / Video Only Plan / Composer requirements.
- Dialogue asset key and resource binding requirements.
- Slot color matrix documents copied from historical root `docs/` and nested `OpenCrew/docs/` sources.
- StoryBoard output structure and split/regression reports.
- Backend contract tests related to StoryBoard, slot state, key binding, and video plan behavior.
- Analysis V1 regression tests referenced by the gold standard.
- StoryBoard Node fixed reorganize tests.
- Slot-state comparison helper script.
- Real Koubo StoryBoard UI runner at `frontend/e2e/koubo-storyboard-regression.mjs`; this orchestrates eight single-purpose tests under `frontend/e2e/koubo-storyboard/` to cover key-preferred slot rendering, upload entry, real pointer drag binding, Final confirmation, talking-head/cutaway state, Merge Dialogue removal boundaries, save-reload visibility, the three plan modals, split/merge key stability, and clear isolation through deterministic API mocks.

Excluded:

- Frontend implementation source.
- Backend implementation source.
- Tool implementation source other than copied regression scripts.
- Generated test outputs, screenshots, runtime workspace data, and local logs.
- Paid real-provider calls unless explicitly enabled.

Execution note:

- Scripts in this bundle are copied for review and regression packaging.
- The executable gate is defined in `script/indexes/runbook.md`.
- Copied scripts are not standalone unless the runbook explicitly marks them runnable.
- Historical source paths in `docs/indexes/source-index.md` are provenance only; they are not instructions for the current checkout.
- UI coverage must exercise the real frontend components in a browser. Deterministic API mocks are acceptable for default UI coverage; manual evidence is still required for real workspace files, history archival, media playback, and drag/drop edge cases outside the deterministic runner.
