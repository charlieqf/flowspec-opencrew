# Koubo Agent 功能使用说明

本文简要介绍 Koubo 工作流中 5 个 Agent 入口的位置、使用方式和预期体验。

## 总体原则

- Agent 是新增辅助能力，不替代原有按钮、输入框、弹窗和执行流程。
- 只有用户显式点击 Agent 按钮后，才会打开 Agent 对话并创建 OpenCode session。
- Agent 给出的候选建议默认不自动生效，必须由用户点击对应动作按钮，例如“填入草稿”“应用到草稿”“生成 VideoPlan”。
- 原有创意助手、预置选项、Prompt Builder、ImagePlan、VideoPlan、Composer 的使用方式保持不变。

## 1. Asset Library / 上传素材：OpenCode Agent

位置：

- 进入任务的 `Asset Library / 素材库` 页面。
- 右侧原有 `Image generation workspace` 面板顶部，点击 `OpenCode Agent` 图标按钮。

用途：

- 帮助用户把生图想法整理成更完整的图片提示词。
- 可结合当前素材、参考图和用户描述，补充主体、构图、光线、风格、负面提示词等内容。

使用方式：

- 原有“我是你的创意助手”和 4 个预置选项仍在原位置使用。
- 需要更强提示词辅助时，单独打开 `OpenCode Agent`。
- Agent 生成候选后，可点击：
  - `填入草稿`：把候选提示词填入原生图输入框，用户继续修改。
  - `直接生成`：确认后直接调用原有生图流程。

预期体验：

- 原创意助手不被覆盖。
- 预置选项仍可一键填入主输入框。
- Agent 像一个额外的提示词顾问，只在需要时打开。

## 2. 故事版编辑页：故事版 Agent

位置：

- 进入 `故事版（口播）` 主编辑页。
- 顶部工具栏点击 `故事版 Agent` 按钮。

用途：

- 辅助改写当前台词。
- 辅助整理镜头、场景、对白结构。
- 适合处理“这段口播不自然”“帮我拆成两句”“优化当前镜头表达”等问题。

使用方式：

- 打开 Agent 后输入修改诉求。
- Agent 返回 StoryBoard 修改候选。
- 点击 `应用到草稿` 后，候选才会写入当前前端草稿，并将页面置为 dirty。
- 用户仍需按原有方式保存 StoryBoard。

预期体验：

- 不会自动改 StoryBoard。
- 不会绕过原保存流程。
- 用户可以先审阅候选，再决定是否应用到草稿。

## 3. ImagePlan 弹窗：ImagePlan Agent

位置：

- 在故事版主编辑页点击原有 `ImagePlanGenerator`。
- 打开 ImagePlan 弹窗后，点击右上角 `ImagePlan Agent` 按钮。

用途：

- 解释当前 ImagePlan 状态。
- 优化单个图片任务的 Prompt。
- 辅助判断下一步是生成 Prompt、生成 Image，还是先补参考图。

使用方式：

- 原 ImagePlan 弹窗仍按原方式打开和执行。
- 打开 `ImagePlan Agent` 后，可让 Agent 针对当前 asset 生成 Prompt 候选。
- 候选动作包括：
  - `打开 Prompt 编辑器`
  - `填入 Prompt`
  - `保存 Prompt`
  - `生成当前 Image`

预期体验：

- 打开 ImagePlan 弹窗本身不会启动 Agent。
- Agent 只在点击 `ImagePlan Agent` 后出现。
- Prompt 的填入、保存、生成都需要用户显式点击。

## 4. VideoPlan 弹窗：VideoPlan Agent

位置：

- 在故事版主编辑页点击原有 `VideoPlan`。
- 打开 VideoPlan 弹窗后，点击右上角 `VideoPlan Agent` 按钮。

用途：

- 辅助解释当前 VideoPlan 状态。
- 建议整片、单镜头或场景级 VideoPlan 生成动作。
- 帮助用户理解为什么视频计划缺失、过期或需要重新生成。

使用方式：

- 原 VideoPlan 弹窗和参数面板仍按原方式使用。
- 打开 `VideoPlan Agent` 后，Agent 可给出下一步动作候选。
- 用户点击候选动作后，才会应用参数或触发 VideoPlan 生成。

预期体验：

- 打开原 VideoPlan 弹窗不会自动启动 Agent。
- Agent 不会自动生成 VideoPlan。
- 用户可以把它当作 VideoPlan 的诊断和操作建议面板。

## 5. Composer 弹窗：合成诊断 Agent

位置：

- 在故事版主编辑页点击原有 `Composer`。
- 打开合成弹窗后，点击右上角 `合成诊断 Agent` 按钮。

用途：

- 排查为什么当前任务不能合成。
- 解释缺少的 VideoPlan、素材、候选或执行状态。
- 给出下一步修复动作，例如生成整片 VideoPlan、刷新状态、重新执行 Composer。

使用方式：

- 原 Composer 检查和合成流程保持不变。
- 打开 `合成诊断 Agent` 后，查看诊断结果。
- 对 Agent 给出的下一步操作，用户需要手动点击确认执行。

预期体验：

- Composer 原有候选、状态和执行按钮不被替换。
- Agent 作为排查助手出现，降低理解合成失败原因的成本。
- 所有修复动作都保持用户显式触发。

## 使用建议

- 简单生图：优先使用 Asset Library 原有创意助手和预置选项。
- 需要高质量提示词：打开 Asset Library 的 `OpenCode Agent`。
- 需要改口播文案或结构：打开 `故事版 Agent`。
- 图片任务卡住或 Prompt 质量不够：打开 `ImagePlan Agent`。
- 视频计划缺失或范围不清：打开 `VideoPlan Agent`。
- 合成失败或不知道下一步做什么：打开 `合成诊断 Agent`。

