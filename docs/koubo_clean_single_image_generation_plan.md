# Koubo 上下文干净的单次图片生成实施方案

- 日期：2026-06-11
- 状态：Draft / 供审核
- 模块：OpenClip / Koubo Storyboard / 图片生成基础能力
- 目标读者：OpenCrew / OpenClip 前后端开发与审核

## 修订记录

- rev2（2026-06-11，团队审核意见吸收）：
  - [High] 移除不存在的 `/api/sessions/{session_id}/raw-file` 响应示例，P0 明确新增只读 `CLEAN_IMAGE_REL` 子树的图片预览端点，并使用 `safe_workspace_rel()` 校验。
  - [High] 明确 P0 不扩展 provider 层 `negative_prompt` 参数；若用户显式填写 negative prompt，则按固定规则拼入 `effective_prompt` 并完整记录。
  - [Medium] Promote to Asset Library 复用 `upsert_asset_manifest_item()` 和现有 sidecar JSON 风格，文件名对齐 `{timestamp}_{index:03d}_{filename}`。
  - [Medium] Promote to Dialogue Image 复用 `bind_asset_to_plan()`，`segment_id` 从请求中移除，选槽位作为 P1 小扩展点；slot source type 后续由 rev4 修正为沿用 `generated`。
  - [Medium] Promote to Consistency 复用 `write_builder_section()`，并要求记录被覆盖图来源。
  - [Low] 新增 `CLEAN_IMAGE_REL` 常量要求、`effective_size` 记录要求，以及 `srt_storyboard.json` / `koubo_storyboard_edit.json` 同步验收项。
- rev3（2026-06-11，功能入口方案补充）：
  - [Medium] 明确 P0 主入口放在 Koubo StoryBoard 顶部 Header，按钮打开 Modal，复用 Host & Product Builder 的挂载心智。
  - [Medium] 明确不放到底部 Timeline 或右侧 AssetPanel，避免与 storyboard 驱动生成流水线或素材库入库语义混淆。
  - [Medium] P1 增加 DialogueCard 图片 slot 第二入口，打开同一个 Modal 并预置 dialogue binding target。
- rev4（2026-06-11，Dialogue promote 与事件记录修正）：
  - [Medium-High] 明确 promote to dialogue 必须让 scratch 图片复制到 `Working`，采用方案 C：`asset_source_type()` 将 `CLEAN_IMAGE_REL` 归类为 `generated`，再复用 `bind_asset_to_plan()` 的既有复制分支。
  - [Low] 第 12 节改为复用 Koubo 现有 session event 机制 `add_event(...)`，不新建“应用事件”基础设施；若 service 层拿不到 event helper，则由 route 层记录。
- rev5（2026-06-11，显示位置与素材库心智补充）：
  - [Medium] 明确 generate 后只在 Clean Image Modal 的 scratch preview/history 中显示，不进入右侧 AssetPanel。
  - [Medium] 明确 promote to asset library 后进入 Koubo StoryBoard 右侧 AssetPanel 的“上传素材”页签，后台路径为 `SessionOutput/storyboard/assets/images` 和 `koubo_storyboard_assets.json`。
  - [Medium] 要求前端用状态标签区分“未入库”“已加入素材库”“已绑定对白”“已设为一致性参考”，避免用户误以为生成即入库。

## 1. 背景

当前仓库里已经有多条图片生成链路：

- StoryBoard ImagePlan：`05_03_ImagePlanGenerator` 规划，`05_04_ImagePlanExecutor` 执行。
- Upload Asset Library：资产库里直接生图或通过 Agent 生成候选提示词后生图。
- Host / Product Builder：生成主播或产品一致性参考图。
- OCRebuild：重建链路里的 asset image / Plan D replacement image。

这些链路在最终调用图片 provider 时，通常只发送最终 prompt 字符串和显式参考图，不会把整份 StoryBoard JSON 或任务状态直接发给图片模型。但 prompt 的生产阶段可能混入模板、storyboard preview、一致性指南、历史 pitfall log、产品示例、Agent 对话历史等上下文。

因此需要一个可审计、可复现、上下文边界明确的“干净单次生图”能力：

> 调用方给什么 prompt，就只用什么 prompt 生成一张图片；除调用方显式传入的参考图外，不隐式读取或注入任何业务上下文。

## 2. 目标

1. 提供一个全局唯一的 Clean Single Image Generate 能力。
2. 保证默认生成过程不污染正式业务产物目录。
3. 生成图片默认不被 ImagePlan、VideoPlan、Composer、Asset Library 等业务流程自动消费。
4. 允许用户显式将生成结果提升为业务素材，从而进入现有业务流程。
5. 记录 exactly-sent prompt、参考图、provider、model、输出路径，便于审计“是否干净”。

## 3. 非目标

1. 不替代现有 ImagePlan / Asset Agent / Host Product / Rebuild 生图流程。
2. 不自动优化、扩写、翻译或模板化用户 prompt；若用户显式填写 `negative_prompt`，P0 只按本文定义的固定规则拼接到 `effective_prompt`，不扩展 provider 层。
3. 不自动读取 storyboard、asset summary、consistency guide、manifest、历史聊天或 pitfall log。
4. 不在生成阶段修改 StoryBoard JSON、素材库索引、Consistency 参考图或 Rebuild asset task。
5. 不让这个能力成为业务流程自动扫描的输入目录。

## 4. 干净上下文定义

Clean Single Image Generate 的图片模型请求只能使用以下显式输入：

- `prompt`
- `negative_prompt`，可选；P0 不扩展 `generate_image_bytes` 签名，只在用户显式填写时按固定规则拼进 `effective_prompt`
- `provider` / `model`，可选或由后端配置解析
- `size` / `aspect_ratio`，可选
- `reference_paths`，可选，且必须由调用方显式传入

生成服务禁止读取或注入：

- StoryBoard spoken text
- `srt_storyboard.json`
- `koubo_storyboard_edit.json`
- `koubo_storyboard_assets.json` 的 summary
- `SessionContext/Consistency` 的 guide 或 manifest 内容
- `Image_GPT.md` / `Image_Gemini.md` / `Image_Grok.md` 模板
- historical pitfall log
- Agent chat messages
- OpenCode session history
- task manifest / session metadata 中的业务描述字段

`task_id` 和 `session_id` 只允许用于鉴权、provider 配置解析、工作区定位、落盘和审计，不允许进入 prompt。

P0 的 `effective_prompt` 构造规则固定如下：

```text
if negative_prompt is empty:
  effective_prompt = prompt
else:
  effective_prompt = prompt.rstrip() + "\n\nNegative: " + negative_prompt.strip()
```

`effective_prompt` 必须如实写入 manifest，并且 mock provider 测试应断言 provider 收到的文本与该字段完全一致。该规则是为了兼容当前 `generate_image_bytes(config, prompt, reference_paths, size)` 只有单一 prompt 参数的事实；P0 不新增 provider 层 `negative_prompt` 参数。

## 5. 推荐架构

采用“全局核心服务 + 业务显式提升”的结构。

```text
Frontend clean image panel
   |
   | generate
   v
Clean image route
   |
   | prompt + explicit references only
   v
Clean image service
   |
   | generate_image_bytes(...)
   v
SessionScratch/CleanImageGenerations/<generation_id>/
   |
   | explicit promote
   v
Asset Library / Dialogue Working Asset / Host Product Consistency
```

推荐新增文件：

```text
OpenClip/backend/openclip_backend/koubo_storyboard/clean_image_services.py
OpenClip/backend/openclip_backend/koubo_storyboard/clean_image_routes.py
OpenClip/frontend/src/KouboStoryBoard/cleanImageApi.js
OpenClip/frontend/src/KouboStoryBoard/components/CleanImagePanel.jsx
```

同时在现有常量文件中新增集中路径常量，避免字符串散落：

```text
OpenClip/backend/openclip_backend/koubo_storyboard/constants.py

CLEAN_IMAGE_REL = "SessionScratch/CleanImageGenerations"
```

底层 provider 调用优先复用：

```text
OpenClip/backend/openclip_backend/koubo_storyboard/provider_services.py
```

其中现有 `generate_image_bytes(config, prompt, reference_paths, size)` 已经接近干净单次生图的底层形态。新增服务应把“上下文干净”的约束放在它之上，而不是在各业务模块重复实现。P0 不修改该函数签名。

## 6. 图片存放位置

### 6.1 默认生成目录

默认输出必须放入隔离目录：

```text
<workspace>/<CLEAN_IMAGE_REL>/<generation_id>/
  image.png
  manifest.json
```

`CLEAN_IMAGE_REL` 的值为 `SessionScratch/CleanImageGenerations`。`SessionScratch` 是建议新增的临时/实验产物空间。Clean image generate 不应默认写入以下正式业务目录：

```text
SessionOutput/storyboard/Working/
SessionOutput/storyboard/assets/images/
SessionOutput/storyboard/
SessionContext/Consistency/
S*_*/Output/
```

这样可以避免生成结果被现有业务流程误认为正式素材、已绑定图片、Consistency 参考图或工具输出。

### 6.2 文件命名

建议 `generation_id` 使用稳定且不含业务语义的 ID：

```text
cln_<timestamp_ms>_<short_random>
```

示例：

```text
SessionScratch/CleanImageGenerations/cln_1780950000000_a1b2c3d4/
  image.png
  manifest.json
```

不建议把 prompt 摘要、产品名、角色名写入文件名，避免泄露或产生误导性的业务含义。

### 6.3 Manifest

每次生成必须写 `manifest.json`：

```json
{
  "schema_version": "clean_single_image_generation_0.1",
  "kind": "clean_single_image_generation",
  "generation_id": "cln_1780950000000_a1b2c3d4",
  "task_id": 78,
  "session_id": 137,
  "created_at": 1780950000000,
  "provider": "grok",
  "model": "grok-imagine-image",
  "requested_size": "1024x1024",
  "effective_size": "1024x1024",
  "prompt": "user supplied prompt",
  "negative_prompt": "",
  "effective_prompt": "exact string sent to image provider",
  "reference_paths": [],
  "output_path": "SessionScratch/CleanImageGenerations/cln_1780950000000_a1b2c3d4/image.png",
  "promotions": []
}
```

`effective_prompt` 必须等于最终送给 provider 的 prompt 文本。若后端为了兼容 provider 把 negative prompt 拼接进 prompt，也必须在这里完整记录。

`effective_size` 必须记录 provider 调用实际使用的 size。若请求未传 size，应记录 `generate_image_bytes` 的真实默认值，而不是示例值。

## 7. API 设计

### 7.1 Generate

```text
POST /api/koubo-storyboard/tasks/{task_id}/clean-image/generate
```

请求：

```json
{
  "prompt": "A realistic product photo...",
  "negative_prompt": "cartoon, CGI, plastic skin",
  "provider": "grok",
  "model": "grok-imagine-image",
  "size": "1024x1024",
  "reference_paths": []
}
```

响应：

```json
{
  "ok": true,
  "generation_id": "cln_1780950000000_a1b2c3d4",
  "image_path": "SessionScratch/CleanImageGenerations/cln_1780950000000_a1b2c3d4/image.png",
  "manifest_path": "SessionScratch/CleanImageGenerations/cln_1780950000000_a1b2c3d4/manifest.json",
  "image_url": "/api/koubo-storyboard/tasks/78/clean-image/cln_1780950000000_a1b2c3d4/image"
}
```

Generate 阶段只允许写入 `SessionScratch/CleanImageGenerations`。

### 7.2 Preview Image

```text
GET /api/koubo-storyboard/tasks/{task_id}/clean-image/{generation_id}/image
```

该接口是 P0 必须新增的图片读取端点，用于前端预览 scratch 图片。当前仓库没有可复用的 `/api/sessions/{session_id}/raw-file` 通用读取接口，现有资源路由也不应直接开放 `SessionScratch`。

安全要求：

1. 只能按 `generation_id` 读取 `CLEAN_IMAGE_REL/<generation_id>/image.*`。
2. 使用 `safe_workspace_rel()` 做 workspace 内路径校验。
3. 校验解析出的相对路径必须位于 `CLEAN_IMAGE_REL` 子树下。
4. 只允许图片后缀和图片 MIME。
5. 不提供任意 `path` 查询参数版本，避免把该接口变成通用文件读取端点。

### 7.3 List

```text
GET /api/koubo-storyboard/tasks/{task_id}/clean-image/generations
```

返回当前 task workspace 下的 clean image generation manifest 列表，用于前端显示历史结果。该接口只读取 `SessionScratch/CleanImageGenerations`，不合并业务资产。

### 7.4 Promote to Asset Library

```text
POST /api/koubo-storyboard/tasks/{task_id}/clean-image/{generation_id}/promote/asset-library
```

行为：

1. 读取 scratch image。
2. 按现有上传资产命名约定复制到：

```text
SessionOutput/storyboard/assets/images/<timestamp>_001_clean_generated_<short_id>.png
```

3. 写入同名 sidecar JSON。字段结构对齐现有 agent generated sidecar，包含 prompt 全文、provider、model、reference_count、request_id / generation_id 等。
4. 通过现有 `upsert_asset_manifest_item()` 更新：

```text
SessionOutput/storyboard/koubo_storyboard_assets.json
```

5. asset manifest item 使用现有结构，`source` 新增枚举值 `clean_generated`，`origin.tool = "clean_single_image_generation"`。
6. 在 clean generation manifest 的 `promotions` 里追加记录。

此动作之后，该图片才成为正式素材库资产，可被现有素材绑定流程使用。

### 7.5 Promote to Dialogue Image

```text
POST /api/koubo-storyboard/tasks/{task_id}/clean-image/{generation_id}/promote/dialogue-image
```

请求：

```json
{
  "dialogue_id": "DIA_001"
}
```

行为：

1. 读取 scratch image，并必须保证它在绑定时会被复制到 `SessionOutput/storyboard/Working/<asset_key>_Image_01.<ext>`。
2. P1 采用方案 C：在 `asset_source_type()` 中增加 `CLEAN_IMAGE_REL` 前缀判断，返回 `"generated"`。
3. 复用现有 `bind_asset_to_plan(workspace, plan, dialogue_id, source_rel, "image")` 完成 dialogue 绑定，而不是重新实现 `working_assets.images` / `bound_image_path` 写入。
4. 因为 scratch 路径会被归类为 `"generated"`，且不是 `WORKING_REL` 前缀，`bind_asset_to_plan()` 会走已有 `copy_to_working()` 分支，最终绑定 `Working` 路径，slot `source_type` 为 `"generated"`。
5. 使用现有 StoryBoard 保存函数保存 `srt_storyboard.json`，并同步 `koubo_storyboard_edit.json`。
6. 在 clean generation manifest 的 `promotions` 里追加记录，记录 `target = "dialogue_image"`、`dialogue_id`、`working_path`。

接口不需要 `segment_id`，因为 `bind_asset_to_plan()` 已通过 `dialogue_id` 查找 shot / scene / dialogue。

P1 第一版按现有 `bind_asset_to_plan()` 语义固定绑定 `Image_01`。若后续要支持 `Image_02` 或其他槽位，需要小幅扩展 `bind_asset_to_plan()` 增加 slot 参数。

不建议先 promote to asset library 再绑定，因为那会让 promote to dialogue 顺带写 `koubo_storyboard_assets.json`，违反第 9 节“promote 只改对应 target 文件”的约束。

slot 的 `source_type` 不新增 `clean_generated`，保持现有兼容值 `"generated"`。clean 来源通过 clean generation manifest 的 `promotions`、session event payload，以及必要时的 sidecar/origin 字段记录。

此动作之后，后续 VideoPlan / Composer 可以按现有 `working_assets.images` 机制使用该图片。

### 7.6 Promote to Host / Product Consistency

```text
POST /api/koubo-storyboard/tasks/{task_id}/clean-image/{generation_id}/promote/consistency
```

请求：

```json
{
  "kind": "host"
}
```

或：

```json
{
  "kind": "product"
}
```

行为：

1. 复制 scratch image 到：

```text
SessionContext/Consistency/HOST.png
SessionContext/Consistency/Product.png
```

2. 使用 `write_builder_section(workspace, kind, patch)` 更新对应 manifest，不直接写 JSON。
3. Manifest patch 记录来源：

```json
{
  "source_type": "clean_generated",
  "clean_generation_id": "cln_..."
}
```

4. Promote 会覆盖已有 `HOST.png` 或 `Product.png`。前端必须二次确认，后端也应在新 manifest 中保留被覆盖图片的来源记录，例如 `previous_output` / `previous_output_origin`。

此动作具有更高业务影响，前端应提供明确确认。

## 8. 是否能被业务流程使用

可以，但必须通过 promote 显式进入业务目录。

| 阶段 | 存放位置 | 前端显示位置 | 业务流程是否自动使用 |
|---|---|---|---|
| Generate | `SessionScratch/CleanImageGenerations` | Clean Image Modal 的结果预览和 clean generation history | 否 |
| Promote to asset library | `SessionOutput/storyboard/assets/images` + `SessionOutput/storyboard/koubo_storyboard_assets.json` | 右侧 `AssetPanel.jsx` 的“上传素材”页签，建议显示“干净生图”徽标 | 可作为素材选择和绑定 |
| Promote to dialogue image | `asset_source_type()` 将 scratch 识别为 `generated`，再通过 `bind_asset_to_plan()` 复制到 `SessionOutput/storyboard/Working` 并绑定 | 对应 `DialogueCard.jsx` 的“新图”slot | 可被 VideoPlan / Composer 使用 |
| Promote to consistency | `SessionContext/Consistency` | `KouboHostProductBuilder.jsx` 的 Host/Product 输出预览 | 可作为 Host / Product 一致性参考 |

默认 generate 结果应只在 Clean Image Panel 里展示，不应出现在普通资产库列表里，除非用户执行 promote。

这里的“素材库”特指 Koubo StoryBoard 右侧 AssetPanel 的上传素材池，不是独立的 Upload Asset Library Agent 页面。后端实际由 `asset_pool_meta()` 读取 `SessionOutput/storyboard/assets/images` 并合并 `koubo_storyboard_assets.json`，因此 `source = "clean_generated"` 的图片仍会进入 `uploaded_images` 集合，在前端表现为“上传素材”页签中的图片卡片。

## 9. 对业务流程的干扰控制

Generate 阶段必须保证不修改：

```text
SessionOutput/storyboard/srt_storyboard.json
SessionOutput/storyboard/koubo_storyboard_edit.json
SessionOutput/storyboard/koubo_storyboard_assets.json
SessionOutput/storyboard/Working/
SessionOutput/storyboard/assets/images/
SessionContext/Consistency/
asset_tasks.json
```

Generate 阶段不得触发：

- ImagePlan 状态更新
- VideoPlan 状态更新
- Composer candidate 更新
- Rebuild task 更新
- Asset Library 自动索引
- Host Product Builder 状态更新

Promote 阶段只允许修改与 promote target 对应的业务文件。比如 promote to asset library 不应修改 dialogue binding；promote to dialogue image 不应修改 Host/Product consistency。

## 10. 前端设计

### 10.1 功能入口

P0 主入口放在 Koubo StoryBoard 顶部 Header：

```text
OpenClip/frontend/src/KouboStoryBoard/components/KouboEditorHeader.jsx
```

形态：按钮 → Modal，完全复制 Host & Product Builder 的入口心智。

实施要求：

1. 在 `KouboEditorHeader.jsx` 的 Header action 区增加一个 icon-only 按钮，例如 title / aria-label 使用 `Clean Image` 或 `干净生图`。
2. 在父级 `OpenClip/frontend/src/KouboStoryBoardModule.jsx` 增加：

```text
const [cleanImageOpen, setCleanImageOpen] = createSignal(false)
const [cleanImageTarget, setCleanImageTarget] = createSignal(null)
```

3. Header 按钮点击时：

```text
setCleanImageTarget(null)
setCleanImageOpen(true)
```

4. 在 `KouboStoryBoardModule.jsx` 的 Modal 挂载区加入 `CleanImagePanel.jsx`，模式参考：

```text
OpenClip/frontend/src/KouboStoryBoard/hostProduct/KouboHostProductBuilder.jsx
```

5. `CleanImagePanel.jsx` 使用 `open` / `setOpen` / `task` / `api` / `runAction` 等与 Host & Product Builder 接近的 props 形态，内部管理 generate / preview / promote 状态。

不放到底部 Timeline：

- `KouboTimeline.jsx` 当前承载 ImagePlan、VideoPlan、Composer，是按 storyboard 驱动的全局生成流水线入口。
- Clean image 的核心语义是“与 storyboard 无关的独立单次工具”，放到 Timeline 会让用户误以为它参与 ImagePlan / VideoPlan / Composer 流水线。

不放到右侧 AssetPanel：

- `AssetPanel.jsx` 当前是素材库常驻侧栏，承载 source / upload / history 三个页签。
- 本方案第 8 节要求 scratch 结果默认不进入素材库列表。入口放 AssetPanel 会强化“生成即入库”的误解，正好放大第 14 节里“用户误以为 generate 后已进入素材库”的风险。

选择 Header 的理由：

- Header 已有 StoryBoard Agent 和 Host & Product Builder，都是“当前 task 级别的辅助工具”。
- Host & Product Builder 同样属于“独立生图 + 结果可显式影响业务”的工具，Clean image 与它的用户心智最接近。
- Modal 内的生图按钮、状态文案、SSE/长任务状态管理可以参考 `KouboHostProductBuilder.jsx` 的 `generateImage()` 和 draft phase 模式。

### 10.2 Modal 内容

建议新增一个轻量 modal：

- prompt textarea
- negative prompt textarea
- provider / model selector
- size selector
- explicit reference picker
- generate button
- clean generation history
- result preview
- promote actions

每张 clean generation 结果卡片必须显示状态标签：

- `未入库`：仅存在于 `SessionScratch/CleanImageGenerations`，不会出现在右侧素材库，也不会被业务流程使用。
- `已加入素材库`：已经复制到 `SessionOutput/storyboard/assets/images`，并出现在右侧 AssetPanel 的“上传素材”页签。
- `已绑定对白`：已经复制到 `SessionOutput/storyboard/Working`，并显示在目标 DialogueCard 的“新图”slot。
- `已设为一致性参考`：已经写入 `SessionContext/Consistency/HOST.png` 或 `Product.png`，会影响后续一致性参考。

Promote actions 应按业务影响分级：

- `加入素材库`
- `绑定到当前对白图片`
- `设为主播一致性图`
- `设为产品一致性图`

默认主按钮只做 generate，不做 promote。

前端展示时应明确区分：

- Clean scratch image：实验性结果，不参与业务流程。
- Asset library image：正式素材。
- Dialogue working image：已绑定对白。
- Consistency image：会影响后续一致性参考。

图片预览必须使用 `GET /api/koubo-storyboard/tasks/{task_id}/clean-image/{generation_id}/image`，不能依赖不存在的通用 raw-file 端点。

Promote 成功后的反馈必须告诉用户图片去了哪里：

- promote to asset library：提示 `已加入右侧素材库 / 上传素材`，并可提供 `打开上传素材页签` 动作，调用 `setActiveAssetTab("upload")`。
- promote to dialogue image：提示 `已绑定到当前对白 / 新图`，并可滚动到目标 `DialogueCard`。
- promote to consistency：提示 `已设为人物一致性参考` 或 `已设为产品一致性参考`，并可打开 Host & Product Builder 查看。

右侧 `AssetPanel.jsx` 的“上传素材”页签应对 `source = "clean_generated"` 的卡片显示 `干净生图` 徽标，避免用户把它和手动上传文件混为一类。这个徽标只改变展示，不改变现有 `uploaded_images` 数据分组。

### 10.3 P1 Dialogue Slot 入口

P1 支持 promote to dialogue image 时，必须解决“目标对白从哪里来”的交互问题。

第一入口仍然是 Header 全局 Modal。全局 Modal 里如果用户选择 `绑定到对白图片`，必须提供 segment/dialogue 选择器，至少展示：

- shot / scene / dialogue 层级
- dialogue 文本预览
- 当前 `Image_01` 是否已有绑定

更顺手的第二入口放在：

```text
OpenClip/frontend/src/KouboStoryBoard/components/DialogueCard.jsx
```

位置：`新图` 图片 slot。按钮文案建议为 `干净生图填充` 或 icon-only tooltip `用干净生图填充此槽`。

点击后打开同一个 `CleanImagePanel.jsx` Modal，但预置：

```json
{
  "target_type": "dialogue_image",
  "dialogue_id": "...",
  "slot": "Image_01"
}
```

P1 第一版由于后端 `bind_asset_to_plan()` 默认写 `Image_01`，前端也只开放 `Image_01` 目标。等后端支持 slot 参数后，再开放 `Image_02` 或其他槽位。

这个第二入口不能绕过 scratch 隔离：点击生成后仍先进入 `CLEAN_IMAGE_REL`，只有用户确认 `绑定到此对白` 后才执行 promote。

## 11. 后端实现步骤

### 11.1 新增 clean image service

职责：

1. 校验 prompt 非空。
2. 校验 reference paths 是调用方显式传入且位于当前 workspace 可读范围。
3. 解析 provider config。
4. 按固定规则构造 `effective_prompt`，P0 不扩展 provider 层 `negative_prompt` 参数。
5. 解析实际生效 size，并在 manifest 里写 `effective_size`。
6. 调用 `generate_image_bytes(config, effective_prompt, reference_paths, effective_size)`。
7. 写入 scratch image 和 manifest。

### 11.2 新增 clean image routes

注册路由：

```text
generate
image
list
promote/asset-library
promote/dialogue-image
promote/consistency
```

`image` 路由是 P0 必须新增的受限读取端点。实现必须使用 `safe_workspace_rel()`，并额外限制只能读取 `CLEAN_IMAGE_REL` 子树下的图片。

路由文件只负责 HTTP 输入输出、鉴权、错误转换；文件读取、文件复制、manifest 更新和业务状态修改放在 service 层。

### 11.3 复用现有业务写入逻辑

Promote 到业务流程时，应尽量复用已有服务函数：

- asset library 写入复用 `upsert_asset_manifest_item()`，sidecar JSON 对齐现有 agent generated 结构，`source` 新增 `clean_generated`。
- dialogue image binding 复用 `bind_asset_to_plan()` 和现有 StoryBoard 保存同步函数；P1 必须扩展 `asset_source_type()`，让 `CLEAN_IMAGE_REL` 前缀返回 `"generated"`，从而触发现有 `copy_to_working()` 分支。后续仅在需要选槽位时扩展 `bind_asset_to_plan()` slot 参数。
- consistency 写入复用 `write_builder_section()`，不要直接写 builder manifest JSON。

不要让 clean image service 自己发明一套新的 storyboard asset schema。

### 11.4 新增常量

在 `constants.py` 增加：

```python
CLEAN_IMAGE_REL = "SessionScratch/CleanImageGenerations"
```

所有 clean image 文件读写、路由校验和测试都引用该常量。

P1 promote to dialogue 还需要在 `asset_core_services.py` 的 `asset_source_type()` 中引用该常量：

```python
if rel_path.startswith(f"{CLEAN_IMAGE_REL}/"):
    return "generated"
```

该改动是保证 scratch 图片不会被原样写入 StoryBoard 的关键约束。

### 11.5 注册路由

在 Koubo StoryBoard router 初始化位置注册 clean image routes，保持与现有 route registration 风格一致。

## 12. 审计与排查

每次 generate 应记录 session event：

```text
koubo_storyboard.clean_image.generated
```

实现要求：

- 不新建“应用事件”基础设施。
- 复用 Koubo StoryBoard 现有事件写入方式：route/service 可调用 runtime 注入的 `add_event(session_id, kind, payload)`，或在 route 层用 `ctx.session_repo.add_event(...)` 记录。
- 如果 `clean_image_services.py` 保持纯 service、不持有 runtime deps，则由 `clean_image_routes.py` 在 service 返回后记录事件。
- P0 不降级为仅 logger 日志，因为现有 Koubo 路由已经大量使用 session event，Clean Image 应保持同一可观测路径。

事件字段建议：

```json
{
  "task_id": 78,
  "generation_id": "cln_...",
  "provider": "grok",
  "model": "grok-imagine-image",
  "effective_size": "1024x1024",
  "prompt_hash": "sha256:...",
  "reference_count": 0,
  "output_path": "SessionScratch/CleanImageGenerations/..."
}
```

不要把完整 prompt 写入 session event payload 或 logger，避免日志泄露；完整 prompt 只保存在 workspace 内的 manifest 中。

每次 promote 应记录 session event：

```text
koubo_storyboard.clean_image.promoted
```

字段包含：

```json
{
  "generation_id": "cln_...",
  "target": "asset_library",
  "target_path": "SessionOutput/storyboard/assets/images/..."
}
```

## 13. 测试与验收标准

### 13.1 Prompt 干净性

构造一个包含 storyboard、asset summary、consistency guide、历史 Agent 对话的 task。调用 clean generate，并 mock provider 断言：

- provider 收到的 prompt 与 manifest 中 `effective_prompt` 完全一致。
- 未填写 `negative_prompt` 时，`effective_prompt == prompt`。
- 填写 `negative_prompt` 时，`effective_prompt == prompt.rstrip() + "\n\nNegative: " + negative_prompt.strip()`。
- provider 请求里不包含 storyboard 文本。
- provider 请求里不包含 consistency guide。
- provider 请求里不包含 `Image_Grok.md` 等模板文本。
- provider 请求里不包含历史聊天内容。

### 13.2 文件树不变性

调用 generate 后，对比以下路径，必须无变化：

```text
SessionOutput/storyboard/
SessionContext/Consistency/
asset_tasks.json
```

唯一新增内容必须在：

```text
SessionScratch/CleanImageGenerations/<generation_id>/
```

### 13.3 Scratch 图片预览

执行 generate 后验证：

- `GET /api/koubo-storyboard/tasks/{task_id}/clean-image/{generation_id}/image` 能返回图片。
- 该接口不能读取 `SessionOutput/storyboard`、`SessionContext/Consistency` 或其他非 `CLEAN_IMAGE_REL` 子树文件。
- 非图片后缀、缺失文件、路径穿越都返回错误。

### 13.4 Promote to Asset Library

执行 promote 后验证：

- 图片复制到 `SessionOutput/storyboard/assets/images`。
- 文件命名符合现有 `{timestamp}_{index:03d}_{filename}` 风格。
- sidecar JSON 字段对齐现有 agent generated sidecar。
- `koubo_storyboard_assets.json` 新增资产记录。
- 资产记录 `source = "clean_generated"`，`origin.tool = "clean_single_image_generation"`。
- Clean Image Modal 显示状态 `已加入素材库`。
- 右侧 AssetPanel 的“上传素材”页签出现该图片，并显示 `干净生图` 徽标。
- 该资产可以通过现有素材库 UI 被选择并绑定。
- scratch 原图仍然存在。

### 13.5 Promote to Dialogue Image

执行 promote 后验证：

- 绑定逻辑复用 `bind_asset_to_plan()`，P1 默认绑定 `Image_01`。
- `asset_source_type("SessionScratch/CleanImageGenerations/...") == "generated"`。
- `bind_asset_to_plan()` 绑定后的 path 必须位于 `SessionOutput/storyboard/Working/`，不能保留 `SessionScratch/...`。
- 对应 slot 的 `source_type` 为 `"generated"`。
- 对应 dialogue 的 `working_assets.images` 和 `bound_image_path` 更新。
- Clean Image Modal 显示状态 `已绑定对白`，并标明目标 dialogue。
- 目标 `DialogueCard.jsx` 的“新图”slot 显示该图片。
- `srt_storyboard.json` 与 `koubo_storyboard_edit.json` 同步更新，避免 edit/source 状态漂移。
- 后续 VideoPlan / Composer 能读取并使用该图片。

### 13.6 Promote to Consistency

执行 promote 后验证：

- 图片复制到 `SessionContext/Consistency/HOST.png` 或 `Product.png`。
- 对应 manifest 通过 `write_builder_section()` 更新，并标明 `source_type = clean_generated`。
- Clean Image Modal 显示状态 `已设为一致性参考`。
- Host & Product Builder 输出预览能看到该图片。
- 如果覆盖已有 HOST/Product 图，manifest 保留被覆盖图的来源记录。
- 后续 ImagePlan / VideoPlan 能将其作为一致性参考图。

### 13.7 Scratch 清理安全性

把已经 promote 的 clean generation scratch 目录删除后，已进入业务流程的图片仍然可用。原因是 promote 使用复制，不使用移动或软链接。

对 dialogue promote 的额外断言：

- 删除 `CLEAN_IMAGE_REL/<generation_id>` 后，dialogue 的 `bound_image_path` 仍指向 `SessionOutput/storyboard/Working/...`。
- `SessionOutput/storyboard/Working/...` 文件仍存在。
- StoryBoard JSON 中不得残留该 generation 的 `SessionScratch/...` 图片路径作为绑定路径。

## 14. 风险与缓解

| 风险 | 缓解 |
|---|---|
| Scratch 图片被业务流程误扫描 | 使用 `SessionScratch/CleanImageGenerations`，现有业务读取逻辑不得扫描该目录 |
| Scratch 图片无法预览 | P0 新增受限图片读取端点，只读取 `CLEAN_IMAGE_REL` 子树 |
| 用户误以为 generate 后已进入素材库 | 前端明确标注“未加入业务流程”，promote 按钮单独呈现 |
| Prompt 仍被隐式污染 | service 层禁止读取业务上下文；单测 mock provider 检查 exactly-sent prompt |
| Dialogue promote 原样绑定 scratch 路径 | P1 扩展 `asset_source_type()`，让 `CLEAN_IMAGE_REL` 归类为 `generated`，强制复用 `copy_to_working()` |
| Promote 修改范围过大 | 每个 promote target 单独 service，测试对应文件变化范围 |
| 删除 scratch 影响业务图片 | promote 使用复制，并在 manifest 记录 target |
| 完整 prompt 泄露到全局日志 | session event 和 logger 只写 prompt hash，完整 prompt 仅进 manifest |

## 15. 分阶段落地

### P0：最小闭环

1. 在 `constants.py` 新增 `CLEAN_IMAGE_REL = "SessionScratch/CleanImageGenerations"`。
2. 新增 clean generate API。
3. 输出到 `CLEAN_IMAGE_REL/<generation_id>`。
4. 写 manifest，记录 `effective_prompt` 和 `effective_size`。
5. 新增受限图片读取端点，使用 `safe_workspace_rel()` 且只允许读取 `CLEAN_IMAGE_REL` 子树。
6. 在 `KouboEditorHeader.jsx` 增加 Clean Image 主入口按钮。
7. 在 `KouboStoryBoardModule.jsx` 增加 `cleanImageOpen` / `cleanImageTarget` 状态并挂载 `CleanImagePanel.jsx` Modal。
8. 前端能从 Header 打开 Modal，输入 prompt 并预览 scratch 结果。
9. 支持 promote to asset library，复用 `upsert_asset_manifest_item()` 和现有 sidecar JSON 风格。
10. 覆盖 prompt 干净性、preview 端点安全性和 generate 文件树不变性测试。

### P1：业务绑定

1. 支持 promote to dialogue image，复用 `bind_asset_to_plan()`。
2. 扩展 `asset_source_type()`，让 `CLEAN_IMAGE_REL` 前缀返回 `"generated"`，确保 scratch 图片通过 `copy_to_working()` 复制后再绑定。
3. 默认绑定 `Image_01`；如需选 `Image_02`，小幅扩展 `bind_asset_to_plan()` slot 参数。
4. slot `source_type` 沿用 `"generated"`；clean 来源记录在 manifest promotions / session event payload 中。
5. Header 全局 Modal 中提供 dialogue 选择器，用于选择 promote target。
6. 在 `DialogueCard.jsx` 的 `新图` slot 增加第二入口，打开同一个 Modal 并预置 `target_type = "dialogue_image"`、`dialogue_id`、`slot = "Image_01"`。
7. 支持后续 VideoPlan / Composer 使用 promoted image。
8. 增加 dialogue binding 回归测试，包含 `srt_storyboard.json` 与 `koubo_storyboard_edit.json` 同步断言、Working 路径断言、删除 scratch 后不断链断言。

### P2：一致性参考

1. 支持 promote to Host / Product consistency，复用 `write_builder_section()`。
2. 增加 manifest 来源记录和被覆盖图来源记录。
3. 增加一致性参考回归测试。

### P3：体验与治理

1. Clean generation history。
2. Scratch 清理策略。
3. Prompt hash 检索。
4. 批量删除未 promoted 的 scratch 结果。

## 16. 审核关注点结论

### 16.1 生成的图片放在哪里

默认放在：

```text
<workspace>/<CLEAN_IMAGE_REL>/<generation_id>/
```

不默认进入任何正式业务产物目录。

### 16.2 是否能被业务流程使用

能，但必须显式 promote：

- 加入素材库：复制到 `SessionOutput/storyboard/assets/images`。
- 绑定对白：`CLEAN_IMAGE_REL` 先被 `asset_source_type()` 识别为 `generated`，再复用 `bind_asset_to_plan()` 复制到 `SessionOutput/storyboard/Working` 并更新 StoryBoard 图片绑定。
- 设为一致性参考：复制到 `SessionContext/Consistency`，并用 `write_builder_section()` 更新 manifest。

### 16.3 是否会干扰业务流程

默认 generate 不会。它不改 storyboard、不改 asset index、不改 consistency、不触发 ImagePlan / VideoPlan / Composer。

### 16.4 是否会污染产出物文件夹

默认不会。正式产物目录只在 promote 时被显式写入，且每个 promote target 的写入范围可测、可审计、可回滚。
