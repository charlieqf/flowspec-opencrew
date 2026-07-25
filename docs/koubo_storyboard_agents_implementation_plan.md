# Koubo StoryBoard 多页面 Agent 对话实施方案

- 日期：2026-06-09
- 代码基线：`origin/main@0c1dd9f`（`Localize asset agent candidate actions`）
- 目标模块：
  - 故事版（口播）编辑页
  - ImagePlan / VideoPlan 任务弹窗
  - Composer / 合成结果排查
- 目标读者：OpenCrew / OpenClip 前后端开发与审核

## 1. 背景

资产库 Agent 已经证明了一个可复用模式：

1. 后端为某个业务页面创建专属 OpenCode session。
2. 每次用户发消息时注入当前 task 的结构化上下文。
3. OpenCode 仅作为对话与建议生成器，不直接执行文件、shell、网络或修改业务状态。
4. 前端从完整 assistant message 中解析机器可读候选块，并让用户显式点击按钮应用。
5. 所有产生成本或修改业务状态的动作走现有业务 API，而不是让 OpenCode 直接调用工具。

当前已落地的参考实现：

- `OpenClip/backend/openclip_backend/koubo_storyboard/asset_routes.py`
  - `AGENT_SETTINGS_SCHEMA = "upload_asset_library_agent_settings_0.2"`
  - `ASSET_AGENT_CHAT_DISABLED_TOOLS`
  - `ensure-session / message / events / abort / messages`
  - `sanitize_opencode_event()`、`safe_opencode_message()`、`opencode_event_has_tool_use()`
- `backend/opcrew_backend/adapters/opencode.py`
  - `OpenCodeSessionClient.prompt_async(..., system=..., tools=...)`
  - `messages()` / `abort()` / `collect_events()`
- `backend/opcrew_backend/model_policy.py`
  - `SURFACE_KOUBO_ASSET_AGENT_CHAT = "koubo.asset_library.agent_chat"`
- `OpenClip/frontend/src/UploadAssetLibrary/components/AgentPanel.jsx`
  - OpenCode event reducer
  - `<PROMPT_CANDIDATE>` 完整文本解析
  - 候选卡片按钮：`填入草稿` / `直接生成`

本方案将这个模式扩展到 StoryBoard 编辑、ImagePlan/VideoPlan 任务和 Composer 诊断。

## 2. 不做什么

1. 不做全局万能助手。
2. 不让 OpenCode 直接读写文件、执行命令、访问网络或调用业务 API。
3. 不让 Agent 自动保存 StoryBoard、自动执行 ImagePlan/VideoPlan、自动合成视频。
4. 不复制 OpenCode 完整 messages 作为业务主状态；仍只保存 OpenCode session id。
5. 不绕开现有 `kbApi.save`、`executeImagePlan`、`executeVideoPlan`、`executeComposer` 等业务入口。

## 3. 总体架构

新增一个 Koubo task-scoped Agent 层：

```text
KouboStoryBoardModule / Modal Agent UI
   |
   | ensure-session / messages / message / events / abort
   v
OpenClip backend koubo_storyboard/agent_chat_routes.py
   |
   | build_context(agent_key, task, client_context)
   | prompt_async(..., tools=KOUBO_AGENT_CHAT_DISABLED_TOOLS)
   v
OpenCode local service
   |
   | sanitized SSE events
   v
Frontend reducer parses candidate blocks
   |
   | user confirms
   v
Existing business APIs / frontend mutators
```

建议新增后端文件：

- `OpenClip/backend/openclip_backend/koubo_storyboard/agent_chat_routes.py`
- `OpenClip/backend/openclip_backend/koubo_storyboard/agent_chat_services.py`

并在：

- `OpenClip/backend/openclip_backend/koubo_storyboard/router.py`

注册：

```python
from .agent_chat_routes import register_agent_chat_routes

register_agent_chat_routes(router, deps)
```

## 4. 共享后端设计

### 4.1 Agent key

第一阶段支持 4 个 key：

| agent_key | 页面 | 作用 |
|---|---|---|
| `storyboard_edit` | 故事版编辑页 | 台词、结构、镜头命名、拆分合并建议 |
| `image_plan` | ImagePlan 弹窗 | 解释 ImagePlan 状态，优化单个 image prompt，建议执行步骤 |
| `video_plan` | VideoPlan 弹窗 | 解释 VideoPlan 状态、失败/blocked 原因，建议重跑与参数 |
| `composer` | Composer 弹窗 | 解释为什么不可合成、为什么只合成部分、建议下一步 |

### 4.2 Session 存储

资产库 Agent 继续使用既有：

```text
SessionContext/AgentSettings.json
```

新 Agent 使用独立文件，避免覆盖资产库设置：

```text
SessionContext/AgentChats/storyboard_edit.json
SessionContext/AgentChats/image_plan.json
SessionContext/AgentChats/video_plan.json
SessionContext/AgentChats/composer.json
```

这是新增机制，不是复用资产库 `AgentSettings.json` 的 `chat_opencode_session_id` 字段。P0 必须实现独立 session 文件的创建、读取、更新和兼容空文件逻辑。

文件 schema：

```json
{
  "schema_version": "koubo_storyboard_agent_chat_0.1",
  "agent_key": "storyboard_edit",
  "task_id": 78,
  "session_id": 137,
  "opencode_session_id": "ses_xxx",
  "created_at": 1780950000000,
  "updated_at": 1780950000000,
  "last_message_at": 1780950000000
}
```

### 4.3 通用 API

新增通用路由：

```text
POST /api/koubo-storyboard/tasks/{task_id}/agents/{agent_key}/chat/ensure-session
GET  /api/koubo-storyboard/tasks/{task_id}/agents/{agent_key}/chat/messages
POST /api/koubo-storyboard/tasks/{task_id}/agents/{agent_key}/chat/message
GET  /api/koubo-storyboard/tasks/{task_id}/agents/{agent_key}/chat/events
POST /api/koubo-storyboard/tasks/{task_id}/agents/{agent_key}/chat/abort
```

资产库 Agent 维持既有 `/api/koubo-storyboard/tasks/{task_id}/asset-library-agent/chat/...` 路径，不在本次迁移到通用 `/agents/{agent_key}/chat/...`，避免影响已上线入口。

`POST .../message` payload：

```json
{
  "message": "帮我把当前 scene 的台词改得更自然",
  "provider": "Max",
  "model": "Max",
  "client_context": {
    "selection": {
      "scope": "scene",
      "shot_id": "shot_001",
      "scene_id": "shot_001_scene_001",
      "dialogue_id": "shot_001_scene_001_dialogue_001"
    },
    "dirty": true,
    "focused_plan_excerpt": {}
  }
}
```

`client_context` 用于前端把当前未保存的局部编辑状态传给后端。后端仍会从 workspace 读取保存态作为基础上下文，但必须明确告诉 Agent：`client_context` 是用户当前屏幕上的最新状态。

### 4.4 OpenCode 工具禁用

从资产库 Agent 提炼通用常量与清洗函数，放到单一共享模块，例如：

```text
OpenClip/backend/openclip_backend/koubo_storyboard/agent_chat_common.py
```

资产库 Agent 和新 Agent 必须共用同一个 `KOUBO_AGENT_CHAT_DISABLED_TOOLS`、`sanitize_opencode_event()`、`safe_opencode_message()`、`opencode_event_has_tool_use()`。不要复制一份安全逻辑，避免后续字段脱敏或 tool-block 修复发生漂移。

```python
KOUBO_AGENT_CHAT_DISABLED_TOOLS = {
    "bash": False,
    "read": False,
    "glob": False,
    "grep": False,
    "edit": False,
    "write": False,
    "task": False,
    "webfetch": False,
    "websearch": False,
    "codesearch": False,
    "repo_clone": False,
    "repo_overview": False,
    "skill": False,
    "apply_patch": False,
    "question": False,
    "todowrite": False,
    "lsp": False,
    "plan_exit": False
}
```

`events` 路由沿用共享逻辑：

- 对 `message.updated` / `message.part.updated` / `message.part.delta` / `message.part.removed` / `session.status` 做字段清洗。
- 如检测到 tool event 或 tool part，立即 `abort()` 并发送 `koubo_agent.chat.tool_blocked`。
- 候选块解析仍放前端，只在完整 part/message 后解析，避免 delta 分片误判。

### 4.5 模型 surface

新增 model policy surface：

```python
SURFACE_KOUBO_STORYBOARD_EDIT_AGENT_CHAT = "koubo.storyboard.edit_agent_chat"
SURFACE_KOUBO_IMAGE_PLAN_AGENT_CHAT = "koubo.image_plan.agent_chat"
SURFACE_KOUBO_VIDEO_PLAN_AGENT_CHAT = "koubo.video_plan.agent_chat"
SURFACE_KOUBO_COMPOSER_AGENT_CHAT = "koubo.composer.agent_chat"
```

必须在 `DEFAULT_USER_MODEL_POLICY["surfaces"]` 中为这 4 个 surface 显式登记 alias 配置，第一版内容复制 `SURFACE_KOUBO_ASSET_AGENT_CHAT` 的 alias options。后续如果成本需要区分，再按 surface 调整默认模型。

原因：`provider_services.py` 当前 `resolve_model(..., surface=...)` 已支持显式 surface，但 `model_policy.py` 的普通用户 alias/hide 行为依赖 surface 表项。未登记的新 surface 会落入 raw 模型路径，不符合“普通用户只看到 alias 模型”的预期。

## 5. 前端共享设计

新增共享组件：

```text
OpenClip/frontend/src/KouboStoryBoard/components/KouboAgentDrawer.jsx
OpenClip/frontend/src/KouboStoryBoard/kouboAgentChat.js
```

`kouboAgentChat.js` 提供：

- OpenCode event reducer
- `EventSource` 生命周期
- `sendMessage / abort / loadMessages / ensureSession`
- 完整文本候选块解析

`KouboAgentDrawer.jsx` 提供：

- 右侧抽屉或弹窗式对话 UI
- agent title / contextual chips
- 候选卡片渲染
- `Stop` / `Send`
- `应用到草稿` / `打开编辑器` / `执行前确认` 等业务动作槽

不要复用 Upload Asset Library 的 `AgentPanel.jsx`。它现在强绑定生图草稿、reference images 和 asset library settings；复制会把不相关状态带入 StoryBoard。

## 6. 故事版（口播）编辑页 Agent

### 6.1 当前代码落点

页面主状态在：

- `OpenClip/frontend/src/KouboStoryBoardModule.jsx`

关键状态：

- `plan`
- `selectedShotIndex`
- `selectedDialogueId`
- `scope`
- `dirty`
- `groupingDirty`
- `editingDialogueId`
- `selectedAsset`

现有可复用编辑函数：

- `updateShotName(shotId, value)`
- `updateDialogue(dialogueId, key, value)`
- `addDialogueAfter(shotId, sceneId, dialogueId)`
- `splitScene(shotId, sceneId, dialogueId)`
- `splitShot(shotId, sceneId, dialogueId)`
- `mergeDialogueUp(shotId, sceneId, dialogueId)`
- `mergeSceneUp(shotId, sceneId)`
- `mergeShotUp(shotIndex)`
- `deleteDialogue(shotId, sceneId, dialogueId)`
- `setDialogueTalkingHead(dialogueId, isTalkingHead)`
- `savePlan()`

渲染点：

- `KouboEditorHeader` 当前负责保存、Host/Product Builder、TTS/timing 等入口。
- `ShotCard` / `DialogueCard` 是编辑粒度。
- `KouboTimeline` 管理 `all/shot/scene` scope。

### 6.2 UI 入口

建议在 `KouboEditorHeader` 右侧新增一个 Agent 图标按钮：

```text
故事版 Agent
```

点击后在 `KouboStoryBoardModule` 内打开右侧 drawer。不要占用全局右侧 `Asset Pool`，因为用户编辑时经常需要同时看素材。

Drawer 顶部显示当前上下文：

```text
当前范围：Scene / Shot / 整片
当前 Dialogue：xxx
未保存修改：是/否
```

### 6.3 上下文包

后端基础上下文：

- `task.id / session_id / title / status`
- `load_plan(task)` 保存态
- 当前 `SessionOutput/storyboard/koubo_storyboard_assets.json` 资产摘要
- `meta.source_asset_groups / uploaded_images / uploaded_videos / history_versions` 摘要

前端每次发消息附带 `client_context`：

```json
{
  "selection": {
    "scope": "scene",
    "shot_id": "shot_001",
    "scene_id": "shot_001_scene_002",
    "dialogue_id": "shot_001_scene_002_dialogue_001"
  },
  "dirty": true,
  "focused_plan_excerpt": {
    "shot": {},
    "scene": {},
    "dialogues": []
  }
}
```

`focused_plan_excerpt` 由前端从当前 `plan()` 取，不传整片大 JSON。整片场景下最多传前 20 个 dialogue 的摘要和总数。

### 6.4 System prompt

Agent 角色：

- 你是 Koubo StoryBoard 编辑助手。
- 只能基于上下文提出修改建议。
- 不要声称已经保存或执行。
- 如果给出可应用修改，必须输出 `<STORYBOARD_EDIT_CANDIDATE>` JSON。
- 所有操作必须引用现有 `shot_id / scene_id / dialogue_id`。
- 不确定时先用中文追问。

候选块：

```text
<STORYBOARD_EDIT_CANDIDATE>
{
  "title": "让这一段更像自然口播",
  "summary": "保留原意，减少书面语，分成两个短句。",
  "operations": [
    {
      "type": "replace_dialogue_text",
      "dialogue_id": "shot_001_scene_002_dialogue_001",
      "text": "..."
    },
    {
      "type": "update_shot_name",
      "shot_id": "shot_001",
      "name": "痛点引入"
    }
  ],
  "warnings": []
}
</STORYBOARD_EDIT_CANDIDATE>
```

### 6.5 V1 允许操作

第一版只允许这些低风险操作：

| type | 前端映射 |
|---|---|
| `replace_dialogue_text` | `updateDialogue(dialogue_id, "text", text)` |
| `update_dialogue_duration` | `updateDialogue(dialogue_id, "duration", seconds)` |
| `update_shot_name` | `updateShotName(shot_id, name)` |
| `add_dialogue_after` | `addDialogueAfter(...)` 后写入新 text |
| `split_scene_after_dialogue` | `splitScene(...)` |
| `split_shot_after_dialogue` | `splitShot(...)` |
| `merge_dialogue_up` | `mergeDialogueUp(...)` |
| `merge_scene_up` | `mergeSceneUp(...)` |

暂不允许：

- 删除 dialogue
- 标记口播/空镜 `set_talking_head`
- 自动保存
- 自动运行 TTS / ImagePlan / VideoPlan
- 自动绑定素材

`setDialogueTalkingHead(dialogue_id, boolean)` 当前是 async 业务动作，会在 `dirty()` 为 false 时直接调用 `kbApi.save(...)` 落库，并在 dirty 时提示“请先保存当前 StoryBoard，再标记口播/空镜”。它不能归入“应用到草稿”操作集。后续若要开放，应作为单独的“需确认并立即保存”动作处理。

### 6.6 应用策略

候选卡片按钮：

- `应用到草稿`：只改当前前端 `plan()`，设置 `dirty=true`。
- `复制建议`：复制候选 summary 或 operations 文本。
- `保存 StoryBoard`：不放在候选卡上，仍用现有 Header Save，避免用户误以为 Agent 自动保存。

应用前验证：

- 所有 id 必须存在。
- 操作数最多 20。
- 文本长度限制：单 dialogue `<= 800` 字符。
- 结构操作如果当前 plan 已变化导致目标不存在，拒绝并提示重新询问。

## 7. ImagePlan / VideoPlan 任务弹窗 Agent

### 7.1 当前代码落点

ImagePlan：

- `OpenClip/frontend/src/KouboStoryBoard/kouboStoryboardImagePlan.js`
- `OpenClip/frontend/src/KouboStoryBoard/components/KouboImagePlanModal.jsx`
- 后端：
  - `POST /api/koubo-storyboard/tasks/{task_id}/image-plan`
  - `POST /api/koubo-storyboard/tasks/{task_id}/image-plan/execute`
  - `GET /api/koubo-storyboard/tasks/{task_id}/image-plan/execution`
  - `GET /api/koubo-storyboard/tasks/{task_id}/image-plan/prompts/{asset_key}`
  - `PUT /api/koubo-storyboard/tasks/{task_id}/image-plan/prompts/{asset_key}`

VideoPlan：

- `OpenClip/frontend/src/KouboStoryBoard/kouboStoryboardVideoPlan.js`
- `OpenClip/frontend/src/KouboStoryBoard/components/KouboVideoPlanModal.jsx`
- 后端：
  - `POST /api/koubo-storyboard/tasks/{task_id}/video-plan`
  - `POST /api/koubo-storyboard/tasks/{task_id}/video-plan/execute`
  - `GET /api/koubo-storyboard/tasks/{task_id}/video-plan/execution`

### 7.2 UI 入口

在两个弹窗 header 的 `kbsp-vpm-actions` 中新增 Agent 图标：

- ImagePlan：`ImagePlan Agent`
- VideoPlan：`VideoPlan Agent`

点击后在当前 modal 右侧打开内嵌 drawer，不关闭原 modal。Agent 需要能看到用户正在看的任务列表和状态。

### 7.3 ImagePlan 上下文

后端读取：

- `SessionOutput/storyboard/image_generation_plan.json`
- `SessionOutput/storyboard/image_plan_execution_state.json`
- `SessionOutput/storyboard/image_plan_execution_result.json`
- `image_plan_artifact_status(workspace, plan)`
- `video_plan_consistency_reference_snapshot(workspace)`

前端附加：

- 当前 modal 的 `result()`
- 当前选中的 `asset_key` 或 prompt editor 状态
- 当前 `executionRunning`

### 7.4 ImagePlan 候选块

```text
<IMAGE_PLAN_CANDIDATE>
{
  "title": "优化当前人物新图 Prompt",
  "kind": "image_prompt",
  "asset_key": "shot_001_scene_002_image_001",
  "positive_prompt": "...",
  "negative_prompt": "...",
  "notes": "..."
}
</IMAGE_PLAN_CANDIDATE>
```

候选按钮：

- `打开 Prompt 编辑器`：调用现有 `openPromptEditor(task)`。
- `填入 Prompt`：把 positive/negative 写入 `KouboImagePlanModal.jsx` 内部 `promptEditor` state，不保存。
- `保存 Prompt`：调用 modal 内部 `savePromptEditor()`，它再委托 `props.api?.saveImagePlanPrompt?.(...)`，需要确认。
- `生成当前 Image`：modal 内部继续走 `props.executePlan?.("image-only", task)`；宿主 controller 的真实导出名是 `executeImagePlan(mode, targetTask)`。

落地前需要先改造 `KouboImagePlanModal.jsx`：`promptEditor`、`openPromptEditor(task)`、`savePromptEditor()` 当前都是组件局部状态/函数。Agent drawer 若不直接写在同一组件闭包里，就必须通过 props/callback 暴露 `openPromptEditor`、`fillPromptEditor`、`savePromptEditor` 这三个能力。

另一个动作候选：

```text
<IMAGE_PLAN_ACTION>
{
  "title": "先补全所有 Prompt",
  "action": "execute_image_plan",
  "mode": "prompt-only",
  "target_asset_key": "",
  "reason": "当前 8 个 image task 还没有 prompt。"
}
</IMAGE_PLAN_ACTION>
```

按钮映射：

- `execute_image_plan` → modal prop `props.executePlan(mode, task?)` → controller `executeImagePlan(mode, targetTask)`
- `refresh_image_execution` → modal prop `props.refreshExecution()` → controller `refreshImagePlanExecution()`

### 7.5 VideoPlan 上下文

后端读取：

- `SessionOutput/storyboard/video_generation_plan.json`
- `SessionOutput/storyboard/video_plan_execution_state.json`
- `SessionOutput/storyboard/video_plan_execution_result.json`
- `video_plan_artifact_status(workspace, plan)`
- `video_plan_execution_payload(workspace, plan)`
- `SessionOutput/storyboard/video_plan_settings.json`，通过 `VIDEO_PLAN_SETTINGS_REL` 读取，并用 `video_plan_settings({"settings": read_json(...)})` 归一化

前端附加：

- 当前 `target`
- 当前 `settings`
- 当前 modal `result()`
- 用户正在看的 segment id（可选，后续加点击 segment 选中）

### 7.6 VideoPlan 候选块

```text
<VIDEO_PLAN_ACTION>
{
  "title": "重新生成整片 VideoPlan",
  "action": "open_video_plan",
  "target": {"target_type": "task", "shot_id": "", "scene_id": ""},
  "settings": {"max_video_seconds": 8, "min_video_seconds": 2, "split_tolerance_seconds": 2},
  "reason": "当前计划只覆盖 scene，Composer 需要整片范围。"
}
</VIDEO_PLAN_ACTION>
```

候选按钮：

- `应用参数`：调用 `applyVideoPlanSettings(settings)`，只保存设置。
- `生成 VideoPlan`：调用 `openVideoPlan({target_type, shot_id, scene_id, force:true, action_source:"agent_candidate"})`，需要确认。
- `执行 VideoPlan`：调用 `executeVideoPlan()`，需要确认。
- `刷新状态`：调用 `refreshVideoPlanExecution()`。

注意 `openVideoPlan(targetOverride = null)` 当前只有一个参数，但会读取 `targetOverride.force` 和 `targetOverride.action_source`。它不会解析 `{target: {...}}` 这种嵌套形态；Agent action 必须把 `target_type / shot_id / scene_id / force / action_source` 放在同一个对象顶层。

VideoPlan 第一版不做直接编辑 video prompt，因为当前 UI 和后端没有对应的单 prompt 读写 API。若后续要做，需要先为 `video_generation_plan.json` 中的 segment prompt 建立类似 ImagePlan 的 `GET/PUT prompt` 接口。

## 8. Composer / 合成结果排查 Agent

### 8.1 当前代码落点

前端：

- `OpenClip/frontend/src/KouboStoryBoard/kouboStoryboardComposer.js`
- `OpenClip/frontend/src/KouboStoryBoard/components/KouboComposerModal.jsx`

后端：

- `GET /api/koubo-storyboard/tasks/{task_id}/composer/candidates`
- `POST /api/koubo-storyboard/tasks/{task_id}/composer/execute`
- `GET /api/koubo-storyboard/tasks/{task_id}/composer/execution`
- `composer_candidates_payload(task, payload)`
- `composer_execution_payload(task)`

最新远端代码已经支持：

- requested target / plan target trace
- `composer.scope_mismatch_warning`
- task target 下当前 VideoPlan 只覆盖 scene/shot 时的 CTA：`生成整片 VideoPlan`

### 8.2 UI 入口

在 `KouboComposerModal` header actions 中新增：

```text
合成诊断 Agent
```

Drawer 展示在 Composer modal 内部，不关闭候选列表和视频预览。

### 8.3 上下文

后端读取：

- `composer_candidates_payload(task, {"target": requested_target})`
- `composer_execution_payload(task)`
- `SessionOutput/storyboard/video_generation_plan.json`
- `SessionOutput/storyboard/video_plan_compose_result.json`
- `SessionOutput/storyboard/video_plan_compose_state.json`
- 当前 StoryBoard plan

前端附加：

- `requestedTarget`
- `planTarget`
- 当前 selected composer candidate
- `needsTaskVideoPlan`
- `props.state()` 当前 phase/status

### 8.4 候选块

```text
<COMPOSER_DIAGNOSIS>
{
  "title": "当前不能合成整片的原因",
  "findings": [
    {
      "severity": "warning",
      "message": "当前 VideoPlan 只覆盖 scene_003，整片候选缺 scene_001 / scene_002。",
      "evidence": ["Plan: shot_001 / scene_003", "Req: 整片"]
    }
  ],
  "next_actions": [
    {
      "label": "生成整片 VideoPlan",
      "action": "generate_task_video_plan",
      "target": {"target_type": "task", "shot_id": "", "scene_id": ""},
      "confirm": true
    }
  ]
}
</COMPOSER_DIAGNOSIS>
```

按钮映射：

| action | 前端映射 |
|---|---|
| `generate_task_video_plan` | `props.regenerateVideoPlan({target_type:"task", ...})` |
| `execute_composer` | `props.executeComposer(target)`，必须确认 |
| `select_candidate` | `setSelectedId(candidate_id)` |
| `refresh_composer_execution` | 重新调用 `kbApi.composerExecution(taskId)`，后续可通过 controller 暴露 |

Composer Agent 第一版以诊断为主，不直接修改计划。

## 9. 前端 API 扩展

在 `OpenClip/frontend/src/KouboStoryBoard/kouboStoryboardApi.js` 新增：

```js
agentChatEnsureSession: (taskId, agentKey) =>
  kbRequest(`/api/koubo-storyboard/tasks/${taskId}/agents/${agentKey}/chat/ensure-session`, { method: "POST", body: JSON.stringify({}) }),
agentChatMessages: (taskId, agentKey) =>
  kbRequest(`/api/koubo-storyboard/tasks/${taskId}/agents/${agentKey}/chat/messages`),
agentChatSendMessage: (taskId, agentKey, payload) =>
  kbRequest(`/api/koubo-storyboard/tasks/${taskId}/agents/${agentKey}/chat/message`, { method: "POST", body: JSON.stringify(payload || {}) }),
agentChatAbort: (taskId, agentKey) =>
  kbRequest(`/api/koubo-storyboard/tasks/${taskId}/agents/${agentKey}/chat/abort`, { method: "POST", body: JSON.stringify({}) }),
agentChatEventsUrl: (taskId, agentKey) =>
  new URL(`api/koubo-storyboard/tasks/${taskId}/agents/${agentKey}/chat/events`, API_BASE).toString(),
```

## 10. 事件与审计

新增 `session_events` 类型：

```text
koubo_storyboard.agent_chat.session.created
koubo_storyboard.agent_chat.message.sent
koubo_storyboard.agent_chat.tool_blocked
koubo_storyboard.agent_chat.aborted
koubo_storyboard.storyboard_agent.candidate.applied
koubo_storyboard.image_plan.agent.prompt_filled
koubo_storyboard.image_plan.agent.prompt_saved
koubo_storyboard.video_plan.agent.action_requested
koubo_storyboard.composer.agent.action_requested
```

事件 payload 必须包含：

- `task_id`
- `session_id`
- `agent_key`
- `opencode_session_id`
- `action`
- `target`
- `message_length` 或 `candidate_id`

不得包含：

- provider key
- Authorization / Cookie
- OpenCode Basic auth
- 未脱敏 provider 错误页面

当前 `KouboRuntime.add_event()` 使用 `json.dumps(payload, ensure_ascii=True)` 写入事件 payload，中文会以 Unicode escape 入库。新事件沿用该行为，不在本方案中改变存储格式；展示侧读取 payload 时按 JSON 解码后展示中文。

## 11. 权限与安全

1. 所有 Agent chat 必须使用 `KOUBO_AGENT_CHAT_DISABLED_TOOLS`。
2. 所有 OpenCode event 必须字段清洗后再发到前端。
3. 检测到工具调用必须 abort。
4. 用户可见模型必须走 `mask_prompt_models_for_role`。
5. 用户选择模型必须走 `resolve_prompt_model_for_role`，不能直接信任前端 provider/model。
6. 所有写操作和成本操作必须由用户点击业务按钮触发。
7. StoryBoard 编辑 Agent 默认只改前端草稿，不自动保存。
8. ImagePlan/VideoPlan/Composer 执行前必须二次确认，至少使用浏览器 confirm；后续可做统一确认 modal。

## 12. 实施阶段

### P0：共享 Agent Chat 基础设施

- 新增 `agent_chat_services.py`
- 新增 `agent_chat_routes.py`
- 新增 `agent_chat_common.py`，把资产库 Agent 的工具禁用常量、安全清洗、tool-use 检测提炼为单一共享实现，并让资产库 Agent 改用共享实现
- 新增 `SessionContext/AgentChats/{agent_key}.json` 独立 session 文件读写
- 新增 model policy surfaces，并在 `DEFAULT_USER_MODEL_POLICY["surfaces"]` 显式登记 alias 配置
- 新增 `kbApi.agentChat*`
- 新增通用 `KouboAgentDrawer` 与 reducer

验收：

- 4 个 agent_key 都能 ensure session / send / stream / abort / reload messages。
- OpenCode 工具调用会被 abort。
- 普通用户只看到 alias 模型。

### P1：StoryBoard 编辑 Agent

- `KouboStoryBoardModule.jsx` 增加 agent drawer 状态与入口。
- 构造 `client_context.focused_plan_excerpt`。
- 解析 `<STORYBOARD_EDIT_CANDIDATE>`。
- 实现 allowed operations validator。
- `应用到草稿` 只改前端 plan 并置 dirty。

验收：

- 可让 Agent 改写当前 dialogue 文本。
- 应用后 textarea 更新，Header 保存状态变 dirty。
- 不自动调用 `kbApi.save`。
- 目标 id 不存在时拒绝应用。

### P2：ImagePlan / VideoPlan Agent

- 在两个 modal 中加 Agent 入口。
- 先改造 `KouboImagePlanModal.jsx`，通过 props/callback 暴露 `openPromptEditor`、`fillPromptEditor`、`savePromptEditor`，再支持 prompt 候选填入现有 prompt editor。
- ImagePlan 支持通过 modal prop `executePlan("prompt-only" / "image-only")` 触发宿主 controller 的 `executeImagePlan(mode, targetTask)`。
- VideoPlan action 传参使用顶层 `{target_type, shot_id, scene_id, force, action_source}`，不使用 `{target:{...}}` 包装。
- VideoPlan 支持解释 blocked/failed 状态和生成/执行动作候选。

验收：

- Image prompt 候选能填入 Positive/Negative textarea。
- 保存 Prompt 走现有 `PUT /image-plan/prompts/{asset_key}`。
- VideoPlan action 候选不会绕过现有 `openVideoPlan / executeVideoPlan`，且 `force/action_source` 能被 `openVideoPlan(targetOverride)` 读取。

### P3：Composer 诊断 Agent

- 在 `KouboComposerModal` 中加 Agent 入口。
- 上下文包含 requested target、plan target、warnings、候选 readiness 和 execution state。
- 支持 `COMPOSER_DIAGNOSIS` 候选渲染。
- `生成整片 VideoPlan` 复用现有 `regenerateVideoPlan` CTA。

验收：

- 当 task 请求但 plan 是 scene scope 时，Agent 能解释 scope mismatch。
- 候选按钮调用现有 `regenerateVideoPlan({target_type:"task"})`。
- 不自动执行 Composer。

### P4：自动化测试与截图

- 后端 contract tests。
- UI 自动化：
  - StoryBoard Agent 应用台词候选。
  - ImagePlan Agent 填 Prompt。
  - Composer Agent 诊断 scope mismatch。
- 更新用户说明截图。

## 13. 测试计划

### 13.1 后端 contract tests

新增：

```text
backend/tests/contracts/test_koubo_storyboard_agents_chat_contract.py
```

覆盖：

- 通用 routes 存在。
- 新 surface 常量存在，且 `DEFAULT_USER_MODEL_POLICY["surfaces"]` 有对应 alias 表项。
- routes 调用 `tools=KOUBO_AGENT_CHAT_DISABLED_TOOLS`。
- `opencode_event_has_tool_use` 命中后 abort。
- `sanitize_opencode_event` 不透出未知字段。
- session 文件路径在 `SessionContext/AgentChats/` 下。
- 资产库 Agent 与新 Agent 引用同一套共享清洗函数和工具禁用常量。

### 13.2 StoryBoard operation validator tests

如果 validator 写在前端 JS，可先做文本 contract；更建议把 validator 做成纯函数：

```text
OpenClip/frontend/src/KouboStoryBoard/kouboStoryboardAgentActions.js
```

覆盖：

- replace text 成功。
- unknown dialogue id 拒绝。
- 超长文本拒绝。
- 不允许 delete 操作。
- split/merge 操作目标不存在时拒绝。

### 13.3 UI 自动化

复用之前 Asset Agent CDP 测试模式：

- mock Agent chat endpoints 和 EventSource。
- 打开 `#/koubo-storyboard/tasks/{id}`。
- 点击 StoryBoard Agent。
- 注入 `<STORYBOARD_EDIT_CANDIDATE>`。
- 点击 `应用到草稿`。
- 断言当前 textarea 更新、dirty save button 可见/启用、没有保存请求。

ImagePlan：

- 打开 ImagePlan modal。
- mock 候选 `<IMAGE_PLAN_CANDIDATE>`。
- 点击 `填入 Prompt`。
- 断言 Prompt editor textarea 更新。

Composer：

- 打开 Composer modal。
- mock `COMPOSER_DIAGNOSIS`。
- 点击 `生成整片 VideoPlan` 时拦截确认，确认 false，断言未调用真实生成。

## 14. 风险与缓解

| 风险 | 缓解 |
|---|---|
| Agent 输出 JSON 不合法 | 前端只渲染可解析候选；原文仍显示 |
| delta 分片导致解析不稳定 | 只在完整 message/part 后解析 |
| Agent 建议修改过大 | operations 数量和文本长度限制 |
| 用户误触执行成本动作 | 所有 Image/Video/Composer 执行动作二次确认 |
| 当前前端 dirty 状态和后端保存态不一致 | message payload 带 `client_context`；system prompt 明确其优先级 |
| 多 Agent session 混淆 | 每个 `agent_key` 独立 OpenCode session 文件 |
| OpenCode 工具越权 | per-prompt tools 禁用 + tool event abort + contract test |
| 现有 Asset Agent 回归 | 不改 `SessionContext/AgentSettings.json` 和既有 asset endpoints |

## 15. 审核关注点

1. StoryBoard Agent 第一版是否只允许“改草稿不保存”，还是允许候选按钮直接保存。
2. ImagePlan Agent 是否允许一键保存 Prompt，还是必须只填入 editor 由用户再点保存。
3. VideoPlan Agent 是否需要新增单 segment video prompt 编辑 API；本方案第一版不做。
4. Composer Agent 的 `execute_composer` 是否第一版开放；建议第一版只开放诊断与生成整片 VideoPlan CTA。
5. 新 Agent UI 是统一 drawer，还是每个 modal 内部局部 drawer；本方案建议使用同一组件、不同容器挂载。

## 16. 同事反馈核实结果

### 16.1 必须修正的事实性问题

| 反馈 | 最新代码核实 | 结论与处理 |
|---|---|---|
| `video_plan_settings.json` 不存在，只存在 `video_plan_settings()` | `constants.py` 已有 `VIDEO_PLAN_SETTINGS_REL = "SessionOutput/storyboard/video_plan_settings.json"`；`task_routes.py` 的 `PUT /video-plan/settings` 会 `write_json(workspace / VIDEO_PLAN_SETTINGS_REL, saved)`；`video_plan_load_services.py` 会读取该文件并调用 `video_plan_settings({"settings": read_json(...)})` 归一化 | 反馈与最新 `origin/main@0c1dd9f` 不符。文档已改为精确表述：读取 `VIDEO_PLAN_SETTINGS_REL`，并通过 `video_plan_settings()` helper 归一化 |
| ImagePlan 前端命名不准 | Controller 真实导出是 `executeImagePlan(mode, targetTask)` / `refreshImagePlanExecution()`；modal 内部 prop 名是 `executePlan` / `refreshExecution`；`promptEditor`、`openPromptEditor()`、`savePromptEditor()` 都是 `KouboImagePlanModal.jsx` 局部状态/函数 | 反馈属实。文档已补充 controller 与 modal prop 的映射，并把“暴露 prompt editor 操作能力”列为 P2 前置改造 |
| VideoPlan 候选调用形参与实际不符 | `openVideoPlan(targetOverride = null)` 只有一个参数，但当前实现会读取 `targetOverride.force` 和 `targetOverride.action_source`；`normalizeTarget()` 只识别顶层 `target_type / shot_id / scene_id`，不识别 `{target:{...}}` | 反馈部分属实。`force/action_source` 已支持，但文档的嵌套 `{target, ...}` 形态不对。已改为顶层 `{target_type, shot_id, scene_id, force, action_source}` |

### 16.2 设计矛盾

| 反馈 | 最新代码核实 | 结论与处理 |
|---|---|---|
| `set_talking_head` 与“只改草稿不保存”冲突 | `setDialogueTalkingHead()` 会在 dirty=false 时调用 `kbApi.save(...)` 立即落库；dirty=true 时拒绝并提示先保存 | 反馈属实。已从 V1 草稿操作集移除，改为后续单独的“需确认并立即保存”业务动作 |
| 安全清洗逻辑必须提炼，不能复制 | 当前 `ASSET_AGENT_CHAT_DISABLED_TOOLS`、`sanitize_opencode_event()`、`opencode_event_has_tool_use()` 只在 `asset_routes.py` 内定义 | 反馈属实。文档已改为必须新增 `agent_chat_common.py`，资产库 Agent 与新 Agent 共用同一套安全函数和禁用工具常量 |

### 16.3 补充确认点

| 反馈 | 最新代码核实 | 结论与处理 |
|---|---|---|
| 新 surface 是否需要注册映射 | `model_policy.py` 的 alias/hide 行为依赖 `DEFAULT_USER_MODEL_POLICY["surfaces"]` 表项；未登记 surface 对普通用户会落入 raw 模型路径 | 必须显式登记 4 个新 surface 的 alias 配置，并加 contract test |
| 会话持久化形态差异 | 资产库 Agent 使用 `SessionContext/AgentSettings.json` 的 `chat_opencode_session_id`；新方案用 `SessionContext/AgentChats/{agent_key}.json` | 确认为新机制。文档已把独立 session 文件读写列入 P0 |
| URL 形态不统一 | 资产库 Agent 已有 `/asset-library-agent/chat/...`；新方案使用 `/agents/{agent_key}/chat/...` | 两套并存。文档已明确资产库旧路径不迁移 |
| 事件 payload 编码 | `KouboRuntime.add_event()` 使用 `json.dumps(payload, ensure_ascii=True)` | 沿用现状。文档已注明中文入库会被转义，展示侧按 JSON 解码 |
