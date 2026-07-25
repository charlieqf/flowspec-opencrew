# Koubo Asset Library Prompt Builder 需求设计

## 1. 背景和现有位置

### 前端入口

截图中的聊天输入框位于：

- `OpenCrew/OpenClip/frontend/src/UploadAssetLibrary/components/AgentPanel.jsx`
- 样式位于 `OpenCrew/OpenClip/frontend/src/UploadAssetLibrary/styles/agent-chat.css`
- 页面装配位于 `OpenCrew/OpenClip/frontend/src/UploadAssetLibrary/UploadAssetLibraryOverlay.jsx`
- API 封装位于 `OpenCrew/OpenClip/frontend/src/KouboStoryBoard/kouboStoryboardApi.js`

当前输入框右侧已有三个工具按钮：

- 图片参考：`Load consistency reference images`
- Prompt 图标：当前只有按钮 UI，尚未绑定逻辑
- Settings：打开生成设置

本需求绑定当前 Prompt 图标，不新增第二套入口。

### 后端入口

Asset Library Agent 当前后端在：

- `OpenCrew/OpenClip/backend/openclip_backend/koubo_storyboard/asset_routes.py`

现有接口：

- `GET /api/koubo-storyboard/tasks/{task_id}/asset-library-agent/settings`
- `PUT /api/koubo-storyboard/tasks/{task_id}/asset-library-agent/settings`
- `POST /api/koubo-storyboard/tasks/{task_id}/asset-library-agent/generate/events`

当前生成接口只接收一个最终 `prompt` 字符串，并把生成审计写到：

- `SessionOutput/storyboard/assets/images/{generated_image}.json`

### Grok 模板来源

Repo 级模板位于：

- `OpenCrew/ToolLibrary/Analysis_V1/Reference/05_02/Image_Grok.md`
- `OpenCrew/ToolLibrary/Analysis_V1/Reference/05_02/Video_Grok.md`

运行时 `05_02` 的既有约定是把模板快照拷贝到当前 Session 工具目录：

- `S9_05_02_VideoPlanExecutor/Prompt/Ref_05_02_Image_Grok.md`
- `S9_05_02_VideoPlanExecutor/Prompt/Ref_05_02_Video_Grok.md`

并把渲染后的提示词写成：

- `S9_05_02_VideoPlanExecutor/Prompt/PromptRendered_{asset_key}_ImagePrompt.json`
- `S9_05_02_VideoPlanExecutor/Prompt/PromptRendered_{asset_key}_VideoPrompt.json`

其中 JSON 内已经包含 `positive_prompt`、`negative_prompt`、`prompt`、`template_blocks`、`template_source` 等字段。

### 可复用的编辑弹窗

StoryBoard ImagePlan 里已经有正向/负向 Prompt 编辑弹窗，可复用交互模式：

- `OpenCrew/OpenClip/frontend/src/KouboStoryBoard/components/KouboImagePlanModal.jsx`

它已经实现了：

- 从 Prompt JSON 中读取 `positive_prompt` / `negative_prompt`
- fallback 拆分 `Negative prompt:`
- 两个 textarea 分别编辑
- 保存时重新合成 `prompt`

本需求应复用该交互范式，但不要依赖 ImagePlan 必须先生成。

## 2. 产品目标

在 Asset Library Agent 聊天输入框增加一个 Prompt Builder 工具：

1. 当当前图片生成 Provider 是 Grok/xAI，点击 Prompt 图标后，系统自动基于 Grok md 模板生成一个可编辑 Prompt 草稿。
2. 弹窗中展示正向提示词和负向提示词，用户可直接修改。
3. 用户可选择把正向、负向或合成后的完整 Prompt 添加到聊天输入框中。
4. 用户确认后，聊天输入框仍然是唯一发送入口；Prompt Builder 只负责构建和填充文本，不直接触发生成。
5. 每次打开/应用 Prompt Builder 都要在当前 Session 留下可审计文件，方便追溯使用了哪个模板、哪些字段和用户改动。

## 3. 使用流程

### 3.1 默认流程

1. 用户进入 Asset Library Agent。
2. 用户在 Settings 中选择 Grok/xAI 图像模型，或系统当前 active image provider 是 `xai`。
3. 用户点击输入框右侧 Prompt 图标。
4. 前端请求后端构建 Prompt Builder 草稿。
5. 后端：
   - 判断 provider/model 是否属于 Grok/xAI。
   - 把 Repo 模板 `Image_Grok.md` 拷贝到当前 Session 的 Prompt Builder 目录。
   - 根据当前聊天草稿、参考图、Session 一致性参考图、可用 StoryBoard 上下文生成正向/负向提示词。
   - 返回 `positive_prompt`、`negative_prompt`、`prompt`、模板路径、渲染路径。
6. 弹窗显示两个编辑区：
   - Positive Prompt
   - Negative Prompt
7. 用户修改后点击：
   - `添加完整 Prompt`
   - 或 `只添加正向`
   - 或 `只添加负向`
8. 文本追加/替换到聊天输入框，用户再点击发送按钮生成图片。

### 3.2 非 Grok Provider 流程

如果当前 Provider 不是 `xai` 且 model 不包含 `grok`：

- Prompt 图标仍可点击。
- 弹窗提示当前 Builder 只支持 Grok。
- 提供两个动作：
  - 切换到当前可用的 Grok 图像模型
  - 继续使用普通输入框

不应自动偷偷切换 Provider，避免用户以为仍在用原模型。

## 4. Session 文件设计

新增 Asset Library Agent 专属 Prompt Builder 目录：

```text
SessionContext/PromptBuilder/
  Ref_05_02_Image_Grok.md
  Draft_{request_id}_ImagePrompt.json
  Applied_{request_id}_ImagePrompt.json
```

说明：

- `Ref_05_02_Image_Grok.md` 是从 Repo 模板复制到 Session 的快照。
- `Draft_*` 是后端第一次生成并返回给弹窗的草稿。
- `Applied_*` 是用户点击“添加到聊天”时保存的最终编辑版本。
- 该目录属于聊天辅助工具，不写入 `S9_05_02_VideoPlanExecutor/Prompt/`，避免和 05_02 正式执行链路混淆。
- 如果后续要支持视频 Prompt Builder，可同目录增加 `Ref_05_02_Video_Grok.md` 和 `*_VideoPrompt.json`。

`Draft_*` / `Applied_*` JSON 字段：

```json
{
  "schema_version": "asset_library_prompt_builder_grok_image_0.1",
  "request_id": "asset_prompt_builder_...",
  "task_id": 31,
  "session_id": 87,
  "provider": "xai",
  "model": "grok-imagine-image",
  "template_source": "Ref_05_02_Image_Grok.md",
  "template_snapshot_sha256": "...",
  "template_blocks": ["GROK_POSITIVE_BASE", "GROK_NEGATIVE_BASE", "GROK_PROMPT"],
  "source": {
    "composer_draft": "...",
    "reference_images": [],
    "consistency_references": [],
    "storyboard_context": {}
  },
  "positive_prompt": "...",
  "negative_prompt": "...",
  "prompt": "...",
  "user_edited": false,
  "created_at": "...",
  "updated_at": "..."
}
```

## 5. 后端接口设计

### 5.1 获取 Prompt Builder 草稿

新增：

```text
POST /api/koubo-storyboard/tasks/{task_id}/asset-library-agent/prompt-builder
```

请求：

```json
{
  "provider": "xai",
  "model": "grok-imagine-image",
  "draft": "用户当前聊天输入",
  "reference_images": ["SessionOutput/storyboard/assets/images/...png"],
  "mode": "image",
  "aspect": "9:16"
}
```

响应：

```json
{
  "ok": true,
  "request_id": "asset_prompt_builder_...",
  "provider": "xai",
  "model": "grok-imagine-image",
  "template_path": "SessionContext/PromptBuilder/Ref_05_02_Image_Grok.md",
  "draft_path": "SessionContext/PromptBuilder/Draft_..._ImagePrompt.json",
  "positive_prompt": "...",
  "negative_prompt": "...",
  "prompt": "...",
  "warnings": []
}
```

后端行为：

- 复用 `image_grok.py` 的模板读取/块提取/渲染逻辑，避免前后端各写一套模板 parser。
- 不读取或返回 API key。
- 只允许 Session workspace 内的参考图路径。首版白名单包括 `SessionOutput/storyboard/assets/images/`、`SessionOutput/storyboard/Working/` 中的图片，以及 `SessionContext/Consistency/` 下的人物/产品一致性参考图。
- 如果 Grok 模板缺失，返回明确错误和缺失路径。

### 5.2 保存用户应用后的 Prompt

新增：

```text
PUT /api/koubo-storyboard/tasks/{task_id}/asset-library-agent/prompt-builder/{request_id}
```

请求：

```json
{
  "positive_prompt": "...",
  "negative_prompt": "...",
  "prompt": "...",
  "apply_mode": "full"
}
```

响应：

```json
{
  "ok": true,
  "applied_path": "SessionContext/PromptBuilder/Applied_..._ImagePrompt.json",
  "prompt": "..."
}
```

说明：

- 该接口只保存用户编辑后的 Prompt Builder 结果。
- 不触发图片生成。
- 图片生成仍走现有 `/generate/events`，并继续把最终发送 prompt 写入生成图片 sidecar JSON。

## 6. 前端设计

### 6.1 AgentPanel 状态

`AgentPanel.jsx` 新增状态：

- `promptBuilderOpen`
- `promptBuilderLoading`
- `promptBuilderDraft`
- `promptBuilderError`
- `promptBuilderApplyMode`

Prompt 按钮逻辑：

- 点击时调用 `openPromptBuilder()`
- 读取当前 settings/provider/model
- 如果 `imageModelConfig` 未加载，先加载
- 把当前 `draft()` 和 `referenceAssets()` 传给后端

### 6.2 弹窗 UI

弹窗可作为新组件：

- `OpenCrew/OpenClip/frontend/src/UploadAssetLibrary/components/PromptBuilderModal.jsx`

内容：

- 顶部：`Prompt Builder`、Provider/Model、模板路径
- 两个 textarea：
  - `Positive Prompt`
  - `Negative Prompt`
- 底部动作：
  - `取消`
  - `添加正向`
  - `添加负向`
  - `添加完整 Prompt`

合成规则：

```text
{positive_prompt}

Negative prompt:
{negative_prompt}
```

添加方式建议：

- 如果聊天输入框为空：直接填入。
- 如果聊天输入框已有文本：弹窗提供 `替换当前输入` / `追加到当前输入` 二选一，默认追加。
- 追加时加两个换行，避免黏连。

### 6.3 视觉约束

- 保持当前输入框的紧凑工具条风格。
- Prompt 图标使用已有 `FlowIcon name="addNotes"`。
- 弹窗不要做大面积营销式布局；使用和一致性参考图 picker / ImagePlan Prompt editor 相近的轻量工具弹窗。
- 两个 textarea 高度固定并可滚动，避免长 prompt 撑坏界面。

## 7. Prompt 生成规则

首版只支持 `mode=image`。

生成正向 Prompt 时应组合：

1. `GROK_POSITIVE_BASE`
2. 如果已选择人物一致性图：`GROK_HOST_PRESENT`，否则 `GROK_HOST_MISSING`
3. 如果已选择产品一致性图：`GROK_PRODUCT_PRESENT`，否则 `GROK_PRODUCT_MISSING`
4. 用户当前聊天草稿作为业务目标/补充要求
5. 参考图顺序说明
6. 当前 aspect/size 说明

生成负向 Prompt 时应组合：

1. `GROK_NEGATIVE_BASE`
2. 如用户选择 product-only/cutaway 模式，再加 `GROK_NEGATIVE_CUTAWAY`
3. `GROK_PITFALLS_APPEND_ONLY`

首版可以不做完整 StoryBoard task 匹配；但应预留字段：

- `shot_id`
- `scene_id`
- `dialogue_id`
- `asset_key`

这样后续从某个 Scene/Dialogue 打开 Asset Library Agent 时，可以把具体场景上下文带进来。

## 8. 验收标准

### P0

- Grok/xAI 图像模型下，点击 Prompt 图标能打开弹窗。
- 弹窗展示正向/负向两个可编辑文本框。
- 点击 `添加完整 Prompt` 后，聊天输入框出现合成后的 Prompt。
- 点击生成后，现有图片生成流程不受影响。
- 当前 Session 中出现 `SessionContext/PromptBuilder/Ref_05_02_Image_Grok.md` 和对应 `Draft_*` / `Applied_*` 文件。
- 非 Grok provider 下，弹窗明确提示只支持 Grok，不自动切换。

### P1

- 能识别已选参考图，并在 Prompt 中写明参考图角色和顺序。
- 能识别人物/产品一致性参考图，并选择对应 Grok 模板块。
- 用户修改后的 Prompt 会写入 `Applied_*`，且 sidecar 里的最终生成 prompt 与聊天实际发送一致。

### P2

- 支持视频模式 `Video_Grok.md`。
- 支持从 StoryBoard Scene/Dialogue 上下文进入 Builder。
- 支持 Prompt 历史版本选择和回滚。

## 9. 测试用例

1. Grok image provider + 空聊天输入：
   - 打开 Builder，添加完整 Prompt，输入框被填入完整 Prompt。
2. Grok image provider + 已有聊天输入：
   - 打开 Builder，选择追加，原文本保留，新 Prompt 追加在后面。
3. 修改负向 Prompt：
   - 保存应用后，`Applied_*` 中 `negative_prompt` 和 `prompt` 同步变化。
4. 非 Grok provider：
   - 打开 Builder 只显示不支持提示，不写入 Draft 文件。
5. 模板缺失：
   - 返回缺失模板路径，不触发生成。
6. 带参考图：
   - 后端只接受 workspace 内白名单图片路径，非法路径返回 400。
7. 带人物/产品一致性参考图：
   - `SessionContext/Consistency/` 下的可用图片能进入 Prompt Builder 上下文，不因不在 Upload 资产池中被过滤。

## 10. 实现顺序建议

1. 后端新增 Prompt Builder 读取/渲染/保存接口。
2. 前端新增 `PromptBuilderModal.jsx`，绑定现有 Prompt 图标。
3. 保存 `SessionContext/PromptBuilder` 审计文件。
4. 增加前端交互测试和后端接口单测。
5. 再考虑接入视频 Prompt Builder。
