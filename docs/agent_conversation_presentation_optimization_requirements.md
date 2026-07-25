# Agent 对话呈现优化需求文档

- 日期：2026-06-14
- 目标模块：OpenCrew / OpenClip / Upload Asset Library / Images-Agent
- 主要页面：`Asset Library / Images-Agent`
- 参考体验：Google Flow 项目页的清爽对话式创作界面，以及当前用户截图中的问题对照
- 目标读者：OpenCrew / OpenClip 产品、前端、后端、测试与审核

## 1. 背景

当前 Images-Agent 已能通过 OpenCode Agent 辅助用户进行图片提示词规划、参考图绑定和受控生图。但在真实使用中，Agent 的输出经常呈现为大段混杂文本：

- 用户原始指令、参考图路径、Agent 内部提示、模型协议说明、生成结果、保存文件名混在同一个消息流里。
- 用户可读信息与机器可读信息没有清晰边界。
- 长英文提示词、JSON/协议文本和路径原样进入主聊天区，导致视觉上像日志窗口，而不是创作工具。
- 图片结果虽然能显示，但缺少稳定的结构化上下文，例如“参考图是什么”“生成状态是什么”“结果保存在哪里”。

本需求文档的目标，是把 Images-Agent 对话从“OpenCode 原始消息展示”升级为“面向创作工作的结构化对话界面”。主界面只呈现用户真正需要理解和操作的内容；内部协议、调试信息、请求 payload 等内容默认折叠到详情区。

## 2. 当前实现位置

当前主要实现点：

- `OpenCrew/OpenClip/frontend/src/UploadAssetLibrary/components/AgentPanel.jsx`
  - OpenCode Agent 抽屉/工作区渲染
  - OpenCode SSE event reducer
  - Agent 消息列表
  - 图片生成事件
  - Prompt candidate 解析与动作按钮
- `OpenCrew/OpenClip/frontend/src/UploadAssetLibrary/styles/agent-chat.css`
  - Agent 对话区布局
  - 消息文本、图片、候选卡、输入框样式
- `OpenCrew/OpenClip/frontend/src/UploadAssetLibrary/components/VideoAgentPanel.jsx`
  - 视频 Agent 的相似对话实现，可作为后续统一组件的迁移对象

当前关键问题来自：

1. `visibleMessageText()` 只过滤 `<PROMPT_CANDIDATE>` 和 `<IMAGE_GENERATION_REQUEST>` 两类块。
2. 剩余文本会直接渲染到 `<p>` 中。
3. CSS 使用 `white-space: pre-wrap` 原样保留换行。
4. 用户消息右对齐、助手消息全宽，没有内容类型层级。
5. 文件路径、参考图清单、内部指令、生成摘要没有独立视觉组件。

## 3. 目标

### 3.1 产品目标

把 Images-Agent 对话界面整理成清楚、干净、适合持续创作的工作台：

- 用户一眼能看到自己说了什么。
- 用户一眼能看到 Agent 当前做了什么。
- 图片生成进度和结果以卡片呈现。
- 参考图以缩略图和角色标签呈现。
- 文件路径、JSON、内部协议默认不污染主界面。
- 整体观感接近 Flow 式创作界面：轻、白、留白充分、层级明确、输入框稳定固定。

### 3.2 工程目标

- 不改变 OpenCode Agent 的安全边界。
- 不改变图片生成业务 API。
- 不让 OpenCode 直接执行业务状态修改。
- 尽量在前端渲染层完成第一阶段优化。
- 为后续 ImagePlan Agent、VideoPlan Agent、Composer Agent 复用同一消息呈现体系打基础。

## 4. 不做什么

第一阶段不做以下事项：

1. 不重写 OpenCode Agent 后端协议。
2. 不改变 OpenCode session 创建、message、events、abort 的核心流程。
3. 不改变图片生成模型选择、参考图上传、一致性图加载、Prompt Builder 等既有功能。
4. 不把所有历史消息迁移成新的数据库 schema。
5. 不让 Agent 自动保存、自动修改素材库或自动执行非用户确认的动作。
6. 不做全局万能助手样式重构，本次聚焦 Images-Agent 对话呈现。

## 5. 用户问题描述

用户反馈的核心感受：

- “通过 Open Code 的回答很混乱”
- “看起来也很混乱”
- 希望参考 Flow 页面，把对话规整到清楚、干净、统一的界面

从截图中可见的典型问题：

- 一条消息同时包含：
  - 生图请求
  - 参考图路径
  - 参考图角色
  - 生成图片预览
  - 文件保存名
  - 模型内部约束说明
  - `Produce exactly one ... JSON block` 等协议内容
- 文本大段居中/右侧排布，阅读顺序不稳定。
- 图片预览和文字结果没有组成明确的“结果卡”。
- 底部输入框可用，但上方消息区的信息密度和噪音过高。

## 6. 体验原则

### 6.1 主界面只显示用户可理解内容

默认聊天流只显示：

- 用户输入
- Agent 对用户说的话
- 生成进度
- 生成结果
- 可点击候选
- 错误摘要

默认不显示：

- 系统提示词
- 模型约束说明
- 原始 JSON
- OpenCode 内部事件
- 大段 reference payload
- 完整调试栈
- 对模型的格式指令

### 6.2 内容按类型展示，不按原始文本倾倒

同一条 OpenCode response 进入界面前，应被拆成结构化片段：

- `user_message`
- `assistant_summary`
- `reference_images`
- `generation_status`
- `generation_result`
- `prompt_candidates`
- `debug_details`
- `error`

主聊天区只渲染前六类；`debug_details` 默认折叠。

### 6.3 图片和文件是产物，不是正文

图片、文件名、保存路径不应混在自然语言正文中。它们应使用专门组件：

- 参考图 chip
- 生成结果 card
- 文件名 secondary text
- 保存位置 badge

### 6.4 Flow 式视觉方向

视觉应保持：

- 轻背景
- 低边框密度
- 清楚分割线
- 适度留白
- 固定底部 composer
- 小而清楚的工具按钮
- 内容列宽受控
- 图片缩略图稳定
- 交互动作明确

## 7. 信息架构

Images-Agent 工作区由四个稳定区域组成：

```text
┌─────────────────────────────────────────────┐
│ Header                                      │
│ Images-Agent / subtitle / close             │
├─────────────────────────────────────────────┤
│ Conversation                                │
│ - user message                              │
│ - assistant summary                         │
│ - reference image strip                     │
│ - generation progress card                  │
│ - result image card                         │
│ - prompt candidate cards                    │
│ - collapsed debug detail                    │
├─────────────────────────────────────────────┤
│ Pending confirmation / status bar           │
├─────────────────────────────────────────────┤
│ Composer                                    │
│ reference chips / textarea / tools / send   │
└─────────────────────────────────────────────┘
```

## 8. 功能需求

### 8.1 消息渲染分层

#### FR-1 用户消息

用户消息应：

- 右侧对齐。
- 最大宽度不超过内容区的 72%。
- 使用轻微背景或浅边框区分。
- 保留用户输入中的必要换行。
- 长 prompt 超过 8 行时默认折叠，提供“展开”。
- 如果用户消息发送时携带参考图，应把参考图缩略图嵌入同一个用户消息卡顶部，而不是另起一段路径文本。
- 用户消息卡内的参考图缩略图尺寸应稳定，建议 `32px x 32px` 或 `36px x 36px`，圆角 6px，横向排列。
- 用户消息卡内的缩略图最多首行显示 4 张，超过后显示 `+N` 聚合 chip。
- 用户消息卡内文字与缩略图之间保持 8-10px 间距，整体像一个完整输入单元。

验收：

- 用户输入的长英文 prompt 不会占满整个面板宽度。
- 多行 prompt 不会把后续结果推得难以辨认。
- 用户带 2 张参考图发送时，消息卡顶部能看到 2 个小缩略图，正文在下方。
- 主界面不显示 `Selected reference images:` 这类路径清单作为用户消息正文。

#### FR-2 Agent 摘要消息

Agent 摘要消息应：

- 左侧或居中窄列展示。
- 只显示自然语言说明。
- 不显示内部协议或 JSON。
- 不显示完整路径列表。
- 可以包含短句，例如：
  - “我会用 3 张参考图生成 16:9 电商直播封面。”
  - “已生成图片并保存到 Upload。”

验收：

- 主对话区不再出现 `Produce exactly one ... JSON block`。
- 主对话区不再出现 `The user explicitly requested...` 这类内部说明。

#### FR-3 参考图展示

当消息或 composer 中有参考图时，应展示为 reference strip：

- 缩略图尺寸固定。
- 显示角色标签：
  - `Target`
  - `Host`
  - `Product`
  - `Ref`
- 显示短文件名，不显示完整路径。
- 鼠标悬停或点击“详情”时可查看完整路径。

验收：

- `SessionOutput/storyboard/assets/images/srt_0001_Image_01.png` 在主界面中显示为 `srt_0001_Image_01.png`。
- `SessionContext/Consistency/HOST.png` 显示为 `HOST.png`，并带 `Host` 标签。

#### FR-4 图片生成状态

图片生成开始时，应展示生成状态卡：

- 显示固定比例占位图。
- 显示状态文案：
  - `Generating image`
  - `Preparing references`
  - `Saving to Upload`
- 显示进度百分比或轻量进度条。
- 失败时变为错误状态卡。

验收：

- 生成中不会只显示一行 `Agent is generating...`。
- 进度变化不会导致布局跳动。

#### FR-5 生成结果卡

生成完成后，应展示结果卡：

- 图片预览。
- 保存状态：`已保存到 Upload`。
- 文件名：短文件名。
- 操作按钮：
  - 查看
  - 设为参考图
  - 复制文件名或复制路径
  - 可选：重新生成

第一阶段至少实现图片预览、保存状态、短文件名。

验收：

- 图片结果和保存文本组成一个视觉整体。
- 文件名不会出现在一大段正文中。

#### FR-5.1 结果动作栏

生成结果卡下方应提供一组轻量图标动作，参考 Flow 的结果图下方操作栏：

- 点赞：记录该结果有用。
- 点踩：记录该结果不满意。
- 复制：复制结果文件路径或复制可复用 prompt，第一阶段默认复制文件路径。
- 标记/举报：打开反馈入口或把结果标记为需要复查。

交互要求：

- 动作用图标按钮表达，不使用大段文字按钮。
- 图标按钮尺寸建议 `28px x 28px`。
- hover 时显示 tooltip：
  - `Like`
  - `Dislike`
  - `Copy`
  - `Report`
- 复制成功后给出短暂状态反馈，例如 tooltip 或 toast 显示 `Copied`。
- 点赞/点踩互斥，同一条结果只能保留一个态。
- 第一阶段如果后端没有反馈 API，可先作为前端本地状态和复制功能实现，预留回调。

验收：

- 每张生成结果图下方都有稳定的图标动作栏。
- 点击复制按钮能复制当前结果的 path 或 filename。
- 点赞/点踩切换不会引发布局跳动。

#### FR-6 Prompt Candidate 卡片

对于 `<PROMPT_CANDIDATE>`：

- 不显示原始 JSON。
- 渲染为候选卡。
- 显示：
  - 标题
  - aspect ratio
  - positive prompt 摘要
  - negative prompt 摘要，若有
  - `填入草稿`
  - `生成`
- 长 prompt 默认折叠。

验收：

- 候选卡没有 JSON 语法噪音。
- 按钮动作沿用现有 `applyPromptCandidate()`。

#### FR-7 调试详情折叠区

每条复杂 Agent 消息可提供 `详情` 折叠入口：

默认折叠内容包括：

- 原始 message text
- reference_images payload
- generation request
- provider/model
- chat session id
- OpenCode event type

验收：

- 普通用户默认看不到这些内容。
- 排查问题时，开发或高级用户仍能展开查看。

#### FR-7.1 Thinking 折叠区

Agent 的思考过程、规划过程或阶段性推理应使用独立的 `Show thinking` 折叠控件展示，参考截图中的小型下拉按钮。

展示规则：

- 默认折叠。
- 折叠按钮文案为 `Show thinking`。
- 展开后文案为 `Hide thinking`。
- 按钮位置放在对应 Agent 回答上方或回答内部开头，不能打断用户消息和结果卡。
- thinking 内容使用较弱视觉层级，例如小字号、muted 文本、轻背景或左侧细线。
- thinking 内容允许标题分段，例如：
  - `Considering Image Style`
  - `Focusing on Structure Flow`
  - `Refining Detail & Composition`
- thinking 内容不等同于 debug。thinking 面向高级用户解释 Agent 思路；debug 面向开发排查原始 payload。
- 如果后端或 OpenCode 没有明确 thinking 字段，不应从普通回答中强行伪造 thinking。

验收：

- 默认状态下，用户只看到干净回答，不看到大段思考过程。
- 点击 `Show thinking` 后，能展开查看 Agent 的规划说明。
- thinking 展开/收起不会改变 composer 固定位置。
- thinking 与 debug details 是两个不同入口。

### 8.2 内容清洗规则

#### FR-8 内部协议过滤

主界面应过滤或迁移到调试详情的文本模式：

- `The user explicitly requested ...`
- `Produce exactly one ... JSON block`
- `Use these selected reference_images ...`
- `If TARGET_FRAME, HOST_REFERENCE ...`
- `<IMAGE_GENERATION_REQUEST>...</IMAGE_GENERATION_REQUEST>`
- `<PROMPT_CANDIDATE>...</PROMPT_CANDIDATE>`
- 单独的大段 JSON object
- 包含 `role/path objects` 的模型指令

验收：

- 截图中从 `The user explicitly requested Images-Agent image generation.` 开始的内部说明不再出现在主聊天流。

#### FR-9 路径压缩

主界面中的完整路径应压缩为短文件名：

```text
SessionOutput/storyboard/assets/images/srt_0001_Image_01.png
```

显示为：

```text
srt_0001_Image_01.png
```

可在详情或 tooltip 中查看完整路径。

验收：

- 任意路径不会撑破消息宽度。
- 中文括注如“人物一致性”“产品一致性”应作为标签或辅助说明保留。

#### FR-10 保存结果标准化

保存结果文案统一为：

```text
已保存到 Upload
```

文件名显示为次级信息：

```text
1781405814956_agent_generated_1a23b50b.png
```

验收：

- 不再显示“已经生成并保存到 Upload：长文件名”作为正文大句。

### 8.3 Composer 优化

#### FR-11 底部输入框固定

Composer 应固定在底部：

- 输入框区域不会随消息滚动消失。
- 参考图 chip 位于 textarea 上方或同一容器内顶部。
- 工具按钮保持一行：
  - 上传参考图
  - 加载一致性参考图
  - Prompt Builder
  - Settings
  - Send

验收：

- 长对话滚动时，输入框始终可见。

#### FR-12 输入框占位文案

占位文案建议：

```text
让 Agent 帮你优化生图提示词
```

状态文案：

- 上传中：`Uploading references...`
- 等待中：`Waiting for agent...`
- 生成中：`Generating image...`

验收：

- 用户能清楚知道当前是否可输入。

### 8.4 确认与安全

#### FR-13 生成前确认

若设置 `confirmBeforeGenerate` 为 true，保留生成前确认，但确认 UI 应从浏览器 `window.confirm` 升级为面板内轻量确认卡。

确认卡显示：

- 标题：`Generate image?`
- 摘要 prompt，最多 2-3 行。
- 按钮：
  - `Cancel`
  - `Generate`

验收：

- 不再弹出系统 confirm 阻塞 UI。
- 用户能在上下文中确认。

#### FR-14 错误展示

错误应分为：

- 用户可读错误摘要
- 可展开技术详情

示例：

```text
生成失败：缺少 Host 参考图。
```

详情：

```text
missing_reference_images: [...]
raw_error: ...
```

验收：

- 错误不会整段 JSON 倾倒在主聊天区。

## 9. 视觉需求

### 9.1 色彩

推荐第一阶段沿用现有 CSS 变量：

- `--ual-panel`
- `--ual-panel-soft`
- `--ual-border`
- `--ual-text`
- `--ual-muted`
- `--ual-accent`
- `--ual-accent-soft`

视觉方向：

- 主背景白色或极浅灰。
- 边框只用于分区，不堆叠卡片。
- 重要动作使用 accent。
- 错误用柔和红色，不使用大面积警告底色。

### 9.2 间距

建议值：

- Header 高度：64-72px。
- Conversation padding：24-32px。
- 消息间距：16-20px。
- 主内容列最大宽度：640px。
- 图片预览最大宽度：192-240px。
- Composer padding：16-24px。

### 9.3 消息样式

用户消息：

- 右对齐。
- 最大宽度 72%。
- 背景 `var(--ual-panel-soft)`。
- 圆角 8px。
- 内部采用纵向布局：
  - 顶部是附件缩略图行。
  - 下方是用户文本。
  - 文本行高 1.45。
- 附件缩略图行高度稳定，不因图片原始比例变化。
- 用户消息内缩略图不显示路径文本，路径仅在 tooltip 或详情中出现。

Agent 消息：

- 左对齐。
- 最大宽度 680px。
- 正文无重卡片背景，保持干净。
- thinking 折叠控件应比正文弱一层，按钮不应抢占主要视觉焦点。

结果卡：

- 圆角 8px。
- 图片固定比例。
- 文件信息次级文字。
- 图片下方动作栏紧贴结果卡，图标间距 8-10px。
- 动作栏图标颜色默认使用 muted，hover/active 后加深。

Thinking 折叠控件：

- 高度建议 28-32px。
- 文案 `Show thinking` / `Hide thinking`。
- 右侧使用下拉箭头图标。
- 控件宽度随内容，不做整行大按钮。
- 展开内容最大宽度跟随 Agent 消息正文。
- 展开内容与正文之间留 8-12px。

用户消息卡参考截图细节：

- 深色主题下，用户消息卡背景可比页面背景亮一级。
- 附件缩略图放在正文上方左侧。
- 卡片内 padding 建议 12px。
- 卡片宽度按内容收缩，但不超过最大宽度。
- 中文长句自然换行，不强制居中。

### 9.4 响应式

窄屏要求：

- 用户消息最大宽度可变为 88%。
- 参考图 chip 横向滚动。
- Composer 工具按钮不换行挤压文字。
- 图片卡不超过容器宽度。

## 10. 建议组件拆分

第一阶段可在前端新增以下组件，逐步替换当前消息内联渲染：

```text
UploadAssetLibrary/components/AgentConversation/
  AgentMessageRenderer.jsx
  AgentMessageText.jsx
  AgentUserBubble.jsx
  AgentReferenceStrip.jsx
  AgentAttachmentStrip.jsx
  AgentGenerationCard.jsx
  AgentResultCard.jsx
  AgentResultActions.jsx
  AgentPromptCandidateCard.jsx
  AgentThinkingToggle.jsx
  AgentDebugDetails.jsx
```

也可以先放在 `AgentPanel.jsx` 内部实现，验证后再抽离。

推荐最终结构：

```text
raw OpenCode message
  -> normalizeAgentMessage()
  -> renderable blocks
  -> AgentMessageRenderer
```

## 11. 数据结构建议

前端可将一条消息标准化为：

```json
{
  "id": "msg_xxx",
  "role": "assistant",
  "created_at": 1781400000000,
  "blocks": [
    {
      "type": "summary",
      "text": "我会用 3 张参考图生成 16:9 电商直播封面。"
    },
    {
      "type": "thinking",
      "collapsed": true,
      "sections": [
        {
          "title": "Considering Image Style",
          "text": "I am focusing on how to interpret the provided reference images..."
        }
      ]
    },
    {
      "type": "references",
      "items": [
        {
          "role": "TARGET_FRAME",
          "label": "srt_0001_Image_01.png",
          "path": "SessionOutput/storyboard/assets/images/srt_0001_Image_01.png",
          "imageUrl": "/api/..."
        }
      ]
    },
    {
      "type": "generation_result",
      "status": "completed",
      "filename": "1781405814956_agent_generated_1a23b50b.png",
      "path": "Upload/1781405814956_agent_generated_1a23b50b.png",
      "imageUrl": "/api/...",
      "actions": {
        "liked": false,
        "disliked": false,
        "copiable": true,
        "reportable": true
      }
    },
    {
      "type": "debug",
      "rawText": "..."
    }
  ]
}
```

第一阶段无需改变后端返回结构，可以在前端从现有 message 与 event 中组装。

用户消息可标准化为：

```json
{
  "id": "local-xxx",
  "role": "user",
  "text": "请严格参考图1的精细程度，来构造这个血管的视图...",
  "attachments": [
    {
      "role": "REFERENCE_IMAGE",
      "label": "reference_01.png",
      "path": "Upload/reference_01.png",
      "imageUrl": "/api/..."
    },
    {
      "role": "REFERENCE_IMAGE",
      "label": "reference_02.png",
      "path": "Upload/reference_02.png",
      "imageUrl": "/api/..."
    }
  ]
}
```

## 12. 解析规则建议

### 12.1 文本切分

新增函数：

```js
normalizeAgentDisplayMessage(message, context)
```

输入：

- OpenCode message
- `partsByMessageId`
- 当前 reference payload
- image generation local message metadata

输出：

- render blocks

### 12.2 过滤顺序

建议顺序：

1. 抽取 `<PROMPT_CANDIDATE>`，生成 candidate blocks。
2. 抽取 `<IMAGE_GENERATION_REQUEST>`，生成 debug block 或 generation intent block。
3. 抽取 thinking 段，生成 thinking block。
4. 抽取用户消息附件或 reference payload，生成 attachment / references block。
5. 抽取参考图清单，生成 references block。
6. 抽取保存结果，生成 result block。
7. 移除内部协议段。
8. 剩余自然语言作为 summary block。
9. 如果 summary 为空但有结果卡，不强行显示空消息。

### 12.3 内部协议识别

以下文本段默认放入 debug：

- 包含 `Produce exactly one`
- 包含 `JSON block`
- 包含 `selected reference_images`
- 包含 `role/path objects`
- 包含 `TARGET_FRAME, HOST_REFERENCE`
- 包含 `The user explicitly requested`

### 12.4 文件名提取

路径显示规则：

```js
shortPathLabel(path) = path.split("/").pop()
```

角色显示规则沿用：

- `TARGET_FRAME` -> `Target`
- `HOST_REFERENCE` -> `Host`
- `PRODUCT_REFERENCE` -> `Product`
- 其他 -> `Ref`

### 12.5 Thinking 识别

thinking block 的优先来源：

1. 后端明确提供的 thinking 字段。
2. OpenCode / Agent event 中明确标记为 reasoning、thinking 或 planning 的 part。
3. 前端已知的专用 XML block，例如 `<THINKING>...</THINKING>`，如果后续引入。

不应把普通 assistant summary 强行切成 thinking。以下文本即使看起来像分析，也只有在来源明确时才进入 thinking：

- `Considering Image Style`
- `Focusing on Structure Flow`
- `Refining Detail & Composition`

如果来源不明确，这些内容应当按普通 assistant text 处理，再由内部协议过滤规则决定是否放入 debug。

## 13. 后端建议

第一阶段前端即可改善 70%-80% 的视觉问题。后端可作为第二阶段增强：

### 13.1 Agent 输出约束

在 Images-Agent system prompt 中明确：

- 面向用户只输出简短自然语言。
- 机器可读内容必须放入指定 XML block。
- 不要重复 reference_images 完整路径，除非放入机器块。
- 不要向用户解释内部协议。

### 13.2 结构化事件增强

后端可为前端提供更明确的事件：

```text
asset_agent.reference_images.selected
asset_agent.image_generation.requested
asset_agent.image_generation.started
asset_agent.image_generation.completed
asset_agent.image_generation.failed
```

现有已存在部分生成事件，本需求要求前端优先使用结构化事件，而不是从自然语言中猜测结果。

## 14. 验收标准

### 14.1 视觉验收

使用用户截图中的场景复现，验收必须满足：

1. 主聊天区不显示内部协议大段英文。
2. 参考图以缩略图和角色标签展示。
3. 生成结果以图片卡展示。
4. 保存文件名为次级信息，不进入正文长句。
5. 底部输入框固定可见。
6. 消息区留白清楚，不像日志窗口。
7. 长 prompt、长路径不会撑破布局。
8. 用户消息卡顶部能显示随消息发送的参考图缩略图。
9. thinking 默认折叠，仅显示 `Show thinking` 小控件。
10. 结果图片下方显示点赞、点踩、复制、标记/反馈图标动作栏。

### 14.2 功能验收

1. 用户仍可发送 Agent 消息。
2. 用户仍可上传参考图。
3. 用户仍可加载人物/产品一致性参考图。
4. 用户仍可打开 Prompt Builder。
5. 用户仍可选择 Settings。
6. Agent 仍可触发生图请求。
7. 生图确认逻辑仍有效。
8. 图片生成中、成功、失败状态都能展示。
9. Prompt candidate 的“填入草稿”仍有效。
10. 非 OpenCode workspace 下原创意助手不受影响。
11. `Show thinking` 能展开和收起，不影响消息滚动位置。
12. 复制按钮能复制结果文件路径或文件名，并给出成功反馈。
13. 点赞和点踩互斥。
14. 用户消息附件缩略图点击或悬停时能查看基本信息，至少能通过详情看到完整路径。

### 14.3 回归验收

需要检查：

- Asset Library 原始 Image generation workspace。
- Images-Agent workspace 模式。
- Agent 抽屉模式。
- 深色主题。
- 无参考图、1 张参考图、3 张参考图。
- 生图成功。
- 生图失败。
- Agent stream 中断。
- Prompt candidate 多个候选。
- 带 2 张参考图的用户消息。
- thinking 展开/收起。
- 结果动作栏复制、点赞、点踩。

## 15. 实施阶段

### P0：前端显示清洗

目标：最快解决“看起来混乱”。

内容：

- 扩展 `visibleMessageText()` 或新增 `normalizeAgentDisplayMessage()`。
- 过滤内部协议。
- 长路径压缩。
- 保存结果文案标准化。
- 图片结果和文本结果组合成结果卡。
- 用户消息内联附件缩略图。
- thinking 内容默认折叠。

预期收益：

- 主聊天区立即清爽。
- 改动范围小。

### P1：结构化组件

目标：稳定形成 Flow 式对话工作台。

内容：

- 新增 message renderer。
- 新增 reference strip。
- 新增 generation result card。
- 新增 debug details。
- 新增 thinking toggle。
- 新增 result actions。
- 调整 CSS 布局和消息宽度。

预期收益：

- 视觉层级完整。
- 后续 VideoAgentPanel 可复用。

### P2：后端输出协同

目标：减少前端猜测。

内容：

- 修改 Images-Agent system prompt。
- 增强结构化 SSE event。
- 减少自然语言中泄漏机器协议。

预期收益：

- 更稳定。
- 更少过滤规则。

### P3：跨 Agent 统一

目标：统一 Koubo 多页面 Agent 体验。

内容：

- 将同一套消息 renderer 用于：
  - Asset Library Images-Agent
  - VideoAgentPanel
  - StoryBoard Agent
  - ImagePlan Agent
  - VideoPlan Agent
  - Composer Agent

预期收益：

- 全系统 Agent 对话体验一致。

## 16. 风险与注意事项

### 16.1 过度过滤风险

如果过滤规则太激进，可能误删用户需要看的内容。

缓解：

- 所有被过滤内容进入 debug details。
- P0 阶段保留“查看详情”。

### 16.2 历史消息兼容

老 OpenCode session 的历史消息可能没有结构化 metadata。

缓解：

- renderer 必须兼容纯文本消息。
- 无法识别的文本仍显示为 summary，但应用长度限制。

### 16.3 图片 URL 缺失

部分历史生成消息只有 path，没有可用 imageUrl。

缓解：

- 有 imageUrl 显示图片。
- 无 imageUrl 显示文件卡。
- 点击详情可看原始 path。

### 16.4 与 VideoAgentPanel 分叉

Images-Agent 与 VideoAgentPanel 目前有相似但不完全相同的实现。

缓解：

- P1 后抽出共用渲染组件。
- 不在 P0 强行重构视频 Agent。

## 17. 测试用例

### TC-1 基础生图请求

输入：

```text
Generate image realistic ecommerce livestream cover female host holding the product package...
```

期望：

- 用户消息显示为右侧 compact bubble。
- Agent 摘要不超过 2-3 行。
- 不显示内部协议。

### TC-2 三参考图生图

参考图：

- `TARGET_FRAME`
- `HOST_REFERENCE`
- `PRODUCT_REFERENCE`

期望：

- 三张参考图以 strip 展示。
- 标签分别为 `Target`、`Host`、`Product`。
- 主界面只显示短文件名。

### TC-2.1 用户消息内联参考图

输入：

```text
请严格参考图1的精细程度，来构造这个血管的视图，不要任何红细胞，只是蓝色包裹橙色的分子结构，如果液体一样流动，不要任何文字。
```

同时携带 2 张参考图。

期望：

- 用户消息卡顶部显示 2 张参考图缩略图。
- 文本显示在缩略图下方。
- 不额外显示完整路径清单。
- 深色主题下，消息卡背景、缩略图、正文层级清楚。

### TC-3 生成成功

期望：

- 显示图片。
- 显示 `已保存到 Upload`。
- 显示短文件名。
- 不出现长路径正文。
- 图片下方显示点赞、点踩、复制、标记/反馈图标。
- 点击复制后有 `Copied` 反馈。

### TC-4 生成失败

期望：

- 显示可读错误摘要。
- 详情中可查看 raw error。
- 页面布局不跳动。

### TC-5 Prompt Candidate

期望：

- 多个候选以卡片展示。
- JSON 不显示。
- `填入草稿` 有效。

### TC-6 历史消息

期望：

- 旧消息可以正常显示。
- 无法识别内容不会导致空白。
- 详情可查看原始文本。

### TC-7 Thinking 折叠

输入或模拟消息包含明确 thinking 内容。

期望：

- 默认只显示 `Show thinking`。
- 点击后展开 thinking 内容。
- 展开内容弱于主回答，不抢占结果卡视觉焦点。
- 再次点击后收起。
- thinking 内容不进入 debug details，debug details 仍单独存在。

### TC-8 结果动作栏

生成一张图片后操作结果图下方动作栏。

期望：

- 点赞后点赞图标进入 active 状态。
- 再点点踩，点赞取消，点踩 active。
- 点击复制复制结果 path 或 filename。
- 点击标记/反馈后出现预留反馈入口或本地状态，不报错。

## 18. 完成定义

本优化完成时，应满足：

1. 用户截图中的混乱场景被清理为结构化对话。
2. 主界面不再展示 OpenCode 内部协议。
3. 参考图、生成状态、生成结果有独立视觉组件。
4. 输入框固定、清楚、可持续使用。
5. Prompt candidate、Settings、Prompt Builder、参考图上传等现有功能不回退。
6. 用户消息内联参考图、thinking 折叠、结果动作栏全部落地。
7. 至少完成一轮真实生图流程验证。
8. 文档中 TC-1 到 TC-8 全部通过。
