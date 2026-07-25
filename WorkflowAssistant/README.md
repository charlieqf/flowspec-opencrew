# WorkflowAssistant

Reusable assistant layer for OpenCrew workflow pages.

This module owns the workflow-agnostic assistant backend and frontend code. Workflow-specific pages such as OpenClip should only register adapters/configuration and render the reusable drawer.

## Layout

```text
WorkflowAssistant/
  backend/workflow_assistant/   FastAPI routes, registry, context, OpenCode proxy, SSE events
  frontend/src/                 Solid drawer, API client, reducer/event helpers, styles
  docs/                         Architecture and integration notes
```
