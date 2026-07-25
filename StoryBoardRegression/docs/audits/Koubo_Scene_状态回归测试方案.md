# Koubo StoryBoard Scene 状态回归测试方案

版本：v0.3

状态：测试方案草案。本文用于定义单个 Scene 的状态回归测试，覆盖 StoryBoard 槽位、Image Plan、Video Only Plan、Video Plan 三个计划面板之间的状态一致性。测试重点是“有 / 没有”的槽位呈现、删除不级联、Raw / Final 独立、Dialogue Audio 对 Segment Audio 的影响，以及 Video Plan 允许呈现计算后的执行状态。

## 1. 测试目标

目标 Scene 以 `Task #4 / Session #5 / Shot_001 / Scene_001` 为基准样例，后续可以复用到任意 Scene。

本测试要确认：

1. StoryBoard 主界面的槽位是金标准：`Audio / 原图 / 新图 / 新视频 / 终视频 / 尾帧`。
2. Image Plan 读取并反映 StoryBoard 的新图状态。
3. Video Only Plan 读取并反映 StoryBoard 的 `Audio / 新图 / Raw Video / Final Video` 状态。
4. Video Plan 可以呈现计算后的完整执行结果；在绑定视频已足够执行时，某些步骤可以显示为灰色“不执行”，不要求和 Video Only Plan 的白色 pending 完全一致。
5. 删除 Audio 只影响 Audio，不得级联清除或改变 `新图 / Raw Video / Final Video / TailFrame`。
6. Raw Video 和 Final Video 独立判断：Final 不能把 Raw 点绿，Raw 也不能把 Final 点绿。
7. Segment Audio 的完成状态由 Segment 内全部 Dialogue Audio 决定；单个 Dialogue Audio 缺失时，Segment Audio 在 Video Plan / Video Only Plan 中都应变为未完成。
8. 随机从中间删除或补回任一产出物后，三个计划面板刷新后仍能回到正确状态。
9. 覆盖“先生成 Audio，再只用 Video Plan 一键生成全部后续素材，最后拖拽回填验证”的完整链路案例；该案例禁止借助 Image Plan / Video Only Plan 补产物。
10. 覆盖多 Shot、多 Scene、Split Scene、Split Shot、Merge 恢复、跨 Segment / 跨 Scene / 跨 Shot 尾帧依赖，以及这些结构变化下的绑定状态一致性。

## 2. 术语和状态口径

### 2.1 目标槽位

| 槽位 | 标准文件 | 状态来源 |
| --- | --- | --- |
| Audio | `Working/{asset_key}_Audio_Final.wav` | Dialogue `working_assets.audio.path` + 文件存在 |
| 新图 | `Working/{asset_key}_Image_New.png` | Dialogue `working_assets.images[0].path` 或 `bound_image_path` + 文件存在 |
| Raw Video / 新视频 | `Working/{asset_key}_Video_Raw.mp4` | 标准文件存在 |
| Final Video / 终视频 | `Working/{asset_key}_Video_Final.mp4` | 标准文件存在 + StoryBoard `working_assets.video.path` 绑定 |
| TailFrame / 尾帧 | `Working/{asset_key}_TailFrame.jpg` | 标准文件存在 |

说明：

1. `Raw Video` 不写入 `working_assets.video`，只由标准文件判断。
2. `Final Video` 必须文件存在并绑定到 StoryBoard，才算完成。
3. 测试中的“清空所有内容”指清空目标 Scene 的可再生产产出物槽位，不删除历史素材库，不删除原始参考输入，不破坏 Task / Session 基础数据。
4. 清 Audio、清新图、清 Raw、清 Final 都必须通过产品接口或界面操作完成，不直接手工删除 Working 文件。

### 2.2 三个计划面板的差异口径

| 面板 | 预期口径 |
| --- | --- |
| Image Plan | 面向新图生成。应跟随 StoryBoard 新图槽位的有 / 无变化。 |
| Video Only Plan | 面向 Raw Video 生成和确认 Final。应按槽位状态所见即所得显示；新图有则绿，缺失则白色 pending。 |
| Video Plan | 面向完整执行链。可以显示计算后的执行结果；如果当前 Segment 已是绑定视频或某步骤不需要执行，新图可以是灰色“不执行”。Raw / Final 仍必须按各自槽位独立显示。 |

## 3. 基础测试数据

以 `Task #4 / Session #5 / Shot_001 / Scene_001` 为固定回归样例：

| 角色 | 样例值 |
| --- | --- |
| Scene | `shot_001 / scene_001` |
| Segment | `shot_001_scene_001_segment_001` |
| 第一条 Dialogue | `scene_001_dialogue_001` / `srt_0001` |
| 第二条 Dialogue | `scene_001_dialogue_002` / `srt_0004` |
| Segment Audio 判断 | `srt_0001_Audio_Final.wav` 和 `srt_0004_Audio_Final.wav` 都存在才算完成 |
| Raw Video 判断 | `srt_0001_Video_Raw.mp4` |
| Final Video 判断 | `srt_0001_Video_Final.mp4` + `scene_001_dialogue_001.working_assets.video.path` 绑定 |

## 4. 测试前准备

1. 确认本地 OpenCrew 服务健康：

```text
frontend: http://127.0.0.1:18080/
backend:  http://127.0.0.1:8011
```

2. 确认历史素材中至少存在可恢复的样例产物：

```text
srt_0001_Audio_Final.wav
srt_0004_Audio_Final.wav
srt_0001_Image_New.png
srt_0001_Video_Raw.mp4
srt_0001_Video_Final.mp4
```

3. 对所有“清空后重新覆盖产物”的场景，固定执行顺序必须先生成 Audio：

```text
清空目标槽位
-> 先生成 srt_0001 / srt_0004 Dialogue Audio
-> 确认 Segment Audio 在 Video Plan / Video Only Plan 中完成
-> 再继续生成新图 / Raw Video / Final Video / TailFrame
```

说明：Audio 是后续 Segment Audio、Video Plan 完整链路和 Final / lipsync 判断的前置条件；测试计划中的全量覆盖场景不得跳过“先生成音频”这一步。

4. 每一步操作后都刷新并记录：

```text
StoryBoard 主界面槽位
Image Plan 状态
Video Only Plan 状态
Video Plan 状态
接口状态快照
```

5. 每一步记录至少包含：

```text
audio_exists
new_image_exists
raw_video_exists
final_video_exists
final_video_bound
Image Plan image 状态
Video Only Plan audio / first_frame / raw / final 状态
Video Plan audio / image / raw / final 状态
```

## 5. 场景一：清空产出物后，用 Image Plan + Video Only Plan 覆盖

### 5.1 操作步骤

1. 清空目标 Scene 的产出物槽位：
   - 清 `srt_0001` Audio。
   - 清 `srt_0004` Audio。
   - 清 `srt_0001` 新图。
   - 清 `srt_0001` Raw Video。
   - 清 `srt_0001` Final Video。
   - 如测试范围包含 TailFrame，则清 `srt_0001` TailFrame。
2. 先从 StoryBoard 主界面生成 Audio：
   - 点击目标 Scene 的音频生成入口。
   - 等待 `srt_0001_Audio_Final.wav` 和 `srt_0004_Audio_Final.wav` 都生成完成。
   - 刷新并确认 Video Plan / Video Only Plan 的 Segment Audio 都显示完成。
3. 打开 Image Plan，生成或恢复新图产物，覆盖 `srt_0001_Image_New.png`。
4. 打开 Video Only Plan，生成或恢复 Raw Video，覆盖 `srt_0001_Video_Raw.mp4`。
5. 在 Video Only Plan 中确认 Raw 为 Final，覆盖并绑定 `srt_0001_Video_Final.mp4`。
6. 刷新 StoryBoard / Image Plan / Video Only Plan / Video Plan。

### 5.2 预期结果

| 检查项 | 预期 |
| --- | --- |
| StoryBoard Audio | 两条 Dialogue Audio 都存在，Audio 槽位显示可播放 |
| Segment Audio | 由两条 Dialogue Audio 计算为完成，Video Plan / Video Only Plan 的 Audio 状态完成 |
| StoryBoard 新图 | 有图，槽位显示真实缩略图 |
| StoryBoard 新视频 | Raw 存在，显示真实视频缩略图 |
| StoryBoard 终视频 | Final 存在并绑定，显示真实视频缩略图 |
| Image Plan | 新图步骤完成 |
| Video Only Plan | 新图、Raw、Final 完成 |
| Video Plan Audio | 完成 |
| Video Plan 新图 | 允许按完整执行计划计算为灰色“不执行”，尤其当前 Segment 已绑定视频时 |
| Video Plan 新视频 | Raw 存在则完成 |
| Video Plan 终视频 | Final 存在且绑定则完成 |

### 5.3 覆盖的历史 bug

1. Video Only Plan 的新图状态必须和 StoryBoard 新图槽位一致。
2. Raw 存在不能自动代表 Final 完成，Final 必须绑定。
3. Final 存在不能反向点亮 Raw；Raw 仍由 `Video_Raw` 文件独立判断。

## 6. 场景二：先生成 Audio，再只用 Video Plan 一键生成全部素材并拖拽验证

本场景是强制覆盖用例：第一步只生成 Audio；Audio 完成后，必须只通过 Video Plan 的一键执行生成该 Segment 后续全部素材。不能用 Image Plan 生成新图，也不能用 Video Only Plan 生成 Raw / Final。

### 6.1 操作步骤

1. 清空目标 Scene 的产出物槽位：
   - 清 `srt_0001` Audio。
   - 清 `srt_0004` Audio。
   - 清 `srt_0001` 新图。
   - 清 `srt_0001` Raw Video。
   - 清 `srt_0001` Final Video。
   - 清 `srt_0001` TailFrame。
2. 先从 StoryBoard 主界面生成 Audio：
   - 点击目标 Scene 的音频生成入口。
   - 等待 `srt_0001_Audio_Final.wav` 和 `srt_0004_Audio_Final.wav` 都生成完成。
   - 刷新并确认 Video Plan / Video Only Plan 的 Segment Audio 都显示完成。
3. 打开 Video Plan，生成当前 Scene 的完整计划。
4. 在 Video Plan 中点击一键执行，让 Video Plan 一次性生成并发布该 Segment 的全部后续素材：
   - `srt_0001_SegmentAudio_Final.wav`
   - `srt_0001_Image_New.png`
   - `srt_0001_Video_Raw.mp4`
   - `srt_0001_Video_Final.mp4`
   - `srt_0001_TailFrame.jpg`
5. 本场景禁止使用其他计划面板补产物：
   - 不得打开 Image Plan 点击“提示词+新图”或“新图”。
   - 不得打开 Video Only Plan 点击“提示词+新视频”、“新视频”或“拷贝成终视频”。
   - 如果 Video Plan 自身因为已有素材而跳过某一步，本场景必须重新清空对应槽位后重跑，直到证明这些素材由 Video Plan 一键链路产出。
6. 刷新 StoryBoard / Image Plan / Video Only Plan / Video Plan。
7. 生成都覆盖后，执行至少一次拖拽验证：
   - 清空一个已生成槽位，推荐优先清 `srt_0001_Image_New.png`。
   - 保留 Raw / Final 不动。
   - 从历史素材把同类产物拖回对应槽位。
   - 保存 StoryBoard 并刷新三个计划面板。
8. 如果 Final Video 因外部 provider、网络或本机代理规则失败：
   - 保留 Video Plan 错误截图和执行状态 JSON。
   - 明确记录已完成到哪一步，例如 Segment Audio / 新图 / Raw / TailFrame。
   - 继续用历史素材拖拽回填缺失槽位，单独验证 StoryBoard 槽位、左侧绑定状态与计划状态是否一致。

### 6.2 预期结果

| 检查项 | 预期 |
| --- | --- |
| Dialogue Audio | Segment 中所有 Dialogue Audio 都存在 |
| Segment Audio | 由全部 Dialogue Audio 拼接或匹配生成，且本场景中必须由 Video Plan 一键链路产出或确认复用 Audio 先生成结果 |
| 新图 | `srt_0001_Image_New.png` 必须由 Video Plan 一键链路生成并发布到 StoryBoard Working，StoryBoard / Image Plan / Video Only Plan 都看到新图完成 |
| Raw Video | `srt_0001_Video_Raw.mp4` 必须由 Video Plan 一键链路生成并发布到 StoryBoard Working，Video Plan / Video Only Plan 的 Raw 完成 |
| Final Video | `srt_0001_Video_Final.mp4` 必须由 Video Plan 一键链路生成并绑定，Video Plan / Video Only Plan 的 Final 完成 |
| TailFrame | `srt_0001_TailFrame.jpg` 必须由 Video Plan 一键链路生成并发布到 StoryBoard Working |
| 拖拽回填 | 被清空槽位拖回后，StoryBoard 槽位、左侧绑定状态、Image Plan / Video Only Plan / Video Plan 状态重新一致 |
| 外部生成阻断 | 不直接判定为状态逻辑失败；必须记录阻断原因，并继续完成历史素材拖拽回填验证 |

### 6.3 覆盖的历史 bug

1. Audio 生成完成后，Video Plan 和 Video Only Plan 都必须按 Dialogue Audio 判断，不只看旧的 SegmentAudio 文件。
2. Video Plan 的 Raw / Final 状态必须和 StoryBoard 槽位一致。
3. 执行完成后，业务产物必须发布到 StoryBoard Working，而不是只留在工具 Working 目录。
4. 一键执行 Video Plan 后，拖拽历史素材回填任一槽位，计划面板必须按最新 StoryBoard 槽位重新计算。
5. Video Plan 一键执行必须能独立覆盖从新图到 Final 的完整产物链，不能依赖 Image Plan / Video Only Plan 预先生成素材。

## 7. 场景三：按槽位顺序移除和加回

本场景从完整状态开始。完整状态定义为：

```text
srt_0001_Audio_Final.wav = 存在
srt_0004_Audio_Final.wav = 存在
srt_0001_Image_New.png = 存在
srt_0001_Video_Raw.mp4 = 存在
srt_0001_Video_Final.mp4 = 存在且绑定
```

### 7.1 Audio 移除 / 加回

| 步骤 | 操作 | 预期 |
| --- | --- | --- |
| A1 | 移除 `srt_0001` Audio | Video Plan / Video Only Plan Audio 未完成；新图、Raw、Final 不变 |
| A2 | 加回 `srt_0001` Audio | Audio 恢复完成；新图、Raw、Final 不变 |
| A3 | 移除 `srt_0004` Audio | Segment Audio 未完成；新图、Raw、Final 不变 |
| A4 | 加回 `srt_0004` Audio | Segment Audio 恢复完成；新图、Raw、Final 不变 |

验收重点：

```text
清 Audio 只清 Audio。
不得清 Raw。
不得清 Final。
不得清 TailFrame。
不得改变 StoryBoard 视频绑定。
```

### 7.2 新图移除 / 加回

| 步骤 | 操作 | Image Plan | Video Only Plan | Video Plan |
| --- | --- | --- | --- | --- |
| I1 | 移除 `srt_0001_Image_New.png` | 新图未完成 | first_frame 未完成或 pending | 可保持灰色不执行，或按当前完整计划计算 |
| I2 | 加回 `srt_0001_Image_New.png` | 新图完成 | first_frame 完成 | 如果当前 Video Plan 计算不需要新图，可仍为灰色不执行 |

验收重点：

```text
Image Plan 和 Video Only Plan 应体现新图槽位有 / 无。
Video Plan 允许呈现计算后的执行结果，不强制与 Video Only Plan 同色。
```

### 7.3 Raw Video 移除 / 加回

| 步骤 | 操作 | 预期 |
| --- | --- | --- |
| R1 | 移除 `srt_0001_Video_Raw.mp4` | Video Plan Raw 未完成；Video Only Plan Raw 未完成；Final 如果仍存在且绑定则继续完成 |
| R2 | 加回 `srt_0001_Video_Raw.mp4` | Video Plan Raw 完成；Video Only Plan Raw 完成；Final 状态不变 |

验收重点：

```text
Final 存在不能把 Raw 点绿。
Raw 加回后，Final 不存在时 Raw 必须能单独点绿。
```

### 7.4 Final Video 移除 / 加回

| 步骤 | 操作 | 预期 |
| --- | --- | --- |
| F1 | 移除 `srt_0001_Video_Final.mp4` 或清 Final 绑定 | Raw 保持完成；Final 未完成；下一步应指向从 Raw 到 Final |
| F2 | 加回并绑定 `srt_0001_Video_Final.mp4` | Raw 完成；Final 完成 |

验收重点：

```text
清 Final 不得清 Raw。
清 Final 不得清新图。
Final 加回后不得反向影响 Raw 的真实判断。
Final 只有文件存在且 StoryBoard 绑定时才算完成。
```

## 8. 场景四：随机中间状态移除和补回

### 8.1 操作原则

从完整状态或任意中间状态开始，随机选择一个或多个槽位执行“移除 -> 刷新 -> 补回 -> 刷新”。

推荐随机组合：

| 组合 | 操作 | 重点观察 |
| --- | --- | --- |
| M1 | 移除 Audio + Raw，保留 Final | Audio 未完成，Raw 未完成，Final 仍完成 |
| M2 | 移除 Final + 新图，保留 Raw | Image Plan / Video Only Plan 新图未完成，Raw 完成，Final 未完成 |
| M3 | 移除两个 Dialogue Audio，保留 Raw / Final | Segment Audio 未完成，Raw / Final 不变 |
| M4 | 移除 Raw + Final，保留新图 | 新图完成，Raw / Final 未完成 |
| M5 | 只补回 Final，不补 Raw | Final 完成，Raw 不应被 Final 点绿 |
| M6 | 只补回 Raw，不补 Final | Raw 完成，Final 未完成 |
| M7 | 先补回 Audio，再补回 Raw，最后补回 Final | 每一步只改变对应槽位状态 |

### 8.2 随机测试断言

每次随机操作后都要断言：

1. StoryBoard 主界面显示和接口槽位状态一致。
2. Image Plan 对新图的完成态和 StoryBoard 新图槽位一致。
3. Video Only Plan 对 Audio / 新图 / Raw / Final 的完成态和 StoryBoard 槽位一致。
4. Video Plan 对 Audio / Raw / Final 的完成态和 StoryBoard 槽位一致。
5. Video Plan 的新图步骤允许是计算结果；如果当前 Segment 已可由绑定视频继续或不需要新图，则灰色“不执行”是合法状态。
6. 删除前置槽位不得清除后置槽位。
7. 删除后置槽位不得清除前置槽位。
8. 状态恢复后，三类计划面板都能回到完整状态。

## 9. 场景五：多 Shot / 多 Scene / 分割 / 尾帧绑定覆盖案例集

本场景用于在第一个 Shot 的第一个 Scene 绑定逻辑已经通过后，继续验证 StoryBoard 结构变化和多范围状态是否正确。

最短路径目标：

1. 不把每个 Shot / Scene 都完整随机测一遍，而是用一个完整基线、两次结构分割、五个槽位矩阵和一组随机组合覆盖主要风险。
2. 用 `Task #4 / Session #5` 现有结构作为样例：
   - `shot_001 / scene_001`：单 Scene 多 Dialogue，适合验证多 Dialogue 合一个 Segment。
   - `shot_002`：多 Scene，适合验证 Scene 间尾帧继承。
   - `shot_003`：后续 Shot，适合验证跨 Shot 状态和尾帧依赖。
3. 结构测试结束后必须恢复到原始结构，避免后续状态测试混入结构变更噪音。

### 9.1 多范围完整基线

先建立可观察基线：

1. 清空 `Task #4 / Session #5` 中目标测试范围的可再生产产物。
2. 先生成全部 Dialogue Audio。
3. 用 Video Plan 一键覆盖至少 `shot_001 / scene_001`，并尽量覆盖 `shot_002` 和 `shot_003`。
4. 如果 Final 因外部 provider / 代理失败，可以用历史素材拖回 Final，但必须在测试记录中区分“自然生成阻断”和“拖拽绑定通过”。

Should 状态：

| 检查项 | Should |
| --- | --- |
| StoryBoard | 目标范围内 Audio / 新图 / Raw / Final / TailFrame 可见状态和接口一致 |
| Image Plan | 每个有 `Image_New` 的位置显示新图完成；缺新图的位置显示待生成或灰色不可执行 |
| Video Only Plan | Audio / 新图 / Raw / Final 按 StoryBoard Working 实际槽位显示 |
| Video Plan | Audio / Raw / Final 按 StoryBoard Working 实际槽位显示；新图允许为计算后的灰色不执行 |
| SegmentAudio | 每个 Segment 的 SegmentAudio 由该 Segment 内全部 Dialogue Audio 决定 |
| TailFrame | 每个已完成 Final 的 Segment 有对应 `{first_dialogue_asset_key}_TailFrame.*` |

### 9.2 现有结构分割状态检查

在不改变结构的情况下先打开 Video Plan，记录当前 plan 的结构：

| 位置 | 检查 | Should |
| --- | --- | --- |
| `shot_001 / scene_001` | 两条 Dialogue 是否合成一个 Segment | 应为一个 Segment，SegmentAudio 由 `srt_0001` + `srt_0004` 决定 |
| `shot_001 / scene_001` | Segment 业务 asset key | Segment 级音频、Raw、Final、TailFrame 以第一条 Dialogue 的 `srt_0001` 为主 |
| `shot_002` 多 Scene | Scene 间首帧来源 | 后续 Scene 有自己的新图则用新图；没有视觉输入时才使用上一 Segment TailFrame |
| `shot_003` | 跨 Shot 状态 | 状态不能被前一个 Shot 的旧 execution result 误点亮；只按 StoryBoard Working 和当前 plan 判断 |

### 9.3 Split Scene 覆盖

在 `shot_001 / scene_001` 的第二条 Dialogue 上执行 Split Scene。

Should 状态：

| 阶段 | Should |
| --- | --- |
| Split 后结构 | 原来的一个 Scene 变成两个 Scene / 两个 Segment |
| 第一段归属 | 第一段继续以 `srt_0001` 为 first dialogue asset key |
| 第二段归属 | 第二段以 `srt_0004` 为 first dialogue asset key |
| SegmentAudio | 第一段只看第一条 Dialogue Audio；第二段只看第二条 Dialogue Audio |
| 第二段首帧 | 如果第二段没有自己的原图 / 新图，应依赖第一段 TailFrame |
| 删除第一段 TailFrame | 第二段 Video Plan 应变为 blocked、缺少前置尾帧，或显示待补首帧；不能继续显示完整可执行 |
| 恢复第一段 TailFrame | 第二段恢复可计划 / 可执行 |
| Image Plan | 只跟随当前 Scene / Segment 的新图槽位，不把上一段 TailFrame 当作新图完成 |
| Video Only Plan | 第二段 Raw / Final 状态只看 `srt_0004` 对应标准槽位，不继承 `srt_0001` 的 Raw / Final |
| Video Plan | 第二段可以使用上一段 TailFrame 作为执行输入，但不能把上一段 Raw / Final 当作本段完成态 |

### 9.4 Merge Scene 恢复

将 Split Scene 产生的新 Scene merge 回原结构。

Should 状态：

| 检查项 | Should |
| --- | --- |
| StoryBoard 结构 | 回到 `shot_001 / scene_001` 内两条 Dialogue |
| Video Plan Segment | 回到一个 Segment |
| SegmentAudio | 再次由 `srt_0001` + `srt_0004` 两条 Dialogue Audio 共同决定 |
| 旧 split execution | 旧分段执行状态不能压过当前 StoryBoard 结构 |
| TailFrame | 当前合并后 Segment 的 TailFrame 以 `srt_0001` 为主 |

### 9.5 Split Shot 覆盖

在 `shot_001 / scene_001` 的第二条 Dialogue 上执行 Split Shot。

Should 状态：

| 阶段 | Should |
| --- | --- |
| Split 后结构 | 第二条 Dialogue 进入新 Shot 的第一个 Scene |
| 新 Shot 首段 asset key | 新 Shot 首段以 `srt_0004` 为 first dialogue asset key |
| 新 Shot 首帧 | 如果新 Shot 首段没有自己的视觉输入，应尝试使用上一个 Shot 最后一段 TailFrame |
| 删除上一个 Shot 尾帧 | 新 Shot 首段应 blocked、待补首帧，或明确显示缺少前置 TailFrame |
| 恢复上一个 Shot 尾帧 | 新 Shot 首段恢复可计划 / 可执行 |
| 跨 Shot 状态 | 新 Shot 不能继承上一个 Shot 的 Raw / Final 完成态，只能继承 TailFrame 作为输入 |

Split Shot 测完后必须恢复到原始 `shot_001 / scene_001` 双 Dialogue 结构。

### 9.6 多 Shot / 多 Scene 最小槽位矩阵

结构恢复后，按不同位置分散测试槽位变化，避免只测 `shot_001 / scene_001`。

| 位置 | 操作 | Should 状态 |
| --- | --- | --- |
| `shot_001 / scene_001` | 清 Audio，再补回 | Audio / SegmentAudio 变化；新图、Raw、Final、TailFrame 不变 |
| `shot_002 / scene_002` | 清新图，再补回 | Image Plan / Video Only Plan 新图跟随变化；Raw / Final / TailFrame 不被级联清 |
| `shot_002 / scene_003` | 清 Raw，再补回 | Raw 状态变化；Final 如果存在且绑定仍完成；Final 不能反向点亮 Raw |
| `shot_003 / scene_004` | 清 Final，再补回 | Raw 保留；Video Only Plan 回到可 Confirm；Video Plan 回到 Final from Raw 或 Sync from Raw |
| `shot_002 / scene_002` | 清 TailFrame，再补回 | 依赖该 TailFrame 的后续 Scene / Segment 状态随之 blocked / 恢复；本段 Audio / 新图 / Raw / Final 不被清 |

### 9.7 随机组合补测

只做一组随机组合，验证中间状态恢复能力：

1. 同时清 `shot_002 / scene_002` 的 Audio + TailFrame。
2. 同时清 `shot_003 / scene_004` 的 Final。
3. 刷新 StoryBoard / Image Plan / Video Only Plan / Video Plan。
4. 按相反方向补回：Audio -> TailFrame -> Final。

Should 状态：

| 步骤 | Should |
| --- | --- |
| 清 Audio + TailFrame | Audio 未完成；依赖 TailFrame 的后续段 blocked 或待补；Raw / Final 不被清 |
| 清 Final | 目标段 Final 未完成；Raw 若存在仍完成 |
| 补 Audio | 只恢复 Audio / SegmentAudio；TailFrame 依赖仍未恢复 |
| 补 TailFrame | 依赖该尾帧的后续段恢复可计划 / 可执行 |
| 补 Final | 只恢复 Final；不得反向改变 Raw 判断 |

### 9.8 本场景通过标准

本场景通过必须同时满足：

1. Split Scene 后 Segment 数量、first dialogue asset key、SegmentAudio 归属正确。
2. Merge Scene 后旧分段状态不再压过当前结构。
3. Split Shot 后新 Shot 只继承上一个 Shot 的 TailFrame 作为输入，不继承 Raw / Final 完成态。
4. 删除 TailFrame 只影响尾帧依赖，不清 Audio / 新图 / Raw / Final。
5. 多 Shot / 多 Scene 下，所有槽位删除和补回都只影响目标槽位。
6. StoryBoard、Image Plan、Video Only Plan、Video Plan 的状态均能在刷新后回到一致。

## 10. 接口级回归建议

### 10.1 推荐操作接口

| 动作 | 接口方向 |
| --- | --- |
| 获取 StoryBoard + 槽位状态 | `GET /api/koubo-storyboard/tasks/{task_id}` |
| 清槽位 | `POST /api/koubo-storyboard/tasks/{task_id}/asset-clear` |
| 从历史恢复素材 | `POST /api/koubo-storyboard/tasks/{task_id}/asset-history/restore` |
| 绑定素材到槽位 | `POST /api/koubo-storyboard/tasks/{task_id}/asset-bind` |
| 生成 / 刷新 Image Plan | `POST /api/koubo-storyboard/tasks/{task_id}/image-plan` |
| 生成 / 刷新 Video Only Plan | `POST /api/koubo-storyboard/tasks/{task_id}/video-only-plan` |
| 生成 / 刷新 Video Plan | `POST /api/koubo-storyboard/tasks/{task_id}/video-plan` |

### 10.2 最小断言表

| 状态 | StoryBoard Audio | StoryBoard 新图 | StoryBoard Raw | StoryBoard Final | Image Plan | Video Only Plan | Video Plan |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 全空 | false | false | false | false | 未完成 | 未完成 | Audio/Raw/Final 未完成，新图可灰 |
| 仅新图 | false | true | false | false | 完成 | first_frame 完成 | Audio/Raw/Final 未完成 |
| 新图 + Raw | false | true | true | false | 完成 | Raw 完成，Final 未完成 | Raw 完成，Final 未完成 |
| 新图 + Raw + Final | false | true | true | true | 完成 | Raw/Final 完成 | Audio 未完成，Raw/Final 完成 |
| Audio 全部存在 + Raw，无 Final | true | 任意 | true | false | 跟随新图 | Audio/Raw 完成，Final 未完成 | Audio/Raw 完成，Final 未完成 |
| Audio 全部存在 + Final，无 Raw | true | 任意 | false | true | 跟随新图 | Final 完成，Raw 未完成 | Final 完成，Raw 未完成 |
| 完整状态 | true | true | true | true | 完成 | 完成 | Audio/Raw/Final 完成，新图可按计算灰或完成 |

## 11. 手工 UI 验收清单

每个关键步骤都要通过 Computer Use 或浏览器截图确认：

1. StoryBoard 主界面实际槽位缩略图和删除按钮状态。
2. Image Plan 的新图步骤颜色和计数。
3. Video Only Plan 的 `音频 / 新图 / 提示词 / 新视频 / 拷贝成终视频` 颜色和计数。
4. Video Plan 的 `音频 / 新图 / 新视频 / 终视频` 颜色和计数。
5. Video Plan 新图如果显示灰色，确认原因是“不执行”而不是丢失状态。
6. 清 Audio 后，右侧历史素材中 Raw / Final / TailFrame 没有被误移除。
7. 清 Final 后，StoryBoard 主界面 Raw 仍然可见，Video Plan / Video Only Plan Raw 仍然完成。
8. 只补回 Raw 时，Final 不被自动点亮。
9. 只补回 Final 时，Raw 不被自动点亮。
10. Split Scene / Split Shot 后，Video Plan 中 Segment 数量、first dialogue asset key、SegmentAudio 归属和 TailFrame 依赖显示正确。
11. Merge 恢复后，旧分割状态不再压过当前 StoryBoard 结构。

## 12. 通过标准

一次完整状态回归通过，必须满足：

1. 五个场景全部完成。
2. 每个步骤均有接口快照。
3. 每个关键状态至少有一张 UI 截图或 Computer Use 观察记录。
4. 所有断言通过。
5. 测试结束时，`shot_001 / scene_001` 恢复到原始双 Dialogue 结构；Split Scene / Split Shot 临时结构不得残留。
6. 测试结束时恢复到完整状态：

```text
srt_0001_Audio_Final.wav exists
srt_0004_Audio_Final.wav exists
srt_0001_Image_New.png exists
srt_0001_Video_Raw.mp4 exists
srt_0001_Video_Final.mp4 exists and bound
srt_0001_TailFrame.jpg exists
```

7. 多 Shot / 多 Scene 的抽样槽位恢复到测试前完整状态。
8. 未发现删除级联：

```text
清 Audio 不清新图 / Raw / Final / TailFrame
清新图不清 Raw / Final / TailFrame
清 Raw 不清 Final
清 Final 不清 Raw
清 TailFrame 不清 Audio / 新图 / Raw / Final
```

9. 未发现 Raw / Final 互相误点亮。
10. TailFrame 只作为后续 Segment / Scene / Shot 的首帧依赖输入，不能被当作当前 Segment 的新图、Raw 或 Final 完成态。
11. Video Plan 的灰色“不执行”状态只出现在计划计算确实不需要该步骤的情况下，不能掩盖 Raw / Final / Audio 槽位真实缺失。
