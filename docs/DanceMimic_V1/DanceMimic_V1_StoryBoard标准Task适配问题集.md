# DanceMimic_V1 StoryBoard 标准 Task 适配问题集

版本：v0.3

> v0.6 implementation note：本文保留问题讨论记录。实施以 `DanceMimic_V1_实施收敛设计.md` v0.6 和 `DanceMimic_V1_03_StoryBoardStandardTaskBuild_工具实现需求.md` 为准；其中 03 MVP 生成 `srt_storyboard.json` + `storyboard_seed.json` + 参考资产副本，`koubo_storyboard_edit.json` 由后端 lazy normalize，后续视频执行不依赖 `provider_module = video_sdr2v_dancemimic`。

状态：已确认需求草案。本文记录新工具集运行完成后，如何产出一个能被现有 Task List 和现有 Koubo StoryBoard 直接打开、展示、继续编辑和继续执行的标准 Task。

## 1. 核心理解

这里的“复用 StoryBoard”不是重新实现一个 StoryBoard，也不是设计一套新的页面。

目标是：

1. 新工具集运行完成后，产物仍然表现为一个标准 Task。
2. 用户可以从现有 Task List 进入。
3. 用户可以用现有 StoryBoard 页面打开。
4. 现有 StoryBoard 能识别 Shot / Scene / Dialogue、素材槽位、Plan、执行状态、Working 文件和 history。
5. 后续如果继续执行 Video Plan / Image Plan / Video Only Plan，仍走现有 05_02 / 05_04 / 05_06 一致的执行与回写合同。

因此，工具实现时要做的不是“做一个 StoryBoard”，而是按现有 StoryBoard 合同生成和回写标准数据。

## 1.1 本轮确认结论

用户已确认：

```text
Q1-Q54 = A
```

含义：

1. 适配方式全部采用最高复用、最小开发量方案。
2. 运行结果必须成为现有 Task List / StoryBoard 可直接打开的标准 Task。
3. 现有 `05_02 / 05_04 / 05_06` 执行与回写合同默认复用。
4. 唯一差异点在 StoryBoard 初始生成方式：固定生成 1 个 Shot、1 个 Scene，Dialogue 按参数切分。

## 2. 工具运行后必须形成的标准 Task 结果

新工具集完成后，至少要让当前 workspace 具备：

```text
SessionContext/
  Variables.json

SessionOutput/
  storyboard/
    srt_storyboard.json
    koubo_storyboard_edit.json        # 如当前编辑态需要
    Working/
    assets/
      images/
      videos/
      audios/
      history/
```

并且 Task / Session 层必须能让现有 Task List 定位到这个 workspace 和 StoryBoard 类型。

## 3. StoryBoard 标准数据合同

### 3.1 结构索引

现有 StoryBoard 的结构索引仍是：

```text
SessionOutput/storyboard/srt_storyboard.json
```

工具必须写出或更新：

1. `shots[]`
2. `scenes[]`
3. `dialogue_items[]`
4. Dialogue 的时间、文本、素材引用、`dialogue_asset_key`
5. 当前素材槽位状态

不得让 StoryBoard 通过目录扫描猜结构。

### 3.1.1 DanceMimic_V1 初始 StoryBoard 生成差异

DanceMimic_V1 生成 StoryBoard 时，与通用口播 StoryBoard 的主要差异是结构固定：

```text
Shot: 1 个
Scene: 1 个
Dialogue: 按输入参考视频时长和切分参数生成 N 个
```

默认结构：

```text
shots[0]
  scenes[0]
    dialogue_items[0..N-1]
```

规则：

1. 工具只生成一个 Shot。
2. 工具只生成一个 Scene。
3. Dialogue 是本工具生成的最小可绑定视频片段。
4. 每个 Dialogue 必须写入 `start`、`end`、`duration`。
5. 每个 Dialogue 必须有稳定 `dialogue_asset_key`。
6. 每个 Dialogue 可使用占位文本，例如 `片段 01`、`片段 02`；如果未来有真实文本，再写入真实文本。

#### 切分参数

StoryBoard 生成阶段至少需要两个参数：

```text
target_video_seconds
minimum_video_seconds
```

参数含义：

1. `target_video_seconds`：每个 Dialogue / 视频片段的目标上限。生成出的每段时长默认不得超过该值。
2. `minimum_video_seconds`：每个 Dialogue / 视频片段的最小时长。生成出的尾段或任何短段不得低于该值。

#### 切分原则

给定输入参考视频总时长 `D`：

1. 优先用最少 Dialogue 覆盖完整时长。
2. 每个 Dialogue 的时长不得超过 `target_video_seconds`。
3. 每个 Dialogue 的时长不得低于 `minimum_video_seconds`。
4. 如果按 `target_video_seconds` 直接切分后，尾段时长大于等于 `minimum_video_seconds`，允许保留“满段 + 尾段”的切法。
5. 如果直接切分后的尾段低于 `minimum_video_seconds`，必须重新均分或近似均分，避免出现 1-2 秒这类极短 Dialogue。
6. 重新均分时仍使用最少 Dialogue 数，并尽量让每段大小接近。
7. 如果 `target_video_seconds < minimum_video_seconds`，参数非法，必须 blocked。
8. 如果输入总时长 `D < minimum_video_seconds`，无法满足最小时长约束，必须 blocked 或要求用户降低 `minimum_video_seconds`。

#### 示例

输入参考视频 30 秒：

```text
target_video_seconds = 8
minimum_video_seconds <= 6
=> Dialogue: 8, 8, 8, 6
```

输入参考视频 30 秒：

```text
target_video_seconds = 15
minimum_video_seconds <= 15
=> Dialogue: 15, 15
```

输入参考视频 34 秒：

```text
target_video_seconds = 8
直接切分会得到 8, 8, 8, 8, 2
如果 minimum_video_seconds > 2，则尾段 2 秒不允许
=> 改为最少 Dialogue 数下的近似均分，例如 7, 7, 7, 7, 6
```

输入参考视频 34 秒：

```text
target_video_seconds = 15
直接切分会得到 15, 15, 4
如果 minimum_video_seconds > 4，则尾段 4 秒不允许
=> 改为最少 Dialogue 数下的近似均分，例如 12, 11, 11
```

#### 推荐切分算法

推荐实现为：

1. 校验 `target_video_seconds >= minimum_video_seconds`。
2. 校验 `D >= minimum_video_seconds`。
3. 先计算 `n = ceil(D / target_video_seconds)`，这是满足“不超过目标上限”的最少 Dialogue 数。
4. 如果 `D % target_video_seconds == 0`，直接生成 `n` 段。
5. 如果尾段 `remainder >= minimum_video_seconds`，使用满段 + 尾段。
6. 如果尾段 `remainder < minimum_video_seconds`，使用 `n` 段近似均分。
7. 近似均分时，所有段必须满足：
   - `duration <= target_video_seconds`
   - `duration >= minimum_video_seconds`
   - 相邻段时长尽量接近
   - 总和等于输入视频总时长

近似均分可采用“先算平均值，再把余数分散到前几个 Dialogue”的方式，避免最后一个 Dialogue 过短。

### 3.2 稳定资源锚点

所有素材、计划、执行、回写必须统一使用：

```text
dialogue.dialogue_asset_key
segment.asset_key
segment.dialogue_ids[]
```

字段职责默认保持：

| 字段 | 职责 |
| --- | --- |
| `dialogue_asset_key` | 素材绑定和 Plan 执行唯一锚点 |
| `dialogue_id` | 页面编辑对象 ID |
| `srt_id` | 原始来源追踪 |
| `dialogue_index` | 当前排序 |

禁止用 Shot ID、Scene ID、数组下标代替 `dialogue_asset_key`。

### 3.3 标准 Working 文件

当前业务素材必须发布到：

```text
SessionOutput/storyboard/Working/
```

标准命名：

```text
{dialogue_asset_key}_Audio_Final.*
{dialogue_asset_key}_Image_Source.*
{dialogue_asset_key}_Image_New.*
{asset_key}_ImagePrompt.json
{asset_key}_VideoPrompt.json
{asset_key}_SegmentAudio_Final.*
{asset_key}_Video_Raw.*
{asset_key}_Video_Final.*
{asset_key}_TailFrame.*
```

工具不能只把文件放在自己的 `S{step}_*/Output/` 或 `Working/`。只写工具目录不等于 StoryBoard 完成。

### 3.4 绑定闭环

一个槽位完成，必须同时满足：

1. 标准业务文件真实存在。
2. StoryBoard JSON 中对应 Dialogue / Segment 已绑定该文件。
3. 状态派生能从 StoryBoard + Working 读出正确颜色。
4. 刷新页面后状态一致。

## 4. 05_02 / 05_04 / 05_06 的一致性原则

现有三套执行器虽然入口不同，但适配 StoryBoard 的核心合同应一致。

### 4.1 共同点

三者都必须：

1. 读取标准 StoryBoard 结构。
2. 使用 `dialogue_asset_key` / `asset_key` 作为资源锚点。
3. run 阶段读取本工具 `Working/` 快照。
4. 执行结果先进入本工具 `Output/` 或执行快照。
5. 最终业务结果发布到 `SessionOutput/storyboard/Working/`。
6. 回写 `srt_storyboard.json`，必要时同步 `koubo_storyboard_edit.json`。
7. 写 `Report/Result.json` 和执行状态。
8. 不把 API key、Authorization header、cookie、数据库连接串写入产物。

### 4.2 差异点

| 执行器 | 目标 | 核心输出 | 回写重点 |
| --- | --- | --- | --- |
| `05_02_VideoPlanExecutor` | 完整视频链路 | Audio / Image / Raw / Final / TailFrame | 生成最终业务视频，并同步 StoryBoard |
| `05_04_ImagePlanExecutor` | 图像可控子流程 | ImagePrompt / Image_New | 新图和 Prompt 回写 StoryBoard 图片槽位 |
| `05_06_VideoOnlyPlanExecutor` | 只生成 Raw Video，可 Confirm Final | VideoPrompt / Raw / Confirmed Final / TailFrame | Raw 不等于 Final；Confirm 后才绑定 Final |

### 4.3 Raw / Final 语义

默认保持现有合同：

1. Raw Video 表示视频模型输出或视频本体已完成。
2. Raw Video 不等于 Final Video。
3. Final Video 才是 StoryBoard 当前最终业务视频。
4. Video Only 的 Confirm Final 由 Raw 复制为 Final。
5. 下游 TailFrame 依赖只能由 Final 语义解锁。

### 4.4 DanceMimic Seedance SDR2V 专用视频生成路线

DanceMimic_V1 生成标准 StoryBoard 后，后续仍从现有 Video Plan / Video Only Plan 入口执行，但视频生成 provider 路线需要专用适配。

推荐新增：

```text
OpenCrew/ToolLibrary/Analysis_V1/video_plan_executor_modules/video_sdr2v_dancemimic.py
OpenCrew/ToolLibrary/Analysis_V1/Reference/05_02/Video_SDR2V_DanceMimic.md
```

执行输入规则：

1. 首帧图片来自当前 Dialogue 的标准图片槽位，例如 `{dialogue_asset_key}_Image_New.*`、`{dialogue_asset_key}_Image_Source.*` 或已物化首帧。
2. 参考视频来自 02 拆分并遮脸后的对应片段，例如 `{dialogue_asset_key}_Reference_FaceMasked.mp4`。
3. Seedance SDR2V 调用必须同时使用首帧和当前分段参考视频。
4. 参考视频只提供动作、姿态、节奏和镜头内运动参考，不提供人物身份。
5. 首帧才是人物、服装、背景、构图和画面身份锚点。
6. 缺少首帧或缺少对应参考视频时，当前 segment 必须 blocked。
7. 不得用全量参考视频、其它 Dialogue 参考视频或从参考视频临时抽帧替代首帧。

Plan 字段建议：

```json
{
  "video_generation_mode": "seedance_sdr2v_dancemimic",
  "provider_module": "video_sdr2v_dancemimic",
  "prompt_template": "Video_SDR2V_DanceMimic.md",
  "first_frame_path": "SessionOutput/storyboard/Working/dak_0001_Image_New.png",
  "reference_video_path": "SessionOutput/storyboard/assets/videos/dak_0001_Reference_FaceMasked.mp4",
  "reference_video_role": "dance_mimic_segment_motion_reference"
}
```

`05_02` 与 `05_06` 的差异仍只体现在 Raw / Final 发布语义上：

1. `05_02` 完整链路最终发布 Final 并抽取 TailFrame。
2. `05_06` 只发布 Raw，Confirm Final 后才绑定 Final。

## 5. 已确认的问题与答案

下面的问题是围绕“如何产出标准 Task，并能被现有 StoryBoard 打开”整理后的确认清单。本轮已确认 `Q1-Q54=A`。每题的 A 选项都是“复用程度最高、开发量最小”的已确认方案。

### A. Task List 与入口

| 编号 | 问题 | A 推荐：最高复用 / 最小开发量 | 其它可选 |
| --- | --- | --- | --- |
| Q1 | 新工具集运行完成后，是否创建 / 更新一个标准 Task，使它出现在现有 Task List 中？ | 是，创建 / 更新现有 Task List 可识别的标准 StoryBoard Task。 | B：只生成 workspace 文件，不进入 Task List；C：新增独立任务列表。 |
| Q2 | Task 类型如何标记？ | 直接标记为现有 Koubo StoryBoard 可打开类型。 | B：新增工具集类型，但路由到现有 StoryBoard；C：新增页面类型。 |
| Q3 | Task List 标题、封面、状态、更新时间由谁决定？ | 沿用现有 Task 字段；封面从 StoryBoard 首个可用 Source/Image/Final 派生，状态由标准 StoryBoard 产物和执行状态派生。 | B：工具集自定义一套展示字段。 |
| Q4 | 从 Task List 打开时进入哪里？ | 直接进入现有 StoryBoard 页面。 | B：先进工具集结果页，再跳 StoryBoard。 |

### B. StoryBoard 文件生成

| 编号 | 问题 | A 推荐：最高复用 / 最小开发量 | 其它可选 |
| --- | --- | --- | --- |
| Q5 | 是否必须生成 `SessionOutput/storyboard/srt_storyboard.json`？ | 必须生成，这是 StoryBoard 打开的结构真源。 | B：只生成中间结构，再由适配器转换。 |
| Q6 | 是否必须同步生成或更新 `koubo_storyboard_edit.json`？ | 如果当前 StoryBoard 页面优先读取 edit 文件，则同步生成 / 更新；否则保持与现有策略一致。 | B：只写 `srt_storyboard.json`，打开时再生成 edit。 |
| Q7 | 如果已有 StoryBoard，工具如何写入？ | 默认生成一个新的标准结果版本 / 新 Task，避免覆盖已有人工编辑。 | B：覆盖当前 StoryBoard；C：追加 Shot/Scene 到当前 StoryBoard。 |
| Q8 | 失败但生成部分 StoryBoard 时是否允许打开？ | 允许打开，Task 状态标记 `partial` 或 `completed_with_warnings`，缺失项在 StoryBoard 状态中显示。 | B：失败即 blocked，不允许打开。 |

### C. Shot / Scene / Dialogue 结构

| 编号 | 问题 | A 推荐：最高复用 / 最小开发量 | 其它可选 |
| --- | --- | --- | --- |
| Q9 | Shot 在标准 Task 中代表什么层级？ | 沿用现有 StoryBoard：Shot 是上层编排容器。 | B：把 Shot 映射为工具集业务阶段。 |
| Q10 | Shot 如何生成和调整？ | 工具自动生成初稿，用户后续可在现有 StoryBoard 中手动调整。 | B：只允许工具生成；C：只允许用户手动。 |
| Q11 | Scene 在标准 Task 中代表什么层级？ | 沿用现有 StoryBoard：Scene 是 Shot 内的可规划片段容器。 | B：Scene 映射为工具集自定义业务段。 |
| Q12 | Scene 是否仍是 Segment 规划边界？ | 是，默认不允许 Segment 跨 Scene。 | B：允许 Segment 跨 Scene，但需要改规划和拼接规则。 |
| Q13 | Dialogue 的最小单位是什么？ | 使用工具生成的最小可绑定行，映射到现有 Dialogue。 | B：强制必须是字幕句；C：强制必须是视频/动作片段。 |
| Q14 | Dialogue 是否必须有 `start / end / duration`？ | 是，尽量写入；没有真实时间时也写入可排序的估算时间。 | B：允许无时间，但需要页面兼容。 |
| Q15 | Dialogue 是否必须有文本？ | 不必须；无文本时写占位展示文案，如“无对白片段”。 | B：强制每个 Dialogue 都有文本。 |
| Q16 | 工具生成结构是否允许用户继续编辑？ | 允许，沿用现有 StoryBoard 的拆分、合并、重排能力。 | B：只读，不允许用户改结构。 |

### D. Key 与素材绑定

| 编号 | 问题 | A 推荐：最高复用 / 最小开发量 | 其它可选 |
| --- | --- | --- | --- |
| Q17 | 是否完全沿用 `dialogue_asset_key`？ | 是，作为素材、Plan、执行、回写唯一锚点。 | B：新增工具集 key，再映射到 `dialogue_asset_key`。 |
| Q18 | 工具生成新 Dialogue 时 key 如何命名？ | 生成稳定 `dialogue_asset_key`，优先采用 `dak_0001` 或兼容现有规范的稳定 key，不从 Shot/Scene 派生。 | B：用 `srt_id` 派生；C：用 Scene/Dialogue 位置派生。 |
| Q19 | Split Dialogue 时新 key 如何生成？ | 保留原 Dialogue key，新增部分生成新的稳定 key。 | B：全部重算 key。 |
| Q20 | Merge Dialogue 时保留哪个 key？ | 保留主 Dialogue 的 key，消失 Dialogue 的 generated 素材进 history。 | B：生成全新 key。 |
| Q21 | 是否禁止运行时 fallback 到其它 key？ | 是，禁止从 `srt_id`、`dialogue_id`、Scene ID、Shot ID fallback 到素材 key。 | B：迁移期允许 fallback，但必须有审计和清理计划。 |

### E. Working / Assets / History

| 编号 | 问题 | A 推荐：最高复用 / 最小开发量 | 其它可选 |
| --- | --- | --- | --- |
| Q22 | 最终业务素材是否必须发布到 `SessionOutput/storyboard/Working/`？ | 必须。只写工具目录不算 StoryBoard 完成。 | B：由打开页面时同步。 |
| Q23 | 上传或外部输入素材是否进入 assets 池？ | 是，按类型进入 `assets/images|videos|audios/`，并通过 StoryBoard 绑定引用。 | B：保留在工具目录，StoryBoard 引用工具目录。 |
| Q24 | generated 素材删除 / 替换 / Dialogue 消失时是否进 history？ | 是，进入 `assets/history/`。 | B：直接删除；C：只记录不搬文件。 |
| Q25 | 上传素材和原始参考素材是否进入 history？ | 不进入，只解除绑定。 | B：所有素材都进 history。 |
| Q26 | 覆盖同名 Working 文件前是否备份旧 generated 文件？ | 是，先进入 history，再覆盖。 | B：直接覆盖。 |

### F. Plan 生成

| 编号 | 问题 | A 推荐：最高复用 / 最小开发量 | 其它可选 |
| --- | --- | --- | --- |
| Q27 | 是否必须生成 `video_generation_plan.json`？ | 需要，只要后续要接现有完整视频链路。 | B：暂不生成，仅展示 StoryBoard。 |
| Q28 | 是否必须生成 `image_generation_plan.json`？ | 需要，如果要复用现有 `05_04` 图像子流程。 | B：暂不生成，图像由 Video Plan 间接处理。 |
| Q29 | 是否必须生成 `video_only_generation_plan.json`？ | 需要，如果要复用现有 `05_06` 先出 Raw / Confirm Final 流程。 | B：暂不生成，只走完整 Video Plan。 |
| Q30 | 是否仍由 Video Plan 负责统一 Segment Truth？ | 是，沿用现有 Video Plan 作为 Segment Truth 来源。 | B：工具集自定义 Segment Truth，再转换。 |
| Q31 | Image Plan / Video Only Plan 是否只能复用 Segment Truth？ | 是，不重新拆 Segment。 | B：允许各自重新拆，需新增一致性规则。 |
| Q32 | 工具集业务计划和 StoryBoard Plan 不一致时以谁为准？ | 以 StoryBoard 标准 Plan / Segment Truth 为准。 | B：以工具集业务计划为准，再同步 StoryBoard。 |

### G. `05_02 / 05_04 / 05_06` 适配

| 编号 | 问题 | A 推荐：最高复用 / 最小开发量 | 其它可选 |
| --- | --- | --- | --- |
| Q33 | 新工具集产出的 plan 是否要能直接交给现有 `05_02` 执行？ | 是，尽量直接复用现有 `05_02`。 | B：新增同合同执行器。 |
| Q34 | 新工具集产出的 image plan 是否要能直接交给现有 `05_04` 执行？ | 是，尽量直接复用现有 `05_04`。 | B：新增图像执行器。 |
| Q35 | 新工具集产出的 video only plan 是否要能直接交给现有 `05_06` 执行？ | 是，尽量直接复用现有 `05_06`。 | B：新增 Video Only 执行器。 |
| Q36 | 需要定制执行逻辑时怎么做？ | 优先复用现有 `05_02 / 05_06` 执行器合同；DanceMimic 的 Seedance SDR2V 差异通过专用 provider 模块、专用 Prompt 模板和 plan 输入字段适配。 | B：新增同合同的新执行器。 |
| Q37 | 三套执行器回写规则是否必须一致？ | 是，统一为：文件落盘 + JSON 绑定 + 状态派生。 | B：每套执行器自定义回写规则。 |

### H. 槽位与状态

| 编号 | 问题 | A 推荐：最高复用 / 最小开发量 | 其它可选 |
| --- | --- | --- | --- |
| Q38 | 是否沿用固定槽位 `[Audio, Source, Image, Raw, Final]`？ | 是，直接复用现有槽位矩阵。 | B：新增自定义槽位。 |
| Q39 | Prompt 是否作为特殊槽位？ | 是，Prompt 文件存在即绿色。 | B：Prompt 只作为审计文件，不显示状态。 |
| Q40 | 是否沿用颜色优先级？ | 是，绿 > 黄 > 红 > 白 > 灰。 | B：新增工具集颜色规则。 |
| Q41 | 绿色完成态来源是什么？ | 标准业务文件真实存在，或本轮成功落盘并绑定。 | B：execution state 成功即可绿色。 |
| Q42 | 旧 execution state 是否不得覆盖真实文件绿色？ | 是，真实文件绿色优先。 | B：execution state 优先。 |
| Q43 | Final 文件存在但未绑定时是什么状态？ | 绿色修复态 / warning，提示修复绑定，不强迫重跑。 | B：failed；C：blocked。 |
| Q44 | `blocked` 与 `skipped` 是否必须区分？ | 必须区分。 | B：合并成一种不可执行状态。 |

### I. 回写失败与修复

| 编号 | 问题 | A 推荐：最高复用 / 最小开发量 | 其它可选 |
| --- | --- | --- | --- |
| Q45 | 文件生成成功但 StoryBoard JSON 回写失败时，工具状态是什么？ | `completed_with_sync_error` 或 warning，不冒充完全成功。 | B：failed；C：completed。 |
| Q46 | 是否需要自动修复绑定入口？ | 需要，至少提供后端修复能力或后续工具修复入口。 | B：人工重新运行。 |
| Q47 | 是否允许页面显示“文件存在但绑定待修复”？ | 允许，作为绿色修复态。 | B：不显示，要求重跑。 |
| Q48 | 修复绑定后是否需要重新生成 plan？ | 默认不需要，除非 Segment Truth 或素材依赖已改变。 | B：总是重新生成 plan。 |

### J. 验收测试

| 编号 | 问题 | A 推荐：最高复用 / 最小开发量 | 其它可选 |
| --- | --- | --- | --- |
| Q49 | 是否必须覆盖单 Dialogue 槽位矩阵？ | 必须，复用现有槽位矩阵测试。 | B：只做人工测试。 |
| Q50 | 是否必须覆盖多 Dialogue / 单 Scene Segment 拆分？ | 必须，防止 Segment Truth 漂移。 | B：后续补。 |
| Q51 | 是否必须覆盖跨 Scene / 跨 Shot TailFrame？ | 必须，复用现有 TailFrame 规则。 | B：只覆盖单 Scene。 |
| Q52 | 是否必须覆盖新增 / 删除 / 合并 / 拆分 Dialogue？ | 必须，保护 `dialogue_asset_key` 和 history。 | B：只覆盖生成结果。 |
| Q53 | 是否必须覆盖三类 Plan 对齐？ | 必须，确保 Video Plan / Image Plan / Video Only Plan 共享 Segment Truth。 | B：只覆盖用到的 plan。 |
| Q54 | 是否必须覆盖刷新页面、退出重进 Task 后状态一致？ | 必须，证明 Task List + StoryBoard 真实可复用。 | B：只测即时页面状态。 |

## 6. 已确认默认方案

按 `Q1-Q54=A`，DanceMimic_V1 StoryBoard 标准 Task 适配按以下方向实现：

1. 运行结果生成标准 Task，并从现有 Task List 进入现有 StoryBoard 页面。
2. 必须生成 `SessionOutput/storyboard/srt_storyboard.json`。
3. 必须使用 `dialogue_asset_key` 作为唯一素材和执行锚点。
4. 必须发布业务结果到 `SessionOutput/storyboard/Working/`。
5. 必须回写 StoryBoard JSON，不能只写工具目录。
6. Video Plan 仍是统一 Segment Truth。
7. Image Plan / Video Only Plan 不重新拆 Segment。
8. `05_02 / 05_04 / 05_06` 的执行与回写合同保持一致。
9. Raw 和 Final 分离，Raw 不等于 Final。
10. 以 Working 标准文件 + StoryBoard JSON 绑定 + 状态派生一致作为完成标准。
11. DanceMimic 视频生成默认通过 `video_sdr2v_dancemimic.py` + `Video_SDR2V_DanceMimic.md` 调用 Seedance 首帧 + 分段参考视频能力。

## 7. 后续正式需求结构

后续正式适配需求应补齐：

1. Task List 入口和 Task 类型。
2. StoryBoard 文件生成规则。
3. Shot / Scene / Dialogue 结构生成规则。
4. `dialogue_asset_key` 生成和继承规则。
5. Working / assets / history 落盘规则。
6. 三类 Plan 是否生成及其字段合同。
7. 05_02 / 05_04 / 05_06 执行器复用或扩展方式。
8. DanceMimic Seedance SDR2V 专用模块与提示词模板。
9. 回写 StoryBoard JSON 的成功 / 失败 / 修复规则。
10. 状态颜色和槽位派生规则。
11. 回归测试矩阵。

## 8. 参考基线

本文依据：

1. `OpenCrew/docs/SessionDesign-R2/STORYBOARD_OUTPUT_STRUCTURE.md`
2. `OpenCrew/docs/SessionDesign-R2/Koubo_Storyboard_DialogueKey_统一资源绑定需求与测试案例.md`
3. `OpenCrew/docs/SessionDesign-R2/Koubo_槽位矩阵与SegmentTruth回归测试金标准.md`
4. `OpenCrew/docs/SessionDesign-R2/Koubo_VideoPlan_槽位颜色测试案例表.md`
5. `OpenCrew/docs/SessionDesign-R2/Analysis_V1_05_01_VideoGenerationPlan_工具需求整理.md`
6. `OpenCrew/docs/SessionDesign-R2/Analysis_V1_05_03_05_04_ImagePlan_工具需求整理.md`
7. `OpenCrew/docs/SessionDesign-R2/Analysis_V1_05_05_05_06_VideoOnlyPlan_工具需求整理.md`
8. `OpenCrew/docs/DanceMimic_V1/DanceMimic_V1_后续执行_Seedance_SDR2V_DanceMimic_适配需求.md`
