# OpenClip Integration

OpenClip integrates WorkflowAssistant as a consumer:

- Backend canonical and wrapper assistant routes are provided by `WorkflowAssistant.backend.workflow_assistant.routes`.
- `OpenClipModule.jsx` renders `WorkflowAssistantDrawer` with `workflowId="openclip_analysis"`.
- OpenClip business APIs remain in `OpenCrew/OpenClip`.
