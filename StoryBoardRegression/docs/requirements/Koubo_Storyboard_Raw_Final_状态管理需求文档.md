# Koubo StoryBoard Raw / Final 状态管理需求文档

版本：v0.6

状态：状态优先级确认稿。本文件用于梳理 StoryBoard 中 `Audio / 原图 / New Image / Raw Video / Final Video` 的槽位语义、标准 Working 文件命名、后向优先状态判断、前端界面改动、后端绑定与工具适配范围，以及当前实现与目标需求之间的差异点。

## 1. 背景

StoryBoard 当前的素材槽位中，视频只有一个 `视频` 槽。这个槽在实际流程里同时承担了 `Raw Video` 和 `Final Video` 两个含义，导致 Plan 判断、拖拽绑定、删除和继续执行时容易混乱。

目标不是新增一套 JSON 文件或一套业务状态字段，而是在现有底层文件合同上，把 `Raw Video` 槽位和状态重新显性化。

核心链路如下：

```text
Audio
  -> 原图 + Image Prompt
  -> New Image
  -> New Image + Video Prompt
  -> Raw Video
  -> Final Video
```

其中 Final 的形成方式按 Dialogue 类型分支：

```text
口播:
  Raw Video + Audio -> Lip Sync -> Final Video

空镜:
  Raw Video + Audio -> audio_replace_retime / 替换音频 -> Final Video

Video Only Plan Confirm:
  Raw Video -> 直接拷贝为 Final Video
```

## 2. 设计原则

### 2.1 不新增 JSON 文件和业务状态字段

本需求不新增类似以下文件或字段：

```text
raw_video_state.json
storyboard_slot_state.json
dialogue.raw_video
dialogue.final_video
working_assets.raw_video
```

状态必须从现有文件和现有绑定派生：

```text
Raw Video:
SessionOutput/storyboard/Working/{asset_key}_Video_Raw.mp4

Final Video:
SessionOutput/storyboard/Working/{asset_key}_Video_Final.mp4
working_assets.video.path
```

`Final Video` 继续使用现有 `working_assets.video` 表达。`Raw Video` 是固定命名文件产物，不写入新的 StoryBoard JSON 字段。

### 2.2 状态判断从后往前看

工作流状态判断必须从 Final 往前看，而不是从 Audio / 原图往后级联删除。

优先级：

```text
Final Video
  > Raw Video
  > New Image
  > 原图
  > Audio
```

判断规则：

1. 如果 `Final Video` 存在并绑定，当前 Dialogue / Segment 视为已完成。
2. 如果 `Final Video` 不存在，但 `Raw Video` 存在，下一步只需要到 Final。
3. 如果 `Raw Video` 不存在，但 `New Image` 存在，下一步生成 Raw，再到 Final。
4. 如果 `New Image` 不存在，但 `原图` 存在，下一步生成 New Image。
5. 如果 `原图` 不存在，先补原图。
6. 如果是 Video Plan，从 Raw 到 Final 需要 Audio；如果缺 Audio，下一步先补 Audio。
7. 如果是 Video Only Plan，Raw 到 Final 的 Confirm 不需要 Audio。

### 2.3 删除和替换不级联

用户删除或替换某个槽位时，只影响用户明确操作的槽位。不得因为前置素材改变而自动删除已经更接近 Final 的产物。

换句话说：

```text
删除前面的内容，不自动删除后面的内容。
删除后面的内容，不自动删除前面的内容。
Plan 根据剩余最靠后的可用产物，选择最少步骤到 Final。
```

### 2.4 保留现有 JSON 文件体系

本次大改动暂不收敛、合并或删除现有 Plan / State / Result / Cache / Settings JSON 文件。

必须保留：

```text
SessionOutput/storyboard/video_generation_plan.json
SessionOutput/storyboard/video_generation_plan.ui_cache.json
SessionOutput/storyboard/video_plan_settings.json
SessionOutput/storyboard/video_plan_execution_state.json
SessionOutput/storyboard/video_plan_execution_result.json
SessionOutput/storyboard/image_generation_plan.json
SessionOutput/storyboard/image_plan_execution_state.json
SessionOutput/storyboard/image_plan_execution_result.json
SessionOutput/storyboard/video_only_generation_plan.json
SessionOutput/storyboard/video_only_plan_execution_state.json
SessionOutput/storyboard/video_only_plan_execution_result.json
SessionOutput/storyboard/video_plan_compose_state.json
SessionOutput/storyboard/video_plan_compose_result.json
```

### 2.5 槽位文件名是金标准

绑定到 StoryBoard 槽位的素材，无论来自以下哪一种来源：

```text
原始素材
上传素材
历史素材
工具生成素材
已有 Working 素材
```

保存绑定时都必须复制或物化到 `SessionOutput/storyboard/Working/` 下的标准槽位文件名。不能继续沿用来源素材自己的原文件名。

标准槽位文件名：

| 槽位 | 标准文件名 | 说明 |
| --- | --- | --- |
| Audio | `{asset_key}_Audio_Final.{ext}` | 语音槽位 |
| 原图 | `{asset_key}_Image_Source.{ext}` | 生成新图的参考图 |
| 新图 | `{asset_key}_Image_New.{ext}` | 生成新视频的首帧或新画面 |
| 新视频 | `{asset_key}_Video_Raw.{ext}` | Raw Video |
| 终视频 | `{asset_key}_Video_Final.{ext}` | Final Video |
| 尾帧 | `{asset_key}_TailFrame.{ext}` | 后续 Segment 可用的尾帧 |

硬性规则：

1. `原图` 只能落到 `Image_Source`，不能落到 `Image_New`。
2. `新图` 只能落到 `Image_New`，不能复用 `Image_Source`。
3. 旧的 `Image_01` 不能再作为新实现中的业务槽位名使用。
4. StoryBoard JSON 中保存的绑定路径必须指向标准槽位文件。
5. UI、Plan 和执行工具判断完成态时，以标准槽位绑定和真实文件存在为准。
6. 绑定状态和 Working 文件必须一致；不能出现 JSON 绑定为空但 Working 旧文件仍被当作完成，也不能出现 JSON 绑定指向来源素材而 Working 标准槽位缺失。
7. 替换同一槽位时，旧标准槽位文件先进入 Asset History，再写入新标准槽位文件。
8. 删除槽位时，清空绑定，并把对应标准槽位文件移入 Asset History。

说明：

1. `video_plan_settings.json` 是 Video Plan / Image Plan / Video Only Plan 当前共用的参数来源，必须保留。
2. `video_generation_plan.ui_cache.json` 服务于 Video Plan 的缓存检查与复用逻辑，必须保留。
3. 本次不新增统一状态 JSON，不把 Raw / Final 状态写入 StoryBoard JSON 新字段。
4. UI 最终显示状态以后向派生判断为准，但底层文件布局保持现状。
5. 凡是可以从现有 StoryBoard JSON、Working 文件状态、现有 Plan / Execution JSON 计算得到的状态，都不得新增持久字段或新增 JSON 文件，必须用函数实时计算。

## 3. 前端槽位需求

### 3.1 槽位顺序

DialogueCard 的槽位顺序改为：

```text
Audio / 原图 / New Image / 新视频 / 终视频
```

现有 `视频` 槽位必须拆成两个明确槽位，前端不再显示泛泛的 `Video / 视频`：

1. `新视频`：内部 target 为 `raw_video`，对应 Raw Video，未最终合成。
2. `终视频`：内部 target 为 `final_video`，对应 Final Video，最终可交付。

### 3.2 槽位语义

| 槽位 | 业务含义 | 持久表达 |
| --- | --- | --- |
| Audio | 语音输入，口播对口型或空镜配音使用 | `working_assets.audio.path`，标准文件 `{asset_key}_Audio_Final.{ext}` |
| 原图 | 生成 New Image 的参考图 | `source_image_paths[0]` / `image_path`，标准文件 `{asset_key}_Image_Source.{ext}` |
| New Image / 新图 | 生成 Raw Video 的首帧或新画面 | `working_assets.images[0].path` / `bound_image_path`，标准文件 `{asset_key}_Image_New.{ext}` |
| 新视频 | 未最终合成的 Raw Video | `Working/{asset_key}_Video_Raw.{ext}` |
| 终视频 | 最终可交付的 Final Video | `working_assets.video.path` / `Working/{asset_key}_Video_Final.{ext}` |

槽位显示文案：

1. StoryBoard 主界面使用中文：`音频 / 原图 / 新图 / 新视频 / 终视频`。
2. Image Plan 使用中文：`提示词 / 新图 / 提示词+新图`。
3. Video Only Plan 使用中文：`提示词 / 新视频 / 提示词+新视频`，步骤为 `音频 / 新图 / 提示词 / 新视频 / 拷贝成终视频`。
4. Video Plan 使用中文：`音频 / 新图 / 新视频 / 终视频`。

### 3.3 Raw Video 槽位显示

Raw Video 槽不从 `working_assets` 读取。它应通过 `asset_key` 派生路径：

```text
SessionOutput/storyboard/Working/{asset_key}_Video_Raw.mp4
```

前端可以通过后端返回的派生状态判断文件是否存在，也可以由详情接口直接返回可预览 URL。不要把 Raw 路径写回 `working_assets`。

### 3.4 Final Video 槽位显示

Final Video 继续使用现有逻辑：

```text
dialogue.working_assets.video.path
```

正常执行路径下，Final 文件存在就应该已经绑定。Video Plan / Video Only Plan / 终视频槽位替换一旦成功生成或写入 `Working/{asset_key}_Video_Final.*`，必须同步写回 `working_assets.video.path`，确保文件状态和任务执行状态一致。

当 `working_assets.video.path` 为空，但规范 Final 文件存在时，这是异常修复状态，不是正常业务状态。UI 显示终视频绿色，并提示“Final 文件存在但未绑定，可修复绑定”。是否自动绑定必须由已有确认动作、保存动作或专门的修复动作完成，不应由前端静默写入。

可能出现“Final 文件存在但未绑定”的典型情况：

1. Video Only Plan 或 Video Plan 工具已经生成了规范 Final 文件，但 StoryBoard JSON 绑定写回失败或被中断。
2. 用户从外部手工拷贝 Final 文件到标准 Working 路径。
3. 历史流程或迁移脚本只恢复了文件，没有同步恢复 `working_assets.video.path`。
4. Asset History 恢复或人工拷贝后，物理文件恢复了，但绑定关系尚未恢复。
5. 并发执行、页面刷新、进程中断或保存失败导致文件写入成功但 JSON 写回失败。

该状态的目标是修复绑定，而不是要求用户重新生成 Final 或 Raw。

确认入口只放在 `终视频` 槽位上。Video Plan / Video Only Plan Modal 可以提示状态，但不提供额外的绑定入口。

如果 `working_assets.video.path` 存在，但物理文件已经不存在：

1. 不自动清空绑定。
2. 不视为完成。
3. UI 显示终视频文件丢失或不可用。
4. 用户可以手动清除、重新绑定或重新生成。

### 3.5 执行成功与 Working 文件一致性

任务执行成功不能只以 execution JSON 的 `success / completed` 为准。对普通槽位而言，成功完成必须形成以下闭环：

```text
工具执行成功
-> 产物写入 Working 标准槽位文件
-> StoryBoard JSON 对应槽位绑定同步写回
-> 统一派生函数重新读取 Working 文件和绑定
-> UI 显示对应槽位绿色
```

普通槽位的完成态判定：

| Working 标准文件 | StoryBoard 绑定 | UI 状态 | 处理方式 |
| --- | --- | --- | --- |
| 存在 | 已绑定到该标准文件 | 绿色 | 正常完成 |
| 存在 | 未绑定或绑定为空 | 绿色 + 修复提示 | 异常修复态，提示修复绑定，不要求重跑 |
| 不存在 | 绑定存在 | 不算完成 | 提示文件丢失或绑定失效 |
| 不存在 | 绑定为空 | 按派生规则白 / 灰 / 黄 / 红 | 正常未完成 |

硬性规则：

1. 绿色只认当前 Working 标准槽位文件真实存在，或本轮执行刚成功并已落到标准 Working 文件。
2. execution JSON 只作为黄 / 红 / 辅助说明来源，不能单独制造绿色完成态。
3. 任务执行成功后，必须同步写回对应 StoryBoard 绑定；写文件成功但绑定失败时，进入“文件存在但绑定缺失”的异常修复态。
4. 绑定存在但物理文件不存在时，不自动清空绑定，也不显示绿色；UI 提示用户清除、重新绑定或重新生成。
5. Prompt 是唯一例外：Prompt 不通过普通槽位清除动作删除；Prompt 文件在 Working 中存在就显示绿色，允许打开、修改、保存。
6. 三类 Plan 和 StoryBoard 主界面必须消费同一套派生状态，不能分别按 execution JSON、plan item 或局部缓存独立判断完成态。

## 4. 删除和替换规则

### 4.0 界面删除与 Working 文件一致性

用户在界面清除普通槽位时，不能只清 StoryBoard JSON 绑定。必须同步处理对应 Working 标准文件，使“界面所见”和“Working 文件状态”一致：

```text
用户清除槽位
-> 清空 StoryBoard JSON 对应绑定
-> 将对应 Working 标准槽位文件移走或归档到 Asset History
-> 统一派生函数重新读取 Working 文件和绑定
-> UI 按当前真实状态显示颜色
```

删除后的一致性要求：

1. 不能出现“界面显示空槽，但 Working 标准文件仍被当作当前完成产物”的状态。
2. 不能出现“界面显示完成，但 Working 标准文件不存在”的状态。
3. 删除动作只影响用户明确操作的槽位，不级联删除更上游或更下游产物。
4. 被移走的 Working 标准文件进入 Asset History；当前 Working 标准路径必须不存在。
5. Prompt 不随普通槽位删除而删除；只有专门的 Prompt 编辑/重建流程可以修改 Prompt 文件。

槽位删除隔离规则：

| 用户清除槽位 | 必须清空的绑定 | 必须移走 / 归档的 Working 标准文件 | 不得影响 |
| --- | --- | --- | --- |
| 音频 | Audio 绑定 | `{asset_key}_Audio_Final.*` 或 Segment Audio 标准文件 | 原图 / 新图 / Raw / Final |
| 原图 | `Image_Source` / 原图绑定 | `{asset_key}_Image_Source.*` | 新图 / Raw / Final / Prompt |
| 新图 | `Image_New` / 新图绑定 | `{asset_key}_Image_New.*` | 原图 / Raw / Final / Prompt |
| 新视频 | Raw 绑定 | `{asset_key}_Video_Raw.*` | Final / 图片 / 音频 / Prompt |
| 终视频 | Final 绑定 | `{asset_key}_Video_Final.*` | Raw / 图片 / 音频 / Prompt |

### 4.1 操作矩阵

| 用户操作 | Audio | 原图 | New Image | Raw Video | Final Video | 下一步判断 |
| --- | --- | --- | --- | --- | --- | --- |
| 删除 Audio | 删除 | 不动 | 不动 | 不动 | 不动 | 若 Final 存在则完成；若只有 Raw，则先补 Audio 再到 Final |
| 替换 Audio | 替换 | 不动 | 不动 | 不动 | 不动 | 若 Final 存在则完成；否则用新 Audio 继续 |
| 删除 原图 | 不动 | 删除 | 不动 | 不动 | 不动 | 从 Final / Raw / New Image 后向判断 |
| 替换 原图 | 不动 | 替换 | 不动 | 不动 | 不动 | 从 Final / Raw / New Image 后向判断 |
| 删除 New Image | 不动 | 不动 | 删除 | 不动 | 不动 | 若 Final 存在则完成；若 Raw 存在则从 Raw 到 Final |
| 替换 New Image | 不动 | 不动 | 替换 | 不动 | 不动 | 若 Final 存在则完成；若 Raw 存在则从 Raw 到 Final；用户手动删除 Raw 后才用新图重生 Raw |
| 删除 Raw Video | 不动 | 不动 | 不动 | 删除 | 不动 | 若 Final 存在则完成；否则从 New Image 生成 Raw |
| 替换 Raw Video | 不动 | 不动 | 不动 | 替换 | 不动 | 若 Final 存在则完成；否则从 Raw 到 Final |
| 删除 Final Video | 不动 | 不动 | 不动 | 不动 | 删除 | 若 Raw 存在则从 Raw 到 Final |
| 替换 Final Video | 不动 | 不动 | 不动 | 不动 | 替换 | 完成 |

说明：

1. 删除 Raw / Final 的用户体验叫删除，底层进入 Asset History 归档，不做不可恢复的直接丢弃。
2. 替换 Raw 时，新视频统一复制或归档到规范 Raw 文件路径，并覆盖当前 Raw 语义；不影响 Final。
3. 替换 Final 时，终视频统一复制或归档到规范 Final 文件路径，并更新 `working_assets.video.path`。
4. 删除 Raw / Final 后，当前 Working 标准路径必须移除，使对应槽位显示为空；历史副本由 Asset History 保留。
5. 替换 Raw / Audio / New Image 后，如果 Final 已存在，Final 仍保持完成态，不额外提示不一致。
6. 替换原图时，来源图片必须复制为 `{asset_key}_Image_Source.{ext}`，并更新 `source_image_paths[0]` / `image_path`。
7. 替换新图时，来源图片必须复制为 `{asset_key}_Image_New.{ext}`，并更新 `working_assets.images[0].path` / `bound_image_path`。
8. 从原始素材、上传素材、历史素材拖入原图或新图时，都必须按目标槽位重新命名到 Working；不能保留来源图片文件名。
9. 删除原图时，只清空原图绑定，并归档 `Image_Source`；不得清空新图。
10. 删除新图时，只清空新图绑定，并归档 `Image_New`；不得清空原图、Raw 或 Final。

### 4.2 最少步骤原则

每次判断下一步时，系统必须选择离 Final 最近的路径。

示例：

1. 用户替换 Audio，但 Raw 和 Final 都存在：不动 Raw 和 Final，状态仍显示 Final 完成。
2. 用户删除 New Image，但 Raw 存在：不要求重新生成 New Image，下一步仍从 Raw 到 Final。
3. 用户删除 Final，但 Raw 存在：Video Only Plan 下一步是 Confirm Final；Video Plan 下一步是 Lip Sync 或音频替换；都不重新生成 Raw。
4. 用户替换 New Image 后想用新图重新生 Raw：用户必须手动删除 Raw，系统才从 New Image 重新生成 Raw。

## 5. Plan 判断规则

### 5.0 统一槽位任务色彩优先级

本节定义所有 StoryBoard Plan Modal 中的任务颜色，不新增持久状态字段，只作为 UI 派生状态规则。

颜色含义：

| 颜色 | 语义 | 判定来源 |
| --- | --- | --- |
| 绿 | 当前槽位已经完成 | 对应槽位的标准 Working 文件真实存在，或本轮执行刚成功并已落到标准 Working 文件 |
| 黄 | 当前槽位正在执行 | 当前执行状态为 queued / running，且对应槽位尚未完成 |
| 白 | 当前槽位等待执行 | 执行条件满足，且没有更下游产物使它变成无需执行 |
| 灰 | 当前槽位无法执行或无需执行 | 执行条件不满足，或更下游产物已经存在导致本槽位不需要执行 |
| 红 | 当前槽位执行失败 | 当前执行状态为 failed，且对应槽位尚未完成 |

优先级必须固定为：

```text
绿 > 黄 > 红 > 白 > 灰
```

硬性规则：

1. 只要当前槽位标准 Working 文件真实存在，该槽位显示绿色，覆盖旧 execution JSON 中的 queued / running / failed / skipped / blocked。
2. 如果没有当前槽位文件，但执行状态显示当前槽位正在执行，显示黄色。
3. 如果没有当前槽位文件，且执行状态显示当前槽位失败，显示红色。
4. 如果没有当前槽位文件，但执行条件满足并且没有更下游产物让它无需执行，显示白色。
5. 如果没有当前槽位文件，且执行条件不满足，显示灰色。
6. 如果没有当前槽位文件，但更下游产物已经存在，本槽位显示灰色，表示无需执行。
7. 对 `新图` 而言，如果 `新视频` 或 `终视频` 已存在，且 `Image_New` 当前不存在，则 `新图` 显示灰色，不显示白色。
8. 对 `新视频` 而言，如果 `终视频` 已存在，且 `Video_Raw` 当前不存在，则 `新视频` 显示灰色，不显示白色。
9. 对 `音频` 而言，因为音频由独立 Plan 操作，不存在“下游已有所以无需执行”的灰色状态；音频文件存在则绿色，音频文件不存在则白色。
10. 对 `终视频` 而言，如果 `Video_Final` 不存在但 `Video_Raw` 存在，则显示白色，表示可通过 Video Only Plan 手动拷贝 / Confirm 到 Final；音频缺失只影响 Video Plan 自动合成按钮，不把终视频槽置灰。
11. 对 `Prompt` 而言，如果 Prompt 文件存在，显示绿色；如果 Prompt 文件不存在但执行条件满足，显示白色；如果 Prompt 文件不存在且条件不满足或已被更下游产物消费，显示灰色。
12. 旧 execution state/result 只能决定黄、红和辅助说明，不能压过真实 Working 文件的绿色，也不能让已经不存在的文件继续显示绿色。
13. Plan 生成和执行按钮可用性也必须按同一套派生状态判断，不能只看旧 plan step 的 required/status。
14. 任务执行成功并生成槽位文件时，必须同步写回对应槽位绑定；用户在界面清除槽位绑定时，必须同步把对应 Working 标准文件移走或归档到 Asset History。
15. 除 Prompt 外，槽位绑定状态和 Working 文件状态必须保持一致；不能出现界面显示空槽但 Working 标准文件仍被当作当前完成产物，也不能出现界面显示完成但标准文件不存在。
16. Prompt 是唯一不可通过槽位清除动作移除的任务产物；Prompt 文件一旦存在于 Working 中，就显示绿色，用户可以打开、修改、保存。
17. Video Plan 的 `新图` 等待态不要求 Image Prompt 先存在；只要原图存在，且新图 / Raw / Final 不存在，`新图` 显示白色。
18. Image Plan 的 `新图` 等待态要求原图和 Image Prompt 同时存在；只有原图但没有 Image Prompt 时，`提示词` 白色、`新图` 灰色。
19. Video Only Plan 的 `新视频` 等待态要求 `Image_New` 和 Video Prompt 同时存在；只有新图但没有 Video Prompt 时，`提示词` 白色、`新视频` 灰色。
20. Video Plan 的 `新视频` 等待态不要求 Video Prompt 先存在；`Image_New` 存在且 Raw / Final 不存在时，`新视频` 显示白色。
21. Video Plan 的 Final 生成分为对嘴型和合成音频，两者都需要音频；Video Only Plan 的 Confirm 是 Raw 到 Final 的拷贝，不需要音频。
22. Video Plan / Image Plan / Video Only Plan 都必须始终显示自己的固定槽位集合，不允许出现“空槽位 / 未渲染 / 无状态”的情况。
23. 即使输入为 `[音频=0, 原图=0, 新图=0, 新视频=0, 终视频=0]`，Plan Modal 也必须显示所有固定槽位，并按统一派生函数填入白 / 灰等颜色。
24. Plan Modal 的槽位渲染与颜色派生必须分离：是否渲染由 Plan 类型决定，颜色由统一派生状态决定，不能因为没有 plan item、没有 execution JSON 或没有 Working 文件而隐藏槽位。

灰色原因分型与自动流转：

灰色不是单一状态。统一派生状态必须同时输出 `tone=gray` 和可机器识别的 `reason`，用于区分“等待条件”和“无需执行”：

| 灰色类型 | 建议 reason | 含义 | 是否可在上游完成后变白 |
| --- | --- | --- | --- |
| 条件不满足 | `blocked_waiting_input` | 当前任务缺少必要上游输入、Prompt 或音频等执行条件 | 可以。上游任务成功写入 Working 文件并绑定后，重新派生可变白 |
| 下游已消费 / 无需执行 | `skipped_consumed_by_downstream` | 当前槽位缺失，但更下游产物已存在，本任务无需再执行 | 不可以。保持灰色，除非用户删除对应下游产物 |

自动流转规则：

1. 上一个任务执行成功后，必须先写入 Working 标准文件并同步绑定，再触发整个 Plan 重新派生状态。
2. 原本灰色且 reason 为 `blocked_waiting_input` 的下游任务，如果执行条件因此满足，应变为白色。
3. 自动执行器只能启动当前派生结果为白色的任务；启动后任务显示黄色。
4. 任务执行成功后，只有对应 Working 标准文件存在并完成绑定，才显示绿色。
5. 原本灰色且 reason 为 `skipped_consumed_by_downstream` 的任务，不因上游补齐而变白；只有下游产物被用户删除、替换或移走后，重新派生才可能变白。
6. 任何实现都不能直接把灰色任务推成绿色；颜色变化必须经过统一派生：灰色 -> 白色 -> 黄色 -> 绿色，或在文件已经存在时直接绿色覆盖。

示例：

```text
Image Plan:
提示词灰 / 新图灰
-> 原图存在后：提示词白 / 新图灰
-> 提示词生成成功并写入 Working：提示词绿 / 新图白
-> 新图开始执行：新图黄
-> Image_New 文件落盘并绑定：新图绿
```

```text
Video Only Plan:
新图绿 / 提示词白 / 新视频灰
-> Video Prompt 生成成功并写入 Working：提示词绿 / 新视频白
-> Raw 生成中：新视频黄
-> Video_Raw 文件落盘并绑定：新视频绿
```

```text
下游已消费:
Image_New 缺失，但 Video_Raw 或 Video_Final 已存在
-> 新图灰，reason=skipped_consumed_by_downstream
-> 即使原图或 Prompt 补齐，也不变白
-> 只有 Raw / Final 被删除后，才重新判断是否可生成新图
```

标准槽位文件对应关系：

| 任务 | 标准文件 |
| --- | --- |
| 音频 | `SessionOutput/storyboard/Working/{asset_key}_Audio_Final.*` 或当前 Segment 聚合音频标准文件 |
| Image Prompt | `SessionOutput/storyboard/Working/{asset_key}_ImagePrompt.json` |
| 新图 | `SessionOutput/storyboard/Working/{asset_key}_Image_New.*` |
| Video Prompt | `SessionOutput/storyboard/Working/{asset_key}_VideoPrompt.json` |
| 新视频 | `SessionOutput/storyboard/Working/{asset_key}_Video_Raw.*` |
| 终视频 | `SessionOutput/storyboard/Working/{asset_key}_Video_Final.*` |

已确认决议：

1. `Final Video` 标准文件存在时，终视频显示绿色；正常实现中它也必须已经绑定。
2. 如果出现标准 Final 文件存在但未绑定，这是异常修复态：终视频仍绿色，但 UI 需要提示修复绑定。
3. `final_bound` 只表示 StoryBoard JSON 绑定是否完整，不压过文件存在的绿色；它用于提示绑定修复、验收一致性和防止 JSON/文件脱节。
4. 除音频外，下游产物存在时，上游缺失显示灰色，表示无需执行；例如有 Raw / Final 时，新图缺失不显示白色。
5. Raw 存在但 Final 不存在时，终视频显示白色；这是跨 Plan 的槽位状态，表示可从 Raw 到 Final。
6. 三类 Plan 必须走统一派生状态函数，保障文件状态、槽位绑定状态和任务执行状态一致。

### 5.1 全局后向判断伪代码

```text
if final_video_bound and final_video_file_exists:
  state = completed
  next_action = none
elif raw_video_exists:
  if plan_type == video_only:
    state = raw_ready
    next_action = confirm_final
  elif audio_exists:
    state = raw_ready
    next_action = final_from_raw
  else:
    state = raw_ready_missing_audio
    next_action = audio_then_final
elif new_image_exists:
  state = image_ready
  next_action = raw_from_image
elif source_image_exists:
  state = source_ready
  next_action = image_from_source
else:
  state = missing_source
  next_action = bind_source
```

### 5.2 Image Plan

`Image Plan` 只负责：

```text
原图 + Image Prompt -> New Image
```

需求：

1. 若 Final 已存在，不强迫用户重跑图片。
2. 若 Raw 已存在，不强迫用户重跑图片。
3. 删除或替换 New Image 不自动删除 Raw / Final。
4. 若用户想基于新图重新生成 Raw，必须手动删除 Raw。
5. Image Plan 的 `提示词` 和 `新图` 状态只看当前 StoryBoard 的原图、新图绑定、Image Prompt 文件和真实文件；不能用旧 execution JSON 压过当前文件状态。
6. 新图只有在 `Image_New` 绑定存在且物理文件存在时才显示绿色。
7. 原图只有在 `Image_Source` 绑定存在且物理文件存在时才作为原图存在。
8. 从原始素材、上传素材、历史素材绑定到新图时，都应先复制为 `Image_New`，之后 Image Plan 才显示新图绿色。
9. 从原始素材、上传素材、历史素材绑定到原图时，只应复制为 `Image_Source`，不能让 Image Plan 的新图变绿。

Image Plan 槽位状态矩阵：

| 原图 | Prompt | 新图 | 提示词状态 | 新图状态 | 说明 |
| --- | --- | --- | --- | --- | --- |
| 有 | 无 | 无 | 白 | 灰 | 有原图，可生成提示词；新图等待 Prompt |
| 有 | 有 | 无 | 绿 | 白 | 提示词完成；新图待生成 |
| 有 | 无 | 有 | 灰 | 绿 | 新图已存在；提示词无需补 |
| 有 | 有 | 有 | 绿 | 绿 | 提示词和新图都完成 |
| 无 | 无 | 无 | 灰 | 灰 | 没有可执行上下文 |
| 无 | 有 | 无 | 绿 | 灰 | 提示词存在但无原图，不能生成新图 |
| 无 | 有 | 有 | 绿 | 绿 | 提示词和新图都完成 |
| 无 | 无 | 有 | 灰 | 绿 | 新图已存在；提示词无需补 |

状态含义：

1. `灰`：没有当前可执行上下文，Plan 中不应出现可直接执行的有效任务。
2. `白`：当前步骤可执行或待补齐，但尚未完成。
3. `绿`：当前步骤完成，且对应文件真实存在。
4. Prompt 完成以当前业务 Prompt JSON 文件存在为准。
5. 新图完成以当前 StoryBoard 新图槽 `Image_New` 绑定和物理文件存在为准。
6. 原图只决定是否具备从原图生成新图的输入，不等同于新图。

涉及工具：

```text
05_03_ImagePlanGenerator.py
05_04_ImagePlanExecutor.py
```

原则上不修改工具主逻辑，只要求界面和后端状态判断不要把上游变化解释成下游必须清除。

### 5.3 Video Plan

`Video Plan` 负责完整链路：

```text
Audio -> New Image -> Raw Video -> Final Video
```

需求：

1. 进入计划时应优先识别已有 Final。
2. Final 已完成时，UI 不应提示必须重跑 Video。
3. Raw 已存在且 Final 不存在时，执行器应复用 Raw，只补 Final。
4. Raw 不存在但 New Image 存在时，才生成 Raw。
5. New Image 不存在但原图存在时，才补 New Image。
6. Video Plan 中不提供 Raw 直接 Confirm Final；它只能按 Dialogue 类型执行到 Final：
   - 口播：Raw Video + Audio -> Lip Sync -> Final Video
   - 空镜：Raw Video + Audio -> audio_replace_retime / 替换音频 -> Final Video
7. Video Plan 的 `新图` 完成态必须和 Image Plan 一致：只有当前 StoryBoard 新图槽 `Image_New` 绑定存在且物理文件真实存在时，才显示完成绿色；但等待态不同，Video Plan 有原图即可让 `新图` 显示白色，Image Plan 的 `新图` 还需要 Image Prompt。
8. `Image_Source` / 原图只能作为生成新图的输入，不能让 Video Plan 的 `新图` 步骤变绿。
9. 旧 plan、旧 execution state/result 只能作为历史参考，不能压过当前 StoryBoard 绑定和 Working 文件状态。
10. 若旧 `video_generation_plan.json` 中仍出现 `Image_01`，必须视为旧命名规则缓存，下一次打开 Video Plan 时强制失效并重新生成。
11. Video Plan 的 artifact status 不能因为 `first_frame.source_path` 是 Working 路径就把新图视为完成；必须限定为标准 `Image_New` 文件。
12. 如果当前只有 `Image_Source`，没有 `Image_New`，Video Plan 的 `新图` 应显示待生成或可执行状态，而不是完成状态。
13. `05_01` 生成计划消费图片时，`Image_Source` 只能走 `old_image_visual` 作为生成 `Image_New` 的参考；`working_assets.images[]` 中只有 slot 为 `Image_New` 或路径为 `{asset_key}_Image_New.*` 的图片才能走 `new_image_visual` 作为视频首帧。
14. `05_02` 执行时，生成新图的参考来自 `first_frame.source_path`，输出必须发布到 `{asset_key}_Image_New.*`；生成 Raw Video 的首帧只能来自刚生成的 `Image_New`、已绑定的 `Image_New`，或按规则物化到 `Image_New` 的素材。
15. 旧 `Image_01` 不得在 Video Plan 生成和执行链路中作为 New Image 消费；如果旧 StoryBoard JSON 中仍有 `Image_01` 槽位或路径，不能把它当作新图完成或视频首帧。

涉及工具：

```text
05_01_VideoPlanGenerator.py
05_02_VideoPlanExecutor.py
```

现状判断：`05_02` 已存在 Raw 复用逻辑，原则上不修改工具主逻辑。主要改 StoryBoard 后端的 artifact status 和前端展示。

### 5.4 Video Only Plan

`Video Only Plan` 负责：

```text
New Image + Video Prompt -> Raw Video
Raw Video -> Confirm Final
```

需求：

1. Raw 已存在、Final 不存在：显示待 Confirm Final。
2. Confirm 时直接把 Raw 拷贝为 Final。
3. Confirm 后同步 StoryBoard JSON 中 Final 视频绑定。
4. 删除 Final 后，Raw 保留，Video Only Plan 应回到可 Confirm 状态。
5. 删除 Raw 后，Final 若存在仍完成；Final 不存在时才回到生成 Raw。
6. Video Only Plan 的 Confirm 不依赖 Audio；Raw 存在即可 Confirm 为 Final。
7. Video Only Plan 必须复用 Video Plan 的统一 Segment Truth；不得先清空 StoryBoard 里的 Final Video 绑定再重新拆 Segment。
8. Final / Raw / Prompt 只能影响 Video Only Plan 的任务状态和槽位颜色，不能影响 Segment 数量、顺序、每段包含的 Dialogue、TailFrame 依赖关系。
9. 如果某个 Dialogue 已绑定 Final Video，该 Dialogue 在统一 Segment Truth 中单独成为已完成 Segment；后续 Dialogue 通过该 Segment 的 TailFrame 继续，而不是并入同一个 Final Video Segment。

涉及工具：

```text
05_05_VideoOnlyPlanGenerator.py
05_06_VideoOnlyPlanExecutor.py
```

现状判断：Video Only 文档和实现已经具备 Raw / Final 文件合同；需补齐界面 Raw 槽操作和后向状态同步。

## 6. 后端需求

### 6.1 新增 raw_video 绑定目标

现有素材绑定目标主要有：

```text
audio
source
image
video
```

需要新增：

```text
raw_video
final_video
```

兼容规则：

1. 前端不再发送或显示泛泛的 `video` 目标；前端必须明确发送 `raw_video` 或 `final_video`。
2. `raw_video` 必须写入 `Working/{asset_key}_Video_Raw.mp4`。
3. `raw_video` 不写入 `working_assets.video`。
4. `final_video` 写入或绑定 `working_assets.video`。
5. 后端可在内部保留旧 `video` 作为 `final_video` 的兼容别名，用于兼容历史调用；但新增前端代码不得继续使用 `video`。
6. 新实现中前端、界面文案、API 包装都必须使用 `final_video`，不得继续出现 `video` 作为可选目标，避免 Raw / Final 混淆。
7. 后端如需兼容历史 `target_kind: "video"`，只能解释为 `final_video`。

涉及文件：

```text
OpenCrew/OpenClip/backend/openclip_backend/koubo_storyboard/asset_reference_services.py
OpenCrew/OpenClip/backend/openclip_backend/koubo_storyboard/asset_history_services.py
OpenCrew/OpenClip/backend/openclip_backend/koubo_storyboard/asset_routes.py
OpenCrew/OpenClip/backend/openclip_backend/koubo_storyboard/asset_core_services.py
```

### 6.2 绑定 source / image 标准槽位

绑定图片素材时必须区分目标槽位：

```text
target_kind: "source" -> 原图 -> Image_Source
target_kind: "image"  -> 新图 -> Image_New
```

后端保存绑定时必须执行：

1. 校验来源素材确实是图片。
2. 根据 `dialogue_asset_key` 计算标准槽位文件名。
3. 如果标准槽位已存在旧文件，先移动到 Asset History。
4. 把来源文件复制到标准槽位文件名。
5. 原图绑定写回 `source_image_paths[0]` 和 `image_path`。
6. 新图绑定写回 `working_assets.images[0].path` 和 `bound_image_path`。
7. `working_assets.images[0].slot` 必须是 `Image_New`。
8. 绑定后 StoryBoard JSON 中保存的路径必须是 Working 标准槽位路径。

不得发生：

1. 原图绑定写入 `Image_New`。
2. 新图绑定写入 `Image_Source`。
3. 原图和新图共用旧的 `Image_01`。
4. 绑定路径指向原始素材、上传素材或历史素材的来源文件。
5. 删除绑定后 Working 标准槽位文件仍留在原地并被 Plan 误判为完成。

### 6.3 删除 raw_video

删除 Raw Video 时：

1. 只删除或归档 `Working/{asset_key}_Video_Raw.mp4`。
2. 不删除 `Working/{asset_key}_Video_Final.mp4`。
3. 不清空 `working_assets.video.path`。
4. 不删除 Audio / 原图 / New Image。
5. 不标记、不归档、不删除 TailFrame。

TailFrame 不跟随 Raw 删除。后续 Plan 重新生成时，由工具刷新 TailFrame。

### 6.4 删除 final_video

删除 Final Video 时：

1. 清空 `working_assets.video.path`。
2. 删除或归档 `Working/{asset_key}_Video_Final.mp4`。
3. 不删除 Raw Video。
4. 不删除 TailFrame。
5. 状态回到“Raw exists -> wait final”。

### 6.5 派生状态接口

派生状态字段不是新的持久状态，也不是新的 JSON 文件。它们是后端在每次返回 StoryBoard 详情或 Plan execution payload 时，根据以下现有事实即时计算出来的 UI 展示字段：

1. 当前 StoryBoard JSON 中的槽位绑定。
2. `SessionOutput/storyboard/Working/` 中规范文件是否真实存在。
3. 当前 asset_key 对应的 Raw / Final 文件路径。
4. 从 Final 往前看的最短下一步。

后端给前端返回 StoryBoard 详情或 Plan execution payload 时，应派生以下信息：

```text
asset_key
raw_video_path
raw_video_exists
final_video_path
final_video_exists
final_video_bound
next_action
```

这些信息只作为 API 返回值，不写入 StoryBoard JSON。

硬性规则：

1. 能通过函数计算的状态不存入 JSON。
2. Raw / Final 是否存在不存状态，只查文件。
3. Final 是否绑定不存状态，只查 `working_assets.video.path`。
4. 下一步动作不存状态，只按后向规则实时计算。
5. Plan / Execution JSON 可以作为计算输入，但不能因为 UI 展示需要而新增派生字段。
6. 完成态以物理文件真实存在为准；如果绑定路径存在但文件缺失，不能视为完成。
7. 旧 execution state/result 不能覆盖真实文件状态，只能作为历史或辅助展示。
8. UI 按钮状态、下一步动作、是否完成，必须以当前文件状态和当前绑定状态计算结果为准，不以旧 execution JSON 的 completed 记录为准。
9. 绑定路径失效时，不自动修改 StoryBoard JSON；由 UI 提示用户手动清除、重新绑定或重新生成。

示例：

```text
旧 execution JSON:
  Video step = completed

当前真实文件:
  Working/{asset_key}_Video_Raw.mp4 不存在

正确 UI:
  不显示 Raw 已完成
  不允许把旧 Video step completed 当作可 Confirm / 可 Final 的依据
  应根据 New Image / 原图 / Audio 的当前状态重新判断下一步
```

涉及文件：

```text
OpenCrew/OpenClip/backend/openclip_backend/koubo_storyboard/video_plan_artifact_services.py
OpenCrew/OpenClip/backend/openclip_backend/koubo_storyboard/video_plan_execution_state_services.py
OpenCrew/OpenClip/backend/openclip_backend/koubo_storyboard/video_only_plan_routes.py
OpenCrew/OpenClip/backend/openclip_backend/koubo_storyboard/image_plan_routes.py
OpenCrew/OpenClip/backend/openclip_backend/koubo_storyboard/storyboard_plan_services.py
```

### 6.6 状态清理不是级联删除

旧理解中，“替换上游就清下游状态”不适用于本需求。

正确做法：

1. 用户删除哪个槽位，只清哪个槽位对应的文件或绑定。
2. execution state/result 可以在展示时被后向派生状态覆盖，不需要强制删除所有旧 step。
3. 如果必须清 step，只能清被用户明确删除的那个产物对应 step，不清更靠后的有效产物。

示例：

```text
删除 New Image:
  可以清 image step 的当前结果提示
  不能清 Raw step
  不能清 Final / Sync step

删除 Final:
  可以清 final/sync 完成态
  不能清 Raw/video 完成态
```

## 7. 前端需求

### 7.1 DialogueCard

涉及文件：

```text
OpenCrew/OpenClip/frontend/src/KouboStoryBoard/components/DialogueCard.jsx
OpenCrew/OpenClip/frontend/src/KouboStoryBoard/styles/dialogue-card.css
```

需求：

1. 增加 `新视频` 槽，内部 target 为 `raw_video`。
2. 前端显示文案使用中文：`新视频` 和 `终视频`，不再显示泛泛的 `Video / 视频`。
3. 槽位顺序为 `Audio / 原图 / New Image / 新视频 / 终视频`。
4. 新视频槽支持预览视频。
5. 新视频槽支持拖入视频。
6. 新视频槽支持删除。
7. 终视频槽继续支持现有最终视频绑定、预览和删除。
8. 新视频和终视频的删除按钮必须分别操作各自槽位。

### 7.2 KouboStoryBoardModule

涉及文件：

```text
OpenCrew/OpenClip/frontend/src/KouboStoryBoardModule.jsx
```

需求：

1. `assignAsset` 支持 `raw_video`。
2. `dropAsset` 支持 `raw_video`。
3. `clearAsset` 支持 `raw_video` 和 `final_video`。
4. 前端新增代码只使用 `raw_video` 和 `final_video`，不再使用 `video`。
5. 拖入 Raw 后刷新 StoryBoard 详情和 Plan 状态。
6. 删除 Final 后刷新 Video Only Plan，使其显示可 Confirm。

### 7.3 API 包装

涉及文件：

```text
OpenCrew/OpenClip/frontend/src/KouboStoryBoard/kouboStoryboardApi.js
```

需求：

1. 绑定接口允许传 `target_kind: "raw_video"`。
2. 绑定接口允许传 `target_kind: "final_video"`。
3. 删除接口允许传 `target_kind: "raw_video"`。
4. 删除接口允许传 `target_kind: "final_video"`。
5. 前端不再传 `target_kind: "video"`；新增代码中不再把 `video` 作为素材目标。
6. 若后端新增派生状态字段，API 不做二次持久化，只透传给 UI。
7. 拖入 Raw / Final 时，后端统一复制或归档到对应规范 Working 路径，前端不直接绑定任意外部路径作为最终状态。

### 7.4 Plan Modal

涉及文件：

```text
OpenCrew/OpenClip/frontend/src/KouboStoryBoard/components/KouboImagePlanModal.jsx
OpenCrew/OpenClip/frontend/src/KouboStoryBoard/components/KouboVideoPlanModal.jsx
OpenCrew/OpenClip/frontend/src/KouboStoryBoard/components/KouboVideoOnlyPlanModal.jsx
```

需求：

1. Image Plan：显示 New Image 状态，但不要因为 Raw/Final 存在而要求重跑图片。
2. Video Plan：将 `VIDEO` 语义展示为 `Raw Video`，将 `SYNC` 语义展示为 `Final Video`。
3. Video Only Plan：Raw 已存在时显示 `Raw 已生成 / 待 Confirm Final`。
4. Final 已存在时显示完成，不再提示生成 Raw。
5. 删除 Final 后，若 Raw 存在，Confirm Final 按钮可用。
6. 如果旧 execution JSON 显示步骤 completed，但对应 Raw / Final 物理文件已经不存在，Modal 必须显示当前可执行状态，而不是旧完成态。
7. 旧 execution JSON 的完成记录可以作为历史记录展示，但不能决定主按钮是否可用、是否完成、下一步是什么。
8. Video Plan 中 Raw 已存在但 Audio 缺失时，自动合成 Final 的按钮置灰；但终视频槽位仍显示白色，因为可通过 Video Only Plan 手动拷贝 / Confirm 到 Final。
9. Final 文件存在但未绑定时，Modal 只提示状态；绑定确认入口只在终视频槽位。

### 7.5 Asset Panel

涉及文件：

```text
OpenCrew/OpenClip/frontend/src/KouboStoryBoard/components/AssetPanel.jsx
OpenCrew/OpenClip/frontend/src/KouboStoryBoard/kouboStoryboardAssets.js
```

需求：

1. 视频资产拖到 Raw 槽时，目标是 `raw_video`。
2. 视频资产拖到 Final 槽时，目标是 `final_video`。
3. 不根据文件类型自动决定 Raw 或 Final，必须由用户拖入的槽位决定语义。
4. Asset History 恢复 Raw / Final 时，只恢复到文件夹下，不自动绑定回槽位；界面提示用户手动操作。

## 8. 工具需求影响面

### 8.1 不改工具主流程

以下工具原则上不改主流程：

```text
05_01_VideoPlanGenerator.py
05_02_VideoPlanExecutor.py
05_03_ImagePlanGenerator.py
05_04_ImagePlanExecutor.py
05_05_VideoOnlyPlanGenerator.py
05_06_VideoOnlyPlanExecutor.py
```

原因：

1. 05_02 已经有 Raw 生成和 Raw 复用逻辑。
2. 05_05 / 05_06 已经有 Raw / Final 文件合同。
3. 当前需求是 UI 槽位和状态判断适配，不是工具算法重写。

### 8.2 可选工具修正

本次原则上不修改 Analysis_V1 工具主逻辑。如果实现时发现仅靠 StoryBoard 后端适配和前端展示无法满足后向判断，必须先单独确认，再在以下位置做最小修正：

```text
OpenCrew/ToolLibrary/Analysis_V1/05_01_VideoPlanGenerator.py
OpenCrew/ToolLibrary/Analysis_V1/05_05_VideoOnlyPlanGenerator.py
```

修正原则：

1. 只增强 Raw/Final 存在性判断。
2. 不新增输出 JSON 文件。
3. 不新增 StoryBoard JSON 字段。
4. 不改变 Prompt 或模型调用逻辑。
5. 不改变现有 Plan / State / Result / Cache / Settings 文件体系。

## 9. 现有实现差异点

### 9.1 当前 DialogueCard 只有一个视频槽

当前 UI 中视频槽位只有一个，无法表达 Raw / Final 的区别。需要拆分。

### 9.2 当前 working_assets 只表达 Final

`working_assets.video` 目前语义上是 `Video_Final`。这点应保留，但 Raw 不能塞进这个字段。

### 9.3 当前删除视频可能被理解成删除所有视频阶段

现有相关视频清理逻辑会聚合 `Video_Final / Video_Raw / TailFrame`。本需求要求用户删除 `Raw` 和删除 `Final` 时行为分离，不能一删视频就清全部相关视频产物。

### 9.4 旧文档中存在“媒体绑定变化导致计划过期”的表达

旧的 Video Plan Button / Modal 需求中，有“StoryBoard 中绑定的图片/音频/视频等媒体发生变化，则重新生成 plan”的表达。新规则要求更细：

1. 绑定变化可以导致计划签名变化。
2. 但不能因此自动删除更靠后的已有产物。
3. UI 下一步应从 Final / Raw / New Image 后向判断，而不是从 Audio / 原图正向强制重跑。

### 9.5 `STORYBOARD_ASSET_HISTORY_REQUIREMENTS` 对 Working 的表述需要补充

旧文档说 `Working/` 始终只代表当前 StoryBoard 中 Dialogue 级 generated 最终素材。Raw Video 已经是业务产物，但不是 Final。需要补充说明：

1. `Working/{asset_key}_Video_Raw.mp4` 是当前 Dialogue 级 Raw 业务产物。
2. 它不进入 `working_assets.video`。
3. 它仍受 Asset History 管理。

### 9.6 Video Plan Modal 的 VIDEO / SYNC 文案不够直观

现有 `VIDEO` 和 `SYNC` 对用户不够直观。目标文案应接近：

```text
Raw Video
Final Video
```

### 9.7 Video Plan 新图状态不能复用原图或旧缓存

已发现的差异点：

1. 主界面新图槽为空，且 Working 中没有 `{asset_key}_Image_New.*` 时，Video Plan 仍可能把 `新图` 显示成完成。
2. 根因之一是旧 `video_generation_plan.json` 仍包含 `{asset_key}_Image_01.*`，但 UI cache 签名未把命名规则版本纳入失效条件，导致旧 plan 被继续复用。
3. 根因之二是 Video Plan 的 artifact status / Modal 状态曾把 `first_frame.source_path` 或任意 Working 路径当成新图完成；这会把 `Image_Source` 错当成 `Image_New`。

修正要求：

1. 旧 plan 中出现 `Image_01` 时，缓存必须失效并重新生成。
2. Video Plan 的 `新图` 完成态只认标准槽位 `Image_New` 的真实文件状态。
3. `Image_Source` 只表示生成新图的参考输入，不参与 `新图` 完成态。
4. 旧 execution JSON 中的 completed 不能让 `新图` 变绿；最多用于显示历史执行记录。
5. 该规则必须与 Image Plan 的 `新图` 状态保持一致。

### 9.8 已确认逻辑异常点：统一色彩优先级尚未完全落地

本节按 `Video Plan / Image Plan / Video Only Plan` 对照 5.0 中的色彩规则，列出当前实现和目标规则之间需要修正的异常点。

#### 9.8.1 全局异常

1. 当前三类 Modal 都没有统一的 `绿 > 黄 > 红 > 白 > 灰` 判定函数，而是在各自组件内分别计算状态，容易出现同一槽位在不同 Plan 中颜色不一致。
2. 旧 execution state/result 仍会参与部分进度统计和黄色/完成态展示；必须收敛为：文件存在先绿色，文件不存在时才允许 execution state 决定黄/红。
3. 除音频外，更下游产物存在时，上游槽位应显示灰色“无需执行”，但当前部分 UI 仍会显示白色“待生成”。
4. `Final Video` 的颜色规则与旧文档存在冲突：旧规则要求 `final_exists && final_bound` 才算完成；已确认新规则为标准 Final 文件存在即绿色，未绑定时显示绑定修复提示。
5. Plan 重新生成时会清理 Plan / State / Result JSON，但不清理 Working 标准槽位文件。这符合本需求；因此所有颜色必须以 Working 文件重新派生，不能依赖被清掉或残留的旧执行状态。

#### 9.8.2 Image Plan 异常点

1. `Prompt` 绿色优先级不足：如果 ImagePlan task 因 blocked / skipped / executable=false 被判定不可执行，即使 Prompt 文件已经存在，当前 UI 仍可能先显示灰色；目标应为 Prompt 文件存在即绿色。
2. `新图` 对标准槽位校验不足：后端 `image_plan_artifact_status` 当前主要检查 StoryBoard 新图路径是否存在，但没有强制限定路径必须是 `{asset_key}_Image_New.*`。目标应为只有标准 `Image_New` 文件存在才绿色。
3. `新图` 未考虑下游产物：如果 `Image_New` 不存在，但 `Video_Raw` 或 `Video_Final` 已存在，当前 Image Plan 可能仍显示新图白色待生成；目标应显示灰色“无需执行”。
4. `Prompt` 进度统计可能被旧 execution state 放大：顶部 Prompt done 数会取 artifact、execution summary、state completed 的最大值；目标应以 Prompt 文件存在数量为主，旧 state 只能作为历史说明。
5. Image Plan artifact payload 当前没有返回 Raw / Final 存在信息，因此前端很难正确判断“新图已被下游产物消费后无需执行”。后端需要补派生字段或复用统一槽位状态函数。
6. 如果原图存在但 Prompt 缺失，Prompt 白色是正确的；如果原图不存在但已有新图且没有 Raw / Final，Prompt 可以白色表示可补 Prompt；如果 Raw / Final 已存在且 Prompt 缺失，Prompt 必须灰色表示无需执行。

#### 9.8.3 Video Plan 异常点

1. `音频` / `新图` 绿色优先级不足：当前 Video Plan 中 running execution tone 可能先于文件存在状态生效，导致文件已经在 Working 时仍显示黄色。目标应为文件存在即绿色。
2. `新视频` 已有 Final 时不应白色：如果 `Video_Final` 已存在但 `Video_Raw` 不存在，当前 `新视频` 仍可能因为 plan tasks need_video / need_video_prompt 显示白色待生成；目标应显示灰色“终视频已存在，无需新视频”。
3. `新图` 已有 Raw / Final 时不应白色：如果 `Image_New` 不存在但 `Video_Raw` 或 `Video_Final` 已存在，当前 `新图` 仍可能显示待生成；目标应显示灰色“下游产物已存在，无需新图”。
4. `终视频` 完成态目前依赖 `final_bound`：后端 `sync_in_working` 当前等于 `final_bound`，未绑定 Final 文件存在时不会绿色。目标是标准 Final 文件存在即绿色，`final_bound` 只影响修复提示和一致性告警。
5. `sync_generate_pending` 未显式排除“标准 Final 文件已存在但未绑定”的情况；目标是该状态必须被标准 Final 文件完成态覆盖。
6. `05_02` Raw 复用逻辑只在 Raw 存在且 Final 不存在时触发；如果 Final 标准文件存在但未绑定，应优先提示修复绑定，不要求重跑 Raw，也不默认覆盖已有 Final。
7. `Video Plan` 中旧 plan 仍可能携带旧命名或旧 first_frame 来源。虽然 `05_01` 已限制 `Image_New`，UI cache 仍必须把命名规则版本或旧 `Image_01` 检测纳入失效条件。

#### 9.8.4 Video Only Plan 异常点

1. 绿色优先级不足：当前 `stepRunning` 先于文件存在判断，Prompt / 新图 / 新视频 / 终视频在执行状态仍为 running 时可能显示黄色；目标应为对应文件存在即绿色。
2. `新图` 已有 Raw / Final 时不应白色：如果 `Image_New` 不存在但 `Video_Raw` 或 `Video_Final` 已存在，当前首帧步骤可能显示白色“缺少新图或上一段尾帧”；目标应显示灰色“无需执行”。
3. `Prompt` 已有 Raw / Final 时无文件应灰色：当前 Video Only 生成器已用 `disabled_consumed_by_video` 表达这一点，前端需要确保显示为灰色，而不是待执行。
4. `新视频` 已有 Final 时无 Raw 应灰色：如果 `Video_Final` 已存在但 `Video_Raw` 不存在，当前 video step 会显示绿色“Final 已存在”，但按槽位粒度，`新视频` 槽自身没有文件，应显示灰色“终视频已存在，无需新视频”；只有 `终视频` 槽显示绿色。
5. `Confirm Final` 对 Final 未绑定的处理不清晰：当前 confirm 完成依赖 `final_bound`。如果标准 Final 文件存在但未绑定，且 Raw 不存在，不能要求重跑 Raw；应显示终视频绿色，并提供或提示绑定修复入口。
6. Video Only progress 中 `video.done` 当前按 Raw 或 Final 任一存在计数，这适合“已有下游产物无需生成 Raw”的总进度，但不适合槽位颜色；槽位颜色必须区分 `新视频` 和 `终视频`。

#### 9.8.5 建议收敛方式

1. 后端新增统一的派生函数，但不新增 JSON 文件：输入 StoryBoard JSON、Working 文件状态、当前 plan item、当前 execution state；输出每个槽位的 `tone / reason / file_exists / blocked_by_downstream`。
2. 三个 Modal 只消费统一派生结果，避免各自重复实现颜色优先级。
3. `Image Plan` artifact payload 需要补 Raw / Final 存在性，用于判断新图是否因下游存在而灰色。
4. `Video Plan` artifact payload 需要同时返回 `final_file_exists` 与 `final_bound`；颜色按 `final_file_exists` 判断，绑定完整性单独提示。
5. `Video Only Plan` artifact payload 需要把 `raw_in_working` 与 `final_in_working` 分别映射到 `新视频` 与 `终视频`，不要再用 Raw 或 Final 任一存在让 `新视频` 变绿。
6. 前端所有 running/failed 状态必须放在文件存在判断之后。

## 10. 验收标准

### 10.1 槽位验收

1. DialogueCard 显示 `Audio / 原图 / New Image / 新视频 / 终视频`。
2. 新视频和终视频可分别预览。
3. 新视频和终视频可分别删除。
4. 拖入视频到新视频槽不会写入终视频绑定。
5. 拖入视频到终视频槽不会覆盖新视频。
6. 前端可见文案使用 `新视频` / `终视频`，不再出现泛泛的 `Video / 视频` 槽位。
7. 前端绑定和删除请求使用 `raw_video` / `final_video`，不再使用 `video`。
8. 拖入图片到原图槽后，StoryBoard 绑定路径指向 `Working/{asset_key}_Image_Source.{ext}`。
9. 拖入图片到新图槽后，StoryBoard 绑定路径指向 `Working/{asset_key}_Image_New.{ext}`。
10. 从原始素材、上传素材、历史素材拖入时，Working 文件名都必须重命名为目标槽位标准文件名。
11. 原图和新图不能同时指向同一个 `Image_01` 或同一个来源素材路径。

### 10.2 后向判断验收

1. Final 存在时，删除 Audio 后仍显示完成。
2. Final 存在时，删除 New Image 后仍显示完成。
3. 删除 Final 后，Raw 存在时显示可从 Raw 到 Final。
4. 删除 Raw 后，Final 存在时仍显示完成。
5. 删除 Raw 且 Final 不存在时，New Image 存在则显示可生成 Raw。

### 10.3 Plan 验收

1. Video Only Plan 中 Raw 存在、Final 不存在时，Confirm Final 可用。
2. Video Only Plan Confirm 后，Raw 被拷贝到 Final，并绑定 Final。
3. Video Plan 在 Raw 存在、Final 不存在时复用 Raw 到 Final。
4. Image Plan 不因为 Raw/Final 存在而强制重跑。
5. Plan 按钮状态以当前文件和绑定状态为准，不能被旧 execution JSON 覆盖。
6. Image Plan 的新图绿色只来自当前 `Image_New` 绑定和真实文件存在。
7. Image Plan 的原图状态只来自当前 `Image_Source` 绑定和真实文件存在。
8. Video Only Plan 的新图绿色只来自当前 `Image_New` 的真实文件；上一 Segment 的 TailFrame 只能作为可物化来源，必须先复制成当前 Segment 的 `Image_New` 后才算新图完成。
9. Video Plan 的新图绿色只来自当前 `Image_New` 绑定和真实文件存在，不能来自 `Image_Source`、`Image_01`、`first_frame.source_path` 或旧 execution JSON。
10. Video Plan 打开时如果旧 plan 中包含 `Image_01`，必须判定缓存失效并重新生成。

### 10.4 非目标验收

1. 不新增 JSON 文件。
2. 不新增 StoryBoard JSON 字段。
3. 不改工具注册表。
4. 不新增模型调用链路。
5. 不把 Raw 写入 `working_assets.video`。
6. 不删除或合并 `video_plan_settings.json`。
7. 不删除或合并 `video_generation_plan.ui_cache.json`。
8. 不把 Image Plan / Video Only Plan / Composer 的 JSON 文件体系改成新结构。
9. 不做旧数据批量迁移；旧 `working_assets.video` 直接按终视频显示。

## 11. 界面端测试路径

本节只描述从 StoryBoard UI 出发的最短测试路径。测试重点不是覆盖工具内部算法，而是确认槽位、绑定、删除、后向状态判断和 Plan 按钮状态是否符合本需求。

### 11.1 Image Plan 测试路径

前置条件：

```text
Dialogue 有 Audio
Dialogue 有原图
Dialogue 没有 New Image / Raw Video / Final Video
```

最短路径：

1. 打开 StoryBoard 页面，确认槽位顺序为 `Audio / 原图 / New Image / 新视频 / 终视频`。
2. 打开 Image Plan。
3. 执行 Image Plan，生成 New Image。
4. 回到 StoryBoard，确认 New Image 槽显示新图。
5. 删除 New Image，确认 Raw / Final 不受影响。

必须覆盖的测试案例：

| 编号 | 场景 | 操作 | 期望结果 |
| --- | --- | --- | --- |
| IP-01 | 基础生成 | 原图存在时执行 Image Plan | New Image 槽出现新图，不生成 Raw / Final |
| IP-02 | Final 已存在 | Final 存在后打开 Image Plan | UI 不提示必须重跑图片，Final 保持完成 |
| IP-03 | Raw 已存在 | Raw 存在后打开 Image Plan | UI 不提示必须重跑图片，Raw 保持存在 |
| IP-04 | 删除 New Image | 删除 New Image | Raw / Final 不删除；下一步以后向状态判断 |
| IP-05 | 替换 New Image | 拖入或生成新的 New Image | 不自动删除 Raw / Final；如果要用新图重生 Raw，用户必须手动删除 Raw |
| IP-06 | 原始素材绑定原图 | 从原始素材拖图到原图槽并保存 | 文件复制为 `Image_Source`；Image Plan 新图不变绿 |
| IP-07 | 上传素材绑定原图 | 从上传素材拖图到原图槽并保存 | 文件复制为 `Image_Source`；Image Plan 新图不变绿 |
| IP-08 | 历史素材绑定原图 | 从历史素材拖图到原图槽并保存 | 文件复制为 `Image_Source`；Image Plan 新图不变绿 |
| IP-09 | 原始素材绑定新图 | 从原始素材拖图到新图槽并保存 | 文件复制为 `Image_New`；Image Plan 新图变绿 |
| IP-10 | 上传素材绑定新图 | 从上传素材拖图到新图槽并保存 | 文件复制为 `Image_New`；Image Plan 新图变绿 |
| IP-11 | 历史素材绑定新图 | 从历史素材拖图到新图槽并保存 | 文件复制为 `Image_New`；Image Plan 新图变绿 |
| IP-12 | 删除原图 | 删除原图槽绑定 | `Image_Source` 进入 Asset History；新图状态不受影响 |
| IP-13 | 删除新图 | 删除新图槽绑定 | `Image_New` 进入 Asset History；原图状态不受影响 |
| IP-14 | 覆盖原图 | 新素材替换原图槽 | 旧 `Image_Source` 先进入 Asset History，新文件再覆盖为同名标准槽位 |
| IP-15 | 覆盖新图 | 新素材替换新图槽 | 旧 `Image_New` 先进入 Asset History，新文件再覆盖为同名标准槽位 |

### 11.2 Video Only Plan 测试路径

前置条件：

```text
Dialogue 有 New Image
Dialogue 可以没有 Audio
Dialogue 没有 Raw Video / Final Video
```

最短路径：

1. 打开 Video Only Plan。
2. 执行生成 Raw Video。
3. 回到 StoryBoard，确认 `新视频` 槽出现 Raw Video，`终视频` 槽仍为空。
4. 在 Video Only Plan 中点击 Confirm Final。
5. 回到 StoryBoard，确认 `终视频` 槽出现 Final Video，且 `新视频` 仍存在。
6. 删除 Final，确认 Raw 保留，Video Only Plan 回到可 Confirm 状态。
7. 删除 Raw，确认 Final 若存在仍完成；若 Final 不存在，则回到生成 Raw 状态。

必须覆盖的测试案例：

| 编号 | 场景 | 操作 | 期望结果 |
| --- | --- | --- | --- |
| VOP-01 | 生成 Raw | New Image 存在时执行 Video Only Plan | Raw 写入 `新视频` 槽，Final 不自动生成 |
| VOP-02 | Confirm Final | Raw 存在时点击 Confirm | Raw 复制为 Final，`终视频` 绑定完成；不依赖 Audio |
| VOP-03 | 删除 Final | Final 存在时删除终视频 | Raw 保留，Confirm Final 可用 |
| VOP-04 | 删除 Raw | Raw 存在时删除新视频 | Final 若存在仍完成；Final 不存在时回到生成 Raw |
| VOP-05 | 拖入 Raw | 拖视频到新视频槽 | 替换规范 Raw 文件，不写入终视频绑定 |
| VOP-06 | 拖入 Final | 拖视频到终视频槽 | 复制为规范 Final 文件并绑定，不覆盖 Raw |
| VOP-07 | 缺 Audio | Dialogue 没有 Audio 但 Raw 存在 | Confirm Final 仍可用 |
| VOP-08 | 旧 completed 但文件缺失 | 删除 Raw 文件后打开 Modal | 不按旧 execution completed 显示 Raw 完成，按当前文件状态判断 |

### 11.3 Video Plan 测试路径

Video Plan / Image Plan / Video Only Plan 槽位颜色穷举表另见：

```text
docs/SessionDesign-R2/Koubo_VideoPlan_槽位颜色测试案例表.md
```

前置条件：

```text
Dialogue 有 Audio
Dialogue 有原图或 New Image
Dialogue 没有 Raw Video / Final Video
Dialogue 类型为口播或空镜
```

最短路径：

1. 打开 Video Plan。
2. 执行完整 Video Plan。
3. 回到 StoryBoard，确认 `新视频` 槽显示 Raw Video，`终视频` 槽显示 Final Video。
4. 删除 Final，确认 Raw 保留。
5. 再打开 Video Plan，确认下一步从 Raw 到 Final，不重新生成 New Image / Raw。
6. 删除 Audio，确认 Video Plan 中自动合成 Final 按钮置灰；但若 Raw 存在，终视频槽位仍为白色。
7. 删除 Raw，确认若 Final 存在仍完成；若 Final 不存在，从 New Image 生成 Raw。

必须覆盖的测试案例：

| 编号 | 场景 | 操作 | 期望结果 |
| --- | --- | --- | --- |
| VP-01 | 完整链路 | Audio + 原图存在时执行 Video Plan | 生成 New Image、Raw、Final；Raw 显示在新视频，Final 显示在终视频 |
| VP-02 | Raw 复用 | 删除 Final 后重新打开 Video Plan | 从 Raw 到 Final，不重新生成 Raw |
| VP-03 | 缺 Audio | Raw 存在但 Audio 缺失 | 自动合成 Final 按钮置灰；终视频槽位仍为白色 |
| VP-04 | 口播 Final | 口播 Dialogue 从 Raw 到 Final | 走 Lip Sync，不走 Confirm |
| VP-05 | 空镜 Final | 空镜 Dialogue 从 Raw 到 Final | 走替换音频，不走 Confirm |
| VP-06 | 删除 Raw | 删除 Raw 但 Final 存在 | Final 保持完成 |
| VP-07 | 删除 New Image | Raw / Final 存在时删除 New Image | Raw / Final 不删除，状态以后向判断 |
| VP-08 | 替换 Raw | Final 存在时拖入新 Raw | Final 保持完成，不提示不一致 |
| VP-09 | 绑定失效 | Final 绑定路径存在但文件丢失 | 不算完成；不自动清空绑定；提示手动处理 |
| VP-10 | 未绑定 Final | 标准 Final 文件存在但未绑定 | 只在终视频槽显示确认绑定入口；Modal 只提示状态 |

## 12. 建议实施顺序

1. 锁定现有 JSON 文件体系，不做文件合并和状态 JSON 新增。
2. 后端补 `raw_video` / `final_video` 目标语义。
3. 后端补派生状态返回。
4. 前端 DialogueCard 拆槽。
5. 前端拖拽和删除接入 `raw_video`。
6. Plan Modal 文案和状态展示对齐。
7. 回归 Video Plan / Image Plan / Video Only Plan。

## 13. 已确认 Review 决议

以下问题已在 2026-06-16 确认：

1. 删除 Raw Video 时，不动 TailFrame。
2. 删除 Final Video 时，不动 Raw Video，也不动 TailFrame。
3. 拖入或替换 Raw Video 后，如果已有 Final Video，保持 Final 完成态；除非用户手动删除 Final。
4. Final Video 文件存在但未绑定时属于异常修复态；终视频显示绿色，但必须提示通过确认、保存或修复动作补齐绑定。
5. 前端必须改动，不再使用泛泛的 `Video` 槽位或 target；`raw_video` 中文为 `新视频`，`final_video` 中文为 `终视频`。
6. 派生状态字段不是持久状态，只是后端基于 StoryBoard JSON、Working 文件和现有 Plan / Execution JSON 用函数即时计算后返回给 UI 的显示字段；凡是能计算的状态都不存入 JSON。
7. 拖入终视频时，统一复制或归档到规范 Final 文件，并绑定 `working_assets.video.path`。
8. 拖入新视频时，统一替换 Raw，写入规范 Raw 文件；即使已有 Final，也不动 Final。
9. 删除 Raw / Final 时进入 Asset History 归档。
10. 完成态以物理文件真实存在为准；绑定路径存在但文件丢失时，不算完成。
11. 只有 Video Only Plan 允许 Confirm Final；Video Plan 中只有口播 Lip Sync 和空镜替换音频两种从 Raw 到 Final 的路径。
12. Video Only Plan Confirm 不依赖 Audio，Raw 存在即可 Confirm。
13. Dialogue 的原图只有一张；多槽位来自 Host / Product 等角色一致性区分，不代表同一个 Dialogue 有多张原图。
14. Execution JSON 只能作为参考，不能压过真实文件状态；例如旧结果显示 Video step completed，但 Raw 文件已经被删，UI 必须按 Raw 缺失重新判断当前可执行状态。
15. 绑定路径存在但物理文件丢失时，不自动清空绑定；不算完成，只提示用户手动清除、重新绑定或重新生成。
16. Final 文件存在但未绑定时，确认或修复绑定入口只放在终视频槽位上，Plan Modal 不提供绑定入口。
17. 替换 Raw / Audio / New Image 后，若已有 Final，Final 保持完成态，不额外提示不一致。
18. Asset History 恢复 Raw / Final 时，只恢复到文件夹，不自动恢复到槽位。
19. 旧 `target_kind: "video"` 不再作为新前端语义出现；如后端兼容历史调用，只能解释为 `final_video`。
20. Raw / Final 文件命名沿用 `{asset_key}_Video_Raw.mp4` / `{asset_key}_Video_Final.mp4`，不新增命名规则。
21. 删除 Raw / Final 时，历史归档保留，但当前 Working 标准路径必须移除。
22. Video Plan 中 Raw 已存在但 Audio 缺失时，自动合成 Final 按钮置灰；但终视频槽位仍为白色，因为可通过 Video Only Plan 手动拷贝 / Confirm 到 Final。
23. 不迁移旧数据；旧 `working_assets.video` 按终视频展示。
24. 原图槽位的标准文件名是 `{asset_key}_Image_Source.{ext}`。
25. 新图槽位的标准文件名是 `{asset_key}_Image_New.{ext}`。
26. 从原始素材、上传素材、历史素材绑定到任何槽位时，都必须复制到 Working 并重命名为该槽位标准文件名。
27. 原图绑定不能让 Image Plan 的新图状态变绿；只有新图槽 `Image_New` 绑定且文件存在时，新图才变绿。
28. 删除原图只清空并归档 `Image_Source`，不影响 `Image_New`。
29. 删除新图只清空并归档 `Image_New`，不影响 `Image_Source`、Raw 或 Final。
30. 替换原图或新图时，旧标准槽位文件先进入 Asset History，新文件再覆盖同一个标准槽位路径。
31. 旧 `Image_01` 不再作为新实现中的业务槽位名；新实现必须使用 `Image_Source` / `Image_New`。

以下问题已在 2026-06-19 确认：

32. 实现必须保障文件状态、槽位绑定状态和任务执行状态一致：任务成功生成文件时同步写回绑定；界面移除绑定时同步把对应 Working 标准文件移走或归档。
33. Prompt 是唯一不能通过槽位清除动作移除的任务产物；Prompt 一旦在 Working 中存在就显示绿色，允许修改并保存。
34. 绿色是最高优先级：对应 Working 标准文件存在时，覆盖灰色、白色、黄色和失败状态。
35. 除音频外，下游产物已存在时，上游缺失显示灰色，表示无需执行。
36. Prompt 文件存在时显示绿色；Prompt 文件不存在且 Raw / Final 等下游产物已存在时显示灰色。
37. Image Plan 的新图绿色只认 `{asset_key}_Image_New.*` 标准槽位文件。
38. Image Plan 需要知道 Raw / Final 存在状态，用于判断新图缺失但下游已存在时应显示灰色。
39. Video Only Plan 的 `新视频` 只有 Raw 文件存在才绿色；如果只有 Final 存在而 Raw 不存在，则 `新视频` 灰色、`终视频` 绿色。
40. 标准 Final 文件存在但未绑定时，优先视为异常修复状态：终视频绿色并提示修复绑定，不要求重跑 Raw。
41. 三类 Plan 必须走统一派生状态函数，输出 `tone / reason / file_exists / blocked_by_downstream / binding_consistency` 等 UI 状态，保障状态一致性。
42. 音频由独立 Plan 操作，没有“无需执行”灰色状态；音频文件存在显示绿色，音频文件不存在显示白色。
43. 三类 Plan 都不允许出现空白槽位状态；固定槽位必须始终显示，哪怕所有输入文件都不存在，也要显示对应颜色。

## 14. 2026-06-17 产出物一致性与 TailFrame 物化最终确认

### 14.1 Segment 标准产出物

每个 Segment 在 `Working/` 下只保留一套当前有效产出物。页面状态、Plan 状态和 Executor 状态都必须以这些标准产物的真实文件存在为准。

| 阶段 | 标准产出物 | 状态依据 | 说明 |
| --- | --- | --- | --- |
| 音频 | `{asset_key}_Audio_Final.{ext}` 或 Segment Audio | 真实音频文件存在 | 音频由独立 Plan 操作；有文件为绿色，无文件为白色，不因 Raw / Final 已存在而变灰 |
| 原图 | `{asset_key}_Image_Source.{ext}` | 真实文件存在 | 从原始素材、上传素材、历史素材拖入后复制并重命名到该槽位 |
| 新图提示词 | Image Plan / Video Plan 中的 Prompt 文件或 Prompt 内容 | Prompt 存在 | Prompt 状态和新图文件状态分离 |
| 新图 | `{asset_key}_Image_New.{ext}` | 真实文件存在 | 生成、拖入、历史恢复或 TailFrame 物化后都必须落到该槽位 |
| 新视频提示词 | `{asset_key}_VideoPrompt.json` 等现有 Prompt 文件 | Prompt 文件存在 | 用于生成 Raw Video |
| 新视频 | `{asset_key}_Video_Raw.{ext}` | 真实文件存在 | 不写入 `working_assets.video` |
| 尾帧 | `{asset_key}_TailFrame.{ext}` | 真实文件存在 | 只能作为后续 Segment 的可物化来源 |
| 终视频 | `{asset_key}_Video_Final.{ext}` | 真实文件存在并按规则绑定 | `working_assets.video` 仍表示 Final Video |

扩展名允许保留来源文件类型，例如图片可为 `.png/.jpg/.jpeg/.webp`，视频可为 `.mp4` 等现有支持格式；但文件名 stem 必须使用对应槽位标准名。

### 14.2 绑定、覆盖、删除

1. 从原始素材、上传素材、历史素材拖入任何槽位时，都必须复制到 `Working/`，并重命名为目标槽位标准文件名。
2. 替换或覆盖槽位时，旧标准槽位文件先进入 Asset History，新文件再覆盖当前标准槽位文件。
3. 删除槽位时，当前标准槽位文件进入 Asset History，并使用时间戳或批次目录避免历史文件互相覆盖。
4. 删除 `Image_Source` 不清空 `Image_New`、`Video_Raw`、`Video_Final`。
5. 删除 `Image_New` 不清空 `Image_Source`、`Video_Raw`、`Video_Final`。
6. 删除 `Video_Raw` 不清空 `Video_Final`，也不删除 `TailFrame`。
7. 删除 `Video_Final` 不清空 `Video_Raw`，也不删除 `TailFrame`。
8. 替换 Audio、Image 或 Raw 后，如果 `Video_Final` 仍存在，Final 继续保持完成态；只有用户手动删除 Final 才清 Final。

### 14.3 TailFrame 物化规则

TailFrame 是备用首图来源，不是跨 Segment 的持续绑定关系。后续 Segment 不能直接消费上一 Segment 的 TailFrame；当它需要使用 TailFrame 时，必须先把上一 Segment 的 TailFrame 复制为当前 Segment 的 `{asset_key}_Image_New.{ext}`。

| 场景 | 处理 |
| --- | --- |
| 当前 Segment 已有 `Image_New` | 直接使用当前 `Image_New`，不受上一 Segment TailFrame 变化影响 |
| 当前 Segment 没有 `Image_New`，上一 Segment 有 `TailFrame`，且需要生成视频 | 自动物化：复制上一 Segment TailFrame 为当前 Segment 的 `Image_New` |
| 当前 Segment 没有 `Image_New`，上一 Segment 也没有 `TailFrame` | 不能生成依赖首图的视频 |
| 上一 Segment 重新生成视频，TailFrame 变化 | 不自动覆盖后续 Segment 已经存在的 `Image_New` |
| 用户删除当前 Segment 的 `Image_New` | 不立刻自动物化；下次需要生成视频时再按最新上一 Segment TailFrame 物化 |
| 用户只删除当前 Segment 的 `Video_Raw`，保留 `Image_New` | 不重新物化 TailFrame，继续使用当前 `Image_New` |
| 用户删除当前 Segment 的 `Image_New` 和 `Video_Raw` | 下次生成视频时，若上一 Segment 有 TailFrame，则重新物化为新的 `Image_New` |

自动物化出的 `Image_New` 不进入 Asset History；它是当前 Segment 的 Working 产物。只有用户删除该新图槽位时，它才进入 Asset History。

### 14.4 Segment 拆分、合并和 TailFrame

| 操作 | 规则 |
| --- | --- |
| Scene_001 拆成 A / B | B 允许使用 A 的 TailFrame 作为备用首图来源 |
| B 首次生成视频且没有 `Image_New` | 自动把 A 的 TailFrame 物化成 B 的 `Image_New` |
| A 后续重新生成视频，A 的 TailFrame 改变 | B 已有 `Image_New` 时不自动更新 |
| B 删除 `Image_New` 后重新生成视频 | 再使用当前最新的上一 Segment TailFrame 物化 |
| B 删除 `Video_Raw` 但保留 `Image_New` | 不重新物化，继续使用 B 当前 `Image_New` |
| 多段合并 | 保留前一段或第一段的产出物；后一段产出物不自动混入合并后的 Segment |
| 合并导致后续 Segment 的上一段变化 | 后续 Segment 已有 `Image_New` 时不覆盖；缺 `Image_New` 且需要生成视频时，再按新的上一段 TailFrame 物化 |

### 14.5 页面所得即所见

| 页面显示 | 真实含义 |
| --- | --- |
| 原图绿色 | 当前 Segment 存在 `Image_Source` 文件 |
| 新图绿色，显示“新图” | 当前 Segment 存在 `Image_New` 文件 |
| 新图绿色，显示“尾帧作为新图” | 上一 Segment TailFrame 已被物化为当前 Segment 的 `Image_New` 文件 |
| 新图白色 | 当前 Segment 没有 `Image_New` 文件 |
| 新图待物化 | 当前 Segment 没有 `Image_New`，但上一 Segment TailFrame 可在执行生成视频前物化 |
| 新视频绿色 | 当前 Segment 存在 `Video_Raw` 文件 |
| 终视频绿色 | 当前 Segment 存在 `Video_Final` 文件 |
| Prompt 绿色 | Prompt 存在，不代表对应图片或视频文件存在 |

Plan Modal 的按钮状态必须按当前 Working 文件即时计算。Execution JSON 只能作为历史记录或运行进度参考，不能让缺失文件显示为完成。

### 14.6 临时文件边界

`FirstFrame.png`、`MaterializedFirstFrame.png`、`Video_LipSync.mp4`、`Video_AudioSynced.mp4` 等文件可以继续作为工具内部临时文件保留，但不能作为 UI、Plan 或 StoryBoard 完成态依据。完成态只能来自标准产物：

| 临时或中间文件 | 可用于 | 最终必须发布为 |
| --- | --- | --- |
| `FirstFrame.*` | Executor 内部视频首帧输入 | `Image_New` |
| `MaterializedFirstFrame.*` | TailFrame 物化的临时复制 | `Image_New` |
| `Video_LipSync.mp4` | 口播对嘴临时结果 | `Video_Final` |
| `Video_AudioSynced.mp4` | 空镜配音临时结果 | `Video_Final` |

### 14.7 已发现漏洞与修复验收清单

以下问题来自 2026-06-17 的界面和流程复盘，必须作为 Image Plan、Video Plan、Video Only Plan 和主界面状态一致性的验收项。

| 优先级 | 环节 | 问题 | 必须修复为 |
| --- | --- | --- | --- |
| P0 | Video Only Plan 前端 | 后端能判断“尾帧可物化”，但前端只看 `frame_input_exists` | 当前段无 `Image_New`、上一段有 TailFrame、且无 Raw/Final 时，按钮可执行；执行时物化 TailFrame 并生成 Raw |
| P0 | Video Only Executor | TailFrame 物化后只产生 `Image_New` 文件，未同步绑定 StoryBoard | 物化后必须更新 `working_assets.images[0]` 和 `bound_image_path`，并标记来源为 `tail_frame_materialized` |
| P0 | 覆盖规则 | 替换素材时旧标准槽位文件被直接删除，未归档到 History | 旧标准槽位文件先进入 Asset History，新文件再覆盖当前标准槽位文件 |
| P1 | Video Only Plan 显示 | 物化后的新图来源无法表达 | `source_type=tail_frame_materialized` 时，Plan 显示“尾帧作为新图” |
| P1 | 主界面和 Plan 状态一致性 | Plan 可按 Working 文件显示绿色，但主界面依赖 StoryBoard 绑定 | 任何生成、拖入、物化得到的标准产物都必须同步到 StoryBoard 绑定；避免文件存在但槽位为空 |
| P1 | Segment 拆分/合并 | “保留前一段/第一段产物”未显式落在拆分/合并逻辑 | 拆分后后一段可用前一段 TailFrame；合并保留前一段/第一段产物；后续段已有 `Image_New` 时不自动覆盖 |
| P2 | 临时文件边界 | 工具内部临时文件可能被误读为完成态 | 所有 Plan 和 UI 完成态只认标准产物，不认 `FirstFrame`、`MaterializedFirstFrame`、`Video_LipSync`、`Video_AudioSynced` |
| P2 | Plan 缓存 | Segment 顺序变化可能沿用旧“上一段”关系 | Segment 拆分、合并、顺序变化后，Video Plan / Video Only Plan 必须按新的 Segment 顺序重新计算 TailFrame 来源 |

最容易走到死胡同的路径必须全部覆盖：

| 路径 | 正确结果 |
| --- | --- |
| B 没有 `Image_New`，A 有 TailFrame，打开 Video Only Plan | 显示“尾帧待物化”，可执行生成视频 |
| B 删除 `Image_New` 和 `Video_Raw` 后想重新生成 | 若上一段有 TailFrame，则执行时重新物化为 B 的 `Image_New` |
| Video Only 执行成功物化 `Image_New` | Working 文件存在，StoryBoard 新图槽同步绑定，Plan 显示“尾帧作为新图” |
| 用户替换新图、原图或 Raw | 旧标准槽位文件先进入 Asset History，新文件再覆盖标准槽位文件 |
| Segment 合并 | 明确保留前一段/第一段产物，后一段产物不自动混入 |

## 15. 相关文档

```text
docs/SessionDesign-R2/Analysis_V1_05_01_VideoGenerationPlan_工具需求整理.md
docs/SessionDesign-R2/Analysis_V1_05_02_VideoPlanExecutor_工具需求整理.md
OpenCrew/docs/SessionDesign-R2/Analysis_V1_05_03_05_04_ImagePlan_工具需求整理.md
OpenCrew/docs/SessionDesign-R2/Analysis_V1_05_05_05_06_VideoOnlyPlan_工具需求整理.md
docs/SessionDesign-R2/STORYBOARD_ASSET_HISTORY_REQUIREMENTS.md
docs/SessionDesign-R2/Koubo_VideoPlan_Button_Modal_需求确认.md
docs/SessionDesign-R2/Koubo_VideoPlan_槽位颜色测试案例表.md
```
