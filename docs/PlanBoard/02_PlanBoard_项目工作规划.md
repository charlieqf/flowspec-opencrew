# Plan Board 项目工作规划

版本：v0.1

状态：实施规划稿。

## 1. 项目目标

Plan Board 是 OpenCrew Session 内的工具执行计划板。它把工具链拆成可视化的 Plan Row 和 Job，并用 Control-M 风格的 `Wait for Events / Actions` 明确每个 Job 是否可运行、运行后产出什么、后续 Job 依赖什么。

第一版目标：

1. 支持 StoryBoard 这种“一行一条串行任务链”的 Plan。
2. 每个 Plan Row 对应一个 Segment、Dialogue、文件或其它可重复执行业务单元。
3. 每个 Job 对应一个最小可判断状态的业务产物或明确动作。
4. Job Status 由 Session Workspace 中 `SessionOutput/<domain>/Working/` 一级文件派生。
5. 状态标准对齐 Control-M：`Ended OK / Ended Not OK / Executing / Waiting / Wait User / Wait for Events / Wait Resources`。
6. 产物命名归一化，避免 StoryBoard 中旧 Job 名、新 Job 名、工具目录产物混用。
7. 保证一个 Job 不承担多个业务内容。

## 2. 必须先回答的设计问题

做任何 Plan Board 前，必须先完成以下分析，而不是直接开始画表格或写 UI。

### 2.1 Plan Board 按什么定义 Plan Row

必须明确一个 Plan Row 代表什么业务单元：

| 可能分行方式 | 适用场景 | 风险 |
| --- | --- | --- |
| 一个 Dialogue 一行 | 对白级音频、图片、视频独立生成 | 多 Dialogue 合成 Segment 时会拆太细 |
| 一个 Segment 一行 | StoryBoard 视频生成、尾帧继承 | 一个 Segment 内可能覆盖多个 Dialogue |
| 一个 Shot 一行 | 粗粒度镜头计划 | Job 可能吞掉太多任务 |
| 一个 Scene 一行 | 场景级交付 | 不利于恢复单段失败 |
| 一个文件一行 | 数据处理 / 文档处理 | UI 可能过碎 |

StoryBoard 第一版建议：

```text
一个 Plan Row = 一个 Segment
row_anchor = Segment 的稳定 anchor
```

如果 Segment 覆盖多个 Dialogue，Plan Row 的 `row_anchor` 使用代表 Dialogue 的稳定 key，并在 Plan Row 元数据中记录完整 `dialogue_asset_keys`。

必须产出：

```text
PlanBoard_Plan_Row_Definition.md
```

内容包括：

1. Plan Row 的业务含义。
2. Plan Row 的稳定 ID 规则。
3. Plan Row 重建时如何继承旧 anchor。
4. 拆分、合并、重排时如何处理旧产物。

### 2.2 每个 Plan Row 有多少个 Job

Job 必须按最小可判断状态拆分。一个 Job 如果有多个独立输入、多个独立输出、多个失败原因，通常就不是一个好 Job。

硬性规则：

```text
一个 Job 只能承担一个最小业务产物或一个明确动作。
```

典型反例：

```text
Job = Video
同时代表 New Video / Video_Raw 和 Final Video / Video_Final
```

正确拆法：

```text
Job = Video_Raw
Job = Video_Final_Copy
Job = Video_Final_LipSync
Job = Video_Final_AudioMix
Job = TailFrame
```

StoryBoard 推荐 Job：

```text
AudioPrompt
Audio_Final
SegmentAudio_Final
Image_Source
ImagePrompt
Image_New
VideoPrompt
Video_Raw
Video_Raw_TailFrame
Video_Final_Copy
Video_Final_LipSync
Video_Final_AudioMix
Video_Final
TailFrame
```

UI 可以把多个 Job 合并成一个按钮，但状态模型不能合并。例如“提示词+新视频”按钮可以一次执行 `VideoPrompt` 和 `Video_Raw`，但 Plan Board 内部仍必须保留两个 Job 的状态。

必须产出：

```text
PlanBoard_Job_Catalog.md
```

内容包括：

1. Job 名称。
2. UI 文案。
3. 是否是业务产物。
4. 是否需要绑定业务 JSON。
5. 是否可人工确认。
6. 是否允许自动运行。
7. 是否会写入 TailFrame 或下游凭证。

### 2.3 每个 Job 的 Wait for Events 是什么

每个 Job 必须写出 Wait for Events，不允许只说“前一步完成”。

示例：

| Job | Wait for Events |
| --- | --- |
| `ImagePrompt` | `{anchor}_Image_Source.*` 存在 |
| `Image_New` | `{anchor}_Image_Source.*` 存在，`{anchor}_ImagePrompt.json` 存在 |
| `VideoPrompt` | `{anchor}_Image_New.*` 存在 |
| `Video_Raw` | `{anchor}_Image_New.*` 存在，`{anchor}_VideoPrompt.json` 存在 |
| `Video_Final_Copy` | `{anchor}_Video_Raw.*` 存在，人工确认 |
| `Video_Final_LipSync` | `{anchor}_Video_Raw.*` 存在，音频存在 |
| `TailFrame` | `{anchor}_Video_Final.*` 存在 |

必须产出：

```text
PlanBoard_Wait_For_Events_Matrix.md
```

内容包括：

1. 每个 Job 的 Wait for Events。
2. 条件是 AND 还是 OR。
3. 条件来源文件。
4. 条件缺失时显示灰色还是白色。
5. 下游已完成时本 Job 是否进入 Waiting / 无需运行状态。

### 2.4 每个 Job 的 Actions 是什么

每个 Job 必须声明成功后执行什么 Actions，以及发布什么标准文件。

示例：

| Job | Actions / 输出 |
| --- | --- |
| `ImagePrompt` | `{anchor}_ImagePrompt.json` |
| `Image_New` | `{anchor}_Image_New.*` |
| `VideoPrompt` | `{anchor}_VideoPrompt.json` |
| `Video_Raw` | `{anchor}_Video_Raw.*` |
| `Video_Final_Copy` | `{anchor}_Video_Final.*`，`{anchor}_TailFrame.*` |
| `Video_Final_LipSync` | `{anchor}_Video_Final.*`，`{anchor}_TailFrame.*` |

必须产出：

```text
PlanBoard_Actions_Contract.md
```

内容包括：

1. 业务文件名。
2. Actions 类型。
3. 扩展名策略。
4. 是否要求原子发布。
5. 是否要求同步业务 JSON。
6. 是否会覆盖已有文件。
7. 覆盖前是否进入 Asset History。

### 2.5 依赖关系是什么

依赖关系不能只靠 UI 顺序表达。必须形成机器可读 DAG。

第一版可以限制为：

```text
同一 Plan Row 内部串行
不同 Plan Row 之间无交叉依赖
```

但 JSON 中仍要保留：

```json
{
  "depends_on": ["slot_srt_0001_01_video_prompt"],
  "wait_for_events": [],
  "actions": []
}
```

必须产出：

```text
PlanBoard_Dependency_DAG.md
```

内容包括：

1. Plan Row 内顺序。
2. 可跳过条件。
3. 下游产物已存在时是否回退上游。
4. 是否允许从中间态继续执行。
5. 是否允许人工只执行某个 Job。

## 3. 实施阶段

### 阶段 0：现状盘点

目标：找出现有工具、文件、状态和 UI 的真实边界。

必须完成：

1. 列出现有工具目录。
2. 列出现有 `SessionContext` 文件。
3. 列出现有 `SessionOutput` 文件。
4. 列出现有 Working 文件命名。
5. 列出现有 Plan / State / Result / Report JSON。
6. 列出哪些文件是当前真相，哪些只是历史或缓存。

产出物：

```text
00_Current_State_Audit.md
00_Working_File_Inventory.csv
00_State_Source_Inventory.md
```

验收标准：

1. 能说明当前 UI 每个颜色从哪里来。
2. 能说明每个工具成功后写了哪些文件。
3. 能指出工具目录 Output 与业务 Working 的差异。

### 阶段 1：Plan Row 模型设计

目标：确定 Plan Board 的 Plan Row。

产出物：

```text
01_Plan_Row_Model.md
01_Plan_Row_Anchor_Rules.md
01_Plan_Row_Rebuild_Cases.md
```

验收标准：

1. 重建 Plan 不改变已有产物归属。
2. Segment 拆分、合并、重排有明确继承规则。
3. 不使用数组下标、前端行号、生成时间作为稳定 anchor。

### 阶段 2：Job 模型设计

目标：确定每个 Plan Row 有哪些 Job，以及 UI 按钮和底层 Job 的关系。

产出物：

```text
02_Job_Catalog.md
02_UI_To_Job_Mapping.md
02_Subtask_Rules.md
```

验收标准：

1. 一个 Job 不再吞掉多个不可区分任务。
2. UI 可以合并按钮，但内部 Job 必须可单独拥有 Job Status。
3. Raw / Final / TailFrame 等容易混淆的产物必须拆开。

### 阶段 3：条件与产物合同

目标：确定所有 `Wait for Events / Actions`。

产出物：

```text
03_Wait_For_Events_Matrix.md
03_Actions_Contract.md
03_File_Naming_Contract.md
```

验收标准：

1. 每个 Job 都有标准 Wait for Events。
2. 每个 Job 都有标准 Actions 和输出文件。
3. 输出文件和 Job Status 一一对应。
4. 文件名不依赖工具目录、不依赖 UI 文案。

### 阶段 4：状态派生服务

目标：实现 Job Status 的统一计算，并映射到白、绿、红、黄、灰 UI 颜色。

产出物：

```text
04_Job_Status_Derivation_Spec.md
04_Job_Status_Service_API.md
04_Job_Status_Test_Cases.md
```

验收标准：

1. `Ended OK` 只由标准业务文件和必要绑定决定。
2. `Executing` 只由当前签名 Running 标记决定。
3. `Ended Not OK` 只由当前签名 Failed 标记决定。
4. `Waiting` 表示条件满足但尚未执行，或条件不满足仍在等待。
5. Waiting 必须区分 `blocked_waiting_input`、`skipped_consumed_by_downstream`、`wait_user`、`wait_resources`。

### 阶段 5：执行生命周期

目标：让工具执行前、中、后都有一致的状态落点。

产出物：

```text
05_Execution_Lifecycle.md
05_Running_Failed_Marker_Spec.md
05_Archive_Rules.md
```

验收标准：

1. 点击执行才创建 Running 标记。
2. 外部 API 任务 ID 返回后立即写入 Running 标记。
3. 成功后发布业务文件，再归档 Running。
4. 失败后保留 Failed 热路径标记。
5. 重新运行前归档旧 Running / Failed。

### 阶段 6：UI 与交互

目标：Plan Board 前端只消费后端派生结果。

产出物：

```text
06_UI_Interaction_Spec.md
06_Color_Legend.md
06_Manual_Action_List.md
```

验收标准：

1. UI 不自己合并三套状态。
2. 固定 Job 始终渲染，不因为没有 plan item 就消失。
3. 每个颜色都有可解释 reason。
4. 一键执行可以触发多个 stage，但返回时拆开显示每个 stage 的状态。

### 阶段 7：回归测试与迁移

目标：把 StoryBoard 的历史坑变成测试。

产出物：

```text
07_Regression_Test_Plan.md
07_Migration_Checklist.md
07_Known_Pitfalls.md
```

验收标准：

1. 旧 `Image_01` 不再被误判为新图。
2. Raw 存在不等于 Final 完成。
3. SegmentAudio 不把 Dialogue Audio 染绿。
4. TailFrame 继承优先级正确。
5. Working 文件优先级高于旧 execution JSON。
6. 执行成功但绑定失败进入修复态，不要求重跑。

## 4. StoryBoard 踩坑提醒

### 4.1 一个 Job 代表多个任务

问题：`视频` Job 曾经同时代表 Raw Video 和 Final Video，导致 Job Status、删除、Confirm、Sync 全部混在一起。

规则：一个 Job 如果有两个完成标准，就必须拆成两个 Job。UI 可以合并显示，但 Job Status 不能合并。

### 4.2 工具目录产物被当成业务产物

问题：`Sxx_Tool/Working` 是执行快照，`SessionOutput/storyboard/Working` 才是业务真相。

规则：Plan Board 只认 Session Workspace 中的业务 Working。工具目录产物必须发布到业务 Working 后才算完成。

### 4.3 Execution JSON 压过真实文件

问题：旧 execution JSON 显示 failed / running，但标准业务文件已经存在，UI 仍可能显示红或黄。

规则：绿色优先级最高。业务文件完成后，旧黄红必须归档或被忽略。

### 4.4 SegmentAudio 与 Dialogue Audio 混淆

问题：Segment 合成音频存在，不代表每条 Dialogue 的 Audio 槽完成。

规则：Job 必须声明自己的粒度。Dialogue 级和 Segment 级产物不能互相染绿。

### 4.5 尾帧继承被下游完成态覆盖

问题：有些 Segment 需要上一段 TailFrame 物化成新图，但通用逻辑看到 Final 存在后直接把前置步骤置灰。

规则：`previous_tail_frame` 是明确输入条件。只要当前 stage 需要物化尾帧，就不能被泛化的“下游已完成”逻辑吞掉。

### 4.6 双 JSON 同步失败

问题：只更新 `srt_storyboard.json` 或只更新 `koubo_storyboard_edit.json` 会造成 UI 与执行状态漂移。

规则：需要双写的业务绑定必须作为一个事务。任一失败，不得显示绿色成功。

### 4.7 CLI 与页面执行边界混淆

问题：页面点击 Plan 重建时走 CLI 子进程，会带来路径、环境、JSON 解析和性能问题。

规则：页面触发优先走同一逻辑的函数入口；CLI 保留给自动化、runbook 和人工终端执行。

### 4.8 Prompt 与普通素材删除规则不同

问题：Prompt 文件既是产物又可编辑，不能像图片、视频一样被普通 Job 产物清除动作误删。

规则：Prompt 文件存在即绿色。修改 Prompt 必须走专门编辑流程，并改变后续 stage 的 step signature。

## 5. 最小验收清单

1. 打开 Plan Board，所有固定 Job 都能显示 Job Status。
2. 删除上游文件不会级联删除下游文件。
3. 下游文件存在时，上游缺失显示灰色无需运行。
4. Raw 存在但 Final 不存在时，Final 显示白色可执行。
5. 运行中刷新页面仍显示黄色。
6. 失败后刷新页面仍显示红色。
7. 成功文件落盘后覆盖旧红黄，显示绿色。
8. 重新运行会归档旧状态标记。
9. 文件存在但绑定缺失时显示绿色加修复提示。
10. 绑定存在但文件缺失时不显示绿色。
