# OpenClip Architecture

## Core rule

- 1 OpenClip Task = 1 OpenCode Session = 1 shared Workspace

## Source layout

- `backend/openclip_backend/router.py`: OpenClip backend router and workflow orchestration
- `backend/openclip_backend/repository.py`: OpenClip task, version and attempt persistence
- `backend/openclip_backend/schemas.py`: backend request payloads
- `backend/scripts/openclip_analysis_runner.py`: local stable analysis runner
- `frontend/src/OpenClipModule.jsx`: standalone frontend module
- `frontend/src/api.js`: frontend API client for OpenClip
- `frontend/src/styles.css`: OpenClip-specific styles

## Runtime flow

1. Create Task
2. Immediately bind OpenCode Session
3. Save config and parameter version
4. Generate Final Prompt in the same OpenCode Session
5. Generate Skill in the same OpenCode Session
6. Run or rerun in the same Task and Session
7. Execute local runner for stable outputs, then use the same OpenCode Session to summarize results
