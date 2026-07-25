# Koubo StoryBoard Dialogue Key 统一资源绑定需求与测试案例

版本：v0.1

状态：需求梳理稿。本文用于替代新增 Dialogue 临时 `manual` key 方案，统一界面绑定、Image Plan、Video Plan、Video Only Plan 的素材 key 规则，并给出最小回归测试集。

## 1. 背景

Task #8 / Session #9 暴露的问题不是单一的视频绑定 bug，而是新增 Dialogue 后出现了两套资源锚点：

1. 原始 Dialogue 使用 `srt_0001`、`srt_0002` 这类 SRT key 作为素材锚点。
2. 手动新增 Dialogue 被临时赋予 `scene_001_dialogue_003_manual...` 这类 manual key。

这会让界面绑定、Working 文件名、Image Plan、Video Plan、Video Only Plan、confirm-final 回写之间出现多套查找逻辑。尤其是视频与终视频会通过 `{asset_key}_Video_Raw.*`、`{asset_key}_Video_Final.*` 反查状态；一旦新增 Dialogue 没有进入执行计划，或者旧计划仍按旧 key 执行，资源就容易绑定不到，或者通过 fallback 误命中相邻 Dialogue。

本次调整目标：

- 移除新增 Dialogue 的 `manual` key 概念。
- 新增 Shot / Scene / Dialogue 都统一使用同一套 `dialogue_asset_key` 资源锚点。
- `dialogue_asset_key` 只表示唯一性和资源归属，不作为 UI 排序依据。
- 排序、展示、拖拽位置使用独立顺序字段或数组顺序。
- Segment 与 Dialogue 的关系必须显式使用 `dialogue_asset_key`，不能再混用 `dialogue_id`、`srt_id`、数组下标。

## 2. 术语与字段职责

| 字段 | 职责 | 是否可用于资源绑定 | 是否可用于排序 |
| --- | --- | --- | --- |
| `dialogue_asset_key` | Dialogue 的唯一资源锚点。Working 文件、素材绑定、计划任务、执行结果都按它归属。 | 是，唯一标准 | 否 |
| `dialogue_id` | Dialogue 的稳定编辑对象 ID。页面选中、编辑、删除、合并等操作用它定位对象。 | 否 | 否 |
| `srt_id` | 原始 SRT 来源标识，只代表来源，不代表当前资源归属。 | 否 | 否 |
| `srt_ids` | 多 SRT 合并来源列表，只代表来源 lineage。 | 否 | 否 |
| `dialogue_index` | 当前 Scene 内展示顺序。 | 否 | 是 |
| Dialogue 数组顺序 | 当前真实展示顺序。 | 否 | 是 |
| `segment.dialogue_ids` | Segment 覆盖的 Dialogue 资源锚点列表。字段名可保留，但值必须是 `dialogue_asset_key`。 | 是 | 保持计划内顺序 |
| `segment.asset_key` | Segment 代表 key，默认取 `segment.dialogue_ids[0]`。 | 是，Segment 级输出 | 否 |

约束：

- 所有运行时资源匹配只能以 `dialogue_asset_key` 为准。
- `srt_id`、`srt_ids` 只用于来源追踪；`dialogue_id` 只用于编辑对象定位；三者都不能作为运行时绑定 fallback。
- 新增 Dialogue 的 `srt_id` 可以为空，但 `dialogue_asset_key` 必须非空且全局唯一。

## 2.1 四个字段的最终职责契约

最终职责固定为：

```text
srt_id 只管原始来源。
dialogue_index 只管排序。
dialogue_id 只管编辑对象。
dialogue_asset_key 只管素材绑定和 Plan 执行。
```

这四个字段不能互相代替，尤其是素材绑定只能走 `dialogue_asset_key`。

## 2.2 三个 Key 的不可混用契约

`srt_id`、`dialogue_id`、`dialogue_asset_key` 在原始导入时可能看起来相关，但进入可编辑 StoryBoard 后必须分开使用。

### `srt_id`

定义：原始字幕来源 ID。

回答的问题：这条 Dialogue 来自原始 SRT 的哪一条。

例子：

```text
srt_id = srt_0004
```

允许用途：

- 展示原始字幕来源。
- 追踪 lineage，例如合并、拆分、审计时知道内容来自哪条 SRT。
- 迁移历史数据时辅助推导初始 `dialogue_asset_key`。

禁止用途：

- 禁止作为素材绑定 key。
- 禁止作为 Working 文件名前缀。
- 禁止作为 Image Plan / Video Plan / Video Only Plan 的 `asset_key` 或 `target_asset_key`。
- 禁止作为执行器回绑 StoryBoard 的查找 key。

新增 Dialogue 没有原始字幕来源时，`srt_id` 应为空；不能为了绑定方便伪造一个 `srt_id`。

### `dialogue_id`

定义：稳定的编辑对象 ID。

回答的问题：页面当前正在编辑、选中、删除、合并的是哪一个 Dialogue 对象。

例子：

```text
dialogue_id = dlg_a7c91e
dialogue_id = scene_001_dialogue_003
```

说明：可以继续兼容历史的 `scene_001_dialogue_003` 格式，但后续不能再解析它来判断当前 Scene 或排序；它只是一个稳定对象 ID。

允许用途：

- 前端 DOM 定位、选中态、编辑态。
- 页面按钮操作，例如选择、展开、删除、合并、Agent 指令定位。
- 操作日志和临时编辑状态里的对象引用。

禁止用途：

- 禁止作为素材绑定 key。
- 禁止作为 Working 文件名前缀。
- 禁止作为 Plan 的资源锚点。
- 禁止作为执行器最终回绑素材的依据。
- 禁止用作排序依据。

`dialogue_id` 应尽量不重算。新增 Dialogue 只给新增对象创建新的 `dialogue_id`；已有 Dialogue 的 `dialogue_id` 保持不变。Split Scene、Split Shot、Scene 重排、Dialogue 重排时，只更新其所在数组位置与 `dialogue_index`，不通过重算 `dialogue_id` 表达位置变化。

### `dialogue_index`

定义：当前 Scene 内的排序字段。

回答的问题：这条 Dialogue 当前排第几。

例子：

```text
dialogue_index = 1
dialogue_index = 2
dialogue_index = 3
```

允许用途：

- 页面展示排序。
- Scene 内上下移动、新增、删除、Split 后的顺序重算。
- 导出给用户看的当前顺序。

禁止用途：

- 禁止作为素材绑定 key。
- 禁止作为 Working 文件名前缀。
- 禁止作为 Plan 的资源锚点。
- 禁止作为执行器回绑 StoryBoard 的查找 key。

`dialogue_index` 可以并且应该在保存、重排、Split 后重算；它是顺序，不是身份。

### `dialogue_asset_key`

定义：Dialogue 的稳定素材锚点。

回答的问题：Audio / 原图 / 新图 / Prompt / Raw Video / Final Video 到底属于哪一条 Dialogue。

例子：

```text
dialogue_asset_key = srt_0004
dialogue_asset_key = srt_0004_01
dialogue_asset_key = srt_0004_01_01
```

允许用途：

- 作为所有 Dialogue 级 Working 文件名前缀。
- 作为页面拖拽绑定素材的唯一 key。
- 作为 Plan 生成时的 `asset_key` 来源。
- 作为执行器回绑 StoryBoard 的唯一查找 key。
- 作为 `segment.dialogue_ids` 的值。

禁止用途：

- 禁止用作 UI 排序依据。
- 禁止因为 Scene / Shot / Dialogue 重排而变化。
- 禁止在正常运行时 fallback 到 `srt_id` 或 `dialogue_id`。

### 原始导入与新增后的关系

原始导入时可以这样初始化：

```text
srt_id = srt_0004
dialogue_id = scene_001_dialogue_004
dialogue_index = 4
dialogue_asset_key = srt_0004
```

手动新增 Dialogue 时应该这样：

```text
srt_id = ""
dialogue_id = dlg_a7c91e
dialogue_index = 3
dialogue_asset_key = srt_0002_01
```

这表示：这条 Dialogue 没有原始 SRT 来源；页面编辑对象是 `dlg_a7c91e`；当前排第 3；素材归属锚点是稳定的 `srt_0002_01`。

### 新增 Dialogue 场景

```text
原始顺序：
D1: srt_id=srt_0001, dialogue_id=dlg_001, dialogue_index=1, dialogue_asset_key=srt_0001
D2: srt_id=srt_0002, dialogue_id=dlg_002, dialogue_index=2, dialogue_asset_key=srt_0002

在 D1 后新增：
D1:  srt_id=srt_0001, dialogue_id=dlg_001, dialogue_index=1, dialogue_asset_key=srt_0001
NEW: srt_id="",       dialogue_id=dlg_003, dialogue_index=2, dialogue_asset_key=srt_0001_01
D2:  srt_id=srt_0002, dialogue_id=dlg_002, dialogue_index=3, dialogue_asset_key=srt_0002
```

新增后只重算 `dialogue_index`；已有 Dialogue 的 `dialogue_id` 和 `dialogue_asset_key` 都不应变化。

## 3. 新增 Dialogue Key 规则

### 3.1 原始 key

原始 SRT Dialogue 继续使用：

```text
srt_0001
srt_0002
srt_0003
```

### 3.2 插入 key

新增 Dialogue 的 key 不再使用 `manual`，而是从插入位置左侧 Dialogue 派生子 key。

| 操作 | 结果 |
| --- | --- |
| 在 `srt_0004` 与 `srt_0005` 之间插入第一条 | `srt_0004_01` |
| 在 `srt_0004` 与 `srt_0005` 之间再插入第二条 | `srt_0004_02` |
| 在 `srt_0004_01` 与 `srt_0004_02` 之间插入 | `srt_0004_01_01` |
| 在 `srt_0004_01_01` 与 `srt_0004_01_02` 之间插入 | `srt_0004_01_01_01` |
| 在最后一条 `srt_0008` 后插入 | `srt_0008_01` |

### 3.3 生成算法

输入：当前 Scene / Shot / 全局 Dialogue 数组、插入位置、左邻 Dialogue、右邻 Dialogue。

规则：

1. 优先取左邻的 `dialogue_asset_key` 作为 parent key。
2. 在当前全局 key 集合中查找 parent 的直接子 key：`{parent}_01`、`{parent}_02`、`{parent}_03`。
3. 选择最小可用的两位序号，生成 `{parent}_{NN}`。
4. 如果插入位置在两个已插入子项之间，仍以左邻 key 作为 parent，例如 `srt_0004_01` 后插入得到 `srt_0004_01_01`。
5. 如果没有左邻，使用右邻的前置空间策略，建议生成 `srt_0000_01` 或要求 UI 禁止在第一条前新增；最终实现必须选择一种固定策略并写入测试。
6. key 生成后不可因为重排、Split Scene、Split Shot、保存重算而改变。

注意：key 字符串可以体现插入 lineage，但不能被用作排序来源。排序只能来自 Dialogue 数组顺序或 `dialogue_index`。

## 4. Working 素材命名规则

所有 Dialogue 级素材路径统一为：

| 槽位 | 标准路径 |
| --- | --- |
| Audio | `SessionOutput/storyboard/Working/{dialogue_asset_key}_Audio_Final.*` |
| 原图 | `SessionOutput/storyboard/Working/{dialogue_asset_key}_Image_Source.*` |
| 新图 | `SessionOutput/storyboard/Working/{dialogue_asset_key}_Image_New.*` |
| Image Prompt | `SessionOutput/storyboard/Working/{dialogue_asset_key}_ImagePrompt.json` |
| Video Prompt | `SessionOutput/storyboard/Working/{dialogue_asset_key}_VideoPrompt.json` |
| 新视频 | `SessionOutput/storyboard/Working/{dialogue_asset_key}_Video_Raw.*` |
| 终视频 | `SessionOutput/storyboard/Working/{dialogue_asset_key}_Video_Final.*` |

所有 Segment 级素材路径统一为：

| 槽位 | 标准路径 |
| --- | --- |
| Segment Audio | `SessionOutput/storyboard/Working/{segment.asset_key}_SegmentAudio_Final.*` |
| Segment Raw Video | `SessionOutput/storyboard/Working/{segment.asset_key}_Video_Raw.*` |
| Segment Final Video | `SessionOutput/storyboard/Working/{segment.asset_key}_Video_Final.*` |
| Tail Frame | `SessionOutput/storyboard/Working/{segment.asset_key}_TailFrame.*` |

`segment.asset_key` 默认等于 `segment.dialogue_ids[0]`，也就是 Segment 内第一条 Dialogue 的 `dialogue_asset_key`。

## 5. Segment 与 Dialogue 的关系

Segment 必须显式声明它覆盖哪些 Dialogue：

```json
{
  "segment_id": "shot_001_scene_001_segment_001",
  "asset_key": "srt_0004",
  "dialogue_ids": ["srt_0004", "srt_0004_01"],
  "dialogue_audio_tasks": [
    {
      "dialogue_asset_key": "srt_0004",
      "planned_audio_path": "SessionOutput/storyboard/Working/srt_0004_Audio_Final.wav"
    },
    {
      "dialogue_asset_key": "srt_0004_01",
      "planned_audio_path": "SessionOutput/storyboard/Working/srt_0004_01_Audio_Final.wav"
    }
  ],
  "planned_outputs": {
    "segment_audio_path": "SessionOutput/storyboard/Working/srt_0004_SegmentAudio_Final.wav",
    "video_prompt_path": "SessionOutput/storyboard/Working/srt_0004_VideoPrompt.json",
    "video_raw_path": "SessionOutput/storyboard/Working/srt_0004_Video_Raw.mp4",
    "video_final_path": "SessionOutput/storyboard/Working/srt_0004_Video_Final.mp4"
  }
}
```

约束：

- `segment.dialogue_ids` 的值必须是 `dialogue_asset_key`，不是 `dialogue_id`。
- `segment.asset_key` 只能用于 Segment 级资源，默认取第一条 Dialogue key。
- Dialogue 级 Audio 任务必须逐条使用各自的 `dialogue_asset_key`。
- Segment 跨多条 Dialogue 时，视频仍绑定到代表 key；UI 展示时需要能说明该 Segment 覆盖的 Dialogue 范围。
- 如果新增 Dialogue 后原 Segment 需要覆盖范围变化，必须重新生成相关 Plan，不能让旧 Segment 静默吸收新 Dialogue。

## 6. Plan 失效与重建规则

新增、删除、移动、Split Scene、Split Shot、合并 Dialogue 都属于结构变化。结构变化后必须更新 StoryBoard 的结构签名：

```text
dialogue_key_signature = stable_hash([
  shot_id,
  scene_id,
  dialogue_asset_key,
  dialogue_index,
  text,
  duration
])
```

Image Plan / Video Plan / Video Only Plan 必须记录生成时的签名：

```json
{
  "source_storyboard_hash": "...",
  "dialogue_key_signature": "...",
  "generated_from_storyboard_revision": "..."
}
```

执行规则：

1. 如果当前 StoryBoard 签名与 Plan 签名不一致，Plan 标记为 stale。
2. stale Plan 不允许执行生成或绑定，只允许查看历史结果、重新生成或显式迁移。
3. 计划任务的 `asset_key` / `target_asset_key` 必须在当前 StoryBoard 的 `dialogue_asset_key` 或 Segment `asset_key` 中精确存在。
4. 如果不存在，执行必须阻断并提示重新生成 Plan，不能 fallback 到 `srt_id`、`dialogue_id`、数组下标或 `_{index}`。

## 7. 当前代码梳理与移除范围

### 7.1 必须移除的 manual key 代码

| 文件 | 当前逻辑 | 处理要求 |
| --- | --- | --- |
| `frontend/src/modules/koubo/KouboStoryBoard/kouboStoryboardModel.js` | `manualDialogueAssetKey`、`newManualDialogueFields` 生成 `dialogue_manual` key。 | 删除 manual 生成；替换为插入式 `dialogue_asset_key` 生成函数。 |
| `frontend/src/modules/koubo/KouboStoryBoardModule.jsx` | `addDialogueAfter` 使用 `newManualDialogueFields`。 | 改为按左邻 key 生成层级 key；新增 Dialogue 初始化空素材。 |
| `frontend/src/modules/koubo/KouboStoryBoard/kouboAgentChat.js` | Agent `add_dialogue_after` 使用 `newManualDialogueFields`。 | 改为同一 key 生成函数，保证 UI 和 Agent 行为一致。 |
| `backend/opcrew_backend/koubo/koubo_storyboard/storyboard_plan_services.py` | `manual_dialogue_asset_key` 在 duplicate key 时生成 `_manual`，并清空来源与素材。 | 删除 manual repair；改为确定性层级 key 迁移或保存阻断。 |

### 7.2 必须收敛的多 key fallback

| 文件 | 当前逻辑 | 处理要求 |
| --- | --- | --- |
| `backend/opcrew_backend/koubo/koubo_storyboard/image_plan_routes.py` | `dialogue_match_keys` 同时匹配 `asset_key`、`dialogue_asset_key`、`srt_id`、`dialogue_id`、`srt_ids`、index suffix。 | 运行时只按 `dialogue_asset_key` 精确匹配；历史迁移单独处理。 |
| `backend/opcrew_backend/koubo/koubo_storyboard/video_plan_artifact_services.py` | `dialogue_match_keys` 用多 key 判断 final 是否已绑定、Plan asset 命中哪个 Dialogue。 | 改为 `target_asset_key == dialogue.dialogue_asset_key`。 |
| `backend/opcrew_backend/koubo/koubo_storyboard/video_plan_load_services.py` | `dialogue_lookup_keys` / `existing_working_slot_for_keys` 会从 `dialogue_id`、`srt_id`、`srt_ids` 查 Working 文件。 | 只查 `{dialogue_asset_key}_{slot}.*`；旧文件由迁移重命名。 |
| `backend/opcrew_backend/koubo/koubo_storyboard/video_only_plan_routes.py` | `dialogue_match_keys` 和 slot 查找同样多 key fallback。 | 只允许精确 key；缺失则 stale 或 blocked。 |
| `frontend/src/modules/koubo/KouboStoryBoard/kouboStoryboardTts.js` | `dialogueAssetKey()` fallback 到 `srt_id` / `srt_ids` / `dialogue_id`。 | 正常运行只读 `dialogue_asset_key`；缺失由加载规范化补齐。 |
| `ToolLibrary/Analysis_V1/05_03_ImagePlanGenerator.py` | `asset_key = first_dialogue_id(segment) or segment.asset_key or segment.segment_id`。 | `first_dialogue_id(segment)` 必须返回 `dialogue_asset_key`；移除 `segment_id` 运行时 fallback。 |
| `ToolLibrary/Analysis_V1/05_05_VideoOnlyPlanGenerator.py` | `actual_dialogues_by_asset` 用多 key 建索引，任务 key 可回退 `segment_id`。 | 只按 `dialogue_asset_key` 建索引；计划缺 key 直接 blocked。 |
| `ToolLibrary/Analysis_V1/05_02_VideoPlanExecutor.py` | `dialogue_match_keys` 用多 key 同步 edit StoryBoard；`segment_asset_key` 可回退 `segment_id`。 | 同步只按 `dialogue_asset_key`；`segment_asset_key` 只接受 `segment.asset_key` 或 `segment.dialogue_ids[0]`。 |
| `ToolLibrary/Analysis_V1/05_06_VideoOnlyPlanExecutor.py` | 复用 VideoPlanExecutor 的 `dialogue_match_keys`。 | 改为精确 key。 |

### 7.3 保留但需限定职责的代码

| 逻辑 | 保留条件 |
| --- | --- |
| `emptyDialogueWorkingAssets` | 可保留为新增 Dialogue 的空素材初始化函数，但不能和 manual key 绑定。 |
| 历史字段 `srt_id` / `srt_ids` | 可保留作来源 lineage，不再参与资源查找。 |
| legacy `assets.video` | 只在一次性迁移中读取，迁移完成后不参与运行时绑定。 |

## 8. 迁移规则

现有草稿或历史 Session 中可能已经存在 `*_manual*` key 或重复 `srt_000*` key。需要一次性迁移，不能长期保留运行时 alias。

迁移流程：

1. 读取当前 StoryBoard 数组顺序。
2. 遍历 Dialogue，保留合法且唯一的原始 key。
3. 对 `manual` key、空 key、重复 key，按当前位置左邻生成新的层级 key。
4. 建立临时迁移表：

```json
{
  "scene_001_dialogue_003_manual_171...": "srt_0002_01"
}
```

5. 按迁移表重命名 Working 文件：

```text
{old_key}_Audio_Final.* -> {new_key}_Audio_Final.*
{old_key}_Image_Source.* -> {new_key}_Image_Source.*
{old_key}_Image_New.* -> {new_key}_Image_New.*
{old_key}_ImagePrompt.json -> {new_key}_ImagePrompt.json
{old_key}_VideoPrompt.json -> {new_key}_VideoPrompt.json
{old_key}_Video_Raw.* -> {new_key}_Video_Raw.*
{old_key}_Video_Final.* -> {new_key}_Video_Final.*
{old_key}_TailFrame.* -> {new_key}_TailFrame.*
```

6. 更新 `koubo_storyboard_edit.json` / `srt_storyboard.json` 中的路径引用。
7. Image Plan / Video Plan / Video Only Plan 如果包含旧 key，默认标记 stale 并要求重新生成；只有必要时才做离线迁移。
8. 迁移完成后不得在运行时保留 `legacy_asset_keys` 或 old-to-new fallback。

## 9. 最小回归测试集

### KEY-01 原始 Dialogue 之间插入

步骤：

1. 初始 Dialogue：`srt_0004`、`srt_0005`。
2. 在 `srt_0004` 后新增 Dialogue。
3. 再次在 `srt_0004` 与 `srt_0005` 之间新增 Dialogue。

期望：

| 检查点 | 期望 |
| --- | --- |
| 第一条新增 | `dialogue_asset_key=srt_0004_01` |
| 第二条新增 | `dialogue_asset_key=srt_0004_02` |
| 原始 key | `srt_0004`、`srt_0005` 不变化 |
| 排序 | UI 顺序来自数组顺序，不通过 key 字符串排序 |

### KEY-02 插入到已插入 Dialogue 之间

步骤：

1. 已存在 `srt_0004`、`srt_0004_01`、`srt_0004_02`、`srt_0005`。
2. 在 `srt_0004_01` 与 `srt_0004_02` 之间新增 Dialogue。

期望：新增 key 为 `srt_0004_01_01`，其他 key 不变化。

### KEY-03 重排不改 key

步骤：

1. 将 `srt_0004_02` 拖到 `srt_0004_01` 前。
2. 保存并刷新。

期望：

| 检查点 | 期望 |
| --- | --- |
| key | 所有 `dialogue_asset_key` 保持原值 |
| index | `dialogue_index` 按新顺序重算 |
| 素材 | 已绑定素材仍跟随 key，不跟随原数组位置 |

### BIND-01 新增 Dialogue 五类素材绑定

步骤：

1. 在 `srt_0002` 后新增 Dialogue，得到 `srt_0002_01`。
2. 依次拖拽绑定 Audio、原图、新图、新视频、终视频。
3. 每绑定一次保存并刷新。

期望：

| 槽位 | 期望 |
| --- | --- |
| Audio | `{srt_0002_01}_Audio_Final.*` |
| 原图 | `{srt_0002_01}_Image_Source.*` |
| 新图 | `{srt_0002_01}_Image_New.*` |
| 新视频 | `{srt_0002_01}_Video_Raw.*` |
| 终视频 | `{srt_0002_01}_Video_Final.*` |
| 相邻 Dialogue | `srt_0002` 和 `srt_0003` 的五类槽位均不变化 |

### BIND-02 连续新增后视频不串绑

步骤：

1. 在 `srt_0002` 后连续新增两条 Dialogue，得到 `srt_0002_01`、`srt_0002_02`。
2. 给 `srt_0002_01` 绑定终视频 A。
3. 给 `srt_0002_02` 绑定终视频 B。
4. 清空 `srt_0002_02` 的终视频。

期望：

| 检查点 | 期望 |
| --- | --- |
| 独立性 | 清空 `srt_0002_02` 不影响 `srt_0002_01` |
| 反写 | confirm-final 只命中目标 `dialogue_asset_key` |
| 查找 | 不允许通过 `srt_0002` fallback 命中新增 Dialogue |

### BIND-03 Audio 短文本绑定不被清空

步骤：

1. 新增 `srt_0002_01`，文本为 `123`，duration 为 `0`。
2. 拖拽较长 Audio 到该 Dialogue。
3. 等待元数据加载，刷新。

期望：Audio 保持绑定，前端不因时长不匹配自动触发 clear。

### SCENE-01 Split Scene 不改变 Dialogue key

步骤：

1. 给 `srt_0002` 绑定五类素材。
2. 在 `srt_0001` 后执行 Split Scene，使 `srt_0002` 移入新 Scene。
3. 保存刷新。

期望：

| 检查点 | 期望 |
| --- | --- |
| key | `srt_0002` 保持不变 |
| binding | 五类素材仍绑定在 `srt_0002` |
| scene | Scene ID / index 可重算，但不影响 Dialogue 资源归属 |

### SHOT-01 Split Shot 不改变 Dialogue key

步骤：

1. 构造两个 Scene，后一个 Scene 内有 `srt_0003`。
2. 给 `srt_0003` 绑定五类素材。
3. 执行 Split Shot，使后一个 Scene 进入新 Shot。

期望：`srt_0003` key 和素材绑定均保持；Shot / Scene 重算不影响资源归属。

### PLAN-01 结构变化后旧 Plan stale

步骤：

1. 基于原 StoryBoard 生成 Image Plan / Video Plan / Video Only Plan。
2. 在 `srt_0002` 后新增 `srt_0002_01`。
3. 尝试执行旧 Plan。

期望：

| 检查点 | 期望 |
| --- | --- |
| stale | 三类 Plan 均提示结构签名不一致 |
| 执行 | 不允许继续生成或绑定 |
| 修复 | 用户重新生成 Plan 后才允许执行 |

### IMG-01 Image Plan 使用新增 key

步骤：

1. 新增 `srt_0002_01`。
2. 重新生成 Image Plan。

期望：

| 字段 | 期望 |
| --- | --- |
| `task.asset_key` | `srt_0002_01` 或对应 Segment 代表 key |
| prompt path | `SessionOutput/storyboard/Working/srt_0002_01_ImagePrompt.json` |
| image path | `SessionOutput/storyboard/Working/srt_0002_01_Image_New.*` |
| lookup | 不存在 `dialogue_id` / `srt_id` fallback 命中 |

### VID-01 Video Plan Segment 关系正确

步骤：

1. 新增 `srt_0004_01`。
2. 重新生成 Video Plan，让某 Segment 覆盖 `srt_0004` 与 `srt_0004_01`。

期望：

| 字段 | 期望 |
| --- | --- |
| `segment.dialogue_ids` | `["srt_0004", "srt_0004_01"]` |
| `segment.asset_key` | `srt_0004` |
| Dialogue audio | 两条 Dialogue 分别输出 `{key}_Audio_Final.*` |
| Segment video | 输出 `srt_0004_Video_Raw.*` / `srt_0004_Video_Final.*` |

### VONLY-01 Video Only Plan 精确匹配

步骤：

1. 新增 `srt_0002_01`。
2. 重新生成 Video Only Plan。
3. 执行只针对 `srt_0002_01` 的任务。

期望：

| 检查点 | 期望 |
| --- | --- |
| target | `target_asset_key=srt_0002_01` |
| 绑定 | 只写 `srt_0002_01_Video_Raw.*` / `srt_0002_01_Video_Final.*` |
| 负例 | `target_asset_key=srt_0002` 不得命中 `srt_0002_01` |

### SEG-01 Segment 跨原始与新增 Dialogue

步骤：

1. 存在 `srt_0004` 与 `srt_0004_01`。
2. 生成一个覆盖两条 Dialogue 的 Segment。

期望：

| 检查点 | 期望 |
| --- | --- |
| Segment 范围 | `dialogue_ids=["srt_0004", "srt_0004_01"]` |
| 代表 key | `asset_key=srt_0004` |
| Audio tasks | 每条 Dialogue 使用自己的 `dialogue_asset_key` |
| Segment audio | `srt_0004_SegmentAudio_Final.*` |

### MIG-01 manual key 迁移

输入：

```json
[
  {"dialogue_asset_key": "srt_0002"},
  {"dialogue_asset_key": "scene_001_dialogue_003_manual_1710000000000"},
  {"dialogue_asset_key": "srt_0003"}
]
```

期望：

| 检查点 | 期望 |
| --- | --- |
| 新 key | manual key 迁移为 `srt_0002_01` |
| 文件 | `{old_key}_Video_Final.*` 重命名为 `srt_0002_01_Video_Final.*` |
| JSON | StoryBoard 中不再出现 `manual` key |
| Plan | 旧 Plan 标记 stale |

### NEG-01 重复 key 不静默修复到 manual

输入：两条 Dialogue 都是 `dialogue_asset_key=srt_0002`。

期望：保存或重算时必须做确定性层级迁移为 `srt_0002`、`srt_0002_01`，或直接阻断并提示修复；不得生成 `_manual`。

### NEG-02 目标 key 不存在时阻断执行

步骤：

1. Plan 中有 `target_asset_key=srt_0002_99`。
2. 当前 StoryBoard 不存在该 key。
3. 执行 Image / Video / Video Only 任一任务。

期望：执行 blocked，提示重新生成 Plan；不得 fallback 到 `srt_0002`、`dialogue_id` 或 index suffix。

## 10. 验收口径

本次实现完成后，必须满足：

- 新增 Dialogue 不再产生任何包含 `manual` 的 `dialogue_asset_key`。
- 运行时资源绑定不再使用 `srt_id`、`dialogue_id`、`srt_ids`、数组下标作为 fallback。
- Image Plan、Video Plan、Video Only Plan 的任务 key 与 Working 文件名都能追溯到同一套 `dialogue_asset_key` / `segment.asset_key`。
- Split Scene / Split Shot 只改变结构位置，不改变既有 Dialogue 的资源 key。
- 旧 manual 草稿可以一次性迁移，迁移完成后运行时不保留第二套绑定逻辑。
