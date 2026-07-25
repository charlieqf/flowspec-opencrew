# Plan Board 项目工作规划

版本：v0.2

状态：实施规划稿。

后端实现根目录（本文件所有代码引用均相对此目录）：

```text
backend/opcrew_backend/koubo/koubo_storyboard/
```

---

## 1. 项目目标

Plan Board 是 OpenCrew Session 内的工具执行计划板。它把工具链拆成可视化的 Plan Row 和 Job，并用 Control-M 风格的 `Wait for Events / Actions` 明确每个 Job 是否可运行、运行后产出什么、后续 Job 依赖什么。

第一版目标：

1. 支持 StoryBoard 这种“一行一条串行任务链”的 Plan。
2. 每个 Plan Row 对应一个 Segment、Dialogue、文件或其它可重复执行业务单元。
3. 每个 Job 对应一个最小可判断状态的业务产物或明确动作。
4. Job Status 由 Session Workspace 中 `SessionOutput/storyboard/Working/` 一级文件派生（`constants.py:13` `WORKING_REL`）。
5. 状态标准对齐 Control-M：`Ended OK / Ended Not OK / Executing / Waiting / Wait User / Wait for Events / Wait Resources`。
6. 产物命名归一化，避免 StoryBoard 中旧 Job 名、新 Job 名、工具目录产物混用。
7. 保证一个 Job 不承担多个业务内容。

### 1.1 本项目是增量迁移，不是绿地重建

Plan Board 不是从零搭一套新系统。现网 StoryBoard 已经具备本设计反复强调的几条核心能力，第一版必须**复用而非重写**。

已具备、不得重写的能力：

| 能力 | 现状与证据 |
| --- | --- |
| 五色状态后端集中派生 | `slot_state_services.py:7-11`（GREEN/WHITE/GRAY/YELLOW/RED），三个纯派生函数 `derive_video_plan_slot_states`(`:146`)、`derive_image_plan_slot_states`(`:197`)、`derive_video_only_plan_slot_states`(`:222`) |
| 派生层是纯函数、可单测 | 入参为布尔向量 `SlotInputs`(`:24-36`)，`slot_inputs_from_vector([audio, source, image, raw, final], ...)`(`:42-53`)。是否扫盘由上游决定，派生本身无副作用 |
| 颜色 → UI tone 映射，且同时输出 `tone` 与 `ui_tone` | `:76-86`（GREEN→done、WHITE→pending、GRAY→disabled、YELLOW→running、RED→failed）。这解释了前端为何读 `ui_tone || tone` |
| 前端只消费、不自算颜色 | `frontend/src/modules/koubo/KouboStoryBoard/components/KouboVideoOnlyPlanModal.jsx` 读取后端 tone，套 `is-done / is-running / ...` CSS 类 |
| “绿色优先、文件存在不被 running/failed 覆盖” | `_apply_execution`(`:101-109`) 第 `102` 行 `if slot.get("file_exists"): return slot` 已短路，execution 状态盖不上已完成文件 |
| 输入指纹/签名机制 | `video_plan_signature_services.py:262-301` `video_plan_signature()` 产出六维签名：`scope / parameter / storyboard_structure / media_binding / consistency_reference / input`；`video_plan_cache_matches()`(`:303-322`) 已实现“旧签名是否仍有效”的逐项比对 |
| 稳定 anchor / asset_key 派生 | `video_plan_artifact_services.py:128-141` `video_plan_segment_asset_key()`（优先 `segment_id`，回退 `dialogue_ids`），`dialogue_match_keys()`(`:71-79`) 聚合 `asset_key/dialogue_asset_key/srt_id/dialogue_id/srt_ids` |
| 旧 `_Image_01` 遗留拦截 | `video_plan_signature_services.py:308` `if "_Image_01" in ...: return False, "legacy_image_01_plan"`（活代码，已防“旧图当新图”） |

第一版真正需要新增的增量（现网确无）：

| 需新增 | 说明 |
| --- | --- |
| per-stage 物理 Running / Failed 标记文件 | 现网执行态在 per-plan 的 `*_execution_state.json` / `*_execution_result.json`（`constants.py:24/31/38` 等），不是按 row+stage 的标记文件 |
| `ExecutionArchive` 旧标记归档区 | 现网无 |
| 把已有六维签名“下沉/绑定”到 per-stage 标记 | 现网签名在 VideoPlan target 级、存于 `video_generation_plan.ui_cache.json`(`constants.py:18`)，未与单个 stage 标记关联 |
| `PlanBoard.json` 结构层（结构+依赖+显示，不制造完成态） | 现网无统一结构文件，状态散在多份 plan/state/result/ui_cache JSON 中 |

因此工作主线是：**复用纯派生层与六维签名、复用 `segment_asset_key` 作 anchor，新增物理标记 + 归档区，并把签名挂到标记上**。任何阶段都不得重写 `slot_state_services.py` 已正确完成的派生，只能替换其上游“文件/绑定是否存在”的来源。

---

## 2. 必须先回答的设计问题（动工前硬决策）

做任何 Plan Board 前必须先完成以下分析，而不是直接画表格或写 UI。本节产出并入阶段 1–3 对应文档，不另立重复文件。

### 2.0 硬决策：Plan Row 内是否允许混合粒度 Job（最高优先级）

这是整个数据模型的地基，必须在阶段 1 之前定死，否则 Plan Row、Job、Wait for Events 三层都要返工。

真实数据层级（来自代码，非假设）：

```text
Shot → Scene → Dialogue   video_plan_signature_services.py:207-251（shots[].scenes[].dialogues[]，每个 dialogue 带 working_assets{audio,images,video}）
Shot → Scene → Segment    video_plan_artifact_services.py:156-181（scenes[].segments[]）
```

关键业务事实：**一个 Segment 聚合多个 Dialogue，且音频是两级并存的**——同一 segment 上同时挂着：

- `dialogue_audio_tasks`（**Dialogue 级**，逐条对白音频）—`video_plan_artifact_services.py:176`
- `segment_audio_path`（**Segment 级**合成音）—`:181`，对应命名后缀 `_SegmentAudio_Final`(`:117`)

图像槽是 Dialogue 级：`working_asset_services.py:139-141` `dialogue_image_slot(prefix="Dialogue")` 按 `dialogue_index` 编号。视频与尾帧在 Segment 级：`segment.first_frame / tail_frame`(`video_plan_artifact_services.py:170-171`)，尾帧供下游段继承。

结论：一个 Segment 行内天然混着 Dialogue 级（audio、image）与 Segment 级（segment_audio、video、tail_frame）产物。必须三选一并写入决策文档：

| 方案 | 含义 | 与真实字段的对应 |
| --- | --- | --- |
| A. 单粒度行 + 子行（推荐） | Segment 父行内嵌 Dialogue 子行 | 子行承载 `dialogue_audio_tasks` + `dialogue_image_slot`；父行承载 `segment_audio_path` + video + `tail_frame` |
| B. 混合粒度行（显式标级） | 一行内混合，每个 Job 声明 `grain = dialogue / segment` | 派生与 UI 必须严格按 grain 取文件，否则回到 §4.4 老坑 |
| C. 双 Plan 视图 | Audio 走 Dialogue 行、Video 走 Segment 行 | 行干净，但同一 Segment 被拆到两视图，心智成本高 |

决策时必须回答：

1. `row_anchor` 规则——直接复用 `video_plan_segment_asset_key`(`video_plan_artifact_services.py:128-141`) 作 Segment anchor；Dialogue 子 anchor 复用 `dialogue_match_keys`(`:71-79`)。不得新造第二套 anchor，不得用数组下标 / 前端行号 / 生成时间。
2. Dialogue 级产物与 Segment 级产物的文件名前缀如何区分。
3. 状态派生在哪一层保证 `_SegmentAudio_Final` 存在时**不**把各条 Dialogue 的 Audio 染绿，反之亦然。

必须产出（并入 `01_Plan_Row_Model.md`）：选定方案 + `row_anchor` 规则 + 粒度区分规则。

### 2.1 Plan Board 按什么定义 Plan Row

在 §2.0 选定粒度方案后，明确一个 Plan Row 代表什么业务单元：

| 可能分行方式 | 适用场景 | 风险 |
| --- | --- | --- |
| 一个 Dialogue 一行 | 对白级音频、图片、视频独立生成 | 多 Dialogue 合成 Segment 时拆太细 |
| 一个 Segment 一行 | StoryBoard 视频生成、尾帧继承 | 一个 Segment 覆盖多个 Dialogue（见 §2.0） |
| 一个 Shot 一行 | 粗粒度镜头计划 | Job 可能吞掉太多任务 |
| 一个 Scene 一行 | 场景级交付 | 不利于恢复单段失败 |
| 一个文件一行 | 数据 / 文档处理 | UI 可能过碎 |

StoryBoard 第一版默认：

```text
一个 Plan Row = 一个 Segment（行内结构遵循 §2.0 选定方案）
row_anchor = video_plan_segment_asset_key 派生的稳定 key
```

必须产出（`01_Plan_Row_Model.md`）：Plan Row 业务含义、稳定 ID 规则、重建时如何继承旧 anchor、拆分/合并/重排时如何处理旧产物。

### 2.2 每个 Plan Row 有多少个 Job

Job 必须按最小可判断状态拆分。硬性规则：

```text
一个 Job 只能承担一个最小业务产物或一个明确动作。
```

典型反例：

```text
Job = Video   同时代表 New Video / Video_Raw 和 Final Video / Video_Final
```

正确拆法：

```text
Job = Video_Raw
Job = Video_Final_Copy
Job = Video_Final_LipSync
Job = Video_Final_AudioMix
Job = TailFrame
```

副产物例外：`Video_Final_*` 在成功时除产出 `Video_Final.*` 外，还顺带写出 `TailFrame.*`（segment 级 `tail_frame`，`video_plan_artifact_services.py:170-171`）。这不算违反“一个 Job 一个产物”，但必须显式建模：终视频 Job 的 `actions` 中标注 `TailFrame` 为 `derived = true` 的副产物，且 `TailFrame` 仍是 Job Catalog 中**可独立判断状态**的 Job（Wait for Events = “`Video_Final.*` 存在”）。副产物可由上游顺带写出，但状态判断必须独立成 Job。

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

UI 可把多个 Job 合并成一个按钮，但状态模型不能合并。例如“提示词+新视频”按钮一次执行 `VideoPrompt` 和 `Video_Raw`，Plan Board 内部仍保留两个 Job 状态。

必须产出（`02_Job_Catalog.md`）：每个 Job 的名称、UI 文案、是否业务产物、是否需绑定业务 JSON、是否可人工确认、是否允许自动运行、是否写 TailFrame 或下游凭证、是否为上游 Job 的副产物。

### 2.3 每个 Job 的 Wait for Events 是什么

每个 Job 必须写出 Wait for Events，不允许只说“前一步完成”。这与纯派生层现有逻辑直接呼应：`derive_*_slot_states` 内部已用 `source_exists / image_exists / raw_exists / final_exists / *_prompt_exists`（`slot_state_services.py:24-36`）表达上下游条件，新模型只是把这些隐式条件显式成 Wait for Events。

| Job | Wait for Events |
| --- | --- |
| `ImagePrompt` | `{anchor}_Image_Source.*` 存在 |
| `Image_New` | `{anchor}_Image_Source.*`、`{anchor}_ImagePrompt.json` 存在 |
| `VideoPrompt` | `{anchor}_Image_New.*` 存在 |
| `Video_Raw` | `{anchor}_Image_New.*`、`{anchor}_VideoPrompt.json` 存在 |
| `Video_Final_Copy` | `{anchor}_Video_Raw.*` 存在，人工确认 |
| `Video_Final_LipSync` | `{anchor}_Video_Raw.*` 存在，音频存在 |
| `TailFrame` | `{anchor}_Video_Final.*` 存在 |

必须产出（`03_Wait_For_Events_Matrix.md`）：每个 Job 的 Wait for Events、AND/OR、条件来源文件、缺失时显示灰还是白、下游已完成时本 Job 是否进入无需运行。

### 2.4 每个 Job 的 Actions 是什么

每个 Job 声明成功后执行什么 Actions、发布什么标准文件。副产物按 §2.2 的 `derived` 规则标注。

| Job | Actions / 输出 |
| --- | --- |
| `ImagePrompt` | `{anchor}_ImagePrompt.json` |
| `Image_New` | `{anchor}_Image_New.*` |
| `VideoPrompt` | `{anchor}_VideoPrompt.json` |
| `Video_Raw` | `{anchor}_Video_Raw.*` |
| `Video_Final_Copy` | `{anchor}_Video_Final.*`；`{anchor}_TailFrame.*`（derived） |
| `Video_Final_LipSync` | `{anchor}_Video_Final.*`；`{anchor}_TailFrame.*`（derived） |

必须产出（`03_Actions_Contract.md`）：业务文件名、Actions 类型、扩展名策略、是否原子发布、是否同步业务 JSON、是否覆盖已有文件、覆盖前是否进入 Asset History、副产物标注。

### 2.5 签名与标记基座（前置）

`step_signature` 与物理标记是“条件、状态派生、执行生命周期”三层的公共依赖，必须先于阶段 3–5 交付。注意现网**已有签名算法**，本基座的任务不是重新发明，而是收敛与下沉：

1. 复用 `video_plan_signature_services.py:262-301` 的六维签名思路（`stable_video_plan_hash` = `sha256(sort_keys 规范化 JSON)`，`:45-54`），定义 per-stage 的 `step_signature` 输入项：plan_board_id、row_anchor、stage、输入/输出条件路径、输入文件指纹、prompt 指纹、模型配置引用、绑定指纹。
2. 定义 `signature12` 截断与 `marker_uid` 物理唯一标识规则（`marker_uid` 只作唯一标识，不参与业务判断）。
3. 定义 Running / Failed 标记命名（`{anchor}_{stage}_Running_{signature12}_{marker_uid}.json` 等）与“旧签名标记不染当前 Job”的判定。
4. 明确与现网 `*_execution_state.json`（`constants.py:24/31/38`）及 `video_generation_plan.ui_cache.json`（`:18`）的并存/退役计划，避免出现第二套签名来源。

必须产出（`02_5_Signature_And_Marker_Spec.md`）：签名输入清单、命名规范、旧标记失效规则、与现网执行态/缓存的收敛计划。

### 2.6 依赖关系是什么

依赖关系不能只靠 UI 顺序，必须形成机器可读 DAG。第一版限制为：

```text
同一 Plan Row 内部串行
不同 Plan Row 之间无交叉依赖
```

但 JSON 中仍保留 `depends_on` / `wait_for_events` / `actions`。

必须产出（`03_Dependency_DAG.md`）：Plan Row 内顺序、可跳过条件、下游产物已存在时是否回退上游、是否允许从中间态继续、是否允许人工只执行某个 Job。

---

## 3. 实施阶段

产出物统一采用 `NN_名称.md` 单套编号。

### 阶段 0：现状盘点与差距分析

目标：找出现有工具、文件、状态和 UI 的真实边界，并明确“已有 / 新建 / 迁移”三态。

必须指定基线：选定 1–2 个真实生产 Session 作为盘点样本，所有清单基于该样本。

必须完成：

1. 列出现有工具目录、`SessionContext` / `SessionOutput` 文件、Working 命名。
2. 列出现有 Plan / State / Result / UI cache JSON（`constants.py` 内 `*_generation_plan.json`、`*_execution_state.json`、`*_execution_result.json`、`*.ui_cache.json`），标注哪些是当前真相、哪些是历史或缓存。
3. 对照 §1.1，逐条标注每个 Plan Board 能力属于「已有 / 新建 / 迁移」。

产出物：

```text
00_Current_State_Audit.md
00_Working_File_Inventory.csv      # 基于基线 Session
00_State_Source_Inventory.md
00_Gap_And_Reuse.md
```

验收标准：

1. 能说明当前 UI 每个颜色由 `slot_state_services.py` 哪条路径而来。
2. 能说明每个工具成功后写了哪些文件、工具目录 Output 与业务 Working 的差异。
3. `00_Gap_And_Reuse.md` 明确指出不得重写项（纯派生层、六维签名、`segment_asset_key`、前后端 tone 契约）与需新建项（物理标记、ExecutionArchive、签名下沉）。

### 阶段 1：Plan Row 模型设计

目标：在 §2.0 决策基础上确定 Plan Row。

产出物：

```text
01_Plan_Row_Model.md          # 含 §2.0 混合粒度决策结论
01_Plan_Row_Anchor_Rules.md   # 复用 video_plan_segment_asset_key
01_Plan_Row_Rebuild_Cases.md
```

验收标准：

1. 重建 Plan 不改变已有产物归属。
2. Segment 拆分、合并、重排有明确继承规则。
3. 不使用数组下标、前端行号、生成时间作为稳定 anchor。
4. Dialogue 级与 Segment 级产物在模型层已分开，不会互相染色。

### 阶段 2：Job 模型设计

目标：确定每个 Plan Row 有哪些 Job，以及 UI 按钮与底层 Job 的关系。

产出物：

```text
02_Job_Catalog.md
02_UI_To_Job_Mapping.md
02_Subtask_Rules.md
```

验收标准：

1. 一个 Job 不再吞掉多个不可区分任务。
2. UI 可合并按钮，但内部 Job 必须可单独拥有 Job Status。
3. Raw / Final / TailFrame 等易混产物拆开；副产物按 §2.2 的 `derived` 规则建模。

### 阶段 2.5：签名与标记基座（前置）

目标：交付 `02_5_Signature_And_Marker_Spec.md`，作为阶段 3、4、5 的公共依赖。

验收标准：

1. per-stage 签名输入项确定，且“输入/Prompt/输出路径变更 → 签名变化、仅重建 Plan Board 不变签名”可举例说明。
2. 标记命名、归档命名确定。
3. 与现网 `*_execution_state.json` 及 `ui_cache.json` 的收敛与退役路径明确，不留两套签名来源。

### 阶段 3：条件与产物合同

目标：确定所有 `Wait for Events / Actions` 与文件命名合同。

产出物：

```text
03_Wait_For_Events_Matrix.md
03_Actions_Contract.md
03_File_Naming_Contract.md
03_Dependency_DAG.md
```

验收标准：

1. 每个 Job 都有标准 Wait for Events、标准 Actions 和输出文件。
2. 输出文件和 Job Status 一一对应。
3. 文件名不依赖工具目录、不依赖 UI 文案。

### 阶段 4：状态派生服务（含只读垂直切片里程碑）

目标：把 Job Status 的统一计算落到可复用入口，映射到白 / 绿 / 红 / 黄 / 灰。

实现约束：

1. **复用而非重写** `slot_state_services.py` 的五色派生与 tone 映射（`:76-86`）；只替换上游“文件/绑定是否存在”的来源——从依赖 `*_execution_state.json` 逐步切到 Working 文件 + 物理标记 + per-stage 签名。
2. 注意现网 `_apply_execution`(`:102`) 已保证“绿色优先”；本阶段真正要校准的是 `SlotInputs.file_exists` 的计算来源，以及 `ui_cache.json`(`constants.py:18`) 是否被签名正确失效——这两处才是状态漂移的真实风险点。
3. **里程碑 M1（只读垂直切片）**：对基线 Session 的某个真实 Plan，纯从 Working 文件 + 新物理标记派生 Job Status，前端真实渲染，但不改任何执行器。用于在铺开后续阶段前验证派生正确，对齐 `03_最终需求实现细节.md` §14“先只读接入”。

产出物：

```text
04_Job_Status_Derivation_Spec.md
04_Job_Status_Service_API.md
04_Job_Status_Test_Cases.md
04_ReadOnly_Vertical_Slice_Result.md
```

验收标准：

1. `Ended OK` 只由标准业务文件和必要绑定决定。
2. `Executing` 只由当前签名 Running 标记决定（依赖阶段 2.5，无循环依赖）。
3. `Ended Not OK` 只由当前签名 Failed 标记决定。
4. `Waiting` 区分 `blocked_waiting_input` / `skipped_consumed_by_downstream` / `wait_user` / `wait_resources`。
5. M1 只读切片在真实 Session 上与现网 UI 颜色对照，差异都有可解释 reason。

### 阶段 5：执行生命周期

目标：让工具执行前、中、后都有一致的状态落点，并把执行器产物发布收敛到标准命名。

产出物：

```text
05_Execution_Lifecycle.md
05_Archive_Rules.md
```

（标记规范已前移至 `02_5_Signature_And_Marker_Spec.md`，此处只引用。）

验收标准：

1. 点击执行才创建 Running 标记。
2. 外部 API 任务 ID 返回后立即写入 Running 标记，支持后端重启 / 刷新后恢复查询（参见 `03` §8.2）。
3. 成功后发布业务文件，再归档 Running。
4. 失败后保留 Failed 热路径标记。
5. 重新运行前归档旧 Running / Failed。

### 阶段 6：UI 与交互

目标：Plan Board 前端只消费后端派生结果。

tone 兼容：第一版保留现网 tone 枚举 `done / pending / disabled / running / failed` 与现有 `is-*` CSS 类（`slot_state_services.py:76-86` 已同时输出 `tone` 与 `ui_tone`）。设计文档中的 `white/green/red/yellow/gray` 仅作颜色语义说明，不改前端枚举，避免无价值的大面积前端改动。

产出物：

```text
06_UI_Interaction_Spec.md
06_Color_Legend.md          # 颜色语义 ↔ tone 枚举 ↔ CSS 类 三者对应
06_Manual_Action_List.md
```

验收标准：

1. UI 不自己合并三套状态。
2. 固定 Job 始终渲染，不因为没有 plan item 就消失。
3. 每个颜色都有可解释 reason。
4. 一键执行可触发多个 stage，但返回时拆开显示每个 stage 的状态。

### 阶段 7：回归测试、非功能与迁移

产出物：

```text
07_Regression_Test_Plan.md
07_NonFunctional_Test_Plan.md
07_Migration_Checklist.md
07_Known_Pitfalls.md
```

功能验收标准：

1. 旧 `Image_01` 不再被误判为新图（现网已有拦截 `video_plan_signature_services.py:308`，迁移后须保持）。
2. Raw 存在不等于 Final 完成。
3. SegmentAudio 不把 Dialogue Audio 染绿（落实 §2.0）。
4. TailFrame 继承优先级正确。
5. Working 文件优先级高于旧 execution JSON。
6. 执行成功但绑定失败进入修复态，不要求重跑。

非功能验收标准：

1. **性能**：基线 Session 下，单次 Plan Board 刷新（扫 Working 目录 + 重算签名）耗时有上限，并给出 Segment 数量与耗时关系数据。
2. **并发**：多 Tab / 多用户同时操作同一 Session 时，物理标记与业务文件发布无相互覆盖。
3. **回滚**：新派生服务出错或开关关闭时，可 fallback 到现网 `slot_state_services.py` 旧来源，不阻塞用户。
4. **恢复**：后端重启 / 页面刷新后，能从 Running 标记中的 `external_api_tasks` 恢复外部任务查询。

---

## 4. StoryBoard 踩坑提醒

以下踩坑均经现网代码验证，是本项目要根除或固化的目标。

### 4.1 一个 Job 代表多个任务

问题：`视频` Job 曾同时代表 Raw Video 和 Final Video，导致 Job Status、删除、Confirm、Sync 全部混在一起。
规则：一个 Job 如有两个完成标准，必须拆成两个 Job。UI 可合并显示，Job Status 不能合并。

### 4.2 工具目录产物被当成业务产物

问题：`Sxx_Tool/Working` 是执行快照，`SessionOutput/storyboard/Working`(`constants.py:13`) 才是业务真相。
规则：Plan Board 只认业务 Working。工具目录产物必须发布到业务 Working 后才算完成。

### 4.3 状态来源漂移（精确定位）

现网纯派生层已防住“execution 状态盖过已完成文件”：`_apply_execution`(`slot_state_services.py:102`) 在 `file_exists` 为真时直接短路。因此真实风险**不在**派生层，而在两处上游：

1. `SlotInputs.file_exists`（`:24-36`）由谁、依据什么计算——若上游误判文件存在/不存在，派生再正确也会错色。
2. `video_generation_plan.ui_cache.json`(`constants.py:18`) 是否被六维签名正确失效（`video_plan_cache_matches`，`signature_services.py:303-322`）。缓存未失效会让旧状态压过当前 Working。
规则：业务文件完成后绿色优先；迁移时把 file_exists 来源统一到 Working 一级文件 + 物理标记，并确保 ui_cache 由签名驱动失效。

### 4.4 SegmentAudio 与 Dialogue Audio 混淆（已升级为 §2.0 硬决策）

问题：Segment 合成音频（`segment_audio_path`，`artifact_services.py:181`）存在，不代表每条 Dialogue 的 Audio（`dialogue_audio_tasks`，`:176`）完成。
规则：Job 必须声明粒度（grain）。Dialogue 级与 Segment 级产物不能互相染绿。方案在 §2.0 选定。

### 4.5 尾帧继承被下游完成态覆盖

问题：有些 Segment 需要上一段 `tail_frame`(`artifact_services.py:170-171`) 物化成新图，但通用逻辑看到 Final 存在后直接把前置步骤置灰。
规则：`previous_tail_frame` 是明确输入条件。只要当前 stage 需要物化尾帧，就不能被泛化的“下游已完成”逻辑吞掉。

### 4.6 双 JSON 同步失败

问题：只更新 `srt_storyboard.json`(`constants.py:5`) 或只更新 `koubo_storyboard_edit.json`(`:6`) 会造成 UI 与执行状态漂移。
规则：需双写的业务绑定必须作为一个事务。任一失败，不得显示绿色成功。

### 4.7 CLI 与页面执行边界混淆

问题：页面点击 Plan 重建时走 CLI 子进程，会带来路径、环境、JSON 解析和性能问题。
规则：页面触发优先走同一逻辑的函数入口；CLI 保留给自动化、runbook 和人工终端执行。

### 4.8 Prompt 与普通素材删除规则不同

问题：Prompt 文件既是产物又可编辑，不能像图片、视频一样被普通 Job 产物清除动作误删。
规则：Prompt 文件存在即绿色。修改 Prompt 必须走专门编辑流程，并改变后续 stage 的 step signature。

---

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
11. SegmentAudio_Final 完成时，各条 Dialogue 的 Audio_Final 不被误染绿（§2.0）。
12. 关闭新派生开关时可 fallback 到现网派生，不阻塞用户（回滚）。

---

## 6. 建议落地顺序（与迁移策略对齐）

呼应 `03_最终需求实现细节.md` §14，落地为增量迁移而非一次性切换：

1. 阶段 0：盘点 + `Gap_And_Reuse`，钉死“已有 / 新建 / 迁移”，明确不重写纯派生层与六维签名。
2. §2.0 + 阶段 1：定死 Plan Row 粒度与 anchor（复用 `segment_asset_key`）。
3. 阶段 2 + 2.5：Job Catalog 与“签名下沉到物理标记”基座。
4. 阶段 4 M1：只读垂直切片，从 Working 文件 + 物理标记派生，不改执行器，用真实 Session 验证。
5. 阶段 5：接入物理 Running / Failed 标记，再把执行器发布路径收敛到标准命名。
6. 阶段 6 / 7：UI 收敛（保留现 tone 枚举）、回归 + 非功能测试，最后清理旧 `Image_01` 与旧 execution JSON / ui_cache fallback。

第一版验收标准不是“像 Control-M 一样强”，而是：**每个 Job 为什么是 Ended OK / Executing / Ended Not OK / Waiting，都能从 Workspace 文件、Wait for Events、Actions 合同和 step_signature 解释清楚，且不重写现网已经正确工作的派生层与签名机制。**
