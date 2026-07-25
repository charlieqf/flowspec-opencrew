# Koubo StoryBoard 资源绑定需求与测试案例 v2

版本：v0.2-draft

状态：根据 `Koubo_Storyboard_DialogueKey_审核结果_v1.md` 重规划。本文替代 v0.1 中不准确的代码清单与部分 key 设计，作为实现前确认稿。

## 1. 核心目标

本次改造只解决一个根问题：

```text
资源绑定锚点必须是稳定、唯一、显式存在的 Dialogue 身份。
```

所有 Audio / Image / Prompt / Video 的绑定、生成、状态读取、执行回写，都只能通过 `dialogue_asset_key` 或 Segment 代表 `asset_key` 完成。

禁止运行时 fallback：

```text
禁止用 srt_id 推导素材 key。
禁止用 dialogue_id 推导素材 key。
禁止用 dialogue_index 推导素材 key。
禁止用数组下标推导素材 key。
```

## 2. 已确认字段职责

最终契约：

```text
srt_id 只管原始来源。
dialogue_index 只管排序。
dialogue_id 只管编辑对象。
dialogue_asset_key 只管素材绑定和 Plan 执行。
```

| 字段 | 用途 | 是否稳定 | 是否可重算 | 是否可用于素材绑定 |
| --- | --- | --- | --- | --- |
| `srt_id` | 原始 SRT 来源 lineage | 对原始字幕稳定 | 新增 Dialogue 可为空 | 否 |
| `dialogue_index` | 当前 Scene 内排序 | 不稳定 | 是 | 否 |
| `dialogue_id` | UI / API 编辑对象定位 | 应稳定 | 否，除迁移修复外 | 否 |
| `dialogue_asset_key` | 素材、Plan、执行锚点 | 必须稳定 | 否，除迁移修复外 | 是 |

编辑接口可以继续使用 `dialogue_id` 定位“要操作哪个 Dialogue 对象”。但定位到对象后，文件名前缀、Plan key、状态反查、执行回绑必须切换到该对象的 `dialogue_asset_key`。

## 3. 中心不变量

### 3.1 Dialogue asset key 恒非空

任何可保存的 StoryBoard 中，每条 Dialogue 必须满足：

```text
dialogue_asset_key 非空
dialogue_asset_key 全局唯一
dialogue_asset_key 不随排序、Split Scene、Split Shot、保存刷新变化
```

新增 Dialogue、Agent 新增 Dialogue、历史草稿加载、Plan 执行回写前，都必须检查这个不变量。

### 3.2 运行时 accessor 不允许 fallback

需要拆分后端 key helper：

| 函数 | 用途 | 是否允许从 `srt_id/dialogue_id` 推导 |
| --- | --- | --- |
| `derive_dialogue_asset_key(dialogue, context)` | 迁移 / 加载规范化 / 补齐历史数据 | 是，仅限规范化阶段 |
| `dialogue_asset_key(dialogue)` | 运行时取资源锚点 | 否，缺失即报错或 blocked |

当前风险总闸：

```text
backend/opcrew_backend/koubo/koubo_storyboard/asset_core_services.py
```

当前 `dialogue_asset_key()` 会 fallback 到 `srt_id` / `dialogue_id`。这必须改掉，否则即使各处 `dialogue_match_keys` 改成精确匹配，运行时仍会继续污染数据。

## 4. 对象定位 vs 资源绑定

不要误删 `dialogue_id`。

| 场景 | 允许用 `dialogue_id` 吗 | 说明 |
| --- | --- | --- |
| 页面选中 Dialogue | 允许 | 编辑对象定位 |
| `/asset-bind` 请求目标 | 允许 | 找到要操作的 Dialogue 对象 |
| `/asset-clear` 请求目标 | 允许 | 找到要清空的 Dialogue 对象 |
| 合并 / 删除 / Agent 修改 | 允许 | 编辑对象定位 |
| 复制文件到 Working | 禁止 | 必须用 `dialogue_asset_key` |
| Plan `asset_key` / `target_asset_key` | 禁止 | 必须用 `dialogue_asset_key` 或 Segment 代表 key |
| 执行器回绑 StoryBoard | 禁止 | 必须精确匹配 `dialogue_asset_key` |
| 状态接口反查已存在文件 | 禁止 | 必须只查 `{dialogue_asset_key}_{slot}.*` |

## 5. 新增 Dialogue 规则

新增 Dialogue 时：

1. 不复制左邻的 `dialogue_asset_key`。
2. 不复制左邻的 `working_assets`。
3. 不复制左邻的 `source_image_paths` / `image_path` / `bound_image_path`。
4. 创建新的稳定 `dialogue_id`。
5. 创建新的稳定 `dialogue_asset_key`。
6. 只重算 `dialogue_index`。
7. 已有 Dialogue 的 `dialogue_id` 不变。
8. 已有 Dialogue 的 `dialogue_asset_key` 不变。

当前需要修正的真实入口：

| 入口 | 当前问题 | 目标 |
| --- | --- | --- |
| `KouboStoryBoardModule.jsx:addDialogueAfter` | clone 左邻 Dialogue，容易继承 key / assets | 新建空 Dialogue，并调用统一 key 生成函数 |
| `kouboAgentChat.js:add_dialogue_after` | Agent 直接构造新增字段，需和 UI 同源 | 使用同一个新增 Dialogue 工厂 |
| `kouboStoryboardModel.js:renumberPlan` | 当前重算 `dialogue_id` | 只重算 `dialogue_index`、时间、Scene/Shot 结构，不重算已有 `dialogue_id` |
| `storyboard_plan_services.py:recalculate` | 当前重算 `dialogue_id`，且 duplicate 时可能改 key | 只重算 `dialogue_index`；key 修复仅在迁移规范化阶段执行 |

说明：当前工作区存在临时 `manualDialogueAssetKey/newManualDialogueFields/manual_dialogue_asset_key` 代码；审核基线认为这些符号不存在。无论来源如何，v2 目标都是移除 manual 概念，改为统一新增 Dialogue 工厂。

## 6. Dialogue asset key 生成策略

审核建议指出：素材绑定只需要稳定唯一，不需要 key 承载顺序或 lineage。已确认采用稳定不透明 key。

### 已确认方案：稳定不透明 key

```text
dialogue_asset_key = dak_8f3a2c9e
```

优点：

- 不承载排序语义，不会被误用排序。
- 不需要处理 `srt_0004_01_01` 这种无限层级边界。
- 首条前插入、空 Scene、跨 Scene 插入都无特殊规则。
- 新增、复制、移动、拆分都只需要保证唯一。

来源 lineage 可另存：

```json
{
  "srt_id": "",
  "source_after_asset_key": "srt_0004",
  "insert_origin": "after_dialogue"
}
```

### 已放弃方案：可读层级 key

```text
dialogue_asset_key = srt_0004_01
dialogue_asset_key = srt_0004_01_01
```

优点：

- 人眼能看出大概插入来源。

风险：

- 容易被误用排序。
- 在已有 `_01` 旁边再次插入时，key lineage 与视觉顺序可能反序。
- 首条前插入、空 Scene、跨 Scene 顶部插入需要额外规则。
- `_NN` 超过 99 的行为需要定义。

结论：采用稳定不透明 key；可读 lineage 如需保留，写入独立来源字段，不进入素材 key。

## 7. Segment 与 Dialogue 的关系

### 7.1 字段命名

已确认新增清晰字段：

```json
{
  "dialogue_asset_keys": ["srt_0004", "srt_0004_01"],
  "asset_key": "srt_0004"
}
```

兼容期：

- 读取旧 `segment.dialogue_ids`。
- 写入新 `segment.dialogue_asset_keys`。
- 如果继续写 `dialogue_ids`，必须显著标注：值是 `dialogue_asset_key`，不是 `dialogue_id`。

### 7.2 Segment asset key

Segment 代表 key：

```text
segment.asset_key = segment.dialogue_asset_keys[0]
```

它用于 Segment 级输出：

| 类型 | 路径 |
| --- | --- |
| Segment Audio | `{segment.asset_key}_SegmentAudio_Final.*` |
| Video Prompt | `{segment.asset_key}_VideoPrompt.json` |
| Raw Video | `{segment.asset_key}_Video_Raw.*` |
| Final Video | `{segment.asset_key}_Video_Final.*` |
| TailFrame | `{segment.asset_key}_TailFrame.*` |

### 7.3 Segment / Dialogue 视频路径共用

当前代码约定：Segment 视频产物绑定回代表 Dialogue，也就是首个 Dialogue 的 `dialogue_asset_key`。

已确认采用共用路径：

```text
代表 Dialogue 视频 == Segment 视频
路径 = {segment.asset_key}_Video_Raw.*
路径 = {segment.asset_key}_Video_Final.*
```

说明：

- 不新增 `{asset_key}_Segment_Video_*` 后缀。
- Segment 视频挂在第一条 Dialogue 的视频槽上。
- UI 必须明确：该视频槽展示的是代表 Dialogue 所在 Segment 的视频产物，覆盖范围由 `segment.dialogue_asset_keys` 表达。
- 如果 Segment 覆盖多条 Dialogue，后续 Dialogue 不重复显示同一个 Final Video，除非它本身成为另一个 Segment 的代表。

## 8. Prompt 归属

Image Prompt 与 Video Prompt 分开：

| Prompt | 归属 | 原因 |
| --- | --- | --- |
| ImagePrompt | Segment 级或代表 Dialogue 级，取决于 Image Plan task | 它为 Segment 第一帧 / 新图生成服务 |
| VideoPrompt | Segment 级 | 视频按 Segment 生成，不是单 Dialogue 生成 |

v2 推荐：`VideoPrompt` 从 Dialogue 级素材表移出，归入 Segment 级输出。

标准路径：

```text
SessionOutput/storyboard/Working/{segment.asset_key}_VideoPrompt.json
```

## 9. Plan stale 与签名

不新增孤立的 `dialogue_key_signature` 体系。必须复用并扩展现有：

```text
backend/opcrew_backend/koubo/koubo_storyboard/video_plan_signature_services.py
```

调整点：

1. `storyboard_structure_signature` 必须包含 `dialogue_asset_key`、`dialogue_id`、`dialogue_index`，其中结构身份锚点是 `dialogue_asset_key`。
2. `media_binding_signature` 当前锚在 `dialogue_id`，必须改为锚在 `dialogue_asset_key`。
3. Plan 生成时记录现有 signature 字段，不另造互不感知的新字段。

已确认第一阶段 stale 粒度：

| 方案 | 行为 | 优点 | 缺点 |
| --- | --- | --- | --- |
| 全局粗粒度 | 任意结构 / 文本 / 时长 / 绑定变化，三类 Plan 全 stale | 最安全，最容易落地 | 制作中途新增 Dialogue 会频繁要求重建 |
| 分段细粒度 | 只让受影响 Segment / Task stale | 用户体验更平滑 | 实现复杂，需要更多测试 |

结论：第一阶段采用全局粗粒度，先保证不误绑；第二阶段再评估 per-segment 增量。

## 10. 真实代码改造清单

### 10.1 StoryBoard 编辑层

| 文件 | 改造 |
| --- | --- |
| `frontend/src/modules/koubo/KouboStoryBoardModule.jsx` | `addDialogueAfter` 使用统一新增 Dialogue 工厂，不 clone 继承 key / assets |
| `frontend/src/modules/koubo/KouboStoryBoard/kouboAgentChat.js` | Agent 新增 Dialogue 使用同一工厂 |
| `frontend/src/modules/koubo/KouboStoryBoard/kouboStoryboardModel.js` | `renumberPlan` 不重算已有 `dialogue_id`，只重算 `dialogue_index` / 时间 |
| `backend/opcrew_backend/koubo/koubo_storyboard/storyboard_plan_services.py` | `recalculate` 不重算已有 `dialogue_id`；规范化阶段 enforce `dialogue_asset_key` 非空唯一 |

### 10.2 后端 key 总闸

| 文件 | 改造 |
| --- | --- |
| `asset_core_services.py` | 拆 `derive_dialogue_asset_key()` 与运行时 `dialogue_asset_key()` |
| `storyboard_plan_services.py` | 加载 / 迁移阶段用 derive；运行时不 fallback |
| `asset_reference_services.py` | 绑定文件前缀必须从显式 `dialogue_asset_key` 取，缺失报错 |
| `video_plan_load_services.py` | 状态查找不走多 key，按显式 `dialogue_asset_key` 查标准文件 |

### 10.3 Plan 生成与执行

| 文件 | 改造 |
| --- | --- |
| `05_01_VideoPlanGenerator.py` | `dialogue_key()` 改为读取显式 `dialogue_asset_key`；产出 `dialogue_asset_keys` |
| `05_02_VideoPlanExecutor.py` | `flatten_dialogues()` 按 `dialogue_asset_key` 建索引；回绑精确匹配 asset key |
| `05_03_ImagePlanGenerator.py` | 只接受 Video Plan 的 asset key，不 fallback `segment_id` |
| `05_04_ImagePlanExecutor.py` | 回绑使用 asset key index |
| `05_05_VideoOnlyPlanGenerator.py` | 只继承 asset key，不 fallback `segment_id` |
| `05_06_VideoOnlyPlanExecutor.py` | `prepare_segment_audio` / `bind_first_frame_to_storyboard` 按 asset key 查 Dialogue |
| `06_01_VideoPlanComposer.py` | 按 asset key 建 Dialogue 索引 |
| `video_plan_executor_modules/*` | `prompt_text_from_dialogues()` 使用 asset-key index |

### 10.4 后端状态与 confirm-final

| 文件 | 改造 |
| --- | --- |
| `image_plan_routes.py` | `dialogue_match_keys` 收敛为精确 asset key |
| `video_plan_artifact_services.py` | final bound 状态判断按 asset key |
| `video_only_plan_routes.py` | artifact status / materialize tail / confirm-final 反写按 asset key |
| `video_only_plan_routes.py:bind_video_in_storyboard_payload` | 显式点名改造，禁止 fallback 命中 |

## 11. 测试案例规划

### KEY-00 首条前 / 空 Scene 插入策略

待澄清后补最终期望：

- 若采用不透明 key：首条前、空 Scene、新 Scene 首条均生成唯一 `dak_*`。
- 若采用层级 key：必须定义 `head_*` 或禁止首条前插入。

### KEY-01 新增 Dialogue 不继承左邻资源 key

步骤：

1. D1 有 `dialogue_asset_key=srt_0001` 且有 Audio / Image。
2. 在 D1 后新增 NEW。

期望：

- NEW 有新的 `dialogue_id`。
- NEW 有新的 `dialogue_asset_key`。
- NEW 的 `working_assets` 为空。
- NEW 不继承 D1 的 Audio / Image / Video。
- D1 的 `dialogue_id`、`dialogue_asset_key` 不变。

### KEY-02 保存 / 刷新不重算 dialogue_id

步骤：

1. 记录所有 Dialogue 的 `dialogue_id`。
2. 新增 / 删除 / 移动 Dialogue。
3. 保存并刷新。

期望：

- 未被删除的 Dialogue 的 `dialogue_id` 不变。
- `dialogue_index` 按当前数组顺序重算。
- `dialogue_asset_key` 不变。

### KEY-03 Split Scene / Split Shot 不改变对象身份

步骤：

1. 给 D2 绑定五类素材。
2. Split Scene 或 Split Shot，使 D2 移到新结构。

期望：

- D2 的 `dialogue_id` 不变。
- D2 的 `dialogue_asset_key` 不变。
- D2 的素材路径不变。
- 只更新所在 Scene / Shot 位置与 `dialogue_index`。

### BIND-01 页面拖拽绑定只用 asset key

覆盖：

- Audio
- 原图
- 新图
- Raw Video
- Final Video

期望：

```text
{dialogue_asset_key}_Audio_Final.*
{dialogue_asset_key}_Image_Source.*
{dialogue_asset_key}_Image_New.*
{dialogue_asset_key}_Video_Raw.*
{dialogue_asset_key}_Video_Final.*
```

负例：

- 文件名前缀不得出现 `srt_id` fallback。
- 文件名前缀不得出现 `dialogue_id` fallback。

### BIND-02 asset-bind / asset-clear 保留 dialogue_id 对象定位

步骤：

1. 前端向 `/asset-bind` 发送 `dialogue_id`。
2. 后端通过 `dialogue_id` 找到对象。
3. 后端用该对象的 `dialogue_asset_key` 写 Working 文件。

期望：

- 请求目标仍可用 `dialogue_id`。
- 文件名前缀必须是 `dialogue_asset_key`。
- 清空时只清目标对象，不按 key fallback 清邻居。

### PLAN-01 Video Plan 产出 asset key

期望：

- `segment.asset_key == segment.dialogue_asset_keys[0]`。
- `segment.dialogue_asset_keys` 每一项都能在 StoryBoard 中精确找到。
- `dialogue_audio_tasks[].dialogue_asset_key` 存在。
- `planned_audio_path` 使用对应 Dialogue 的 asset key。

### PLAN-02 执行器按 asset key 取 Dialogue 文本

覆盖：

- `05_02.flatten_dialogues()`
- `video_plan_executor_modules/* prompt_text_from_dialogues()`
- `06_01_VideoPlanComposer`

期望：

- prompt 中能正确取到新增 Dialogue 文本。
- 不依赖 `srt_id` / `dialogue_id`。

### PLAN-03 Image Plan / Video Only Plan 不 fallback segment_id

期望：

- Image Plan task `asset_key` 来自 Video Plan。
- Video Only Plan task `asset_key` 来自 Video Plan。
- 如果 source segment 缺 asset key，任务 blocked，不用 `segment_id` 补。

### EXEC-01 Video Plan 执行回绑

期望：

- Audio 生成后按 `dialogue_asset_key` 回绑对应 Dialogue。
- Image 生成后按代表 asset key 回绑。
- Video Final 生成后按代表 asset key 回绑。
- 新增 Dialogue 不会绑定到相邻 Dialogue。

### EXEC-02 Video Only confirm-final 精确回绑

步骤：

1. 生成或准备 `{asset_key}_Video_Raw.mp4`。
2. 调用 Video Only confirm-final。

期望：

- 只匹配 `dialogue_asset_key == asset_key` 的 Dialogue。
- 不允许 `srt_id` / `dialogue_id` / index suffix 命中。

### SIG-01 media binding signature 锚定 asset key

步骤：

1. 修改 Dialogue 的 `dialogue_id` 不变 / 移动位置。
2. 素材绑定不变。

期望：

- `media_binding_signature` 不因位置变化误变。
- 如果素材路径改变，signature 改变。
- signature payload 中包含 `dialogue_asset_key`。

### MIG-01 历史空 key / 重复 key 修复

输入：

- 空 `dialogue_asset_key`
- 重复 `dialogue_asset_key`
- 临时 manual key

期望：

- 迁移阶段生成稳定唯一 key。
- 从左到右处理；左邻也是坏 key 时，使用已迁移后的左邻或新 key。
- Working 文件按迁移表重命名。
- 迁移后运行时不保留 old-to-new fallback。

### NEG-01 缺失 asset key 时运行时 blocked

步骤：

1. 构造运行时 Dialogue 缺 `dialogue_asset_key`。
2. 调用绑定 / Plan 执行 / 状态加载。

期望：

- 运行时 blocked 或 400。
- 不从 `srt_id` / `dialogue_id` 推导。

## 12. 已确认决策

### Q1：`dialogue_asset_key` 采用哪种生成策略？已确认

结论：采用不透明稳定 key，例如 `dak_8f3a2c9e`。

不采用可读层级 key，例如 `srt_0004_01`。

影响：

- 不透明 key 更稳，边界更少。
- 层级 key 更可读，但要处理首条前插入、跨 Scene 插入、`_NN` 溢出、lineage 与视觉顺序反序。

### Q2：Segment 视频是否与代表 Dialogue 视频共用路径？已确认

结论：共用，保持当前链路。

含义：

```text
Segment Final Video = 代表 Dialogue 的 Final Video
路径 = {segment.asset_key}_Video_Final.*
```

不新增 Segment 专用视频后缀。UI 需要说明视频槽展示的是 Segment 产物。

### Q3：Plan stale 第一阶段采用全局粗粒度还是分段细粒度？已确认

结论：第一阶段采用全局粗粒度，先保证不误绑。

含义：

- 新增 / 删除 / 移动 / 改文本 / 改时长 / 改绑定后，相关 Plan 整体 stale，需要重建。

如果后续要制作中途更顺滑，再进入第二阶段 per-segment 细粒度设计。

### Q4：`segment.dialogue_ids` 是否改名为 `dialogue_asset_keys`？已确认

结论：新增 `dialogue_asset_keys`，读取兼容旧 `dialogue_ids`。

原因：

- 避免继续把 `dialogue_id` 与 asset key 混在字段名里。

## 13. 实施顺序

1. 落 `dialogue_asset_key` 非空唯一规范化。
2. 停止重算已有 `dialogue_id`。
3. 拆后端 key helper，堵住运行时 fallback 总闸。
4. 修新增 Dialogue 工厂。
5. 改 Video Plan 产出端。
6. 改执行器与 prompt 模块消费端。
7. 改 Image Plan / Video Only Plan 继承端。
8. 改状态接口与 confirm-final。
9. 做历史迁移与回归测试。
