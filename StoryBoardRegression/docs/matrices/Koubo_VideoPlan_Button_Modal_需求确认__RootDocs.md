# 故事版（口播）VideoPlan 按钮与执行计划 Modal 需求确认

版本：v0.1

状态：已确认需求稿。本文用于指导在 `故事版（口播）` 工作台中新增 VideoPlan 生成/查看入口的产品逻辑、缓存判断、前后端边界和 UI 文件拆分方式。

## 1. 我对需求的理解

在 Task #31 的 StoryBoard Workspace 中，需要新增一个 `VideoPlan` 生成按钮。这个按钮的使用体验要和当前 Scene 声音按钮保持一致：

1. 用户点击后，系统先检查当前 workspace 中是否已经存在可复用的 `SessionOutput/storyboard/video_generation_plan.json`。
2. 如果已有 plan 与当前选择范围、当前参数、当前 StoryBoard 媒体绑定一致，则直接展示执行计划。
3. 如果已有 plan 不存在、不完整、作用域不符合、参数不一致，或 StoryBoard 中绑定的图片/音频/视频等媒体发生变化，则重新调用 `05_01_VideoPlanGenerator.py` 生成 plan。
4. 重新生成完成后，立刻打开执行计划 Modal 展示最新结果。
5. 执行计划弹窗样式参考 `docs/SessionDesign-R2/video_generation_plan_tracker.html`，图标、布局、颜色、卡片和 timeline 视觉必须保持一致。
6. Modal 必须新建独立文件，不放进 `KouboStoryBoardModule.jsx` 主文件。

这里的 “plan jason” 统一按 `plan JSON` / `video_generation_plan.json` 理解。

## 2. 入口位置

按钮放在底部 Timeline 中央控制区，在声音生成控件左侧。因为 VideoPlan 有独立弹窗，按钮旁不再新增消息区或状态文本区。

参考现有位置：

```text
OpenCrew/OpenClip/frontend/src/KouboStoryBoard/components/KouboTimeline.jsx
```

现有中心控制区：

```jsx
<div class="kbsp-timeline-audio-controls">
  播放按钮
  播放速度按钮
</div>
```

新增按钮使用与声音/倍速控件一致的尺寸、圆角、边框、浮层和视觉密度。

按钮行为：

1. 当前 `scope() === "scene"` 时，生成/查看当前选中 Scene 的 VideoPlan。
2. 当前 `scope() === "shot"` 时，生成/查看当前 Shot 的 VideoPlan。
3. 当前 `scope() === "all"` 时，生成/查看整个 ShotPlan / Task 范围的 VideoPlan。
4. 如果当前选择来自 Scene bar 点击，保持 Scene 范围。
5. 如果当前选择来自 Shot bar 点击，保持 Shot 范围。
6. 如果用户点击时间轴空白区域选择 all，则使用 Task 范围。

按钮文案/图标：

1. 第一版只显示图标，不显示文字。
2. title / aria-label：`VideoPlan`
3. 图标使用 `Workflow` 或与 tracker 顶部一致的节点图标。
4. 只有主保存按钮处于 disabled 状态时，VideoPlan 按钮才 enable。也就是当前 StoryBoard 没有未保存改动时才允许生成/查看 VideoPlan。
5. 如果当前 StoryBoard dirty、保存按钮可点击，则 VideoPlan 按钮 disabled，title 提示先保存。
6. 生成中状态：按钮 disabled，显示 loading/旋转状态。
7. 必须防止并发点击：`checking` / `generating` 期间禁用 VideoPlan 按钮和参数 Apply。
8. 可复用状态：点击后不显示生成中，直接打开 Modal。
9. 生成失败不在按钮旁显示消息，统一走全局 error banner。

## 3. 作用域映射

前端当前选择需要映射为 `05_01` 的目标参数。

### 3.1 Scene 范围

触发条件：

```text
scope() === "scene"
selectedDialogueId() 能定位到所在 scene
```

调用参数：

```text
--target-type scene
--shot-id <current_shot_id>
--scene-id <current_scene_id>
```

复用判断：

1. plan 的 `target.target_type` 必须是 `scene`。
2. plan 的 `target.shot_id` 必须等于当前 shot id。
3. plan 的 `target.scene_id` 必须等于当前 scene id。
4. plan 中只允许展示该 scene 范围内的 segment。
5. plan 的输入指纹必须匹配当前 scene 的结构和媒体绑定指纹。

### 3.2 Shot 范围

触发条件：

```text
scope() === "shot"
selectedShotIndex() 指向当前 shot
```

调用参数：

```text
--target-type shot
--shot-id <current_shot_id>
```

复用判断：

1. plan 的 `target.target_type` 必须是 `shot`。
2. plan 的 `target.shot_id` 必须等于当前 shot id。
3. plan 的 `target.scene_id` 应为空。
4. plan 中只允许展示该 shot 下的 scenes / segments。
5. plan 的输入指纹必须匹配当前 shot 的结构和媒体绑定指纹。

### 3.3 Task / ShotPlan 范围

触发条件：

```text
scope() === "all"
```

调用参数：

```text
--target-type task
```

复用判断：

1. plan 的 `target.target_type` 必须是 `task`。
2. plan 的 `target.shot_id` 和 `target.scene_id` 应为空。
3. plan 的 summary 应能覆盖当前完整 ShotPlan。
4. plan 的输入指纹必须匹配当前完整 StoryBoard 结构和媒体绑定指纹。

## 4. 一致性判断标准

VideoPlan 是否“符合当前选择”，不能只看文件存在，也不能只看 `target` 字段。必须同时比较：

1. 目标范围。
2. 生成参数。
3. StoryBoard 结构。
4. 当前绑定媒体。
5. 计划来源的 StoryBoard 文件版本或内容 hash。

### 4.1 目标范围一致

比较字段：

```json
{
  "target": {
    "target_type": "scene|shot|task",
    "shot_id": "...",
    "scene_id": "..."
  }
}
```

### 4.2 参数一致

比较字段：

```json
{
  "settings": {
    "max_video_seconds": 4.0,
    "min_video_seconds": 4.0,
    "split_tolerance_seconds": 1.0
  }
}
```

第一版就在 UI 中暴露这些参数，交互样式参考声音生成控件的倍速弹窗：点击 VideoPlan 控件旁的参数入口后出现紧凑 popover，包含数字输入和 Apply / Cancel 操作。按钮点击时使用当前 UI 参数计算 signature。

```text
max_video_seconds = 4.0
min_video_seconds = 4.0
split_tolerance_seconds = 1.0
```

参数保存规则：

1. Apply 后更新前端 VideoPlan 配置状态。
2. Apply 不单独写文件、不单独调用保存 API。
3. 下一次生成 plan 时，把这些配置作为 `05_01` 参数传入，并写回 `video_generation_plan.json -> settings`。
4. 与 UI 缓存相关的参数 signature 写入 `video_generation_plan.ui_cache.json`。
5. 不新增其它前端配置文件。

### 4.3 StoryBoard 结构一致

纳入指纹的字段建议包括：

1. `shot_id`
2. `scene_id`
3. `dialogue_id`
4. `srt_id`
5. dialogue text
6. start / end / duration
7. dialogue 顺序
8. scene 顺序
9. shot 顺序

### 4.4 绑定媒体一致

这是本需求的关键点：如果绑定媒体变化，plan 必须重新生成。

纳入指纹的媒体字段建议包括：

1. Scene 级 `working_assets.audio.path`
2. Scene 级 `working_assets.images[].path`
3. Scene 级 `working_assets.video.path`
4. Dialogue 级 `working_assets.audio.path`
5. Dialogue 级 `working_assets.images[].path`
6. Dialogue 级 `working_assets.video.path`
7. `dialogue.bound_image_path`
8. `dialogue.image_path`
9. `dialogue.source_image_paths[]`
10. `asset_key`
11. `dialogue_asset_key`

只要上述任何路径、slot、source_type 变化，就视为当前 plan 过期。第一版只看 path / slot / source_type，不读取媒体文件内容 hash，也不比较同路径文件覆盖后的 size / mtime。

### 4.5 UI 缓存 sidecar 字段

为了不污染 `05_01` 原有 plan schema，UI 缓存字段单独写入 sidecar：

```json
{
  "ui_cache": {
    "scope_signature": "sha256...",
    "parameter_signature": "sha256...",
    "storyboard_structure_signature": "sha256...",
    "media_binding_signature": "sha256...",
    "source": "koubo_storyboard_video_plan_button",
    "created_from_task_id": 31,
    "created_from_session_id": 87
  }
}
```

sidecar 路径：

```text
SessionOutput/storyboard/video_generation_plan.ui_cache.json
```

`video_generation_plan.json` 仍只保存 `05_01` 原始 plan 内容和 `settings`。`video_generation_plan.ui_cache.json` 是前端判断缓存、Modal 展示 `cache_hit/regenerated/reason`、以及后续复用 plan 的唯一 UI 缓存依据。

## 5. 文件存在时的处理流程

按钮点击后的标准流程：

```text
1. 从当前 UI 选择计算 target。
2. 从当前 UI plan 结构计算 current_signature。
3. 请求后端检查 SessionOutput/storyboard/video_generation_plan.json。
4. 如果 plan 存在且 target/settings/signature 均一致：
   4.1 返回 cache_hit=true。
   4.2 前端打开 VideoPlan Modal。
5. 如果 plan 不存在或不一致：
   5.1 后端调用 05_01_VideoPlanGenerator.py。
   5.2 默认强制重跑，先恢复到 05_01 的最开始状态。
   5.3 删除整个 S8_05_01_VideoPlanGenerator 工具目录。
   5.4 删除 SessionOutput/storyboard/video_generation_plan.json。
   5.5 重新生成 SessionOutput/storyboard/video_generation_plan.json。
   5.6 把前端当前配置写回新 plan 的 settings。
   5.7 写入 SessionOutput/storyboard/video_generation_plan.ui_cache.json。
   5.8 返回 cache_hit=false 与新 plan。
   5.9 前端打开 VideoPlan Modal。
```

失败处理：

1. 强制重跑前不会保留旧 plan。
2. 如果 `05_01` 失败，`video_generation_plan.json` 可以不存在，Modal 可以为空。
3. 失败原因通过全局 error banner 展示。
4. 不恢复旧 plan，避免产生多份状态和难以管理的历史文件。

不一致原因需要返回给前端，至少包括：

```text
missing_plan
target_mismatch
settings_mismatch
storyboard_structure_changed
media_binding_changed
invalid_plan_schema
plan_scope_not_covering_current_selection
```

## 6. 后端/API 需求

当前 `kbApi` 已经有：

```text
detail
save
streamCompareAssetTTS
rawFileUrl
```

VideoPlan 建议新增 API，不复用 TTS streaming endpoint。

### 6.1 检查或生成接口

建议：

```text
POST /api/koubo-storyboard/tasks/{task_id}/video-plan
```

请求体：

```json
{
  "target_type": "scene",
  "shot_id": "shot_001",
  "scene_id": "scene_001",
  "settings": {
    "max_video_seconds": 4.0,
    "min_video_seconds": 4.0,
    "split_tolerance_seconds": 1.0
  },
  "current_signature": {
    "scope_signature": "sha256...",
    "parameter_signature": "sha256...",
    "storyboard_structure_signature": "sha256...",
    "media_binding_signature": "sha256..."
  },
  "force": false
}
```

响应：

```json
{
  "status": "ready",
  "cache_hit": true,
  "regenerated": false,
  "reason": "",
  "plan_path": "SessionOutput/storyboard/video_generation_plan.json",
  "ui_cache_path": "SessionOutput/storyboard/video_generation_plan.ui_cache.json",
  "plan": {},
  "ui_cache": {
    "scope_signature": "sha256...",
    "parameter_signature": "sha256...",
    "storyboard_structure_signature": "sha256...",
    "media_binding_signature": "sha256..."
  }
}
```

### 6.2 强制重建接口

复用同一接口。只要现有 plan 与当前 target/settings/signature 不一致，默认就是强制重建；不需要用户额外确认。

```json
{
  "force": true
}
```

后端行为：

1. 调用 `OpenCrew/ToolLibrary/Analysis_V1/05_01_VideoPlanGenerator.py`。
2. 根据 target 传入 `--target-type`、`--shot-id`、`--scene-id`。
3. 使用 `--force` 或等效清理策略。
4. 每次真实运行前必须恢复 05_01 初始状态：
   - 删除整个 `S8_05_01_VideoPlanGenerator/`
   - 删除 `SessionOutput/storyboard/video_generation_plan.json`
   - 删除 `SessionOutput/storyboard/video_generation_plan.ui_cache.json`
5. 不删除 `srt_storyboard.json`、StoryBoard assets、Working 媒体文件、用户手动上传素材或其它工具目录。
6. 成功后读取 `SessionOutput/storyboard/video_generation_plan.json` 返回。
7. 返回前把前端配置写回 plan 的 `settings`，并把缓存指纹写入 `video_generation_plan.ui_cache.json`。
8. 后端必须基于已保存到磁盘的 StoryBoard JSON 和文件路径重新计算 authoritative signature；前端传入的 `current_signature` 只作为调试/对照信息。
9. 当前功能不是为多用户并发设计，但必须做单用户防连点保护：同一 task/session 同一时间只允许一个 VideoPlan 检查/生成请求执行。

### 6.3 直接读取接口

如果前端只需要打开现有 plan，可继续使用：

```text
kbApi.rawFileUrl(sessionId, "SessionOutput/storyboard/video_generation_plan.json")
```

但正式按钮流程不建议只用 raw file，因为 raw file 无法完成“是否符合当前选择”的判断。

## 7. 前端拆分文件

必须新建独立 Modal 文件，不放入主文件。

建议新增：

```text
OpenCrew/OpenClip/frontend/src/KouboStoryBoard/components/KouboVideoPlanModal.jsx
OpenCrew/OpenClip/frontend/src/KouboStoryBoard/kouboStoryboardVideoPlan.js
OpenCrew/OpenClip/frontend/src/KouboStoryBoard/styles/video-plan-modal.css
```

### 7.1 `KouboVideoPlanModal.jsx`

职责：

1. 只负责展示 `video_generation_plan.json`。
2. 使用 `docs/SessionDesign-R2/video_generation_plan_tracker.html` 的 modal 风格。
3. 图标、布局、颜色、header、timeline、segment card、pipeline badge 要完全一致。
4. 展示 shot/scene/segment 层级。
5. 展示顶部 Audio / Image / Video / Lip Sync 进度。
6. 支持 dark/light 切换。
7. 支持关闭。
8. 必须展示 `cache_hit` / `regenerated` / `reason` 状态，但不改变整体布局。
9. Modal 默认展示 plan；进入执行追踪版后，pipeline badge 支持查看产物详情，Sync badge 支持右键设置空镜，见第 15 节。
10. 右上角 X 只关闭 Modal；前端配置保存回 JSON 由参数 Apply / plan 生成 API 负责，不由 X 触发。
11. 如果 `05_01` 返回 completed_with_blocked_items 或 completed_with_skipped_items，Modal 仍正常打开，并在顶部说明 blocked/skipped 情况；这不是 API failed。
12. 如果 API/工具真正失败且没有 plan，Modal 可以为空或不打开，错误走全局 error banner。

Modal 不负责调用 `05_01`。

### 7.2 `kouboStoryboardVideoPlan.js`

职责：

1. 计算当前 target。
2. 计算当前 StoryBoard structure signature。
3. 计算当前 media binding signature。
4. 调用后端检查/生成接口。
5. 管理 `videoPlanState`。
6. 返回给主模块一个 controller，类似 `createKouboStoryboardTtsController`。
7. 只有 `dirty() === false` 时允许 `openVideoPlan`；dirty 时直接阻止并提示先保存。
8. `checking` / `generating` 期间必须忽略重复点击。

建议导出：

```js
export function createKouboStoryboardVideoPlanController(options) {
  return {
    videoPlanModalOpen,
    setVideoPlanModalOpen,
    videoPlanState,
    ensureVideoPlan,
    openVideoPlan,
    forceRegenerateVideoPlan,
  };
}
```

### 7.3 `video-plan-modal.css`

职责：

1. 只放 VideoPlan Modal 相关样式。
2. 不污染 `KouboTimeline`、`ShotCard`、`DialogueCard`。
3. 类名前缀建议统一为：

```text
kbsp-video-plan-
```

## 8. 主模块接入点

`KouboStoryBoardModule.jsx` 只做组合，不承载 Modal 细节。

新增状态和 controller：

```jsx
const {
  videoPlanModalOpen,
  setVideoPlanModalOpen,
  videoPlanState,
  openVideoPlan,
  forceRegenerateVideoPlan,
} = createKouboStoryboardVideoPlanController({
  kbApi,
  task,
  state,
  plan,
  shots,
  scope,
  selectedShotIndex,
  selectedDialogueId,
  sessionId,
  runAction,
});
```

传给 timeline：

```jsx
<KouboTimeline
  ...
  openVideoPlan={openVideoPlan}
  videoPlanPhase={() => videoPlanState().phase}
/>
```

渲染 Modal：

```jsx
<KouboVideoPlanModal
  open={videoPlanModalOpen}
  setOpen={setVideoPlanModalOpen}
  state={videoPlanState}
  forceRegenerate={forceRegenerateVideoPlan}
/>
```

## 9. UI 样式要求

Modal 必须对齐：

```text
docs/SessionDesign-R2/video_generation_plan_tracker.html
```

实现要求：

1. 该 HTML 只作为样式和交互参考。
2. Solid Modal 组件必须接收 API 返回的真实 plan 数据。
3. 不允许复制 tracker HTML 中内嵌的示例 plan JSON 作为运行数据。

具体要求：

1. 外层深色半透明 backdrop。
2. 1152px 左右宽度、85vh 高度、16px 圆角。
3. 白色 header，左侧 `Workflow` 图标 + `Video Generation Plan` 标题。
4. header 下方显示 `4 Shots / 12 Scenes / 19 Segments`。
5. 顶部进度按顺序显示：
   - Mic / Audio
   - Image / Image
   - Clapperboard / Video
   - AudioLines / Lip Sync
6. 右上角显示 Moon/Sun 和 X。
7. 主体为 `SHOT 001` sticky header。
8. Scene 使用左侧竖线和圆点 marker。
9. Segment card 使用白底、浅边框、12px 圆角。
10. 第一张或当前选中卡片可使用 indigo 边框焦点态。
11. Pipeline badge 顺序固定：
    - Audio Gen
    - First Frame
    - Render Video
    - Lip Sync
12. 非必需任务使用灰色 disabled 风格。
13. 已计划但未完成任务显示 amber dot。
14. 已完成任务可显示 green dot。

图标必须和 tracker HTML / PlanModal 语义一致，不能用任意替代图标。

## 10. 与声音按钮的一致性

声音按钮当前逻辑特征：

1. 先根据当前 Scene 和 TTS 设置生成 config key。
2. 如果已有 locked manifest 且 config key、文本、音频文件都匹配，则复用。
3. 如果缓存不匹配或音频不存在，则重新生成。
4. 生成结果写回 `working_assets.audio`，并更新 UI state。
5. UI 按 phase 显示：
   - idle
   - generating
   - ready
   - playing
   - paused
   - error

VideoPlan 按钮应采用同样思想：

1. 先算当前 target + settings + StoryBoard/media signature。
2. 如果已有 plan 与 signature 一致，则复用。
3. 如果不一致，则重新生成。
4. UI phase 建议：
   - idle
   - checking
   - generating
   - ready
   - error
5. 失败时不打开空 modal，统一通过全局 error banner 显示错误。

本需求已确认：VideoPlan 失败时只使用全局 error banner，不在底部 Timeline 中央控制区新增消息区。

## 11. 过期判断示例

### 11.1 当前 Scene 绑定图片变化

用户把 `scene_001` 中某个 dialogue 的 `Image_01` 换成新上传图片。

期望：

1. `media_binding_signature` 改变。
2. 点击 VideoPlan。
3. 旧 plan 判定为 `media_binding_changed`。
4. 后端重新运行：

```text
05_01_VideoPlanGenerator.py --target-type scene --shot-id shot_001 --scene-id scene_001 --force
```

5. Modal 展示新 plan。

### 11.2 当前选择从 Scene 切到 Shot

已有 plan 是：

```json
{"target": {"target_type": "scene", "shot_id": "shot_001", "scene_id": "scene_001"}}
```

用户切到 Shot 001 后点击 VideoPlan。

期望：

1. 判定 `target_mismatch`。
2. 重新运行：

```text
05_01_VideoPlanGenerator.py --target-type shot --shot-id shot_001 --force
```

3. Modal 展示 Shot 范围 plan。

确认规则：已有上层或下层 plan 都不跨 scope 复用。Scene -> Shot、Shot -> Scene、Task -> Scene、Task -> Shot 均判定为 `target_mismatch` 并重新运行。

### 11.3 当前选择为 all

已有 Scene plan 或 Shot plan 均不能复用为 Task plan。

期望：

```text
05_01_VideoPlanGenerator.py --target-type task --force
```

确认规则：已有 Task plan 也不能截取给 Scene 或 Shot 使用；已有 Shot plan 也不能截取给 Scene 使用。当前选择是什么 scope，就必须生成同 scope 的 plan。

## 12. 不做事项

第一版不做：

1. 不执行 `05_02_VideoPlanExecutor.py`。
2. 不直接生成图片/视频/口型同步。
3. 不在 Modal 中编辑 prompt。
4. 不把 Modal 代码写入 `KouboStoryBoardModule.jsx`。
5. 不把 plan 判断只建立在文件存在上。
6. 不在前端直接拼 shell 命令。
7. 不把 API key、provider secret 或 DB secret 写入 plan。

## 13. 验收标准

### 13.1 功能验收

1. Scene 选择下点击 VideoPlan，得到 Scene 范围 plan。
2. Shot 选择下点击 VideoPlan，得到 Shot 范围 plan。
3. All 选择下点击 VideoPlan，得到 Task 范围 plan。
4. 已有 plan 符合当前 target/settings/signature 时，不重新运行 `05_01`。
5. 已有 plan 不符合当前 target 时，重新运行 `05_01`。
6. 已有 plan 不符合当前媒体绑定时，重新运行 `05_01`。
7. 重新生成完成后自动打开 Modal。
8. 生成失败时展示错误，不打开空 Modal。
9. 每次真实运行 `05_01` 前，整个 `S8_05_01_VideoPlanGenerator/`、`SessionOutput/storyboard/video_generation_plan.json` 和 `SessionOutput/storyboard/video_generation_plan.ui_cache.json` 都已被清理。
10. UI 暴露的 VideoPlan 参数会写回新 plan 的 `settings`。
11. `ui_cache` 会写入 `SessionOutput/storyboard/video_generation_plan.ui_cache.json`，不写入 plan 主 JSON。
12. dirty 状态下 VideoPlan 按钮 disabled；只有保存按钮 disabled 时 VideoPlan 按钮 enable。
13. `checking` / `generating` 期间重复点击不会发起第二个请求。
14. `05_01` 生成 blocked/skipped plan 时 Modal 正常展示并说明情况。

### 13.2 UI 验收

1. 弹窗视觉与 `docs/SessionDesign-R2/video_generation_plan_tracker.html` 一致。
2. 图标与 tracker / PlanModal 语义一致。
3. Timeline 中新增按钮位于声音生成控件左侧，与现有声音/倍速控件视觉一致。
4. Modal 在 1224x600 视口下不出现文字重叠。
5. Modal 在窄屏下仍能滚动查看。
6. Modal 顶部展示 `cache_hit` / `regenerated` / `reason`。
7. Modal 默认不提供任意完成态编辑；只允许通过受控交互查看产物，或在 Sync 上右键设置/恢复空镜。
8. API 失败后 Modal 允许为空或不打开，但全局 error banner 必须展示错误。

### 13.3 代码结构验收

1. 新增独立 Modal 文件。
2. 新增独立 VideoPlan controller 文件。
3. 新增独立 CSS 文件或至少独立样式模块。
4. `KouboStoryBoardModule.jsx` 只负责接线。
5. 不破坏现有 TTS 按钮和 timeline 播放逻辑。

## 14. 已确认口径

以下口径已确认，后续实现不再二选一：

1. 按钮位置：底部 Timeline 中央控制区，在声音生成控件左侧。
2. 因为有独立 Modal，不新增按钮旁消息区。
3. 第一版按钮只显示图标。
4. VideoPlan 参数在 UI 暴露，交互参考声音生成控件的倍速 popover。
5. `settings` 写回同一个 `video_generation_plan.json`；`ui_cache` 单独写入 `video_generation_plan.ui_cache.json`，不影响 05_01 原有 plan schema。
6. StoryBoard 结构字段全部纳入 hash。
7. 绑定媒体字段全部纳入 hash；第一版只看 path / slot / source_type，不看文件内容 hash、size 或 mtime。
8. 通过后端 API 调用，不在前端直接跑脚本。
9. 不一致时默认强制重跑。
10. 每次真实运行 `05_01` 前都恢复到 05_01 初始状态，删除整个 `S8_05_01_VideoPlanGenerator/`，并删除 `SessionOutput/storyboard/video_generation_plan.json` 与 `video_generation_plan.ui_cache.json`。
11. Modal 第一版只展示 plan，不提供本地执行追踪编辑。
12. 右上角 X 只关闭 Modal。
13. 前端参数配置必须保存回 plan JSON 的 `settings`；缓存状态写入 sidecar。
14. 第一版不执行 `05_02_VideoPlanExecutor.py`。
15. Modal 必须独立文件，不放进 `KouboStoryBoardModule.jsx`。
16. 必须拆独立 controller。
17. 必须拆独立 CSS。
18. Modal 样式完全使用已确认的 `docs/SessionDesign-R2/video_generation_plan_tracker.html` 风格。
19. 上层/下层 plan 不跨 scope 复用；当前选择 scope 不一致时一律重跑。
20. 错误统一走全局 error banner。
21. Modal 必须展示 `cache_hit` / `regenerated` / `reason`。
22. 只有保存按钮 disabled、当前 StoryBoard 无未保存改动时，VideoPlan 按钮才 enable。
23. 不恢复旧 plan；失败后旧 plan 没有，Modal 空即可。
24. 后端基于落盘 StoryBoard 重新计算 authoritative signature；保存按钮 disabled 后，前后端状态一致问题视为已解决。
25. 必须防止单用户重复点击造成并发请求。
26. 05_01 返回 blocked/skipped plan 时，Modal 说明情况，不当作 API 失败。
27. tracker HTML 只可作样式参考，不复制内嵌示例 JSON。

## 15. Segment 执行态、产物查看与空镜标记

本节是 VideoPlan Modal 的执行追踪补充需求，用于把 `05_02` 执行过程、产物查看和人工空镜判定接入同一个计划面板。核心原则是：界面上的状态可以来自 plan / execution result，但“是否口播、是否需要 Sync”的人工判断必须写回 Dialogue，不能只写在 plan 或 UI cache 中。

### 15.1 Pipeline 状态规则

每个 Segment 的 pipeline 仍按以下顺序展示：

```text
AUDIO -> First Frame -> VIDEO -> SYNC
```

状态颜色和 TTS 生成控件保持一致，但必须比单纯的 plan 状态更精确。Modal 展示状态时优先使用后端返回的真实 `artifact_status`，不能只根据 `tasks.need_*` 或 path 字符串推断完成。

标准状态表示：

1. 绿色区域：该步骤的规范产物已经真实存在于 `SessionOutput/storyboard/Working/` 中，且文件存在、非空。绿色区域表示“已落位 Working”，不是“plan 中有路径”。
2. 白色区域 + 黄色小点：该步骤需要生成，尚未生成到 Working。用于 Audio / First Frame / Video / Sync 的待生成状态。
3. 白色区域 + 绿色小点：该步骤已有可用来源素材，但还没有拷贝 / 重命名到规范 Working 路径。用于原图 materialize、绑定视频拷贝等待拷贝状态。
4. 黄色区域：该步骤正在运行或生成中，颜色和 TTS 生成中的黄色状态一致，可显示旋转 loading。
5. 灰色区域：该步骤明确不执行，例如绑定视频不执行 First Frame、绑定视频不执行 Sync、人工空镜不执行 Sync、blocked segment 或其它跳过态。
6. 错误态：生成或拷贝失败，点击或悬停可查看失败原因。

关键判定规则：

1. “有了的素材”如果已经在 Working 中，显示绿色区域，虽然不可编辑 / disabled，但不能显示成灰色。
2. “有了的素材”如果只在上传区、原始素材区或旧路径中，还没有拷贝到规范 Working 路径，显示白底绿色小点，表示待拷贝。
3. 绑定视频 Segment 的 Video 步骤不显示黄色待生成；只要源视频存在但 Working 规范路径还不存在，就显示白底绿色小点。
4. 绑定视频拷贝完成后，Video 绿色小点移除，整个 Video badge 变成绿色区域。
5. First Frame 的原图 materialize 与绑定视频拷贝使用同一套语义：绿点表示待拷贝，绿色区域表示已在 Working。
6. 黄色小点只用于“模型或工具还需要生成”的步骤，不能用于“已有素材等待拷贝”的步骤。
7. 灰色只表示“不执行 / 跳过 / blocked”，不能表示“已有素材”。
8. 如果 execution result 与当前 plan 不匹配，不能用旧 execution result 给当前 plan 染成绿色，只能作为旧结果查看。

计数规则：

1. 顶部 Dashboard 的 Audio / Frame / Video / Sync 计数应按已完成数 / 需要执行数展示。
2. `skipped_by_cutaway` 不计入 Sync 的待执行数，也不算失败。
3. 如果旧 plan 没有执行态字段，Modal 按 `planned` 展示。

### 15.2 First Frame 查看规则

如果 Segment 有 First Frame 任务：

1. 运行 First Frame 时，First Frame badge 显示黄色并旋转。
2. First Frame 生成完成后，badge 显示绿色。
3. 点击绿色 First Frame badge，打开产物查看层。
4. 查看层至少展示：
   - 生成图片。
   - 使用的图片 prompt。
   - 图片路径。
   - 对应的 dialogue id / segment id。
5. 如果 First Frame 只是原图 materialize 或用户上传图，也允许点击查看图片，并说明来源不是模型生成。

### 15.3 Video 查看规则

Video badge 的行为与 First Frame 一致：

1. 运行中显示黄色旋转。
2. 生成完成显示绿色。
3. 点击绿色 Video badge，打开视频查看层。
4. 查看层至少展示：
   - 最终视频。
   - 视频 prompt。
   - 视频路径。
   - raw video 是否存在、最终视频是否经过 Sync。
5. raw video 只能作为调试信息展示，不能在 StoryBoard Working 中当成最终视频。

### 15.4 Sync 查看规则

Sync badge 的普通点击行为：

1. 运行中显示黄色旋转。
2. 生成完成显示绿色。
3. 点击绿色 Sync badge，打开 Sync 结果查看层。
4. 查看层至少展示：
   - 最终对嘴视频或最终视频。
   - Segment 合成音频。
   - Sync request / response 审计路径。
   - `need_lipsync`、`lipsync_reason` 和 decision source。

### 15.5 右键 Sync 设置空镜

Sync badge 支持右键菜单，第一版至少提供：

```text
设置空镜
```

交互流程：

1. 用户右键某个 Segment 的 Sync badge。
2. 弹出菜单，点击 `设置空镜`。
3. 打开空镜确认弹窗。
4. 弹窗快速展示这个 Segment 的原始帧，用来判断画面是不是人口播。
5. 弹窗同时展示 dialogue id、Segment id、原始帧路径和对白文本。
6. 用户点击 `确定空镜` 后，该 Segment 的 Sync 立即显示为 `skipped_by_cutaway`。
7. 用户取消时不写入任何状态。

空镜确认弹窗只用于人工快速确认，不调用视觉模型，不自动判断是否有人脸。

### 15.6 Dialogue 写回合同

空镜标记必须写到 Dialogue 上，不能只写到：

1. `video_generation_plan.json`
2. `video_generation_plan.ui_cache.json`
3. Modal 前端状态
4. `05_02` execution result

推荐写入字段：

```json
{
  "dialogue": {
    "video_plan": {
      "is_talking_head": false,
      "lipsync_override": "skip_cutaway",
      "lipsync_override_source": "ui_sync_context_menu",
      "lipsync_override_reason": "user_marked_cutaway",
      "lipsync_override_updated_at": "2026-05-31T00:00:00Z"
    }
  }
}
```

语义：

1. `is_talking_head=true`：明确是口播，`05_01` 默认规划 Sync。
2. `is_talking_head=false`：明确是空镜或非口播，`05_01` 不规划 Sync。
3. 字段缺失、为 `null` 或无法读取：按兼容策略当作需要执行 Sync。
4. `lipsync_override=skip_cutaway`：说明这是用户从 Sync 右键菜单设置的跳过原因。
5. 如果同一个 Segment 覆盖多个 Dialogue，第一版优先写到该 Segment 中“有图片 / First Frame 来源”的第一个 Dialogue；如果没有任何 Dialogue 带图片或 First Frame 来源，则写到该 Segment 的第一个 Dialogue。后续如需要可扩展为写入 Segment 覆盖的全部 dialogue，但不能只写 plan。

建议同时提供恢复动作：

```text
恢复为口播 / 需要 Sync
```

恢复时把 `is_talking_head` 改回 `true`，并清除或更新 `lipsync_override`。恢复动作需要提供 Save / Cancel，Save 后写入 StoryBoard JSON，Cancel 不改变任何状态。

### 15.7 API 与保存约束

设置空镜必须走后端 API 写回已保存的 StoryBoard JSON。建议新增接口：

```text
POST /api/koubo-storyboard/tasks/{task_id}/dialogues/{dialogue_id}/video-plan-lipsync-override
```

请求体：

```json
{
  "is_talking_head": false,
  "lipsync_override": "skip_cutaway",
  "segment_id": "shot_001_scene_001_segment_001",
  "source": "ui_sync_context_menu"
}
```

规则：

1. 当前 StoryBoard dirty 时，不允许设置空镜；必须先保存。
2. API 成功后，当前 plan 必须视为过期，并立即重新调用 `05_01` 刷新执行计划；前端不能长期沿用旧 plan。
3. 写回失败时，不改变本地展示状态，走全局 error banner。
4. 如果找不到 dialogue binding，不允许确认空镜，只提示无法写回 Dialogue。
5. 如果 `05_02` 正在执行，VideoPlan Modal 与 Dialogue 上的空镜/口播设置入口全部进入只读状态；只允许查看资源、prompt、视频和执行日志，不允许保存任何修改。

### 15.7.1 Dialogue 上的空镜/口播操作入口

除了在 VideoPlan Modal 的 Sync badge 上右键设置空镜，Dialogue 本身也需要提供同等操作入口：

```text
设置空镜
恢复为口播 / 需要 Sync
```

交互规则：

1. 操作入口绑定在 Dialogue 上，目标 Dialogue 就是当前 Dialogue；如果从 Segment 进入，则目标 Dialogue 优先是该 Segment 中有图片 / First Frame 来源的第一个 Dialogue，没有图片时使用该 Segment 的第一个 Dialogue。
2. 点击后打开确认弹窗，展示该 Dialogue 的原始帧、当前绑定图片、对白文本和当前口播状态。
3. 弹窗提供 Save / Cancel。
4. Save 后写入 StoryBoard JSON 的 Dialogue `video_plan` 字段。
5. Cancel 后不写入、不刷新 plan、不改变 UI 本地状态。
6. Save 成功后必须重新调用 `05_01` 刷新 VideoPlan；如果当前 Modal 已打开，则用新 plan 替换旧 plan。
7. 当前 StoryBoard dirty 时，操作入口 disabled 或点击后提示先保存，避免把旧前端状态覆盖到已落盘 JSON。
8. 如果 `05_02` 正在执行，操作入口 disabled，提示执行中只读。

### 15.8 05_01 消费规则

`05_01_VideoPlanGenerator.py` 生成 plan 时必须读取 Dialogue 上的 `video_plan.is_talking_head`：

1. `is_talking_head=false` 时：
   - `tasks.need_lipsync=false`
   - `tasks.lipsync_disabled_by_ui=true`
   - `tasks.lipsync_reason="user_marked_cutaway"`
   - `tasks.lipsync_decision_source="dialogue.video_plan.is_talking_head"`
2. `is_talking_head=true` 时：
   - 继续按可见口播规划 Sync。
3. 字段缺失、无法读取或旧 StoryBoard 无该字段时：
   - 默认 `need_lipsync=true`
   - `lipsync_reason="default_execute_when_dialogue_flag_missing"`
4. 如果 Dialogue 口播状态发生变化，前端必须刷新执行计划并重新调用 `05_01`，不能只在当前 Modal 中本地切换颜色。

这样可以保证旧数据不会因为字段缺失而错误跳过 Sync。

### 15.8.1 新 plan 与已有 05_02 结果绑定

Dialogue 口播状态变化后会生成新的 plan，但不自动清空已有 `05_02` 执行结果。界面需要把“计划”和“执行结果”分开管理：

1. `05_01` 每次生成 plan 时必须提供可稳定比较的 `plan_hash` 或 `plan_run_id`。
2. `05_02` execution result 必须记录它消费的 `source_plan_hash` / `source_plan_run_id`。
3. Modal 展示执行态时，只有 execution result 的 plan 标识与当前 plan 匹配，才允许把旧结果绑定到当前 plan 上并显示绿色完成态。
4. 如果当前 plan 与已有 `05_02` 结果不匹配，Modal 保留旧结果的可查看入口，但必须标记为“来自旧计划”，不能用它给当前 plan 染色。
5. 是否重新执行 `05_02` 由用户决定；仅重新生成 `05_01` plan 不自动删除、覆盖或重跑 `05_02`。
6. 如果用户恢复口播，旧的空镜 raw video / final video 可以放弃，不再作为当前 plan 的完成结果。

### 15.8.2 清除上次执行内容

Modal 需要提供一个受控按钮，用于人工清除上一次 `05_02` 执行内容：

```text
清除上次执行内容
```

规则：

1. 点击后必须二次确认，说明会清除当前 `05_02` execution result 与本次执行产生的可交付绑定。
2. 确认后清理 `05_02` 工具状态，使下一次执行必须从当前 plan 重新运行。
3. 清理前，旧 Sync 视频、旧最终视频、旧 prompt / audit 如已发布到 StoryBoard Working，需要按 History 规则备份，不直接静默删除。
4. 清理动作不改变 Dialogue 的 `video_plan.is_talking_head` 字段。
5. 清理动作不重新生成 `05_01` plan；如果 plan 已过期，应先刷新 plan，再允许执行新的 `05_02`。
6. `05_02` 正在运行时，清理按钮 disabled，只读查看。

### 15.9 05_02 执行规则

`05_02_VideoPlanExecutor.py` 只消费当前 `05_01` plan，不重新推断 Segment 边界、绑定视频是否完成、或原始帧是不是人口播。

绑定视频执行规则：

1. 如果 Segment 的 `first_frame.source_type="bound_video"` 或存在 `existing_video.materialize_video`，该 Segment 的 Video 步骤不调用视频生成模型。
2. `05_02` 必须把 `existing_video.materialize_video.copy_from_path` 指向的绑定视频拷贝到 `copy_to_path`，并确保 `copy_to_path === planned_outputs.video_path`。
3. `planned_outputs.video_path` 必须使用和生成视频一致的命名规则：`{first_dialogue_asset_key}_Video_Final.mp4`。
4. 如果绑定视频已在 `Working/` 中但命名不规范，仍需拷贝 / 重命名到规范路径；不能把非规范文件路径直接当成当前 Segment 的最终完成产物。
5. 拷贝成功后，该 Segment 的 Video 状态显示为绿色完成；execution result 记录 `completed_by_bound_video=true`、`source_video_path`、`working_video_path`。
6. 绑定视频不需要在 05-02 阶段做时长判断；后期剪辑 / 合并阶段负责裁剪和对齐。
7. 如果拷贝失败或源文件不存在，该 Segment blocked / failed，不能静默退化为重新生成视频。

Sync 执行规则：

1. `need_lipsync=true`：生成 raw video 后继续执行 Sync。
2. `need_lipsync=false` 且 reason 为 `user_marked_cutaway`：跳过 Sync，把校验通过的 raw video 晋升为最终视频。
3. `need_lipsync=false` 且 reason 为 `existing_video_bound_complete`：不执行 Sync，绑定视频视为该 Segment 的最终视频。
4. 跳过 Sync 时必须在 execution result 中记录 `skipped_by_cutaway` 或 `skipped_by_bound_video`，便于 Modal 展示灰色跳过态或绿色已完成态。
5. `05_02` 不重新判断原始帧是不是人口播；人工判断入口在 Modal 右键菜单 / Dialogue 原图菜单。
6. 如果 Sync 已经生成过，后来用户设置空镜或恢复口播，旧 Sync 视频必须进入 History；当前 plan 是否显示完成只看新的 plan 标识与 execution result 是否匹配。

尾帧执行规则：

1. 如果后续 Segment 需要继承某个已完成 Segment 的尾帧，`05_02` 必须确保该 Segment 的 `tail_frame.planned_path` 存在。
2. 对绑定视频 Segment，尾帧应从拷贝到 Working 后的规范视频路径提取，不从上传源路径直接提取。
3. 如果 plan 中 `tail_frame.continuation_allowed=false`，后续空 Dialogue / 空 Scene 不得引用该尾帧；`05_02` 不能绕过 `05_01` 的 blocked 判断自行续接。

### 15.10 05-02 执行态绑定与实时刷新规则

点击 Modal 中的执行按钮后，界面必须能够获取 `05_02_VideoPlanExecutor.py` 的实时执行状态，并把执行状态准确绑定到当前 `05_01` plan 上。这里的核心约束是：plan、实时执行态、最终执行结果、Working 文件状态必须分开管理，不能互相污染。

文件职责：

1. `SessionOutput/storyboard/video_generation_plan.json`：只保存 `05_01` 生成的计划。
2. `SessionOutput/storyboard/video_plan_execution_state.json`：保存 `05_02` 正在执行的实时状态。
3. `SessionOutput/storyboard/video_plan_execution_result.json`：保存 `05_02` 完成后的最终结果。
4. `SessionOutput/storyboard/video_generation_plan.ui_cache.json`：只保存前端缓存 signature，不保存执行状态。

执行启动流程：

1. 用户点击 Modal 中的执行按钮。
2. 前端确认当前 StoryBoard 已保存、当前 plan 未过期、当前没有 `05_02` 正在运行。
3. 后端读取当前 `video_generation_plan.json`，计算或读取 `plan_hash`。
4. 后端创建 `execution_job_id`，写入初始 `video_plan_execution_state.json`。
5. 后端后台启动 `05_02_VideoPlanExecutor.py`，并把 `execution_job_id` 与 `source_plan_hash` 传入工具。
6. Modal 进入执行中只读态：执行按钮 disabled，空镜/口播修改 disabled，清除上次执行 disabled，只允许查看资源、prompt、视频和日志。
7. 前端每 1 秒轮询执行状态接口，直到状态进入 `completed` / `completed_with_failed_items` / `failed` / `interrupted`。

后端 API：

```text
POST /api/koubo-storyboard/tasks/{task_id}/video-plan/execute
GET  /api/koubo-storyboard/tasks/{task_id}/video-plan/execution
POST /api/koubo-storyboard/tasks/{task_id}/video-plan/execution/clear
```

`GET /video-plan/execution` 返回：

```json
{
  "ok": true,
  "plan_hash": "...",
  "artifact_status": {},
  "execution_state": {},
  "execution_result": {},
  "binding_status": {
    "state_matches_current_plan": true,
    "result_matches_current_plan": true,
    "current_plan_hash": "...",
    "state_plan_hash": "...",
    "result_plan_hash": "..."
  }
}
```

`video_plan_execution_state.json` 标准结构：

```json
{
  "schema_version": "koubo_video_plan_execution_state_0.1",
  "job_id": "vp_exec_...",
  "source_plan_hash": "...",
  "source_plan_path": "SessionOutput/storyboard/video_generation_plan.json",
  "status": "queued",
  "started_at": "...",
  "updated_at": "...",
  "current_segment_id": "",
  "current_step": "",
  "segments": {
    "shot_001_scene_001_segment_003": {
      "status": "running",
      "steps": {
        "audio": { "status": "completed_working" },
        "image": { "status": "skipped", "reason": "bound_video" },
        "video": { "status": "running_copy" },
        "sync": { "status": "skipped", "reason": "skipped_by_bound_video" }
      },
      "outputs": {},
      "error": ""
    }
  }
}
```

UI 状态合并优先级：

1. `execution_state`，且 `source_plan_hash` 匹配当前 plan。
2. `execution_result`，且 `source_plan_hash` 匹配当前 plan。
3. 后端实时 `artifact_status`，且对应 Working 文件真实存在。
4. 当前 `05_01` plan 的 `tasks.need_*` 作为 fallback。

状态映射：

1. `pending_generate`：白底黄色小点。
2. `pending_copy`：白底绿色小点。
3. `running_generate`：黄色区域，可显示 loading。
4. `running_copy`：黄色区域，可显示 loading。
5. `completed_working`：绿色区域。
6. `skipped_by_cutaway`：灰色区域。
7. `skipped_by_bound_video`：Sync 灰色；Video 如果已拷贝到 Working，则绿色。
8. `failed`：错误态，点击或悬停可查看错误原因。
9. `interrupted`：全局 error banner，并保留当前执行态，用户清理后才能重跑。

顶部 Dashboard 计数规则：

1. Dashboard 不只读 `05_01.summary.need_*_count`，必须用当前 plan、`artifact_status`、`execution_state` 共同计算。
2. 分母表示当前 plan 内需要处理或物化的步骤数量：Audio 包含 SegmentAudio 物化，Frame 包含生成 / 拷贝首帧，Video 包含生成视频与绑定视频拷贝，Sync 只包含需要执行口播同步的 Segment。
3. 分子只统计已经进入 `Working/` 的完成项，或 `execution_state.steps.*.status=completed_working` 的项。
4. 绑定视频拷贝完成后，Video 计数必须增加；不能因为 `need_video=false` 就把绑定视频排除在 Dashboard 之外。
5. Segment 前置步骤失败后，后续尚未执行的步骤显示为 `前置失败未执行`，不再显示黄色待执行点，避免误导用户以为后续步骤仍可继续。

禁止行为：

1. 不能把旧 `execution_result` 直接染色到当前 plan。
2. 不能因为 plan 中存在 path 就显示绿色；绿色必须以 Working 文件真实存在为准。
3. 不能在 `05_02` 运行中修改 Dialogue 口播 / 空镜状态。
4. 不能在 `05_02` 运行中清理上次执行内容。
5. 不能在 `05_02` 中重新推断 Segment 边界、空镜规则或绑定视频规则。

`05_02` 必须在以下节点刷新 `video_plan_execution_state.json`：

1. 执行开始。
2. 每个 Segment 开始。
3. Audio 开始 / 完成 / 失败。
4. First Frame 生成或拷贝开始 / 完成 / 失败。
5. Video 生成或绑定视频拷贝开始 / 完成 / 失败。
6. Sync 开始 / 完成 / 跳过 / 失败。
7. Tail Frame 提取开始 / 完成 / 失败。
8. 每个 Segment 完成。
9. 全部执行完成或中断。

### 15.11 验收标准补充

1. First Frame 运行中为黄色旋转，生成后变绿色。
2. 点击已完成 First Frame 可以看到图片和 prompt。
3. Video 运行中为黄色旋转，生成后变绿色。
4. 点击已完成 Video 可以看到视频和 prompt。
5. Sync 运行中为黄色旋转，生成后变绿色。
6. 点击已完成 Sync 可以看到最终视频、音频和 Sync 审计信息。
7. 右键 Sync 可以看到 `设置空镜`。
8. 点击 `设置空镜` 后能预览 Segment 原始帧和对白文本。
9. 点击 `确定空镜` 后，Dialogue 写入 `video_plan.is_talking_head=false`。
10. 下一次运行 `05_01` 时，该 Dialogue 对应 Segment 的 `need_lipsync=false`。
11. 旧 Dialogue 没有 `video_plan.is_talking_head` 字段时，`05_01` 默认继续执行 Sync。
12. 当前 StoryBoard dirty 时，设置空镜入口 disabled 或确认时 blocked，提示先保存。
13. `05_02` 运行中所有修改入口 disabled，只允许查看资源。
14. 新 plan 与旧 `05_02` 结果 plan 标识不一致时，旧结果可查看但不能染色为当前完成态。
15. 清除上次执行内容必须二次确认，旧 Sync 视频进入 History。
16. 绑定视频 Segment 不调用视频生成模型，只执行拷贝到规范 Working 视频路径。
17. 绑定视频拷贝完成后，Video 状态为完成，execution result 标记 `completed_by_bound_video=true`。
18. 绑定视频源文件缺失时，Segment 明确 failed / blocked，不自动改走生成视频。
19. 空镜 Segment 的尾帧不可驱动后续无视觉来源 Dialogue；只有口播尾帧可连续生成。
